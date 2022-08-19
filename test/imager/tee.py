#!/usr/bin/env python3

"""
Demonstrate splitting a gstreamer stream
"""

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
from gi.repository import Gst

Gst.init(None)
from gi.repository import GstBase, GObject


class TestGUI(QMainWindow):

    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline(source=source)
        self.initUI()
        self.fakesink = Gst.ElementFactory.make("fakesink")
        self.vidpip.player.add(self.fakesink)
        self.vidpip.setupGst(vc_tees=[self.fakesink])
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
