#!/usr/bin/env python3

from uscope import gstwidget

from uscope.gstwidget import GstVideoPipeline, gstwidget_main, CbSink
from PyQt4.QtGui import QMainWindow
from PyQt4.QtGui import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit
from PyQt4.QtCore import QTimer


class TestGUI(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline()
        self.mysink = CbSink()
        # Initialize this early so we can get control default values
        self.vidpip.setupGst(tee=self.mysink, source="gst-v4l2src")
        # self.vidpip.setupGst(tee=self.mysink, source="gst-toupcamsrc")
        self.initUI()

        # self.mysink = Gst.ElementFactory.make("mysink")
        # self.mysink = MySink()
        def cb(buffer):
            print("got buffer")

        self.mysink.cb = cb
        assert self.mysink
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
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()

        def controlLayout():
            layout = QGridLayout()
            row = 0
            self.ctrls = {}
            if self.vidpip.source_name == "gst-v4l2src":
                self.properties = ("hue", "brightness", "saturation", "contrast")
            else:
                assert 0
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

        layout.addWidget(self.vidpip.widget)
        layout.addLayout(controlLayout())
        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
