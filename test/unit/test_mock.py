#!/usr/bin/env -S uv run
"""
Headless regression tests using the mock microscopes

No hardware is required or touched: these use the "mock" and "mock-grbl"
configurations which emulate the motion controller in software

Run with:
    ./test/unit/test_mock.py
or
    uv run python -m unittest discover -s test/unit
"""

import os
import shutil
import tempfile

# Keep test data out of the working tree
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
# Never pick up a real controller / microscope
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

import threading
import time
import unittest

from uscope.microscope import Microscope, MicroscopeStop, StopEvent
from uscope.imager.imager import MockImager
from uscope.motion.hal import AxisExceeded
from uscope.motion.grbl import GRBL
from uscope.motion.thread import SimpleMotionThread
from uscope.threads import CommandThreadBase
from uscope.subsystem import Subsystem
from uscope.planner.planner_util import microscope_to_planner_config, get_planner


def quiet_log(msg="", *args, **kwargs):
    pass


class TestCommon(unittest.TestCase):
    def setUp(self):
        self.verbose = int(os.getenv("TEST_VERBOSE", "0"))
        self.log = print if self.verbose else quiet_log
        self.out_dir = tempfile.mkdtemp(prefix="pyuscope-out-", dir=_TMP_ROOT)

    def tearDown(self):
        shutil.rmtree(self.out_dir, ignore_errors=True)

    def make_microscope(self, name="mock-grbl", **kwargs):
        microscope = Microscope(name=name, log=self.log, **kwargs)
        self.addCleanup(self.close_microscope, microscope)
        return microscope

    def close_microscope(self, microscope):
        if microscope.motion:
            microscope.motion.close()

    def contour(self, x1=2.0, y1=1.0):
        return {
            "start": {
                "x": 0.0,
                "y": 0.0
            },
            "end": {
                "x": x1,
                "y": y1
            },
        }


class MicroscopeTestCase(TestCommon):
    def test_mock_grbl(self):
        microscope = self.make_microscope("mock-grbl")
        self.assertEqual(microscope.config_name(), "mock-grbl")
        pos = microscope.motion.pos()
        self.assertEqual(set(pos.keys()), {"x", "y", "z"})
        microscope.motion.move_absolute({"x": 1.5, "y": 2.0})
        pos = microscope.motion.pos()
        self.assertAlmostEqual(pos["x"], 1.5, places=3)
        self.assertAlmostEqual(pos["y"], 2.0, places=3)
        microscope.motion.move_relative({"x": -0.5})
        self.assertAlmostEqual(microscope.motion.pos()["x"], 1.0, places=3)

    def test_mock_hal(self):
        microscope = self.make_microscope("mock")
        microscope.motion.move_absolute({"x": 3.0})
        self.assertAlmostEqual(microscope.motion.pos()["x"], 3.0, places=3)
        # mock HAL must also support jogging
        microscope.motion.jog_fractioned({"x": 0.5}, period=0.1)
        microscope.motion.jog_cancel()

    def test_soft_limits(self):
        microscope = self.make_microscope("mock-grbl")
        # mock-grbl: 0 <= x <= 120
        microscope.motion.move_absolute({"x": 10.0})
        with self.assertRaises(AxisExceeded):
            microscope.motion.move_absolute({"x": 500.0})
        # Still at the old position
        self.assertAlmostEqual(microscope.motion.pos()["x"], 10.0, places=3)

    def test_mock_grbl_command(self):
        microscope = self.make_microscope("mock-grbl")
        # Mock serial must return a list of lines like the real one
        self.assertIsInstance(microscope.motion.command("$G"), str)

    def test_status_cb(self):
        microscope = self.make_microscope("mock")
        got = []
        cb = lambda status: got.append(status)
        microscope.motion.register_status_cb(cb)
        microscope.motion.move_absolute({"x": 1.0})
        self.assertTrue(len(got) >= 1)
        microscope.motion.unregister_status_cb(cb)
        n = len(got)
        microscope.motion.move_absolute({"x": 2.0})
        self.assertEqual(n, len(got))

    def test_stop_event(self):
        microscope = self.make_microscope("mock", make_motion=False)
        with StopEvent(microscope) as se:
            se.poll()
            microscope.stop()
            with self.assertRaises(MicroscopeStop):
                se.poll()
        # Unregistered on exit
        self.assertEqual(len(microscope.stops), 0)

    def test_system_status(self):
        microscope = self.make_microscope("mock-grbl")
        microscope.imager = MockImager()
        # Must not crash before any movement has happened
        status = microscope.system_status_ts()
        self.assertIn("motion", status)


