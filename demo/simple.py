#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from PyQt4.QtGui import QMainWindow


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline()
        self.initUI()
        self.vidpip.setupGst(source=source)
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('pyv4l test')

        self.vidpip.setupWidgets()
        self.setCentralWidget(self.vidpip.widget)
        self.showMaximized()
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
