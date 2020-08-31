#!/usr/bin/env python3
"""
Comprehensive image acquisition test GUI
No motion control
"""

from uscope import gstwidget
from uscope.control_scroll import get_control_scroll

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.gst_util import CbSink
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import GstVideo

from gi.repository import Gst
Gst.init(None)
from gi.repository import GstBase, GObject
"""
Initialization constraints:
-Gst initialization needs
"""

import datetime
import os


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline(source=source)
        self.vidpip.size_widgets(frac=0.5)

        self.vidpip.setupGst()
        self.initUI()
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()

        layout = QHBoxLayout()
        self.control_scroll = get_control_scroll(self.vidpip)
        if self.control_scroll:
            layout.addWidget(self.control_scroll)
        layout.addWidget(self.vidpip.full_widget)

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
