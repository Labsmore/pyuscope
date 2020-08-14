#!/usr/bin/env python

'''
To keep things simple,
just 
'''

from uvscada.v4l2_util import ctrl_set

from PyQt4 import Qt
from PyQt4.QtGui import *
from PyQt4.QtCore import *
from PyQt4.QtGui import QWidget, QLabel

import cv2
import numpy as np

import Queue
import threading
import sys
import traceback
import os
import signal
import shutil
import time

import gobject, pygst
pygst.require('1.0')
import gst

import StringIO
from PIL import Image

LAPLACIAN=1
LAPLACIAN2=0
HISTEQ=1
COLORMAP=cv2.COLORMAP_JET

# too big
#SCALE_DST=1
SCALE_DST=4

# shows bayer artifacts
# should be at least 2
#SCALE_BIN=1
SCALE_BIN=4
#SCALE_BIN=256



'''
Do not encode images in gstreamer context or it brings system to halt
instead, request images and have them encoded in requester's context
'''
class CaptureSink(gst.Element):
    __gstdetails__ = ('CaptureSink','Sink', \
                      'Captures images for the CNC', 'John McMaster')

    _sinkpadtemplate = gst.PadTemplate ("sinkpadtemplate",
                                        gst.PAD_SINK,
                                        gst.PAD_ALWAYS,
                                        gst.caps_new_any())

    def __init__(self):
        gst.Element.__init__(self)
        self.sinkpad = gst.Pad(self._sinkpadtemplate, "sink")
        self.add_pad(self.sinkpad)

        self.sinkpad.set_chain_function(self.chainfunc)
        self.sinkpad.set_event_function(self.eventfunc)

        self.img_cb = lambda buffer: None

    '''
    gstreamer plugin core methods
    '''

    def chainfunc(self, pad, buffer):
        #print 'Capture sink buffer in'
        try:
            self.img_cb(buffer)
        except:
            traceback.print_exc()
            os._exit(1)

        return gst.FLOW_OK

    def eventfunc(self, pad, event):
        return True

gobject.type_register(CaptureSink)
# Register the element into this process' registry.
gst.element_register (CaptureSink, 'capturesink', gst.RANK_MARGINAL)

class ImageProcessor(QThread):
    n_frames = pyqtSignal(int) # Number of images
    processed = pyqtSignal()

    def __init__(self):
        QThread.__init__(self)

        self.running = threading.Event()

        self.image_requested = threading.Event()
        self.q = Queue.Queue()
        self._n_frames = 0

    def run(self):
        self.running.set()
        self.image_requested.set()
        while self.running.is_set():
            try:
                img = self.q.get(True, 0.1)
            except Queue.Empty:
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

        laplacian = cv2.resize(laplacian, (0,0), fx=1.0/SCALE_BIN, fy=1.0/SCALE_BIN, interpolation=cv2.INTER_AREA)

        if LAPLACIAN:
            laplacian = cv2.Laplacian(laplacian, cv2.CV_64F)
        if LAPLACIAN2:
            laplacian = cv2.Laplacian(laplacian, cv2.CV_64F)
        laplacian = np.uint8(laplacian)
        if HISTEQ:
            laplacian = cv2.equalizeHist(laplacian)
            print('hist max: %u, hist min: %u' % (np.amax(laplacian), np.amin(laplacian)))
        if COLORMAP is not None:
            laplacian = cv2.applyColorMap(laplacian, COLORMAP)

        laplacian = cv2.resize(laplacian, (0,0), fx=SCALE_BIN/SCALE_DST, fy=SCALE_BIN/SCALE_DST, interpolation=cv2.INTER_AREA)

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
            self.q.put(buffer.data)


