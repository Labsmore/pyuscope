#!/usr/bin/env python3
"""
Comprehensive image acquisition test GUI
No motion control
"""

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.gui.control_scrolls import get_control_scroll
from uscope.imager import gst
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *


class MainWindow(QMainWindow):
    def __init__(self, **args):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.usj = {"imager": gst.gstcliimager_args_to_usj(args)}
        self.vidpip = GstVideoPipeline(usj=self.usj)
        self.vidpip.size_widgets(frac=0.5)

        self.vidpip.setupGst()
        self.initUI()
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()

        layout = QHBoxLayout()
        self.control_scroll = get_control_scroll(self.vidpip, usj=self.usj)
        if self.control_scroll:
            layout.addWidget(self.control_scroll)
        layout.addWidget(self.vidpip.get_widget("overview"))

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()
        self.control_scroll.run()


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    gst.gst_add_args(parser)
    args = parser.parse_args()

    return vars(args)


if __name__ == '__main__':
    gstwidget_main(MainWindow, parse_args=parse_args)
