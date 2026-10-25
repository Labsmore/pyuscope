#!/usr/bin/env -S uv run
"""
Regression tests for motion / planner reliability fixes

No hardware is required or touched: uses the "mock" and "mock-grbl"
configurations which emulate the motion controller in software
"""

import os
import shutil
import tempfile

# Keep test data out of the working tree / real data dir
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
# Never pick up a real controller / microscope
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

import glob
import json
import threading
import time
import types
import unittest
from unittest import mock

import serial

from uscope.config import USCMotion
from uscope.imager.image_sequence import CapturedImage
from uscope.imager.imager import MockImager
from uscope.joystick import Joystick
from uscope.microscope import Microscope, MicroscopeStop
from uscope.motion.grbl import parse_rc_rates
from uscope.motion.hal import AxisExceeded, MotionCritical
from uscope.motion.thread import SimpleMotionThread
from uscope.planner.planner_util import microscope_to_planner_config, get_planner
from uscope.planner.plugins import PlannerStacker


def quiet_log(msg="", *args, **kwargs):
    pass


class TestCommon(unittest.TestCase):

    def setUp(self):
        self.verbose = int(os.getenv("TEST_VERBOSE", "0"))
        self.log = print if self.verbose else quiet_log
        self.out_dir = tempfile.mkdtemp(prefix="pyuscope-out-", dir=_TMP_ROOT)

    def tearDown(self):
        shutil.rmtree(self.out_dir, ignore_errors=True)

    def make_microscope(self, name="mock", **kwargs):
        microscope = Microscope(name=name, log=self.log, **kwargs)
        self.addCleanup(self.close_microscope, microscope)
        return microscope

    def close_microscope(self, microscope):
        if microscope.motion:
            microscope.motion.close()

    def configure_mock(self, microscope, **options):
        """Reconfigure a mock HAL with explicit modifier options"""
        microscope.motion.configure(options)
        return microscope.motion


class MotionHALTestCase(TestCommon):

    def test_backlash_recursing_reset(self):
        """A failed compensation move must not disable compensation"""
        microscope = self.make_microscope()
        motion = self.configure_mock(microscope,
                                     backlash={
                                         "x": 0.1,
                                         "y": 0.1,
                                         "z": 0.1
                                     },
                                     backlash_compensation={"x": +1},
                                     soft_limits={"x": (0.0, 10.0)})
        backlash = motion.modifiers["backlash"]
        motion.move_absolute({"x": 5.0})
        # Coming from above: compensation move to -0.05 violates the limit
        with self.assertRaises(AxisExceeded):
            motion.move_absolute({"x": 0.05})
        self.assertFalse(backlash.recursing)
        # Still compensating: next move from above overshoots then returns
        moves = []
        orig = motion._move_absolute
        motion._move_absolute = lambda pos: (moves.append(dict(pos)), orig(pos)
                                             )
        motion.move_absolute({"x": 2.0})
        self.assertEqual(len(moves), 2, moves)
        self.assertAlmostEqual(moves[0]["x"], 1.9)
        self.assertAlmostEqual(motion.pos()["x"], 2.0)

    def test_soft_limit_one_sided(self):
        microscope = self.make_microscope()
        motion = self.configure_mock(microscope,
                                     soft_limits={"x": (None, 10.0)})
        limits = motion.get_soft_limits()
        self.assertNotIn("x", limits["mins"])
        self.assertEqual(limits["maxs"]["x"], 10.0)
        motion.move_absolute({"x": -5.0})
        with self.assertRaises(AxisExceeded):
            motion.move_absolute({"x": 20.0})
        self.assertAlmostEqual(motion.pos()["x"], -5.0)

        motion = self.configure_mock(microscope,
                                     soft_limits={"x": (-1.0, None)})
        motion.move_absolute({"x": 100.0})
        # No upper bound; lower bound still enforced
        motion.move_absolute({"x": 0.0})
        with self.assertRaises(AxisExceeded):
            motion.move_absolute({"x": -2.0})

    def test_scalar_abs_munge(self):
        microscope = self.make_microscope()
        motion = self.configure_mock(microscope, scalars={"x": 2.0})
        axes = {"x": 1.0, "y": 1.0}
        motion.munge_axes_user2machine_abs(axes)
        self.assertEqual(axes, {"x": 2.0, "y": 1.0})
        # Absolute jogs go through the same path
        motion.jog_abs({"x": 1.0}, 100)
        motion.jog_cancel()

    def test_mock_status_does_not_drift(self):
        """Status callbacks must not munge the mock's live position"""
        microscope = self.make_microscope()
        motion = self.configure_mock(microscope, scalars={"x": 2.0})
        motion.register_status_cb(lambda status: None)
        for _ in range(3):
            motion.move_absolute({"x": 4.0})
        self.assertAlmostEqual(motion.pos()["x"], 4.0)

    def test_idle(self):
        microscope = self.make_microscope()
        motion = microscope.motion
        self.assertTrue(motion.idle())
        motion.jog_fractioned({"x": 0.5}, period=0.1)
        self.assertFalse(motion.idle())
        motion.jog_cancel()
        self.assertTrue(motion.idle())