class SubsystemTestCase(TestCommon):
    class CountingSubsystem(Subsystem):
        def __init__(self, microscope):
            super().__init__(microscope)
            self.count = 0

        def name(self):
            return "counter"

        def cache_save(self, jroot, j):
            j["count"] = self.count

        def cache_load(self, jroot, j):
            self.count = j.get("count", 0)

        def cache_sn_save(self, jroot, j):
            j["count"] = self.count

        def cache_sn_load(self, jroot, j):
            self.count = j.get("count", 0)

        def system_status_ts(self, root_status, status):
            status["count"] = self.count

    def test_cache_roundtrip(self):
        microscope = self.make_microscope("mock", make_motion=False)
        ss = self.CountingSubsystem(microscope)
        microscope.add_subsystem(ss)
        ss.count = 7

        cachej = {}
        microscope.cache_save(cachej)
        self.assertEqual(cachej["subsystems"]["counter"]["count"], 7)
        # Subsystem object must not have been replaced by its JSON
        self.assertIs(microscope.get_subsystem("counter"), ss)

        # Saving twice must keep working
        ss.count = 8
        microscope.cache_save(cachej)
        self.assertEqual(cachej["subsystems"]["counter"]["count"], 8)

        ss.count = 0
        microscope.cache_load(cachej)
        self.assertEqual(ss.count, 8)

        cachej_sn = {}
        microscope.cache_sn_save(cachej_sn)
        self.assertEqual(cachej_sn["subsystems"]["counter"]["count"], 8)
        self.assertIs(microscope.get_subsystem("counter"), ss)
        ss.count = 0
        microscope.cache_sn_load(cachej_sn)
        self.assertEqual(ss.count, 8)

    def test_system_status(self):
        microscope = self.make_microscope("mock-grbl")
        microscope.imager = MockImager()
        ss = self.CountingSubsystem(microscope)
        ss.count = 3
        microscope.add_subsystem(ss)
        status = microscope.system_status_ts()
        self.assertEqual(status["subsystems"]["counter"]["count"], 3)
        self.assertIs(microscope.get_subsystem("counter"), ss)
        # Again, to be sure the object wasn't clobbered
        status = microscope.system_status_ts()
        self.assertEqual(status["subsystems"]["counter"]["count"], 3)


