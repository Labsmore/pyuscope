#!/usr/bin/env -S uv run
"""
Video recording branch tests: videotestsrc pipelines only (no camera / audio hardware)

Run with:
    ./test/unit/test_gst_recorder.py
"""

import os
import shutil
import tempfile
import threading
import time
import unittest

# Keep test data out of the working tree
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-recorder-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

from uscope.gst_util import Gst
from uscope.imager import gst_recorder
from uscope.imager.gst_recorder import (
    GstVideoRecorder,
    RecordError,
    fit_wh,
    have_element,
    plan_recording,
)


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


def has_only(*factories):
    return lambda factory: factory in factories


class PlanRecordingTestCase(unittest.TestCase):
    def test_mp4_x264(self):
        plan = plan_recording("mp4",
                              has_element=has_only("mp4mux", "x264enc",
                                                   "openh264enc", "h264parse"))
        self.assertEqual(plan["container"], "mp4")
        self.assertEqual(plan["extension"], ".mp4")
        self.assertEqual(plan["video_encoder"], "x264enc")
        self.assertEqual(plan["video_parser"], "h264parse")
        self.assertIsNone(plan["audio_encoder"])

    def test_openh264_fallback(self):
        plan = plan_recording("mp4",
                              has_element=has_only("mp4mux", "openh264enc",
                                                   "h264parse"))
        self.assertEqual(plan["video_encoder"], "openh264enc")

    def test_mp4_falls_back_to_mkv_vp8(self):
        plan = plan_recording("mp4",
                              has_element=has_only("mp4mux", "matroskamux",
                                                   "vp8enc"))
        self.assertEqual(plan["container"], "mkv")
        self.assertEqual(plan["video_encoder"], "vp8enc")
        self.assertIsNone(plan["video_parser"])

    def test_audio(self):
        plan = plan_recording("mp4",
                              audio=True,
                              has_element=has_only("mp4mux", "x264enc",
                                                   "h264parse", "voaacenc",
                                                   "aacparse"))
        self.assertEqual(plan["audio_encoder"], "voaacenc")
        self.assertEqual(plan["audio_parser"], "aacparse")

        plan = plan_recording("mkv",
                              audio=True,
                              has_element=has_only("matroskamux", "x264enc",
                                                   "h264parse", "opusenc",
                                                   "avenc_aac"))
        self.assertEqual(plan["audio_encoder"], "opusenc")

    def test_mp4_audio_falls_back_to_mkv(self):
        plan = plan_recording("mp4",
                              audio=True,
                              has_element=has_only("mp4mux", "matroskamux",
                                                   "x264enc", "h264parse",
                                                   "vorbisenc"))
        self.assertEqual(plan["container"], "mkv")
        self.assertEqual(plan["audio_encoder"], "vorbisenc")

    def test_nothing_available(self):
        with self.assertRaises(RecordError):
            plan_recording("mp4", has_element=has_only("mp4mux"))
        with self.assertRaises(RecordError):
            plan_recording("mp4",
                           audio=True,
                           has_element=has_only("mp4mux", "x264enc",
                                                "h264parse"))
        with self.assertRaises(RecordError):
            plan_recording("avi")

    def test_fit_wh(self):
        self.assertEqual(fit_wh(1000, 600), (1000, 600))
        self.assertEqual(fit_wh(1001, 601), (1000, 600))
        self.assertEqual(fit_wh(5440, 3648, (3840, 2160)), (3220, 2160))
        self.assertEqual(fit_wh(1000, 600, (3840, 2160)), (1000, 600))
        self.assertEqual(fit_wh(4000, 1000, (2000, 2000)), (2000, 500))


def ffprobe_ok(fn):
    """
    Duration in seconds via GStreamer discoverer
    """
    import gi
    gi.require_version("GstPbutils", "1.0")
    from gi.repository import GstPbutils
    discoverer = GstPbutils.Discoverer.new(10 * Gst.SECOND)
    info = discoverer.discover_uri(Gst.filename_to_uri(fn))
    streams = [s.get_stream_type_nick() for s in info.get_stream_list()]
    return info.get_duration() / Gst.SECOND, streams


@unittest.skipUnless(
    have_element("mp4mux") and have_element("h264parse")
    and (have_element("x264enc") or have_element("openh264enc")),
    "No h264 / mp4 GStreamer elements")
class RecorderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pyuscope-test-recorder-")
        self.player = Gst.parse_launch(
            "videotestsrc is-live=true name=src ! "
            "video/x-raw,width=641,height=481,framerate=30/1 ! "
            "tee name=tee ! queue ! fakesink sync=true name=view")
        self.tee = self.player.get_by_name("tee")
        self.bus_errors = []
        self.player.set_state(Gst.State.PLAYING)
        self.assertEqual(
            self.player.get_state(10 * Gst.SECOND)[0],
            Gst.StateChangeReturn.SUCCESS)
        self.logs = []
        self.recorder = GstVideoRecorder(self.player,
                                         self.tee,
                                         log=self.logs.append)

    def tearDown(self):
        self.player.set_state(Gst.State.NULL)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check_bus(self):
        bus = self.player.get_bus()
        while True:
            msg = bus.pop_filtered(Gst.MessageType.ERROR
                                   | Gst.MessageType.EOS)
            if msg is None:
                break
            self.bus_errors.append(msg)
        self.assertEqual(self.bus_errors, [])

    def view_position(self):
        return self.player.query_position(Gst.Format.TIME)[1] / Gst.SECOND

    def record(self, seconds, name="rec", **kwargs):
        done = []
        if not callable(name):
            name = os.path.join(self.tmp, name)
        fn = self.recorder.start(name,
                                 done=lambda fn, err: done.append((fn, err)),
                                 **kwargs)
        self.assertTrue(self.recorder.is_recording())
        time.sleep(seconds)
        self.assertTrue(self.recorder.stop(wait=True))
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual(done, [(fn, None)])
        return fn

    def test_record_mp4(self):
        fn = self.record(1.5)
        self.assertTrue(fn.endswith(".mp4"))
        duration, streams = ffprobe_ok(fn)
        self.assertAlmostEqual(duration, 1.5, delta=0.5)
        self.assertEqual(streams, ["video"])
        # Live view kept running and nothing leaked onto the main bus
        self.assertEqual(self.player.get_state(0)[1], Gst.State.PLAYING)
        pos = self.view_position()
        time.sleep(0.3)
        self.assertGreater(self.view_position(), pos)
        self.check_bus()
        self.assertEqual(len(self.tee.srcpads), 1)

    def test_record_untimestamped(self):
        # Like toupcamsrc: frames have no PTS, which mp4mux rejects
        def clear(pad, info):
            buf = info.get_buffer()
            buf.pts = buf.dts = buf.duration = Gst.CLOCK_TIME_NONE
            return Gst.PadProbeReturn.OK

        self.player.get_by_name("src").get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER, clear)
        fn = self.record(1.5)
        self.assertAlmostEqual(ffprobe_ok(fn)[0], 1.5, delta=0.5)
        self.check_bus()

    def test_record_twice(self):
        fn1 = self.record(0.5, name="rec1")
        fn2 = self.record(0.5, name="rec2")
        for fn in (fn1, fn2):
            self.assertAlmostEqual(ffprobe_ok(fn)[0], 0.5, delta=0.3)
        self.assertEqual(len(self.tee.srcpads), 1)
        self.check_bus()

    @unittest.skipUnless(have_element("matroskamux"), "No matroskamux")
    def test_record_mkv(self):
        fn = self.record(1.0, container="mkv")
        self.assertTrue(fn.endswith(".mkv"))
        self.assertAlmostEqual(ffprobe_ok(fn)[0], 1.0, delta=0.5)

    def test_record_audio(self):
        try:
            plan_recording("mp4", audio=True)
        except RecordError:
            self.skipTest("No audio encoder")
        fn = self.record(1.0,
                         audio=True,
                         audio_source="audiotestsrc is-live=true")
        duration, streams = ffprobe_ok(fn)
        self.assertEqual(sorted(streams), ["audio", "video"])
        self.check_bus()

    def test_filename_callable(self):
        exts = []

        def filename(extension):
            exts.append(extension)
            return os.path.join(self.tmp, "called" + extension)

        fn = self.record(0.3, name=filename)
        self.assertEqual(exts, [".mp4"])
        self.assertEqual(fn, os.path.join(self.tmp, "called.mp4"))

    def test_already_recording(self):
        self.recorder.start(os.path.join(self.tmp, "rec"))
        with self.assertRaises(RecordError):
            self.recorder.start(os.path.join(self.tmp, "rec2"))
        self.recorder.stop(wait=True)

    def test_bad_dir(self):
        with self.assertRaises(RecordError):
            self.recorder.start(os.path.join(self.tmp, "nope", "rec"))
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual(len(self.tee.srcpads), 1)
        self.check_bus()
        # Still usable
        self.record(0.3)

    def test_bad_audio_source(self):
        with self.assertRaises(RecordError):
            self.recorder.start(os.path.join(self.tmp, "rec"),
                                audio=True,
                                audio_source="not_an_element_xyz")
        self.assertFalse(self.recorder.is_recording())

    def test_runtime_error_isolated(self):
        """
        A failing recording must not take down the live view
        """
        done = threading.Event()
        errors = []

        def on_done(fn, err):
            errors.append(err)
            done.set()

        self.recorder.start(os.path.join(self.tmp, "rec"), done=on_done)
        # Simulate ex: disk failure
        self.recorder.bin.filesink.post_message(
            Gst.Message.new_error(self.recorder.bin.filesink,
                                  GLibError("disk full"), "test"))
        self.assertTrue(done.wait(5.0))
        self.assertIsNotNone(errors[0])
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual(self.player.get_state(0)[1], Gst.State.PLAYING)
        pos = self.view_position()
        time.sleep(0.3)
        self.assertGreater(self.view_position(), pos)
        self.check_bus()

    def test_stop_not_recording(self):
        self.assertTrue(self.recorder.stop(wait=True))


