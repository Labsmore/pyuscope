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
import datetime
import os

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


class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline(source=source)
        self.vidpip.size_widgets(frac=0.5)

        # self.mysink = Gst.ElementFactory.make("mysink")
        # self.mysink = MySink()
        self.mysink = CbSink()
        self.vidpip.player.add(self.mysink)

        self.snapshot_requested = False
        self.raw = 0
        if self.raw:
            totee = self.mysink
        else:
            print("Create jpegnec")
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.vidpip.player.add(self.jpegenc)
            totee = self.jpegenc

        self.vidpip.setupGst(raw_tees=[totee])
        if not self.raw:
            print("raw add")
            assert self.jpegenc.link(self.mysink)
        self.mysink.cb = self.snapshot_cb

        self.initUI()
        self.vidpip.run()

    def snapshot_fn(self):
        prefix_date = True
        snapshot_dir = "snapshot"
        if self.raw:
            extension = ".bin"
        else:
            extension = ".jpg"
        user = ""

        if not os.path.exists(snapshot_dir):
            os.mkdir(snapshot_dir)

        prefix = ''
        if prefix_date:
            # 2020-08-12_06-46-21
            prefix = datetime.datetime.utcnow().isoformat().replace(
                'T', '_').replace(':', '-').split('.')[0] + "_"

        mod = None
        while True:
            mod_str = ''
            if mod:
                mod_str = '_%u' % mod
            fn_full = os.path.join(snapshot_dir,
                                   prefix + user + mod_str + extension)
            if os.path.exists(fn_full):
                if mod is None:
                    mod = 1
                else:
                    mod += 1
                continue
            return fn_full

    def snapshot_cb(self, buffer):
        if not self.snapshot_requested:
            return
        fn = self.snapshot_fn()
        print("got buffer, size %u, save %s" % (len(buffer), fn))
        open(fn, "wb").write(buffer)
        self.snapshot_requested = False

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

    def assemble_gint(self, name, layoutg, row, ps):
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
        return row

    def assemble_gboolean(self, name, layoutg, row, ps):
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
        return row

    def assemble_group(self, name, layoutg, row):
        self.properties.append(name)
        ps = self.vidpip.source.find_property(name)
        # default = self.vidpip.source.get_property(name)
        if ps.value_type.name == "gint":
            print("%s, %s, default %s, range %s to %s" %
                  (name, ps.value_type.name, ps.default_value, ps.minimum,
                   ps.maximum))
        else:
            print("%s, %s, default %s" %
                  (name, ps.value_type.name, ps.default_value))

        if ps.value_type.name == "gint":
            row = self.assemble_gint(name, layoutg, row, ps)
        elif ps.value_type.name == "gboolean":
            row = self.assemble_gboolean(name, layoutg, row, ps)
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

            btn = QPushButton("Snapshot")

            def requestSnapshot():
                self.snapshot_requested = True

            btn.clicked.connect(requestSnapshot)
            layout.addWidget(btn)

            layout.addWidget(QPushButton("Z"))
            return layout

        def liveTabWidget():
            layout = QVBoxLayout()
            layout.addWidget(self.vidpip.full_widget)

            widget = QWidget()
            widget.setLayout(layout)
            return widget

        def imageTabs():
            tb = QTabWidget()
            tb.addTab(liveTabWidget(), "Live")
            return tb

        def lowerlLayout():
            layout = QHBoxLayout()
            layout.addWidget(controlWidget())
            layout.addWidget(imageTabs())
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
