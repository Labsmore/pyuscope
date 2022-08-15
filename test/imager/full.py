#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *


class TestGUI(QMainWindow):

    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline(source=source)
        self.initUI()
        self.vidpip.setupGst()
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
