#!/usr/bin/env python3
"""
ubuntu 16.04
sudo pip3 install scikit-build
sudo pip3 install --upgrade numpy==1.18.4
sudo pip3 install opencv-python==4.2.0.32
"""

from uscope.gstwidget import GstVideoPipeline, gstwidget_main, Gst
from uscope.gst_util import CbSink
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import threading
import cv2
import numpy as np
import shutil
import queue

# import StringIO
# from PIL import Image

LAPLACIAN = 1
LAPLACIAN2 = 0
HISTEQ = 1
COLORMAP = cv2.COLORMAP_JET

# too big
#SCALE_DST=1
SCALE_DST = 4

# shows bayer artifacts
# should be at least 2
#SCALE_BIN=1
SCALE_BIN = 4
#SCALE_BIN=256
'''
Do not encode images in gstreamer context or it brings system to halt
instead, request images and have them encoded in requester's context
'''


class ImageProcessor(QThread):
    n_frames = pyqtSignal(int)  # Number of images
    processed = pyqtSignal()

    def __init__(self):
        QThread.__init__(self)

        self.running = threading.Event()

        self.image_requested = threading.Event()
        self.q = queue.Queue()
        self._n_frames = 0

    """
    def delay(self, f, ms):
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(f)
        timer.start(ms)
        self.timers.append(timer)
    """

    def run(self):
        self.running.set()
        self.image_requested.set()
        while self.running.is_set():
            try:
                img = self.q.get(True, 0.1)
            except queue.Empty:
                continue
            self.process(img)
            self.image_requested.set()

    def process(self, img):
        print('Processing...')

        img_array = np.asarray(bytearray(img), dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)

        print(type(img))
        cv2.imwrite('focus_eval-in.tmp.png', img)
        shutil.move('focus_eval-in.tmp.png', 'focus_eval-in.png')

        laplacian = np.uint8(img)

        laplacian = cv2.resize(laplacian, (0, 0),
                               fx=1.0 / SCALE_BIN,
                               fy=1.0 / SCALE_BIN,
                               interpolation=cv2.INTER_AREA)

        if LAPLACIAN:
            laplacian = cv2.Laplacian(laplacian, cv2.CV_64F)
        if LAPLACIAN2:
            laplacian = cv2.Laplacian(laplacian, cv2.CV_64F)
        laplacian = np.uint8(laplacian)
        if HISTEQ:
            laplacian = cv2.equalizeHist(laplacian)
            print('hist max: %u, hist min: %u' %
                  (np.amax(laplacian), np.amin(laplacian)))
        if COLORMAP is not None:
            laplacian = cv2.applyColorMap(laplacian, COLORMAP)

        laplacian = cv2.resize(laplacian, (0, 0),
                               fx=SCALE_BIN / SCALE_DST,
                               fy=SCALE_BIN / SCALE_DST,
                               interpolation=cv2.INTER_AREA)

        #laplacian.save('focus_eval.jpg', quality=90)
        cv2.imwrite('focus_eval-out.tmp.png', laplacian)
        shutil.move('focus_eval-out.tmp.png', 'focus_eval-out.png')
        print('Done')
        # XXX: is this thread safe?
        self.processed.emit()

    def stop(self):
        self.running.clear()

    def img_cb(self, buffer):
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
            # Clear before emitting signal so that it can be re-requested in response
            self.image_requested.clear()
            # is there a difference between str(buffer) and buffer.data?
            self.q.put(buffer)


class TestGUI(QMainWindow):

    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.vidpip = GstVideoPipeline(source=source,
                                       full=True,
                                       roi=True,
                                       nwidgets=3)
        self.initUI()
        # self.mysink = Gst.ElementFactory.make("mysink")
        # self.mysink = MySink()
        self.mysink = CbSink()
        self.vidpip.player.add(self.mysink)

        self.raw = 0
        if self.raw:
            totee = self.mysink
        else:
            print("Create jpegnec")
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.vidpip.player.add(self.jpegenc)
            totee = self.jpegenc

        # Initialize this early so we can get control default values
        self.vidpip.roi_zoom = 2
        self.vidpip.setupGst(raw_tees=[totee])
        if not self.raw:
            print("raw add")
            assert self.jpegenc.link(self.mysink)

        self.mysink.cb = self.image_cb
        assert self.mysink
        self.vidpip.run()

        self.processor = ImageProcessor()
        self.processor.n_frames.connect(self.n_frames.setNum)
        self.processor.processed.connect(self.refresh_image)

        self.processor.start()

    def image_cb(self, buffer):
        print("got buffer, size %u" % len(buffer))
        assert not self.raw
        # open("raw.jpg", "wb").write(buffer)
        self.processor.img_cb(buffer)

    def refresh_image(self):
        self.pic.setPixmap(QPixmap("focus_eval-out.png"))
        self.pic.show()

    def initUI(self):
        self.setWindowTitle('Demo')
        self.vidpip.setupWidgets()

        # self.vidpip.size_widgets(frac=0.4)

        def left_layout():
            layout = QGridLayout()
            row = 0

            layout.addWidget(QLabel('N'), row, 0)
            self.n_frames = QLabel('0')
            layout.addWidget(self.n_frames, row, 1)
            row += 1

            layout.addWidget(QLabel('Laplacian: %s' % bool(LAPLACIAN)), row, 0)
            row += 1
            layout.addWidget(QLabel('Laplacian2: %s' % bool(LAPLACIAN2)), row,
                             0)
            row += 1
            layout.addWidget(QLabel('Hist eq: %s' % bool(HISTEQ)), row, 0)
            row += 1
            layout.addWidget(
                QLabel('Color map: %s' % (COLORMAP is not None, )), row, 0)
            row += 1
            layout.addWidget(QLabel('Bin scalar: %s' % SCALE_BIN), row, 0)
            row += 1
            layout.addWidget(QLabel('Display scalar: %s' % SCALE_DST), row, 0)
            row += 1

            return layout

        def right_layout():
            layout = QVBoxLayout()

            def top_layout():
                layout = QHBoxLayout()
                layout.addWidget(self.vidpip.full_widget)
                layout.addWidget(self.vidpip.roi_widget)
                return layout

            layout.addLayout(top_layout())
            self.pic = QLabel(self)
            layout.addWidget(self.pic)
            return layout

        layout = QHBoxLayout()
        layout.addLayout(left_layout())
        layout.addLayout(right_layout())

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        self.showMaximized()
        self.show()


if __name__ == '__main__':
    gstwidget_main(TestGUI)
