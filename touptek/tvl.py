#!/usr/bin/env python3

"""
Comprehensive image acquisition test GUI
No motion control
"""

from uscope import gstwidget

from uscope.gstwidget import GstVideoPipeline, gstwidget_main, CbSink
from PyQt4.QtGui import *
from PyQt4.QtCore import *

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

from collections import OrderedDict

class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline(source=source)
        self.vidpip.size_widgets(frac=0.5)
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

        # QTimer.singleShot(100, self.defaultControls)

    def defaultControls(self):
        print("default controls")
        for name, widget in self.ctrls.items():
            ps = self.vidpip.source.find_property(name)
            if type(widget) == QSlider:
                widget.setValue(ps.default_value)
            elif type(widget) == QCheckBox:
                widget.setChecked(ps.default_value)
            else:
                assert 0, type(widget)

    def assemble_group(self, name, layoutg, row):
        self.properties.append(name)
        ps = self.vidpip.source.find_property(name)
        # default = self.vidpip.source.get_property(name)
        if ps.value_type.name == "gint":
            print("%s, %s, default %s, range %s to %s" % (name, ps.value_type.name, ps.default_value, ps.minimum, ps.maximum))
        else:
            print("%s, %s, default %s" % (name, ps.value_type.name, ps.default_value))

        if ps.value_type.name == "gint":
            def changed(name, value_label):
                def f():
                    slider = self.ctrls[name]
                    try:
                        val = int(slider.value())
                    except ValueError:
                        pass
                    else:
                        self.vidpip.source.set_property(name, val)
                        value_label.setText(str(val))
                        print('%s changed => %d' % (name, val))

                return f

            value_label = QLabel(str(ps.default_value))
            layoutg.addWidget(QLabel(name), row, 0)
            layoutg.addWidget(value_label, row, 1)
            row += 1
            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(ps.minimum)
            slider.setMaximum(ps.maximum)
            slider.setValue(ps.default_value)
            slider.valueChanged.connect(changed(name, value_label))
            self.ctrls[name] = slider
            layoutg.addWidget(slider, row, 0, 1, 2)
            row += 1
        elif ps.value_type.name == "gboolean":
            def changed(name, cb):
                def f(val):
                    self.vidpip.source.set_property(name, val)
                    print('%s changed => %d' % (name, val))

                return f

            cb = QCheckBox(name)
            cb.setChecked(ps.default_value)
            cb.stateChanged.connect(changed(name, cb))
            self.ctrls[name] = cb
            layoutg.addWidget(cb, row, 0, 1, 2)
            row += 1
        else:
            assert 0, ps.value_type.name
        return row


    def initUI(self):
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()


        #print(dir(self.vidpip.source))
        #assert 0

        def controlWidget():
            layout = QVBoxLayout()
            row = 0
            self.ctrls = {}

            self.properties = []
            prop_layout = OrderedDict([
                ("Black balance", {
                    "bb-r",
                    "bb-g",
                    "bb-b",
                }),
                ("White balance", {
                    "wb-r",
                    "wb-g",
                    "wb-b",
                }),
                ("HSV+", {
                    "hue",
                    "saturation",
                    "brightness",
                    "contrast",
                    "gamma",
                }),
                ("Exposure", {
                    "auto-exposure",
                    "expotime",
                }),
                ("Flip", {
                    "hflip",
                    "vflip",
                }),
            ])

            for group_name, group in prop_layout.items():
                groupbox = QGroupBox(group_name)
                groupbox.setCheckable(False)
                layout.addWidget(groupbox)

                layoutg = QGridLayout()
                groupbox.setLayout(layoutg)

                for name in group:
                    row = self.assemble_group(name, layoutg, row)


            widget = QWidget()
            widget.setLayout(layout)
            
            scroll = QScrollArea()
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)

            return scroll


        def buttonBarLayout():
            layout = QHBoxLayout()

            btn = QPushButton("Default")
            btn.clicked.connect(self.defaultControls)
            layout.addWidget(btn)

            layout.addWidget(QPushButton("Y"))
            layout.addWidget(QPushButton("Z"))
            return layout

        def imageLayout():
            """
            Suggestion is to have a tab widget here to save snapshots
            For now just have a simple overview widget
            """
            layout = QVBoxLayout()
            layout.addWidget(self.vidpip.full_widget)
            return layout

        def lowerlLayout():
            layout = QHBoxLayout()
            layout.addWidget(controlWidget())
            layout.addLayout(imageLayout())
            return layout
    
        layout = QVBoxLayout()
        layout.addLayout(buttonBarLayout())
        layout.addLayout(lowerlLayout())

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()

def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    args = parser.parse_args()

    return vars(args)

if __name__ == '__main__':
    gstwidget_main(TestGUI, parse_args=parse_args)
