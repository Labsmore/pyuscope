#!/usr/bin/env python3
"""
Ideal flow:
-Wait for gst/v4l2 to initialize and seed values
-User can't change R or B while AWB running. They also don't poll (except for initial)
-Algorithm changes sliders. Changing sliders triggers gst update
"""

from uscope import gstwidget
from uscope.gui.control_scrolls import get_control_scroll
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

    rval = rval / sz
    gval = gval / sz
    bval = bval / sz

    rbal = 1.0 * rval / gval - 1.0
    gbal = 1.0 * gval / gval - 1.0
    bbal = 1.0 * bval / gval - 1.0

    sfr = 100
    # Blue responds slower
    sfb = 150
    limit = lambda x: max(min(int(x), 1023), 0)
    newr = limit(setr - rbal * sfr)
    newg = setg
    newb = limit(setb - bbal * sfb)

    print("V-RGB %0.1f%% %0.1f%% %0.1f%% => R-B %0.3f B-B %0.3f" %
          (100.0 * rval, 100.0 * gval, 100.0 * bval, rbal, bbal))
    print("RGB %u %u %u => %u %u %u" % (setr, setg, setb, newr, newg, newb))
    return rval, gval, bval, rbal, gbal, bbal, newr, newg, newb


class ImageProcessor(QThread):
    n_frames = pyqtSignal(int)  # Number of images

    # Fraction of range 0.0 to 1.0 * 1000
    # ie 1000 means maxed out
    # ie 0 means channel empty
    r_val = pyqtSignal(float)
    g_val = pyqtSignal(float)
    b_val = pyqtSignal(float)

    # Balance fraction relative to green * 1000
    # 0 means same balance as green
    # 1000 means twice as much as green
    # -1000 menas half as much as green
    # Scaled to 1000 though to make display in GUI easier
    r_bal = pyqtSignal(int)
    b_bal = pyqtSignal(int)

    # New control values
    r_new = pyqtSignal(int)
    g_new = pyqtSignal(int)
    b_new = pyqtSignal(int)

    def __init__(self, tg):
        QThread.__init__(self)

        self.tg = tg
        self.running = threading.Event()

        self.image_requested = threading.Event()
        self.q = queue.Queue()
        self._n_frames = 0

    def run(self):
        self.running.set()
        while self.running.is_set():
            try:
                img, props = self.q.get(True, 0.1)
            except queue.Empty:
                continue
            print("")
            img = Image.open(io.BytesIO(img))

            rval, gval, bval, rbal, _gbal, bbal, newr, newg, newb = process_image(
                img, props["Red"], props["Green"], props["Blue"])

            self.r_val.emit(int(rval * 1000))
            self.g_val.emit(int(gval * 1000))
            self.b_val.emit(int(bval * 1000))

            self.r_bal.emit(int(rbal * 1000))
            self.b_bal.emit(int(bbal * 1000))

            self.r_new.emit(newr)
            self.g_new.emit(newg)
            self.b_new.emit(newb)

            self.image_requested.set()

    def stop(self):
        self.running.clear()

    def img_cb(self, buffer):
        """
        Called in GUI context
        """

        # First frame?
        if self._n_frames == 0:
            # Hack: initialize control values since we can't before pipeline starts
            # self.tg.control_scroll.refresh_defaults()
            # And process the image
            self.image_requested.set()

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
            self.q.put((buffer, self.tg.control_scroll.get_disp_properties()))
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
        self.control_scroll.run()

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

        def update_red(val):
            self.control_scroll.set_disp_properties({"Red": val})

        self.processor.r_new.connect(update_red)

        def update_blue(val):
            self.control_scroll.set_disp_properties({"Blue": val})

        self.processor.b_new.connect(update_blue)

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

        def balLayout():
            layout = QGridLayout()
            row = 0

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
        self.control_scroll = get_control_scroll(self.vidpip)
        props = {"Red", "Blue"}
        self.control_scroll.set_push_gui(False, props)
        self.control_scroll.set_push_prop(True, props)
        layout.addWidget(self.control_scroll)
        layout.addLayout(balLayout())
        layout.addWidget(self.vidpip.full_widget)

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
