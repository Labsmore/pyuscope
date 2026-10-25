#!/usr/bin/env -S uv run
"""
Headless scripting tests: run scripts against a HeadlessSession (no Qt)

Run with:
    ./test/unit/test_headless.py
or
    uv run python -m unittest discover -s test/unit
"""

import json
import os
import shutil
import tempfile

# Keep test data out of the working tree
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-headless-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

import subprocess
import sys
import textwrap
import threading
import unittest

from uscope.scripting import (
    HeadlessScriptHost,
    ScriptRunner,
    input_buttons,
    input_defaults,
    input_find_button,
    input_set,
)
from uscope import config as uconfig
from uscope.config import get_bc
from uscope.planner.planner_util import microscope_to_planner_config
from uscope.scan_runner import ScanRunnerListener
from uscope.session import HeadlessSession

REPO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_DIR = os.path.join(REPO_DIR, "uscope", "script")


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


class HeadlessTestCase(unittest.TestCase):
    session = None
    # None: microscope config imager (mock-grbl: gst-videotestsrc pipeline)
    imager = None

    @classmethod
    def setUpClass(cls):
        cls.session = HeadlessSession(microscope_name="mock-grbl",
                                      imager=cls.imager)

    @classmethod
    def tearDownClass(cls):
        cls.session.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_TMP_ROOT)
        self.logs = []
        self.host = HeadlessScriptHost(image_dir=os.path.join(
            self.tmp, "images"),
                                       log_fn=self.logs.append)
        self.runner = ScriptRunner(session=self.session, host=self.host)

    def write_script(self, body):
        fn = os.path.join(self.tmp, "script.py")
        with open(fn, "w") as f:
            f.write(textwrap.dedent(body))
        return fn

    def run_script(self, filename, input_=None, timeout=60):
        plugin = self.runner.load(filename)
        if input_ is None:
            input_ = input_defaults(plugin.input_config())
        self.host.done_event.clear()
        self.runner.run(input_)
        self.assertTrue(self.host.done_event.wait(timeout),
                        "script did not finish")
        plugin.join(timeout=5)
        return plugin


class TestScripts(HeadlessTestCase):
    def test_hello(self):
        plugin = self.run_script(os.path.join(SCRIPT_DIR, "hello.py"))
        self.assertTrue(plugin.succeeded(), plugin.result_message)
        self.assertIn("Hello, world!", self.logs)

    def test_all_button(self):
        fn = os.path.join(SCRIPT_DIR, "test", "test_all.py")
        plugin = self.runner.load(fn)
        config = plugin.input_config()
        input_ = input_defaults(config)
        input_["button"] = input_find_button(config, "all")
        self.host.done_event.clear()
        self.runner.run(input_)
        self.assertTrue(self.host.done_event.wait(120))
        self.assertTrue(plugin.succeeded(), plugin.result_message)

    def test_exception_fails(self):
        plugin = self.run_script(
            os.path.join(SCRIPT_DIR, "test", "test_exception.py"))
        self.assertFalse(plugin.succeeded())
        self.assertIn("crash", plugin.result_message)

    def test_update_defaults(self):
        plugin = self.run_script(
            os.path.join(SCRIPT_DIR, "test", "test_update.py"))
        self.assertTrue(plugin.succeeded(), plugin.result_message)
        self.assertEqual(plugin.new_defaults, {"Counter": 2.0})

    def test_rerun(self):
        fn = os.path.join(SCRIPT_DIR, "hello.py")
        for _ in range(2):
            plugin = self.run_script(fn)
            self.assertTrue(plugin.succeeded(), plugin.result_message)

    def test_stop(self):
        fn = self.write_script("""
            from uscope.scripting import ScriptingPlugin

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    self.log("started")
                    self.sleep(60)
            """)
        plugin = self.runner.load(fn)
        self.host.done_event.clear()
        self.runner.run({})
        while "started" not in self.logs:
            self.assertTrue(plugin.is_alive())
            threading.Event().wait(0.05)
        self.runner.stop()
        self.assertTrue(self.host.done_event.wait(10))
        self.assertFalse(plugin.succeeded())

    def test_motion_and_image(self):
        fn = self.write_script("""
            from uscope.scripting import ScriptingPlugin

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    self.move_absolute({"x": 1.25, "y": 0.5})
                    pos = self.position()
                    assert abs(pos["x"] - 1.25) < 0.01, pos
                    assert abs(pos["y"] - 0.5) < 0.01, pos
                    im = self.image()
                    self.show_image(im, "My Image")
            """)
        plugin = self.run_script(fn)
        self.assertTrue(plugin.succeeded(), plugin.result_message)
        self.assertEqual(len(self.host.images), 1)
        self.assertTrue(os.path.exists(self.host.images[0]))

    def test_prompt_default_cancels(self):
        fn = self.write_script("""
            from uscope.scripting import ScriptingPlugin

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    self.log("answer: " + str(self.message_box_yes_cancel(
                        "Title", "Continue?")))
            """)
        self.host.interactive = False
        plugin = self.run_script(fn)
        self.assertTrue(plugin.succeeded(), plugin.result_message)
        self.assertIn("answer: False", self.logs)

    def test_planner(self):
        basename = "test_planner_" + os.path.basename(self.tmp)
        scan_dir = os.path.join(self.session.scan_dir(), basename)
        fn = self.write_script(f"""
            from uscope.scripting import ScriptingPlugin
            from uscope.planner.planner_util import microscope_to_planner_config

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    pconfig = microscope_to_planner_config(
                        self.session.microscope,
                        objectivei=0,
                        contour={{
                            "start": {{"x": 0.0, "y": 0.0}},
                            "end": {{"x": 1.0, "y": 1.0}},
                        }})
                    ret = self.run_planner_hconfig({{
                        "pconfig": pconfig,
                        "out_dir_config": {{"user_basename": {basename!r}}},
                    }})
                    assert ret["result"] == "ok", ret
            """)
        plugin = self.run_script(fn, timeout=120)
        self.assertTrue(plugin.succeeded(), plugin.result_message)
        images = [fn for fn in os.listdir(scan_dir) if fn.endswith(".jpg")]
        self.assertGreater(len(images), 1)


