#!/usr/bin/env python3

from PyQt4 import Qt
from PyQt4.QtGui import *
from PyQt4.QtCore import *
from PyQt4.QtGui import QWidget, QLabel

import sys
import traceback
import os
import signal

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# from gi.repository import GdkX11, GstVideo
from gi.repository import GstVideo

from gi.repository import Gst
Gst.init(None)
from gi.repository import GObject


from uscope.gstwidget import GstVideoPipeline, gstwidget_main


class TestGUI(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.initUI()
        self.vidpip.setupGst()

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('pyv4l test')

        self.vidpip = GstVideoPipeline()
        self.setCentralWidget(self.vidpip.widget)
        self.show()

if __name__ == '__main__':
    gstwidget_main(TestGUI)
