#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from PyQt4.QtGui import QMainWindow

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