class TestGUI(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)

        self.initUI()

        self.vid_fd = None

        # Must not be initialized until after layout is set
        self.gstWindowId = None
        #self.config_engine('gstreamer-testsrc')
        self.config_engine('gstreamer')

        self.processor = ImageProcessor()
        self.processor.n_frames.connect(self.n_frames.setNum)
        self.processor.processed.connect(self.refresh_image)
        self.capture_sink.img_cb = self.processor.img_cb

        self.processor.start()

        self.sinkx.set_xwindow_id(0)
        self.timers = []
        # simplest reliable workaround, see SO thread
        self.delay(self.win_init,  100)
        if 0:
            self.delay(self.call_show,      5000)
            self.delay(self.set_winid,      10000)
            self.delay(self.start_playing,  1000)
            self.delay(self.set_winid0,     15000)
            self.delay(self.set_winid,      20000)

    def win_init(self):
        self.call_show()
        self.set_winid()
        self.start_playing()

    def set_winid(self):
        '''
        Can be set after playing
        This causes the gstreamer window to merge into the Qt window
        '''
        print('%s winid: %s' % (time.time(), self.gstWindowId))
        #self.sinkx.set_xwindow_id(self.video_container.winId())
        self.sinkx.set_xwindow_id(self.gstWindowId)

    def set_winid0(self):
        self.sinkx.set_xwindow_id(0)

    def call_show(self):
        print("%s show()" % time.time())
        #self.show()
        self.showMaximized()

    def start_playing(self):
        print("%s starting gstreamer pipeline" % time.time())
        self.player.set_state(gst.STATE_PLAYING)

    def delay(self, f, ms):
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(f)
        timer.start(ms)
        self.timers.append(timer)

    def config_engine(self, engine_config):
        if engine_config == 'gstreamer':
            self.source = gst.element_factory_make("v4l2src", "vsource")
            self.source.set_property("device", "/dev/video0")
            self.vid_fd = -1
            self.setupGst()
        elif engine_config == 'gstreamer-testsrc':
            print 'WARNING: using test source'
            self.source = gst.element_factory_make("videotestsrc", "video-source")
            self.setupGst()
        else:
            raise Exception('Unknown engine %s' % (engine_config,))


    def v4l_updated(self):
        for k, v in self.v4ls.iteritems():
            try:
                val = int(str(v.text()))
            except ValueError:
                continue
            if k == 'E':
                val = min(val, 800)
            else:
                val = min(val, 1023)
            ctrl_set(self.vid_fd, k, val)

    def get_misc_layout(self):
        layout = QGridLayout()
        row = 0

        layout.addWidget(QLabel('N'), row, 0)
        self.n_frames = QLabel('0')
        layout.addWidget(self.n_frames, row, 1)
        row += 1

        layout.addWidget(QLabel('Laplacian: %s' % bool(LAPLACIAN)), row, 0)
        row += 1
        layout.addWidget(QLabel('Laplacian2: %s' % bool(LAPLACIAN2)), row, 0)
        row += 1
        layout.addWidget(QLabel('Hist eq: %s' % bool(HISTEQ)), row, 0)
        row += 1
        layout.addWidget(QLabel('Color map: %s' % (COLORMAP is not None,)), row, 0)
        row += 1
        layout.addWidget(QLabel('Bin scalar: %s' % SCALE_BIN), row, 0)
        row += 1
        layout.addWidget(QLabel('Display scalar: %s' % SCALE_DST), row, 0)
        row += 1

        self.v4ls = {}
        # hacked driver to directly drive values
        for ki, (label, v4l_name) in enumerate((("Red", "Red Balance"), ("Green", "Gain"), ("Blue", "Blue Balance"), ("Exp", "Exposure"))):
            cols = 4
            rowoff = ki / cols
            coloff = cols * (ki % cols)

            layout.addWidget(QLabel(label), row + rowoff, coloff)
            le = QLineEdit('')
            self.v4ls[v4l_name] = le
            layout.addWidget(le, row + rowoff, coloff + 1)
            le.textChanged.connect(self.v4l_updated)
        row += 2

        return layout

    def refresh_image(self):
        self.pic.setPixmap(QPixmap("focus_eval-out.png"))
        self.pic.show()

    def get_left_layout(self):
        layout = QVBoxLayout()

        layout.addLayout(self.get_misc_layout())

        self.pic = QLabel(self)
        layout.addWidget(self.pic)

        return layout

    def get_video_layout(self):
        # Overview
        def low_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Overview"))

            # Raw X-windows canvas
            self.video_container = QWidget()
            # Allows for convenient keyboard control by clicking on the video
            self.video_container.setFocusPolicy(Qt.ClickFocus)
            w, h = 3264/4, 2448/4
            self.video_container.setMinimumSize(w, h)
            self.video_container.resize(w, h)
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.video_container.setSizePolicy(policy)

            layout.addWidget(self.video_container)

            return layout

        layout = QHBoxLayout()
        layout.addLayout(low_res_layout())
        return layout

    def setupGst(self):
        print "Setting up gstreamer pipeline"
        self.gstWindowId = self.video_container.winId()

        self.player = gst.Pipeline("player")
        self.tee = gst.element_factory_make("tee")
        self.sinkx = gst.element_factory_make("ximagesink", 'sinkx_overview')
        fcs = gst.element_factory_make('ffmpegcolorspace')
        caps = gst.caps_from_string('video/x-raw-yuv')
        self.capture_enc = gst.element_factory_make("jpegenc")
        self.capture_sink = gst.element_factory_make("capturesink")
        self.capture_sink_queue = gst.element_factory_make("queue")
        self.resizer =  gst.element_factory_make("videoscale")

        # Video render stream
        self.player.add(      self.source, self.tee)
        gst.element_link_many(self.source, self.tee)

        self.player.add(fcs,                 self.resizer, self.sinkx)
        gst.element_link_many(self.tee, fcs, self.resizer, self.sinkx)

        self.player.add(                self.capture_sink_queue, self.capture_enc, self.capture_sink)
        gst.element_link_many(self.tee, self.capture_sink_queue, self.capture_enc, self.capture_sink)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)

    def on_message(self, bus, message):
        t = message.type

        if self.vid_fd is not None and self.vid_fd < 0:
            self.vid_fd = self.source.get_property("device-fd")
            if self.vid_fd >= 0:
                print 'Initializing V4L controls'
                self.v4l_load()

        if t == gst.MESSAGE_EOS:
            self.player.set_state(gst.STATE_NULL)
            print "End of stream"
        elif t == gst.MESSAGE_ERROR:
            err, debug = message.parse_error()
            print "Error: %s" % err, debug
            self.player.set_state(gst.STATE_NULL)
            ''

    def v4l_load(self):
        from config import get_config

        uconfig = get_config()

        vconfig = uconfig["imager"].get("v4l2", None)
        if not vconfig:
            return
        for configk, configv in vconfig.iteritems():
            break
        #if type(configv) != dict or '"Gain"' not in configv:
        #    raise Exception("Bad v4l default config (old style?)")

        print 'Selected config %s' % configk
        for k, v in configv.iteritems():
            if k in self.v4ls:
                self.v4ls[k].setText(str(v))
            else:
                ctrl_set(self.vid_fd, k, v)

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('pyv4l test')

        # top layout
        layout = QHBoxLayout()
        layout.addLayout(self.get_left_layout())
        layout.addLayout(self.get_video_layout())

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)

def excepthook(excType, excValue, tracebackobj):
    print '%s: %s' % (excType, excValue)
    traceback.print_tb(tracebackobj)
    os._exit(1)

if __name__ == '__main__':
    '''
    We are controlling a robot
    '''
    sys.excepthook = excepthook
    # Exit on ^C instead of ignoring
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    gobject.threads_init()

    app = QApplication(sys.argv)
    _gui = TestGUI()
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    sys.exit(app.exec_())
