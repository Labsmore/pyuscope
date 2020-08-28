#!/usr/bin/env python3

from uscope import gstwidget

from uscope.gstwidget import GstVideoPipeline, gstwidget_main, CbSink
from PyQt4.QtGui import QMainWindow
from PyQt4.QtGui import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit
from PyQt4.QtCore import QTimer

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import GstVideo

from gi.repository import Gst
Gst.init(None)
from gi.repository import GstBase, GObject
"""
Initialization constraints:
-Gst initialization needs
"""


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline(source=source)
        # Initialize this early so we can get control default values
        # self.vidpip.setupGst(tee=self.mysink, source="gst-v4l2src")
        self.vidpip.setupGst()
        self.initUI()

        # print(self.vidpip.source.list_properties())
        print(self.vidpip.source.get_property("hue"))
        print(self.vidpip.source.get_property("saturation"))
        # self.vidpip.source.set_property("hue", 0)
        # self.vidpip.source.set_property("saturation", 0)
        self.vidpip.run()

        QTimer.singleShot(100, self.defaultControls)

    def defaultControls(self):
        print("default controls")
        for name in self.properties:
            default = self.vidpip.source.get_property(name)
            self.ctrls[name].setText(str(default))

    def initUI(self):
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()

        def controlLayout():
            layout = QGridLayout()
            row = 0
            self.ctrls = {}

            if self.vidpip.source_name == "gst-v4l2src":
                self.properties = ("hue", "brightness", "saturation",
                                   "contrast")
            elif self.vidpip.source_name == "gst-toupcamsrc":
                # self.properties = ("hue", "brightness", "saturation", "contrast", "gamma")
                self.properties = ("bb_r", "bb_g", "bb_b", "wb_r", "wb_g",
                                   "wb_b")
            else:
                assert 0, "bad source %s" % self.vidpip.source_name

            for name in self.properties:
                default = self.vidpip.source.get_property(name)
                print("%s, default %s" % (name, default))

                def textChanged(name):
                    def f():
                        try:
                            val = int(self.ctrls[name].text())
                        except ValueError:
                            pass
                        else:
                            self.vidpip.source.set_property(name, val)
                            print('%s changed => %d' % (name, val))

                    return f

                layout.addWidget(QLabel(name), row, 0)
                ctrl = QLineEdit(str(default))
                ctrl.textChanged.connect(textChanged(name))
                self.ctrls[name] = ctrl
                layout.addWidget(ctrl, row, 1)
                row += 1

            return layout

        layout = QHBoxLayout()

        layout.addWidget(self.vidpip.full_widget)
        layout.addLayout(controlLayout())
        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