class TestGstImager(HeadlessTestCase):
    def test_pipeline(self):
        self.assertIsNotNone(self.session.vidpip)
        self.assertEqual(self.session.usc.imager.source(), "gst-videotestsrc")

    def test_properties(self):
        fn = self.write_script("""
            from uscope.scripting import ScriptingPlugin

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    props = self.imager_get_disp_properties()
                    assert "exposure" in props, props
                    self.imager_set_disp_property("exposure", 20000)
                    assert self.imager_get_disp_property("exposure") == 20000
                    # gstreamer enum
                    assert type(props["pattern"]) is int, props
                    # JSON clients may only have floats
                    self.imager_set_disp_property("pattern", 18.0)
                    assert self.imager_get_disp_property("pattern") == 18
                    self.imager_set_disp_property("exposure", 1234.6)
                    assert self.imager_get_disp_property("exposure") == 1235
                    try:
                        self.imager_set_disp_property("pattern", "ball")
                        assert 0, "accepted a string"
                    except ValueError:
                        pass
                    self.imager_set_disp_property("pattern", props["pattern"])
            """)
        plugin = self.run_script(fn)
        self.assertTrue(plugin.succeeded(), plugin.result_message)

    def test_properties_json(self):
        """Web APIs / image metadata need plain values"""
        props = self.session.imager.get_properties()
        self.assertEqual(json.loads(json.dumps(props)), props)
        json.dumps(self.session.imager_properties.get_prop_cache())

    def test_capture_format(self):
        fn = self.write_script("""
            from uscope.scripting import ScriptingPlugin

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    formats = self.imager_supported_capture_formats()
                    assert formats == ["RGB", "ARGB64"], formats
                    try:
                        self.imager_set_capture_format("ARGB64")
                        im = self.image_captured()
                        assert im.pixel_format == "ARGB64", im.pixel_format
                        assert im.data.dtype.name == "uint16", im.data.dtype
                    finally:
                        self.imager_set_capture_format("RGB")
                    im = self.image_captured()
                    assert im.pixel_format == "RGB", im.pixel_format
                    assert im.image.mode == "RGB", im.image.mode
            """)
        plugin = self.run_script(fn)
        self.assertTrue(plugin.succeeded(), plugin.result_message)


class TestSoftwareColor(HeadlessTestCase):
    @classmethod
    def setUpClass(cls):
        # usc is cached by microscope name: an earlier session's microscope
        # (and its calibration store) would otherwise be used
        uconfig.usc = None
        super().setUpClass()

    def test_white_balance_and_gain(self):
        fn = self.write_script("""
            import numpy as np
            from uscope.scripting import ScriptingPlugin

            class Plugin(ScriptingPlugin):
                def run_test(self):
                    # Color bars aren't a white target
                    try:
                        self.white_balance_calibrate(quality="quick")
                        assert 0, "expected failure"
                    except Exception as e:
                        assert "White balance failed" in str(e), e
                    assert self.white_balance_gains() is None
                    raw = np.array(self.image_captured(mode="raw").image)
                    try:
                        self.set_software_gain(0.5)
                        assert self.software_gain() == 0.5, self.software_gain()
                        im = np.array(self.image())
                    finally:
                        self.set_software_gain(1.0)
                    # Bars are static
                    assert abs(im.mean() - raw.mean() / 2) < 2, (im.mean(),
                                                                 raw.mean())
            """)
        plugin = self.run_script(fn)
        self.assertTrue(plugin.succeeded(), plugin.result_message)