@unittest.skipUnless(have_element("x264enc") and have_element("mp4mux"),
                     "No x264enc / mp4mux")
class SlowEncoderTestCase(unittest.TestCase):
    """
    Encoder that can't keep up drops recorded frames, not live view frames
    """
    def test_live_view_not_stalled(self):
        tmp = tempfile.mkdtemp(prefix="pyuscope-test-recorder-")
        player = Gst.parse_launch(
            "videotestsrc is-live=true pattern=snow ! "
            "video/x-raw,width=1920,height=1080,framerate=30/1 ! "
            "tee name=tee ! queue ! fakesink sync=true name=view")
        frames = []
        view = player.get_by_name("view")
        view.set_property("signal-handoffs", True)
        view.connect("handoff", lambda *args: frames.append(1))
        recorder = GstVideoRecorder(player,
                                    player.get_by_name("tee"),
                                    log=lambda s: None)
        old = gst_recorder.VIDEO_ENCODERS["h264"]
        gst_recorder.VIDEO_ENCODERS["h264"] = [("x264enc", {
            "speed-preset": "placebo",
            "tune": "zerolatency",
            "threads": "1",
        })]
        try:
            player.set_state(Gst.State.PLAYING)
            player.get_state(10 * Gst.SECOND)
            recorder.start(os.path.join(tmp, "rec"))
            time.sleep(0.5)
            frames.clear()
            time.sleep(2.0)
            nframes = len(frames)
            recorder.stop(wait=True)
        finally:
            gst_recorder.VIDEO_ENCODERS["h264"] = old
            player.set_state(Gst.State.NULL)
            shutil.rmtree(tmp, ignore_errors=True)
        # 60 nominal
        self.assertGreater(nframes, 40)


@unittest.skipUnless(have_element("mp4mux") and have_element("h264parse"),
                     "No mp4 GStreamer elements")
class ImagerPipelineTestCase(unittest.TestCase):
    """
    Recording from the real (videotestsrc) imager pipeline
    """
    def test_shutdown_finalizes(self):
        from uscope.session import HeadlessSession
        session = HeadlessSession(microscope_name="mock-grbl")
        try:
            recorder = session.vidpip.video_recorder()
            self.assertIs(recorder, session.vidpip.video_recorder())
            fn = recorder.start(os.path.join(_TMP_ROOT, "session"))
            time.sleep(1.0)
        finally:
            session.shutdown()
        self.assertFalse(recorder.is_recording())
        self.assertIsNone(recorder.error)
        self.assertGreater(ffprobe_ok(fn)[0], 0.5)


def GLibError(msg):
    from gi.repository import GLib
    return GLib.Error(msg)


if __name__ == "__main__":
    unittest.main()