class PlannerTestCase(TestCommon):
    def setup_planner_microscope(self, name="mock-grbl"):
        microscope = self.make_microscope(name)
        # Planner validates image size against the configuration
        width, height = microscope.usc.imager.final_wh()
        imager = MockImager(width=width, height=height)
        microscope.imager = imager
        microscope.set_imager_ts(imager)
        microscope.set_motion_ts(microscope.motion)
        return microscope

    def run_planner(self, microscope, pconfig, dry):
        out_dir = os.path.join(self.out_dir, "scan")
        planner = get_planner(microscope=microscope,
                              pconfig=pconfig,
                              out_dir=out_dir,
                              dry=dry,
                              log=self.log)
        meta = planner.run()
        return meta, out_dir

    def test_xy2p(self):
        microscope = self.setup_planner_microscope()
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour())
        meta, out_dir = self.run_planner(microscope, pconfig, dry=False)
        self.assertTrue(meta["image-capture"]["captured"] > 1)
        self.assertEqual(meta["image-capture"]["captured"],
                         meta["image-save"]["saved"])
        self.assertEqual(len(meta["files"]), meta["image-save"]["saved"])
        self.assertTrue(os.path.exists(os.path.join(out_dir, "uscan.json")))
        for fn in meta["files"].keys():
            self.assertTrue(os.path.exists(os.path.join(out_dir, fn)), fn)

    def test_xy2p_dry(self):
        microscope = self.setup_planner_microscope()
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour())
        pos0 = microscope.motion.pos()
        meta, out_dir = self.run_planner(microscope, pconfig, dry=True)
        self.assertTrue(meta["image-capture"]["captured"] > 1)
        # Dry run must not have moved anything or written anything
        self.assertEqual(pos0, microscope.motion.pos())
        self.assertFalse(os.path.exists(out_dir))

    def test_stacker(self):
        microscope = self.setup_planner_microscope()
        microscope.motion.move_absolute({"z": 5.0})
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(
                                                   0.5, 0.5))
        pconfig["points-stacker"] = {
            "number": 3,
            "distance": 0.1,
            "mode": "center",
        }
        meta, _out_dir = self.run_planner(microscope, pconfig, dry=False)
        self.assertEqual(meta["image-capture"]["captured"], 3)
        # Z restored at end
        self.assertAlmostEqual(microscope.motion.pos()["z"], 5.0, places=3)

    def test_hdr(self):
        microscope = self.setup_planner_microscope()
        imager = microscope.imager
        imager.set_properties({"loopback_int": 100})
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(
                                                   0.5, 0.5))
        pconfig["imager"]["hdr"] = {
            "properties_list": [{
                "loopback_int": 10
            }, {
                "loopback_int": 20
            }],
        }
        meta, _out_dir = self.run_planner(microscope, pconfig, dry=False)
        self.assertEqual(meta["image-capture"]["captured"], 2)
        # Properties restored at end
        self.assertEqual(imager.get_property("loopback_int"), 100)

    def test_stop(self):
        """Stopping mid scan must abort cleanly"""
        microscope = self.setup_planner_microscope()
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(
                                                   5.0, 5.0))
        out_dir = os.path.join(self.out_dir, "scan")
        planner = get_planner(microscope=microscope,
                              pconfig=pconfig,
                              out_dir=out_dir,
                              dry=False,
                              log=self.log)
        n = []

        def progress(state):
            if state["type"] == "image":
                n.append(1)
                if len(n) == 2:
                    microscope.stop()

        planner.register_progress_callback(progress)
        with self.assertRaises(MicroscopeStop):
            planner.run()
        self.assertTrue(len(n) < 10)

    def test_hdr_abort_restores(self):
        """HDR imager properties restored even when the scan dies early"""
        microscope = self.setup_planner_microscope()
        imager = microscope.imager
        imager.set_properties({"loopback_int": 100})
        pconfig = microscope_to_planner_config(microscope,
                                               objectivei=0,
                                               contour=self.contour(
                                                   5.0, 5.0))
        pconfig["imager"]["hdr"] = {
            "properties_list": [{
                "loopback_int": 10
            }, {
                "loopback_int": 20
            }],
        }
        out_dir = os.path.join(self.out_dir, "scan")
        planner = get_planner(microscope=microscope,
                              pconfig=pconfig,
                              out_dir=out_dir,
                              dry=False,
                              log=self.log)

        def progress(state):
            if state["type"] == "image":
                # Mid HDR bracket
                self.assertNotEqual(imager.get_property("loopback_int"), 100)
                raise ValueError("boom")

        planner.register_progress_callback(progress)
        with self.assertRaises(ValueError):
            planner.run()
        self.assertEqual(imager.get_property("loopback_int"), 100)


class CommandThreadTestCase(TestCommon):
    class EchoThread(CommandThreadBase, threading.Thread):
        def __init__(self, microscope):
            threading.Thread.__init__(self)
            CommandThreadBase.__init__(self, microscope)
            self.daemon = True
            self.command_map = {
                "echo": lambda x: x,
                "fail": self.fail,
                "stop": self.stop,
            }

        def fail(self, x):
            raise ValueError(x)

        def stop(self):
            raise MicroscopeStop()

        def shutdown_join(self, timeout=3.0):
            self.join(timeout)

    def test_echo(self):
        microscope = self.make_microscope("mock", make_motion=False)
        t = self.EchoThread(microscope)
        t.start()
        self.addCleanup(t.shutdown)
        self.assertEqual(t.command("echo", 42, block=True), 42)

    def test_exception_propagates(self):
        microscope = self.make_microscope("mock", make_motion=False)
        t = self.EchoThread(microscope)
        t.start()
        self.addCleanup(t.shutdown)
        with self.assertRaises(Exception):
            t.command("fail", "boom", block=True)
        # Thread still alive and usable
        self.assertEqual(t.command("echo", 1, block=True), 1)

    def test_stop_propagates(self):
        microscope = self.make_microscope("mock", make_motion=False)
        t = self.EchoThread(microscope)
        t.start()
        self.addCleanup(t.shutdown)
        with self.assertRaises(MicroscopeStop):
            t.command("stop", block=True)
        self.assertEqual(t.command("echo", 1, block=True), 1)


