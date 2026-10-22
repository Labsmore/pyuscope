#!/usr/bin/env -S uv run
"""
Headless scripting tests: run scripts against a HeadlessSession (no Qt)

Run with:
    ./test/unit/test_headless.py
or
    uv run python -m unittest discover -s test/unit
"""

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
from uscope.session import HeadlessSession

REPO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_DIR = os.path.join(REPO_DIR, "uscope", "script")


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


class HeadlessTestCase(unittest.TestCase):
    session = None

    @classmethod
    def setUpClass(cls):
        cls.session = HeadlessSession(microscope_name="mock-grbl")

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
            with HeadlessSession(microscope_name="mock-grbl") as session:
                host = HeadlessScriptHost()
                runner = ScriptRunner(session=session, host=host)
                plugin = runner.load({os.path.join(SCRIPT_DIR, "hello.py")!r})
                runner.run({{}})
                assert host.done_event.wait(30)
                assert plugin.succeeded(), plugin.result_message
            print("NOQT_OK")
            """)
        out = subprocess.run([sys.executable, "-c", code],
                             cwd=REPO_DIR,
                             capture_output=True,
                             text=True,
                             timeout=120)
        self.assertIn("NOQT_OK", out.stdout, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
