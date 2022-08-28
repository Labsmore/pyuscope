#!/usr/bin/env python3
"""
WARNING
As of 2020-08-27 this is broken

Demonstrates rendering a larger image at left and a zoomed in ROI at right

Similar to:
gst-launch-1.0 toupcamsrc ! tee name=t \
    ! queue ! videoconvert ! videocrop left=1000 right=1000 top=1000 bottom=1000 ! videoscale ! ximagesink t. \
    ! queue ! videoconvert ! videoscale ! ximagesink
"""

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main, Gst
from uscope.gst_util import CbSink
from uscope.imager import gst
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *


class MainWindow(QMainWindow):
    def __init__(self, **args):
        QMainWindow.__init__(self)
        usj = {"imager": gst.gstcliimager_args_to_usj(args)}
        self.vidpip = GstVideoPipeline(usj=usj, overview=True, roi=True)
        self.initUI()
        self.mysink = CbSink()

        self.vidpip.setupGst()
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('Demo')
        self.vidpip.setupWidgets()

        layout = QHBoxLayout()
        layout.addWidget(self.vidpip.get_widget("overview"))
        layout.addWidget(self.vidpip.get_widget("roi"))

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        self.showMaximized()
        self.show()


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    gst.gst_add_args(parser)
    args = parser.parse_args()

    return vars(args)


if __name__ == '__main__':
    gstwidget_main(MainWindow, parse_args=parse_args)
