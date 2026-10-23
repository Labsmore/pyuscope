#!/usr/bin/env -S uv run
"""
Video sink / Wayland platform selection and the appsink video widget
"""

import unittest
import os
import time

from uscope.gui import video_sink


class VideoSinkSelectTestCase(unittest.TestCase):
    def test_wayland_session(self):
        self.assertTrue(
            video_sink.is_wayland_session({"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertTrue(
            video_sink.is_wayland_session({"XDG_SESSION_TYPE": "wayland"}))
        self.assertFalse(
            video_sink.is_wayland_session({
                "XDG_SESSION_TYPE": "x11",
                "DISPLAY": ":0"
            }))
        self.assertFalse(video_sink.is_wayland_session({}))

    def test_qt_platform_x11(self):
        self.assertIsNone(
            video_sink.wayland_qt_platform({
                "XDG_SESSION_TYPE": "x11",
                "DISPLAY": ":0"
            }))

    def test_qt_platform_wayland_xwayland(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        self.assertEqual(video_sink.wayland_qt_platform(env), "xcb")
        video_sink.setup_qt_platform(env)
        self.assertEqual(env["QT_QPA_PLATFORM"], "xcb")

    def test_qt_platform_wayland_no_xwayland(self):
        self.assertIsNone(
            video_sink.wayland_qt_platform({"WAYLAND_DISPLAY": "wayland-0"}))

    def test_qt_platform_wayland_native(self):
        self.assertIsNone(
            video_sink.wayland_qt_platform({
                "WAYLAND_DISPLAY": "wayland-0",
                "DISPLAY": ":0",
                "PYUSCOPE_WAYLAND": "native"
            }))

    def test_qt_platform_user_override(self):
        env = {
            "WAYLAND_DISPLAY": "wayland-0",
            "DISPLAY": ":0",
            "QT_QPA_PLATFORM": "wayland"
        }
        self.assertIsNone(video_sink.wayland_qt_platform(env))
        video_sink.setup_qt_platform(env)
        self.assertEqual(env["QT_QPA_PLATFORM"], "wayland")

    def test_qt_platform_bad(self):
        with self.assertRaises(ValueError):
            video_sink.wayland_qt_platform({
                "WAYLAND_DISPLAY": "wayland-0",
                "PYUSCOPE_WAYLAND": "bogus"
            })

    def test_sink_auto(self):
        self.assertEqual(video_sink.video_sink("xcb", {}, "linux"),
                         "ximagesink")
        self.assertEqual(video_sink.video_sink("wayland", {}, "linux"),
                         "appsink")
        self.assertEqual(video_sink.video_sink("offscreen", {}, "linux"),
                         "appsink")
        self.assertEqual(video_sink.video_sink("windows", {}, "win32"),
                         "ximagesink")

    def test_sink_override(self):
        env = {"PYUSCOPE_VIDEO_SINK": "appsink"}
        self.assertEqual(video_sink.video_sink("xcb", env, "linux"), "appsink")
        env = {"PYUSCOPE_VIDEO_SINK": "ximagesink"}
        self.assertEqual(video_sink.video_sink("wayland", env, "linux"),
                         "ximagesink")
        with self.assertRaises(ValueError):
            video_sink.video_sink("xcb", {"PYUSCOPE_VIDEO_SINK": "bogus"},
                                  "linux")


class AppsinkWidgetTestCase(unittest.TestCase):
    def test_frames(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from uscope.gui.gstwidget import AppsinkZoomableWidget, Gst

        app = QApplication.instance() or QApplication([])
        player = Gst.Pipeline.new("player")
        src = Gst.ElementFactory.make("videotestsrc")
        src.set_property("is-live", True)
        player.add(src)
        widget = AppsinkZoomableWidget(player=player,
                                       config={
                                           "gst_element_name":
                                           "sinkx_zoomable",
                                           "incoming_wh_hint": (320, 240),
                                       })
        widget.setupWidget()
        dsts = []
        widget.create_elements(player, dsts)
        widget.gst_link()
        widget.resize(400, 400)
        widget.show()
        self.assertTrue(src.link(dsts[0]))
        player.set_state(Gst.State.PLAYING)
        try:
            tend = time.time() + 10
            while widget.image is None and time.time() < tend:
                app.processEvents()
                time.sleep(0.01)
        finally:
            player.set_state(Gst.State.NULL)
        self.assertIsNotNone(widget.image)
        self.assertEqual(widget.image.width(), 320)
        self.assertEqual(widget.image.height(), 240)
        # Paint path doesn't crash and puts the frame on screen
        grabbed = widget.grab().toImage()
        self.assertEqual(grabbed.width(), 400)
        # videotestsrc top left is white
        self.assertNotEqual(grabbed.pixel(5, 200) & 0xFFFFFF, 0)


if __name__ == "__main__":
    unittest.main()
