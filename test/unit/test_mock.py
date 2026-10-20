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
from uscope.imager.image_sequence import CapturedImage
from uscope.imagep.pipeline import CSImageProcessor, CSIPParams
from uscope.imagep.plugins import AnnotateScalebarPlugin, nice_scalebar_um
from uscope.imagep.util import EtherealImageR, EtherealImageW, TaskBarrier, find_qr_code_match
from uscope.imagep import streams as ipstreams
from uscope import config as uconfig

import glob
from unittest import mock
from PIL import Image


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


class ImageProcessingTestCase(TestCommon):
    WIDTH = 800
    HEIGHT = 600
    # Scalebar adds a black band below the image
    BAND = int(HEIGHT * 0.12)

    def make_image(self):
        return Image.new("RGB", (self.WIDTH, self.HEIGHT), "gray")

    def run_plugin(self, plugin, options):
        data_in = {"image": EtherealImageR(im=self.make_image())}
        data_out = {"image": EtherealImageW(want_im=True, temp_dir=self.out_dir)}
        plugin.run(data_in, data_out, options=options)
        return data_out["image"].get_im()

    def test_nice_scalebar_um(self):
        self.assertEqual(nice_scalebar_um(120.0), 100)
        self.assertEqual(nice_scalebar_um(200.0), 200)
        self.assertEqual(nice_scalebar_um(49.8), 50)
        self.assertEqual(nice_scalebar_um(499.0), 500)
        self.assertEqual(nice_scalebar_um(7000.0), 5000)
        self.assertEqual(nice_scalebar_um(8000.0), 10000)
        self.assertEqual(nice_scalebar_um(0.6), 0.5)

    def test_scalebar_plugin(self):
        """
        Pillow >= 10 removed ImageDraw.textsize() / ImageFont.getsize()
        """
        microscope = self.make_microscope("mock-grbl")
        plugin = AnnotateScalebarPlugin(log=self.log, microscope=microscope)
        im = self.run_plugin(plugin,
                             {"objective_config": {
                                 "um_per_pixel": 1.0
                             }})
        self.assertEqual(im.size, (self.WIDTH, self.HEIGHT + self.BAND))
        # Something white got drawn in the band
        band = im.crop((0, self.HEIGHT, self.WIDTH, self.HEIGHT + self.BAND))
        self.assertEqual(band.getextrema()[0][1], 255)
        # Image itself is untouched
        self.assertEqual(im.getpixel((0, 0)), (128, 128, 128))

    def test_scalebar_plugin_no_cal(self):
        microscope = self.make_microscope("mock-grbl")
        plugin = AnnotateScalebarPlugin(log=self.log, microscope=microscope)
        im = self.run_plugin(plugin, {"objective_config": {}})
        self.assertEqual(im.size, (self.WIDTH, self.HEIGHT + self.BAND))

    def make_csip(self, microscope):
        ip = CSImageProcessor(nthreads=1, log=self.log, microscope=microscope)
        ip.start()
        self.assertTrue(ip.ready.wait(5.0))
        self.addCleanup(ip.shutdown)
        return ip

    def snapshot(self, ip, microscope, plugins):
        image = self.make_image()
        capim = CapturedImage(image=image, meta={}, microscope=microscope)
        options = {
            "captured_image": capim,
            "objective_config": {
                "um_per_pixel": 1.0
            },
            "plugins": plugins,
        }
        return ip.process_snapshot(image, options=options)

    def test_snapshot_scalebar_not_sticky(self):
        """
        Requesting the scalebar on one snapshot must not add it to the
        microscope config and thus all later snapshots / scans
        """
        microscope = self.make_microscope("mock-grbl")
        ipp = uconfig.get_usc(microscope=microscope).ipp
        # Config with the key present returns the live list
        # (configs/lip-vm1 does this)
        old = ipp.j.get("snapshot_correction", None)
        ipp.j["snapshot_correction"] = []

        def restore():
            if old is None:
                ipp.j.pop("snapshot_correction", None)
            else:
                ipp.j["snapshot_correction"] = old

        self.addCleanup(restore)

        ip = self.make_csip(microscope)
        im = self.snapshot(ip, microscope, {"annotate-scalebar": {}})
        self.assertEqual(im.size, (self.WIDTH, self.HEIGHT + self.BAND))
        self.assertEqual(ipp.snapshot_correction(), [])

        im = self.snapshot(ip, microscope, {})
        self.assertEqual(im.size, (self.WIDTH, self.HEIGHT))

    def test_snapshot_temp_files_cleaned(self):
        """
        Intermediate images used to accumulate in the engine temp dir
        for the life of the process
        """
        microscope = self.make_microscope("mock-grbl")
        ip = self.make_csip(microscope)
        for _i in range(3):
            im = self.snapshot(ip, microscope, {"annotate-scalebar": {}})
            self.assertEqual(im.size, (self.WIDTH, self.HEIGHT + self.BAND))
            # Image must be fully loaded, not lazily backed by the file
            im.getpixel((0, 0))
        self.assertEqual(glob.glob(os.path.join(ip.temp_dir, "*")), [])

    def test_worker_survives_callback_exception(self):
        """
        A raising user callback must not kill the worker
        With one worker a dead thread would strand all later tasks
        """
        microscope = self.make_microscope("mock-grbl")
        ip = self.make_csip(microscope)

        def bad_callback(*args):
            raise ValueError("callback bug")

        tb = TaskBarrier()
        data_in = {"image": EtherealImageR(im=self.make_image())}
        data_out = {"image": EtherealImageW(want_im=True, temp_dir=ip.temp_dir)}
        ip.queue_task(
            CSIPParams(task_name="annotate-scalebar",
                       data_in=data_in,
                       data_out=data_out,
                       options={"objective_config": {}},
                       callback=bad_callback,
                       tb=tb))
        tb.wait(timeout=10.0)
        self.assertTrue(all(w.is_alive() for w in ip.workers.values()))
        # Next task still processed
        im = self.snapshot(ip, microscope, {})
        self.assertEqual(im.size, (self.WIDTH, self.HEIGHT))

    def test_invalid_plugin_reports(self):
        microscope = self.make_microscope("mock-grbl")
        ip = self.make_csip(microscope)
        tb = TaskBarrier()
        ip.queue_task(CSIPParams(task_name="no-such-plugin", tb=tb))
        with self.assertRaises(Exception):
            tb.wait(timeout=10.0)

    def test_task_barrier_threaded(self):
        tb = TaskBarrier()
        n = 2000
        for _i in range(n):
            tb.allocate_callback()
        self.assertFalse(tb.idle())

        def worker(count):
            for _i in range(count):
                tb.callback()

        threads = [threading.Thread(target=worker, args=(n // 4, )) for _i in range(4)]
        for t in threads:
            t.start()
        tb.wait(timeout=10.0)
        for t in threads:
            t.join()
        self.assertTrue(tb.idle())
        self.assertEqual(tb.ntasks_completed, n)

    def test_task_barrier_timeout(self):
        tb = TaskBarrier()
        tb.allocate_callback()
        tstart = time.time()
        with self.assertRaises(Exception):
            tb.wait(timeout=0.2)
        self.assertLess(time.time() - tstart, 2.0)

    def test_qr_no_match(self):
        # Exercises the pyzbar import; blank image has no code
        self.assertIsNone(find_qr_code_match(self.make_image(), ".*"))


class DirCSIPTestCase(TestCommon):
    """
    Process a tiny completed scan directory
    mock-grbl has no FF cal or corrections so this is the zero stage path
    """
    COLS = 2
    ROWS = 2

    def make_scan_dir(self, name="scan"):
        d = os.path.join(self.out_dir, name)
        os.mkdir(d)
        for col in range(self.COLS):
            for row in range(self.ROWS):
                Image.new("RGB", (64, 48), "gray").save(
                    os.path.join(d, "c%03u_r%03u.jpg" % (col, row)))
        return d

    def make_csip(self, microscope):
        ip = CSImageProcessor(nthreads=1, log=self.log, microscope=microscope)
        ip.start()
        self.assertTrue(ip.ready.wait(5.0))
        self.addCleanup(ip.shutdown)
        return ip

    def run_dir(self, ip, d, configj):
        ip.process_dir(d,
                       upload=False,
                       configj=configj,
                       verbose=self.verbose)

    def add_stage(self, microscope):
        """
        Make processing go through a nested directory
        """
        ipp = uconfig.get_usc(microscope=microscope).ipp
        old = ipp.j.get("pipeline_first", None)
        ipp.j["pipeline_first"] = [{
            "plugin": "annotate-scalebar",
            "dir": "scalebar"
        }]

        def restore():
            if old is None:
                ipp.j.pop("pipeline_first", None)
            else:
                ipp.j["pipeline_first"] = old

        self.addCleanup(restore)

    def test_no_stages_discard_intermediates(self):
        """
        Used to UnboundLocalError on next_dir when nothing was processed
        """
        microscope = self.make_microscope("mock-grbl")
        ip = self.make_csip(microscope)
        d = self.make_scan_dir()
        self.run_dir(ip, d, {
            "keep_intermediates": False,
            "write_html_viewer": False,
        })
        self.assertTrue(os.path.exists(os.path.join(d, "processing.json")))
        self.assertEqual(len(glob.glob(os.path.join(d, "*.jpg"))),
                         self.COLS * self.ROWS)

    def test_stage_discard_intermediates(self):
        microscope = self.make_microscope("mock-grbl")
        self.add_stage(microscope)
        ip = self.make_csip(microscope)
        d = self.make_scan_dir()
        self.run_dir(ip, d, {
            "keep_intermediates": False,
            "write_html_viewer": False,
        })
        # Processed images moved to the top, nested dir gone
        self.assertFalse(os.path.exists(os.path.join(d, "scalebar")))
        fns = glob.glob(os.path.join(d, "*.jpg"))
        self.assertEqual(len(fns), self.COLS * self.ROWS)
        with Image.open(fns[0]) as im:
            # Scalebar band added => taller than the 48 px source
            self.assertGreater(im.height, 48)

    def test_qr_renames_scan_dir(self):
        """
        With a processing stage the QR rename must apply to the scan
        directory, not the nested processed directory
        """
        microscope = self.make_microscope("mock-grbl")
        self.add_stage(microscope)
        ip = self.make_csip(microscope)
        d = self.make_scan_dir()
        with mock.patch.object(uconfig.bc, "j", dict(uconfig.bc.j, qr_regex=".*")), \
             mock.patch.object(ipstreams, "find_qr_code_match", return_value="ABC123"):
            self.run_dir(ip, d, {"write_html_viewer": False})
        self.assertFalse(os.path.exists(d))
        renamed = d + "_ABC123"
        self.assertTrue(os.path.isdir(renamed))
        self.assertTrue(os.path.isdir(os.path.join(renamed, "scalebar")))
        self.assertTrue(os.path.exists(os.path.join(renamed, "processing.json")))


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