class TestObjectiveState(HeadlessTestCase):
    imager = "mock"

    def setUp(self):
        super().setUp()
        self.state = self.session.objective_state
        self.changes = []
        self.state.add_listener(self.changes.append)
        self.addCleanup(self.state.listeners.remove, self.changes.append)
        self.addCleanup(self.state.cache_sn_load, {})

    def test_select(self):
        names = self.state.names()
        self.assertGreater(len(names), 1)
        self.state.select(names[1])
        self.assertEqual(self.session.get_active_objective(), names[1])
        self.assertEqual(self.session.microscope.get_active_objective(),
                         names[1])
        self.assertEqual(self.session.objective_config()["name"], names[1])
        self.assertEqual(self.changes[-1]["name"], names[1])
        with self.assertRaises(ValueError):
            self.state.select("1000X nope")

    def test_global_scalar(self):
        x_view = self.state.config()["x_view"]
        self.state.set_global_scalar(2.0)
        self.assertAlmostEqual(self.state.config()["x_view"], x_view / 2)
        self.assertAlmostEqual(self.changes[-1]["x_view"], x_view / 2)
        self.state.set_global_scalar(None)
        self.assertAlmostEqual(self.state.config()["x_view"], x_view)

    def test_cache(self):
        name = self.state.names()[-1]
        self.state.select(name)
        self.state.set_global_scalar(1.5)
        cachej = {}
        self.state.cache_sn_save(cachej)
        self.assertEqual(cachej["objective"]["name"], name)
        self.assertEqual(cachej["objective"]["global_scalar"], "1.5")
        self.state.cache_sn_load({})
        self.assertIsNone(self.state.global_scalar)
        self.state.select(self.state.names()[0])
        self.state.cache_sn_load(cachej)
        self.assertEqual(self.state.name(), name)
        self.assertEqual(self.state.global_scalar, 1.5)


class TestArgusState(unittest.TestCase):
    def setUp(self):
        self.fn = get_bc().argus_cache_fn()
        self.addCleanup(lambda: os.path.exists(self.fn) and os.remove(self.fn))

    def write_state(self, objective):
        cachej = {
            "microscopes": {
                "mock-grbl_sn-1234": {
                    "model": "mock-grbl",
                    "serial": "1234",
                    "objective": objective,
                }
            }
        }
        with open(self.fn, "w") as f:
            json.dump(cachej, f)
        with open(self.fn) as f:
            return f.read()

    def session(self, **kwargs):
        session = HeadlessSession(microscope_name="mock-grbl",
                                  imager="mock",
                                  **kwargs)
        self.addCleanup(session.shutdown)
        return session

    def test_load(self):
        before = self.write_state({
            "name": "20X",
            "global_scalar": "2.0",
            "um_per_pixel_raw_1x": 7.5,
        })
        session = self.session()
        self.assertEqual(session.get_active_objective(), "20X")
        self.assertEqual(session.objective_state.global_scalar, 2.0)
        self.assertEqual(session.objective_state.um_per_pixel_raw_1x, 7.5)
        session.objective_state.select("5X")
        session.shutdown()
        with open(self.fn) as f:
            self.assertEqual(f.read(), before)
        # Legacy GUI value is not imported into the calibration store
        self.assertIsNone(session.microscope.cal_store.get_value(
            "optics.um_per_pixel_raw_1x"))

    def test_disabled(self):
        self.write_state({"name": "20X", "global_scalar": "2.0"})
        session = self.session(argus_state=False)
        self.assertEqual(session.get_active_objective(), "5X")
        self.assertIsNone(session.objective_state.global_scalar)

    def test_unknown_objective(self):
        self.write_state({"name": "1000X nope", "global_scalar": ""})
        session = self.session()
        self.assertEqual(session.get_active_objective(), "5X")

    def test_invalid_file(self):
        with open(self.fn, "w") as f:
            f.write("{not json")
        session = self.session()
        self.assertEqual(session.get_active_objective(), "5X")


