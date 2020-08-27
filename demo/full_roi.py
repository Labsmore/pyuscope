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

from uscope.gstwidget import GstVideoPipeline, gstwidget_main, CbSink, Gst
from PyQt4.QtGui import QMainWindow
from PyQt4.QtGui import QHBoxLayout
from PyQt4.QtGui import QWidget


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline(full=True, roi=True)
        self.initUI()
        self.mysink = CbSink()

        self.vidpip.setupGst()
        self.vidpip.run()

    def initUI(self):
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()

        # weird results
        if 0:
            layout = QHBoxLayout()
            layout.addWidget(self.vidpip.full_widget)
            layout.addWidget(self.vidpip.roi_widget)

            widget = QWidget()
            widget.setLayout(layout)
            self.setCentralWidget(widget)
        # ok
        # full widget only
        elif 0:
            self.setCentralWidget(self.vidpip.full_widget)
        # bad
        # full widget only
        else:
            layout = QHBoxLayout()
            layout.addWidget(self.vidpip.full_widget)

            widget = QWidget()
            widget.setLayout(layout)
            self.setCentralWidget(widget)

        self.showMaximized()
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
