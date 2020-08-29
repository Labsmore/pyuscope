#!/usr/bin/env python3
"""
Comprehensive image acquisition test GUI
No motion control
"""

from uscope import gstwidget
from uscope.touptek_widget import TTControlScroll

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.gst_util import CbSink
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

import datetime
import os

class TestGUI(QMainWindow):
    def __init__(self, source=None, esize=None):
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

        self.vidpip.setupGst(raw_tees=[totee], esize=esize)
        if not self.raw:
            print("raw add")
            assert self.jpegenc.link(self.mysink)
        self.mysink.cb = self.snapshot_cb

        self.initUI()
        self.vidpip.run()
        self.control_scroll.run()

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

    def initUI(self):
        self.setWindowTitle('Test')
        self.vidpip.setupWidgets()

        #print(dir(self.vidpip.source))
        #assert 0


        def buttonBarLayout():
            layout = QHBoxLayout()

            self.default_pb = QPushButton("Default")
            layout.addWidget(self.default_pb)

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
            self.control_scroll = TTControlScroll(self.vidpip)
            layout.addWidget(self.control_scroll)
            layout.addWidget(imageTabs())
            return layout

        layout = QVBoxLayout()
        layout.addLayout(buttonBarLayout())
        layout.addLayout(lowerlLayout())

        self.default_pb.clicked.connect(self.control_scroll.defaultControls)

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--esize', type=int, default=0)
    args = parser.parse_args()

    return vars(args)


if __name__ == '__main__':
    gstwidget_main(TestGUI, parse_args=parse_args)