class SoftLimitConfigTestCase(unittest.TestCase):

    def soft_limits(self, raw):
        return USCMotion(j={"soft_limits": raw}).soft_limits()

    def test_missing_bound_is_none(self):
        self.assertEqual(self.soft_limits({"xmax": 10.0}), {"x": (None, 10.0)})
        self.assertEqual(self.soft_limits({"xmin": 5.0}), {"x": (5.0, None)})

    def test_zero_is_a_bound(self):
        self.assertEqual(self.soft_limits({
            "xmin": 0.0,
            "xmax": 0,
        }), {"x": (0.0, 0)})
        self.assertEqual(self.soft_limits({"ymin": -3.0}), {"y": (-3.0, None)})

    def test_unconfigured(self):
        self.assertIsNone(USCMotion(j={}).soft_limits())
        self.assertEqual(self.soft_limits({}), {})


class MotionThreadTestCase(TestCommon):

    def make_thread(self, name="mock", start=True):
        microscope = self.make_microscope(name, make_motion=False)
        mt = SimpleMotionThread(microscope)
        microscope.set_motion(mt.motion)
        microscope.configure()
        if start:
            mt.start()
            self.addCleanup(mt.shutdown)
            tstart = time.time()
            while not mt.running.is_set():
                self.assertTrue(time.time() - tstart < 5.0)
                time.sleep(0.01)
        return microscope, mt

    def blocking_move(self, mt, pos):
        """Start a block=True move in another thread, return its result"""
        result = {}

        def run():
            try:
                result["ret"] = mt.move_absolute(pos, block=True)
            except BaseException as e:
                result["exception"] = e

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, result

    def test_queue_clear_wakes_callers(self):
        _microscope, mt = self.make_thread(start=False)
        got = []
        mt.command("pos", callback=lambda c, a, ret: got.append(ret))
        thread, result = self.blocking_move(mt, {"x": 1.0})
        tstart = time.time()
        while mt.qsize() < 2:
            self.assertTrue(time.time() - tstart < 5.0)
            time.sleep(0.01)
        mt.queue_clear()
        thread.join(5.0)
        self.assertFalse(thread.is_alive(), "blocked caller never woke")
        self.assertIsInstance(result.get("exception"), MicroscopeStop)
        self.assertEqual(len(got), 1)
        self.assertIsInstance(got[0], MicroscopeStop)

    def test_stop_wakes_queued_move(self):
        microscope, mt = self.make_thread()
        # Keep the thread busy (ex: long move) so the next move stays queued
        busy = threading.Event()
        release = threading.Event()

        def slow_home():
            busy.set()
            release.wait(5.0)

        mt.motion.home = slow_home
        mt.home()
        self.assertTrue(busy.wait(5.0))
        thread, result = self.blocking_move(mt, {"x": 1.0})
        tstart = time.time()
        while mt.qsize() < 1:
            self.assertTrue(time.time() - tstart < 5.0)
            time.sleep(0.01)
        # Same path as the GUI STOP button
        microscope.stop_register(mt, mt.stop_now)
        microscope.stop()
        release.set()
        thread.join(5.0)
        self.assertFalse(thread.is_alive(), "blocked caller never woke")
        self.assertIsInstance(result.get("exception"), MicroscopeStop)
        # Thread still usable afterwards
        mt.move_absolute({"x": 2.0}, block=True)
        self.assertAlmostEqual(mt.pos()["x"], 2.0)

    def test_stop_now_interrupts(self):
        _microscope, mt = self.make_thread()
        with mock.patch.object(mt.motion, "async_stop") as async_stop:
            mt.stop_now()
        async_stop.assert_called_once_with()
        mt.wait_idle(timeout=5.0)

    def test_jog_disable_cancels_jog(self):
        """Scan start while jogging must not fail with 'move while jogging'"""
        _microscope, mt = self.make_thread()
        mt.jog_fractioned({"x": 0.5}, period=0.1)
        # Blocking command: FIFO => everything before it has run
        mt.pos()
        self.assertIsNotNone(mt.motion.jog_estimated_end)
        mt.jog_enable(False)
        mt.pos()
        self.assertIsNone(mt.motion.jog_estimated_end)
        mt.move_absolute({"x": 1.0}, block=True)
        mt.jog_enable(True)

    def test_jog_cancel_honored_while_disabled(self):
        _microscope, mt = self.make_thread()
        mt.jog_fractioned({"x": 0.5}, period=0.1)
        # Blocking command: FIFO => everything before it has run
        mt.pos()
        mt._jog_enabled = False
        mt.jog_cancel()
        mt.pos()
        self.assertIsNone(mt.motion.jog_estimated_end)
        # Jogs themselves are still dropped
        mt.jog_fractioned({"x": 0.5}, period=0.1)
        # Blocking command: FIFO => everything before it has run
        mt.pos()
        self.assertIsNone(mt.motion.jog_estimated_end)

    def test_serial_error_is_critical(self):
        _microscope, mt = self.make_thread()
        with mock.patch.object(mt.motion,
                               "_pos",
                               side_effect=serial.SerialException("gone")):
            with self.assertRaises(MotionCritical):
                mt.pos()
        self.assertIsNotNone(mt.dead)

    def test_os_error_is_critical(self):
        _microscope, mt = self.make_thread()
        with mock.patch.object(mt.motion,
                               "_move_absolute",
                               side_effect=OSError(5, "I/O error")):
            with self.assertRaises(MotionCritical):
                mt.move_absolute({"x": 1.0}, block=True)
        self.assertIsNotNone(mt.dead)

    def test_proxy_backlash_compensation(self):
        """Stacker direction must see the real HAL's backlash config"""
        _microscope, mt = self.make_thread(start=False)
        mt.motion.configure({
            "backlash": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.01
            },
            "backlash_compensation": {
                "z": +1
            },
        })
        proxy = mt.get_planner_motion()
        self.assertEqual(proxy.backlash_compensation()["z"], 1)
        stacker = types.SimpleNamespace(motion=proxy, axis="z")
        self.assertEqual(PlannerStacker.get_direction(stacker), 1)

        mt.motion.configure({})
        self.assertEqual(proxy.backlash_compensation(), {})
        self.assertEqual(PlannerStacker.get_direction(stacker), -1)


