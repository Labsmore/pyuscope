#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from PyQt4.QtGui import QMainWindow

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
from gi.repository import Gst
Gst.init(None)
from gi.repository import GstBase, GObject


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline()
        self.showMaximized()
        self.initUI()
        self.fakesink = Gst.ElementFactory.make("fakesink")
        self.vidpip.setupGst(tee=self.fakesink, source=source)
        self.vidpip.run()

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('pyv4l test')
        self.vidpip.setupWidgets()
        self.setCentralWidget(self.vidpip.widget)
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
