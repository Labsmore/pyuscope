#!/usr/bin/env -S uv run
"""
Appsink video widget
"""

import unittest
import os
import time


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
        widget = AppsinkZoomableWidget(
            player=player,
            config={
                "gst_element_name": "sinkx_zoomable",
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