class MicroscopeStopTestCase(TestCommon):

    def test_unregister_during_stop(self):
        microscope = self.make_microscope(make_motion=False)
        called = []

        def stop_a():
            called.append("a")
            # Ex: a planner thread exiting its StopEvent mid stop()
            microscope.stop_unregister("b")

        def stop_b():
            called.append("b")

        microscope.stop_register("a", stop_a)
        microscope.stop_register("b", stop_b)
        microscope.stop()
        self.assertIn("a", called)
        self.assertNotIn("b", microscope.stops)


class PlannerTestCase(TestCommon):

    def setup_planner_microscope(self, name="mock-grbl"):
        microscope = self.make_microscope(name)
        width, height = microscope.usc.imager.final_wh()
        imager = MockImager(width=width, height=height)
        microscope.imager = imager
        microscope.set_imager_ts(imager)
        microscope.set_motion_ts(microscope.motion)
        return microscope

    def make_planner(self, microscope, pconfig):
        out_dir = os.path.join(self.out_dir, "scan")
        planner = get_planner(microscope=microscope,
                              pconfig=pconfig,
                              out_dir=out_dir,
                              dry=False,
                              log=self.log)
        return planner, out_dir

    def contour(self, x1, y1):
        return {"start": {"x": 0.0, "y": 0.0}, "end": {"x": x1, "y": y1}}

    def read_meta(self, out_dir):
        with open(os.path.join(out_dir, "uscan.json")) as f:
            return json.load(f)

    def test_complete_meta(self):
        microscope = self.setup_planner_microscope()
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(0.5, 0.5))
        meta_base = {"microscope": "base"}
        planner, out_dir = self.make_planner(microscope, pconfig)
        planner.meta_base = meta_base
        meta = planner.run()
        self.assertEqual(meta["status"], "complete")
        self.assertEqual(self.read_meta(out_dir)["status"], "complete")
        # Caller's dict not mutated
        self.assertEqual(meta_base, {"microscope": "base"})
        # No temporary files left behind
        self.assertEqual(glob.glob(os.path.join(out_dir, ".tmp_*")), [])

    def test_aborted_meta(self):
        microscope = self.setup_planner_microscope()
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(5.0, 5.0))
        planner, out_dir = self.make_planner(microscope, pconfig)
        n = []

        def progress(state):
            if state["type"] == "image":
                n.append(1)
                if len(n) == 2:
                    microscope.stop()

        planner.register_progress_callback(progress)
        with self.assertRaises(MicroscopeStop):
            planner.run()
        meta = self.read_meta(out_dir)
        self.assertEqual(meta["status"], "aborted")
        self.assertEqual(meta["microscope"]["name"], microscope.name)
        # Tiles saved before the stop are described
        self.assertEqual(len(meta["files"]), meta["image-save"]["saved"])
        self.assertTrue(len(meta["files"]) >= 1)
        for fn in meta["files"]:
            self.assertTrue(os.path.exists(os.path.join(out_dir, fn)))

    def test_failed_save_leaves_no_tile(self):
        microscope = self.setup_planner_microscope()
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(0.5, 0.5))
        planner, out_dir = self.make_planner(microscope, pconfig)

        def bad_save(capim, fn, **kwargs):
            with open(fn, "w") as f:
                f.write("truncated")
            raise IOError("disk full")

        with mock.patch.object(CapturedImage, "save", bad_save):
            with self.assertRaises(IOError):
                planner.run()
        names = os.listdir(out_dir)
        self.assertEqual([n for n in names if n != "uscan.json"], [])
        meta = self.read_meta(out_dir)
        self.assertEqual(meta["status"], "aborted")
        self.assertEqual(meta["image-save"]["saved"], 0)

    def test_xy3p_without_z(self):
        """Corners without z on a z machine scan XY only"""
        microscope = self.setup_planner_microscope()
        microscope.motion.move_absolute({"z": 1.0})
        corners = {
            "ll": {
                "x": 0.0,
                "y": 0.0
            },
            "ul": {
                "x": 0.0,
                "y": 0.5
            },
            "lr": {
                "x": 0.5,
                "y": 0.0
            },
        }
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               corners=corners)
        planner, _out_dir = self.make_planner(microscope, pconfig)
        meta = planner.run()
        self.assertTrue(meta["image-save"]["saved"] >= 1)
        self.assertAlmostEqual(microscope.motion.pos()["z"], 1.0, places=3)

    def test_xy3p_refocus_deferred(self):
        """Refocus moves / autofocus must not run while building the planner"""
        microscope = self.setup_planner_microscope()
        corners = {
            "ll": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.0
            },
            "ul": {
                "x": 0.0,
                "y": 0.5,
                "z": 0.0
            },
            "lr": {
                "x": 0.5,
                "y": 0.0,
                "z": 0.0
            },
        }
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               corners=corners)
        pconfig["points-xy3p"]["refocus"] = True
        with mock.patch(
                "uscope.planner.plugins.PointGenerator3P.refocus_corners"
        ) as refocus:
            planner, _out_dir = self.make_planner(microscope, pconfig)
            refocus.assert_not_called()
            planner.run()
            refocus.assert_called_once()