class MotionThreadTestCase(TestCommon):
    def make_thread(self, name="mock-grbl"):
        microscope = self.make_microscope(name, make_motion=False)
        mt = SimpleMotionThread(microscope)
        mt.daemon = True
        # Thread owns the motion object
        microscope.set_motion(mt.motion)
        microscope.configure()
        mt.start()
        self.addCleanup(mt.shutdown)
        # Wait for the thread to come up
        tstart = time.time()
        while not mt.running.is_set():
            self.assertTrue(time.time() - tstart < 5.0, "thread didn't start")
            time.sleep(0.01)
        return microscope, mt

    def test_pos(self):
        _microscope, mt = self.make_thread()
        pos = mt.pos()
        self.assertEqual(set(pos.keys()), {"x", "y", "z"})

    def test_soft_limit_propagates(self):
        """A blocking caller must learn that a move was refused"""
        _microscope, mt = self.make_thread()
        mt.move_absolute({"x": 10.0}, block=True)
        with self.assertRaises(AxisExceeded):
            mt.move_absolute({"x": 500.0}, block=True)
        self.assertAlmostEqual(mt.pos()["x"], 10.0, places=3)
        # Thread still healthy
        mt.move_absolute({"x": 11.0}, block=True)
        self.assertAlmostEqual(mt.pos()["x"], 11.0, places=3)

    def test_move(self):
        _microscope, mt = self.make_thread()
        mt.move_absolute({"x": 2.0}, block=True)
        self.assertAlmostEqual(mt.pos()["x"], 2.0, places=3)
        mt.move_relative({"x": 1.0}, block=True)
        self.assertAlmostEqual(mt.pos()["x"], 3.0, places=3)

    def test_soft_limit_blocking(self):
        """A rejected move must not hang a blocking caller"""
        _microscope, mt = self.make_thread()
        mt.move_absolute({"x": 2.0}, block=True)
        done = threading.Event()
        with self.assertRaises(AxisExceeded):
            mt.move_absolute({"x": 5000.0}, block=True, done=done)
        # done fires even on failure
        self.assertTrue(done.is_set())
        self.assertAlmostEqual(mt.pos()["x"], 2.0, places=3)

    def test_jog(self):
        _microscope, mt = self.make_thread()
        mt.jog_fractioned({"x": 0.5}, period=0.1)
        mt.jog_cancel()
        mt.wait_idle()
        # Must be able to move again after jog
        mt.move_absolute({"x": 1.0}, block=True)

    def test_planner_motion(self):
        """The thread safe wrapper handed to the planner"""
        _microscope, mt = self.make_thread()
        pm = mt.get_planner_motion()
        self.assertEqual(pm.axes(), mt.motion.axes())
        self.assertEqual(pm.epsilon(), mt.motion.epsilon())
        pm.move_absolute({"x": 1.0})
        self.assertAlmostEqual(pm.pos()["x"], 1.0, places=3)
        self.assertAlmostEqual(mt.pos()["x"], 1.0, places=3)

    def test_crash_unblocks(self):
        """
        If the motion controller dies, blocking callers must get an error
        rather than hang forever
        """
        _microscope, mt = self.make_thread()

        def die(*args):
            from uscope.motion.hal import MotionCritical
            raise MotionCritical("test crash")

        # Inject a crash
        mt.motion.log_info = die
        with self.assertRaises(Exception):
            mt.command("log_info", block=True)
        self.assertIsNone(mt.motion)
        # Subsequent commands must fail immediately, not hang
        with self.assertRaises(Exception):
            mt.command("pos", block=True)
        done = threading.Event()
        mt.move_absolute({"x": 1.0}, done=done)
        self.assertTrue(done.wait(2.0))


class GrblMockTestCase(TestCommon):
    def test_mock_serial(self):
        grbl = GRBL(port="mock")
        self.addCleanup(grbl.close)
        status = grbl.qstatus()
        self.assertEqual(status["status"], "Idle")
        grbl.move_absolute({"x": 1.0}, f=100)
        self.assertAlmostEqual(grbl.mpos()["x"], 1.0)
        self.assertIsInstance(grbl.gs.txrxs("$G"), list)
        self.assertEqual(len(grbl.steps_per_mm()), 3)


if __name__ == "__main__":
    unittest.main()