class TestScanRunner(HeadlessTestCase):
    imager = "mock"

    class Listener(ScanRunnerListener):
        def __init__(self):
            self.events = []
            self.progress = []
            self.done = threading.Event()

        def scan_started(self, hconfig):
            self.events.append(("started", hconfig["out_dir"]))

        def scan_progress(self, state):
            self.progress.append(state["type"])

        def scan_done(self, hconfig, result):
            self.events.append(("done", result["result"]))

        def queue_done(self, results):
            self.events.append(("queue_done", len(results)))
            self.done.set()

    def setUp(self):
        super().setUp()
        self.runner = self.session.scan_runner
        self.listener = self.Listener()
        self.runner.add_listener(self.listener)
        self.addCleanup(self.runner.listeners.remove, self.listener)

    def hconfig(self, basename):
        pconfig = microscope_to_planner_config(self.session.microscope,
                                               objectivei=0,
                                               contour={
                                                   "start": {
                                                       "x": 0.0,
                                                       "y": 0.0
                                                   },
                                                   "end": {
                                                       "x": 0.5,
                                                       "y": 0.5
                                                   },
                                               })
        return {
            "pconfig": pconfig,
            "out_dir_config": {
                "user_basename": basename + "_" + os.path.basename(self.tmp)
            },
        }

    def run_queue(self, hconfigs, **kwargs):
        self.runner.run(hconfigs, **kwargs)
        self.assertTrue(self.listener.done.wait(120), "queue did not finish")
        self.runner.join(timeout=5)
        self.assertFalse(self.runner.is_running())

    def test_scan(self):
        props = self.session.imager.get_properties()
        hconfig = self.hconfig("runner")
        scan_started = self.listener.scan_started

        def mess_up_properties(hconfig):
            scan_started(hconfig)
            self.session.imager.set_properties({"loopback_int": 1})

        self.listener.scan_started = mess_up_properties
        self.run_queue([hconfig])
        self.assertEqual(self.listener.events,
                         [("started", hconfig["out_dir"]), ("done", "ok"),
                          ("queue_done", 1)])
        self.assertIn("begin", self.listener.progress)
        self.assertIn("image", self.listener.progress)
        self.assertIsNone(self.runner.progress_ts())
        with open(os.path.join(hconfig["out_dir"], "uscan.log")) as f:
            self.assertIn("Planner done", f.read())
        self.assertEqual(self.session.imager.get_properties(), props)

    def test_abort_on_failure(self):
        bad = self.hconfig("bad")
        bad["pconfig"] = {}
        results = []
        bad["callback_done"] = lambda result: results.append(result)
        self.run_queue([bad, self.hconfig("next")])
        self.assertEqual(results[0]["result"], "exception")
        self.assertEqual(self.listener.events[-1], ("queue_done", 1))

        self.listener.done.clear()
        self.run_queue([bad, self.hconfig("next2")], abort_on_failure=False)
        self.assertEqual(self.listener.events[-1], ("queue_done", 2))
        self.assertEqual(self.listener.events[-2], ("done", "ok"))


class TestMockImager(HeadlessTestCase):
    imager = "mock"

    def test_no_pipeline(self):
        self.assertIsNone(self.session.vidpip)

    def test_image(self):
        plugin = self.run_script(
            os.path.join(SCRIPT_DIR, "test", "test_image.py"))
        self.assertTrue(plugin.succeeded(), plugin.result_message)


class TestInputHelpers(unittest.TestCase):
    CONFIG = {
        "Mode": {
            "widget": "QComboBox",
            "values": ["Slow", "Fast"],
            "default": "Slow",
        },
        "distance": {
            "widget": "QLineEdit",
            "type": float,
            "default": 1.0,
        },
        "Buttons": {
            "widget": "QPushButtons",
            "buttons": {"Go": "go"},
        },
    }

    def test_defaults(self):
        self.assertEqual(input_defaults(self.CONFIG), {
            "Mode": "Slow",
            "distance": 1.0
        })

    def test_set_converts(self):
        ret = input_set(self.CONFIG, input_defaults(self.CONFIG),
                        {"distance": "2.5"})
        self.assertEqual(ret["distance"], 2.5)
        self.assertEqual(ret["Mode"], "Slow")

    def test_buttons(self):
        button = {"group": "Buttons", "label": "Go", "value": "go"}
        self.assertEqual(input_buttons(self.CONFIG), [button])
        self.assertEqual(input_find_button(self.CONFIG, "Go"), button)
        self.assertEqual(input_find_button(self.CONFIG, "go"), button)
        with self.assertRaises(ValueError):
            input_find_button(self.CONFIG, "nope")


