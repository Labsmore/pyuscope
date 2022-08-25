#!/usr/bin/env python3
"""
Simple app to dispaly the full video feed
"""

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.imager import gst
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *


class MainWindow(QMainWindow):

    def __init__(self, **args):
        QMainWindow.__init__(self)
        usj = {"imager": gst.gstcliimager_args_to_usj(args)}
        self.vidpip = GstVideoPipeline(usj=usj)
        self.initUI()
        self.vidpip.setupGst()
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('Demo')
        self.vidpip.setupWidgets()

        layout = QHBoxLayout()
        layout.addWidget(self.vidpip.get_widget("overview"))

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
