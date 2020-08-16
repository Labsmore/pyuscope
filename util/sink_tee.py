#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main, CbSink
from PyQt4.QtGui import QMainWindow


class TestGUI(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline()
        self.initUI()
        # self.mysink = Gst.ElementFactory.make("mysink")
        # self.mysink = MySink()
        self.mysink = CbSink()

        def cb(buffer):
            print("got buffer")

        self.mysink.cb = cb
        assert self.mysink
        self.vidpip.setupGst(tee=self.mysink)
        self.vidpip.run()

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()
        self.setCentralWidget(self.vidpip.widget)
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
