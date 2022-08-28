#!/usr/bin/env python3
"""
Demonstrates splitting the pipeline into:
-an image capture window
-saving .jpg or raw to disk
"""

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main, Gst
from uscope.gst_util import CbSink
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline(source=source)
        self.initUI()
        # self.mysink = Gst.ElementFactory.make("mysink")
        # self.mysink = MySink()
        self.mysink = CbSink()
        self.vidpip.player.add(self.mysink)

        self.raw = 0
        if self.raw:
            totee = self.mysink
        else:
            print("Create jpegnec")
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.vidpip.player.add(self.jpegenc)
            totee = self.jpegenc

        def cb(buffer):
            print("got buffer, size %u" % len(buffer))
            if self.raw:
                open("raw.bin", "wb").write(buffer)
            else:
                open("raw.jpg", "wb").write(buffer)

        # Initialize this early so we can get control default values
        self.vidpip.setupGst(raw_tees=[totee])
        if not self.raw:
            print("raw add")
            assert self.jpegenc.link(self.mysink)

        self.mysink.cb = cb
        assert self.mysink
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('Demo')
        self.vidpip.setupWidgets()

        layout = QHBoxLayout()
        layout.addWidget(self.vidpip.full_widget)

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        self.showMaximized()
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