class TestNoQt(unittest.TestCase):
    def test_no_qt_import(self):
        """Headless entry points must not pull in Qt"""
        code = textwrap.dedent(f"""
            import sys
            for mod in ("PyQt5", "PyQt6", "PySide2", "PySide6"):
                sys.modules[mod] = None
            import uscope.scripting
            import uscope.session
            import uscope.app.headless.main
            from uscope.session import HeadlessSession
            from uscope.scripting import HeadlessScriptHost, ScriptRunner
            # gst-videotestsrc pipeline
            with HeadlessSession(microscope_name="mock-grbl") as session:
                assert session.vidpip is not None
                host = HeadlessScriptHost()
                runner = ScriptRunner(session=session, host=host)
                plugin = runner.load(
                    {os.path.join(SCRIPT_DIR, "test", "test_image.py")!r})
                runner.run({{}})
                assert host.done_event.wait(30)
                assert plugin.succeeded(), plugin.result_message
            for mod in ("PyQt5", "PyQt6", "PySide2", "PySide6"):
                assert sys.modules[mod] is None, mod
            print("NOQT_OK")
            """)
        out = subprocess.run([sys.executable, "-c", code],
                             cwd=REPO_DIR,
                             capture_output=True,
                             text=True,
                             timeout=120)
        self.assertIn("NOQT_OK", out.stdout, out.stdout + out.stderr)


class TestInstruments(HeadlessTestCase):
    FN = os.path.join(SCRIPT_DIR, "test", "instrument_function.py")

    def make_runner(self, name):
        return ScriptRunner(session=self.session,
                            host=self.host,
                            instrument_config={
                                "name": name,
                                "parameters": {}
                            })

    def test_attach_detach(self):
        subsystems = self.session.microscope.subsystems
        runner = self.make_runner("itest_attach")
        plugin = runner.load(self.FN)
        self.assertIs(subsystems["itest_attach"].plugin, plugin)
        self.assertIs(plugin.subsystem, subsystems["itest_attach"])
        runner.unload()
        self.assertNotIn("itest_attach", subsystems)

    def test_reload_and_call(self):
        # Reload used to leave the subsystem pointing at the old plugin
        microscope = self.session.microscope
        runner = self.make_runner("itest_reload")
        runner.load(self.FN)
        plugin = runner.load(self.FN)
        self.assertIs(microscope.subsystems["itest_reload"].plugin, plugin)
        self.host.done_event.clear()
        runner.run({"init": True})
        try:
            microscope.subsystem_function_ts("itest_reload", "shout", {"n": 1})
            for _ in range(100):
                if "yipeeee!" in self.logs:
                    break
                threading.Event().wait(0.05)
            self.assertIn("yipeeee!", self.logs)
        finally:
            runner.stop()
            self.assertTrue(self.host.done_event.wait(10))
            runner.unload()

    def test_multiple_instances(self):
        subsystems = self.session.microscope.subsystems
        runner1 = self.make_runner("itest_multi")
        runner2 = self.make_runner("itest_multi_2")
        plugin1 = runner1.load(self.FN)
        plugin2 = runner2.load(self.FN)
        self.assertIsNot(plugin1, plugin2)
        self.assertIs(subsystems["itest_multi"].plugin, plugin1)
        self.assertIs(subsystems["itest_multi_2"].plugin, plugin2)
        runner1.unload()
        self.assertIn("itest_multi_2", subsystems)
        runner2.unload()

    def test_removed_subsystem(self):
        from uscope.subsystem import NoSuchFunction
        runner = self.make_runner("itest_removed")
        runner.load(self.FN)
        runner.unload()
        with self.assertRaises(NoSuchFunction):
            self.session.microscope.subsystem_function_ts(
                "itest_removed", "shout", {"n": 1})

    def test_not_instrument(self):
        runner = self.make_runner("itest_not")
        with self.assertRaises(TypeError):
            runner.load(os.path.join(SCRIPT_DIR, "hello.py"))
        self.assertIsNone(runner.plugin)
        self.assertNotIn("itest_not", self.session.microscope.subsystems)

    def test_get_cal(self):
        from uscope.calibration import get_cal
        cal = get_cal(self.session.microscope)
        self.assertIsNotNone(cal.pos_fid70)
        self.assertIsNotNone(cal.pos_fid150)
        self.assertIs(get_cal(self.session.microscope), cal)


if __name__ == "__main__":
    unittest.main()
