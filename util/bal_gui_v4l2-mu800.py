#!/usr/bin/env python3
"""
Comprehensive image acquisition test GUI
No motion control
"""

from uscope import gstwidget
from uscope.control_scroll import get_control_scroll
from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.gst_util import CbSink
import threading
import queue

from PIL import Image
import io
from uscope import v4l2_util

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope import config

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

WEBCAM = 1

import datetime
import os

def process_image(img, setr, setg, setb):
    rval = 0
    gval = 0
    bval = 0
    xs = 5
    ys = 5
    for y in range(0, img.size[1], ys):
        for x in range(0, img.size[0], xs):
            (r, g, b) = img.getpixel((x, y))
            rval += r
            gval += g
            bval += b
    sz = img.size[0] * img.size[1] / xs / ys * 256

    rbal = 1.0 * rval / gval
    gbal = 1.0 * gval / gval
    bbal = 1.0 * bval / gval

    sf = 0.2
    limit = lambda x: max(min(int(x), 1023), 0)
    newr = limit(setr - rbal * sf)
    newg = setg
    newb = limit(setb - bbal * sf)
    
    return rval / sz, gval / sz, bval / sz, rbal, gbal, bbal, newr, newg, newb

class ImageProcessor(QThread):
    n_frames = pyqtSignal(int) # Number of images

    r_val = pyqtSignal(int) # calc red value
    g_val = pyqtSignal(int) # calc green value
    b_val = pyqtSignal(int) # calc blue value

    r_bal = pyqtSignal(int) # calc red bal (r/g)
    b_bal = pyqtSignal(int) # calc blue bal (b/g)

    r_new = pyqtSignal(str) # calc red value
    g_new = pyqtSignal(str) # calc green value
    b_new = pyqtSignal(str) # calc blue value

    def __init__(self, tg):
        QThread.__init__(self)

        self.tg = tg
        self.running = threading.Event()

        self.image_requested = threading.Event()
        self.q = queue.Queue()
        self._n_frames = 0

    def run(self):
        self.running.set()
        self.image_requested.set()
        while self.running.is_set():
            try:
                img, props = self.q.get(True, 0.1)
            except queue.Empty:
                continue
            img = Image.open(io.BytesIO(img))

            
            rval, gval, bval, rbal, _gbal, bbal, newr, newg, newb = process_image(img, props["Red Balance"], props["Gain"], props["Blue Balance"])

            self.r_val.emit(int(rval * 1000.0))
            self.g_val.emit(int(gval * 1000.0))
            self.b_val.emit(int(bval * 1000.0))

            self.r_bal.emit(int((rbal - 1) * 1000.0))
            self.b_bal.emit(int((bbal - 1) * 1000.0))

            self.r_new.emit(str(newr))
            self.g_new.emit(str(newg))
            self.b_new.emit(str(newb))

            self.image_requested.set()

    def stop(self):
        self.running.clear()

    def img_cb(self, buffer):
        """
        Called in GUI context
        """

        self._n_frames += 1
        self.n_frames.emit(self._n_frames)
        '''
        Two major circumstances:
        -Imaging: want next image
        -Snapshot: want next image
        In either case the GUI should listen to all events and clear out the ones it doesn't want
        '''
        #print 'Got image'
        #open('tmp_%d.jpg' % self._n_frames, 'w').write(buffer.data)
        if self.image_requested.is_set():
            #print 'Processing image request'
            # is there a difference between str(buffer) and buffer.data?
            self.q.put((buffer, self.tg.get_properties()))
            # Clear before emitting signal so that it can be re-requested in response
            self.image_requested.clear()