class JoystickTestCase(unittest.TestCase):

    def test_unused_axis_filtered(self):
        """A z binding on an XY machine must not reach the jog controller"""
        js = Joystick.__new__(Joystick)
        motion = mock.Mock()
        motion.only_used_axes = lambda d: dict(
            (k, v) for k, v in d.items() if k in "xy")
        js.microscope = types.SimpleNamespace(motion=motion)
        js.jog_controller = mock.Mock()

        def fake_axis():
            js._jog_add_queue("x", 0.5)
            js._jog_add_queue("z", 0.3)

        js.fake_axis = fake_axis
        js.config = types.SimpleNamespace(
            event_list=[], _function_map={"fake_axis": {
                "args": {}
            }})
        with mock.patch("uscope.joystick.pygame") as pygame:
            pygame.event.get.return_value = []
            js.execute()
        js.jog_controller.update.assert_called_once_with({"x": 0.5})


class GrblDamperTestCase(TestCommon):

    def setUp(self):
        super().setUp()
        self.microscope = self.make_microscope("mock-grbl")
        self.motion = self.microscope.motion
        self.fn = self.motion._nominal_rates_fn()
        self.remove_baseline()
        self.addCleanup(self.remove_baseline)
        # Emulated EEPROM
        self.eeprom = {
            "v": {
                "x": 1000.0,
                "y": 1000.0,
                "z": 600.0
            },
            "a": {
                "x": 30.0,
                "y": 30.0,
                "z": 30.0
            },
        }
        self.writes = []
        grbl = self.motion.grbl
        for name, which in (("axes_max_rate", "v"), ("axes_max_acceleration",
                                                     "a")):
            p = mock.patch.object(grbl,
                                  name,
                                  lambda which=which: dict(self.eeprom[which]))
            p.start()
            self.addCleanup(p.stop)
        for name, which in (("axes_set_max_rate", "v"),
                            ("axes_set_max_acceleration", "a")):
            p = mock.patch.object(
                grbl, name, lambda axes, which=which: self.write(which, axes))
            p.start()
            self.addCleanup(p.stop)

    def remove_baseline(self):
        if os.path.exists(self.fn):
            os.unlink(self.fn)

    def write(self, which, axes):
        for axis, v in axes.items():
            self.writes.append((which, axis, v))
            self.eeprom[which][axis] = round(v, 3)

    def restart(self, options=None):
        """Simulate a new session reading GRBL at configure time"""
        self.motion._load_nominal_rates(options or {})

    def test_no_compounding(self):
        self.restart()
        self.motion.apply_damper(0.5)
        self.assertEqual(self.eeprom["v"], {
            "x": 500.0,
            "y": 500.0,
            "z": 300.0
        })
        self.assertTrue(os.path.exists(self.fn))
        # Next launch: EEPROM already damped
        for _ in range(3):
            self.restart()
            self.writes = []
            self.motion.apply_damper(0.5)
            # Unchanged => no EEPROM writes
            self.assertEqual(self.writes, [])
        self.assertEqual(self.eeprom["v"], {
            "x": 500.0,
            "y": 500.0,
            "z": 300.0
        })
        # Undamped restores the true nominal values
        self.motion.apply_damper(1.0)
        self.assertEqual(self.eeprom["v"], {
            "x": 1000.0,
            "y": 1000.0,
            "z": 600.0
        })
        self.assertEqual(self.eeprom["a"], {"x": 30.0, "y": 30.0, "z": 30.0})

    def test_only_changed_axes_written(self):
        self.restart()
        self.eeprom["v"]["y"] = 500.0
        self.motion.apply_damper(0.5)
        self.assertNotIn(("v", "y", 500.0), self.writes)
        self.assertIn(("v", "x", 500.0), self.writes)

    def test_config_rc_wins(self):
        # EEPROM was left damped by an old session before this fix
        self.eeprom["v"]["x"] = 250.0
        self.restart({"rc_pre_home": ["$110=960.000", "$120=24.000"]})
        self.motion.apply_damper(0.5)
        self.assertEqual(self.eeprom["v"]["x"], 480.0)
        self.assertEqual(self.eeprom["a"]["x"], 12.0)
        with open(self.fn) as f:
            j = json.load(f)
        self.assertEqual(j["max_velocities"]["x"], 960.0)
        self.assertEqual(j["max_velocities"]["y"], 1000.0)

    def test_parse_rc_rates(self):
        vel, acc = parse_rc_rates(
            ["$110=960.000", "$111 = 480", "$112=1", "$122=6.25", "G54", "$X"])
        self.assertEqual(vel, {"x": 960.0, "y": 480.0, "z": 1.0})
        self.assertEqual(acc, {"z": 6.25})


if __name__ == "__main__":
    unittest.main()
