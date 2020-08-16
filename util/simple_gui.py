#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from PyQt4.QtGui import QMainWindow


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline()
        self.initUI()
        self.vidpip.setupGst(source=source)
        self.vidpip.run()

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('pyv4l test')

        self.vidpip.setupWidgets()
        self.setCentralWidget(self.vidpip.widget)
        self.show()

def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='Apply image correction')
    parser.add_argument('source', nargs="?", default=None)
    args = parser.parse_args()

    return vars(args)

if __name__ == '__main__':
    gstwidget_main(TestGUI, parse_args)