class TestGUI(QMainWindow):
    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.vidpip = GstVideoPipeline(source=source)
        self.vidpip.size_widgets(frac=0.5)

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

        self.initUI()
        self.vidpip.run()
        #self.control_scroll.run()

        self.setup_processor()

        """
        # Now driven through image CB
        self.awb_timer = QTimer()
        self.awb_timer.timeout.connect(self.updateControls)
        self.awb_timer.start(1000)
        """

    def setup_processor(self):
        self.processor = ImageProcessor(self)
        self.processor.n_frames.connect(self.n_frames.setNum)

        self.processor.r_val.connect(self.r_val.setNum)
        self.processor.g_val.connect(self.g_val.setNum)
        self.processor.b_val.connect(self.b_val.setNum)

        self.processor.r_bal.connect(self.r_bal.setNum)
        self.processor.b_bal.connect(self.b_bal.setNum)

        self.processor.r_new.connect(self.ctrls["Red Balance"].setText)
        # self.processor.g_new.connect(self.ctrls["Gain"].setText)
        self.processor.b_new.connect(self.ctrls["Blue Balance"].setText)

        # Update after all other signals
        self.processor.b_new.connect(self.set_v4l2)

        self.mysink.set_cb(self.processor.img_cb)

        self.processor.start()

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

        def get_ctrl_layout():
    
            layout = QGridLayout()
            row = 0
    
            self.ctrls = {}
            for name in ("Red Balance", "Gain", "Blue Balance", "Exposure"):
                def textChanged(name):
                    def f():
                        if self.fd() >= 0:
                            if WEBCAM:
                                return
                            try:
                                val = int(self.ctrls[name].text())
                            except ValueError:
                                pass
                            else:
                                print('%s changed => %d' % (name, val))
                                v4l2_util.ctrl_set(self.fd(), name, val)
                    return f
    
                layout.addWidget(QLabel(name), row, 0)
                ctrl = QLineEdit('256')
                ctrl.textChanged.connect(textChanged(name))
                self.ctrls[name] = ctrl
                layout.addWidget(ctrl, row, 1)
                row += 1
    
            return layout


        def balLayout():
            layout = QGridLayout()
            row = 0
    
            self.cal_save_pb = QPushButton("Cal save")
            layout.addWidget(self.cal_save_pb)
            self.cal_save_pb.clicked.connect(self.cal_save)
    
            layout.addWidget(QLabel('N'), row, 0)
            self.n_frames = QLabel('0')
            layout.addWidget(self.n_frames, row, 1)
            row += 1
    
            layout.addWidget(QLabel('R_V'), row, 0)
            self.r_val = QLabel('0')
            layout.addWidget(self.r_val, row, 1)
            row += 1
    
            layout.addWidget(QLabel('G_V'), row, 0)
            self.g_val = QLabel('0')
            layout.addWidget(self.g_val, row, 1)
            row += 1
    
            layout.addWidget(QLabel('B_V'), row, 0)
            self.b_val = QLabel('0')
            layout.addWidget(self.b_val, row, 1)
            row += 1
    
            layout.addWidget(QLabel('R_B'), row, 0)
            self.r_bal = QLabel('0')
            layout.addWidget(self.r_bal, row, 1)
            row += 1
    
            layout.addWidget(QLabel('B_B'), row, 0)
            self.b_bal = QLabel('0')
            layout.addWidget(self.b_bal, row, 1)
            row += 1
    
            return layout

        layout = QHBoxLayout()
        layout.addLayout(get_ctrl_layout())
        layout.addLayout(balLayout())
        #self.control_scroll = get_control_scroll(self.vidpip)
        #layout.addWidget(self.control_scroll)
        layout.addWidget(self.vidpip.full_widget)

        centralWidget = QWidget()
        centralWidget.setLayout(layout)
        self.setCentralWidget(centralWidget)
        self.show()

    def get_properties(self):
        ret = {}
        for ctrl_name, widget in self.ctrls.items():
            ret[ctrl_name] = int(widget.text())
        return ret

    def set_v4l2(self, _hack):
        if self.fd() < 0:
            return
        if WEBCAM:
            return
        for ctrl_name, val in self.get_properties().items():
            v4l2_util.ctrl_set(self.fd(), ctrl_name, val)

    def cal_save(self):
        config.cal_save(source=self.vidpip.source_name,
                        j=self.get_properties())

    def fd(self):
        # -1 before pipeline started
        return self.vidpip.source.get_property("device-fd")


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    args = parser.parse_args()

    return vars(args)


if __name__ == '__main__':
    gstwidget_main(TestGUI, parse_args=parse_args)
