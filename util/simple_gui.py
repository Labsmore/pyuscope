#!/usr/bin/env python

from uscope.config import get_config

from PyQt4 import Qt
from PyQt4.QtGui import *
from PyQt4.QtCore import *
from PyQt4.QtGui import QWidget, QLabel

import queue
import threading
import sys
import traceback
import os
import signal

import gi
#gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
from gi.repository import Gst
Gst.init(None)
from gi.repository import GObject, Gst, GstBase, GObject

uconfig = get_config()


class TestGUI(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.showMaximized()

        self.initUI()

        self.vid_fd = None

        # Must not be initialized until after layout is set
        self.gstWindowId = None
        engine_config = 'gstreamer'
        engine_config = 'gstreamer-testsrc'
        if engine_config == 'gstreamer':
            self.source = Gst.ElementFactory.make('v4l2src', None)
            self.source.set_property("device", "/dev/video0")
            self.vid_fd = -1
            self.setupGst()
        elif engine_config == 'gstreamer-testsrc':
            print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
            self.setupGst()
        else:
            raise Exception('Unknown engine %s' % (engine_config,))

        if self.gstWindowId:
            print("Starting gstreamer pipeline")
            self.player.set_state(Gst.State.PLAYING)


    def get_video_layout(self):
        # Overview
        def low_res_layout():
            layout = QVBoxLayout()
 
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

    def get_ctrl_layout(self):

        layout = QGridLayout()
        row = 0
    
        self.ctrls = {}
        for name in ("Exposure",):

            layout.addWidget(QLabel(name), row, 0)
            ctrl = QLineEdit('0')
            self.ctrls[name] = ctrl
            layout.addWidget(ctrl, row, 1)
            row += 1

        return layout

    def setupGst(self):
        print("Setting up gstreamer pipeline")
        self.gstWindowId = self.video_container.winId()

        self.player = Gst.Pipeline("player")
        self.tee = Gst.ElementFactory.make("tee")
        sinkx = Gst.ElementFactory.make("ximagesink", 'sinkx_overview')
        assert sinkx is not None
        videoconvert = Gst.ElementFactory.make('videoconvert')
        assert videoconvert is not None
        caps = Gst.caps_from_string('video/x-raw,format=yuv')
        self.capture_enc = Gst.ElementFactory.make("jpegenc")
        self.resizer =  Gst.ElementFactory.make("videoscale")
        assert self.resizer is not None

        # Video render stream
        self.player.add(      self.source, self.tee)
        self.source.link(self.tee)

        self.player.add(videoconvert,                 self.resizer, sinkx)
        self.tee.link(videoconvert)
        videoconvert.link(self.resizer)
        self.resizer.link(sinkx)


        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def on_message(self, bus, message):
        t = message.type

        if self.vid_fd is not None and self.vid_fd < 0:
            self.vid_fd = self.source.get_property("device-fd")
            if self.vid_fd >= 0:
                print('Initializing V4L controls')

        if t == Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL)
            print("End of stream")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print("Error: %s" % err, debug)
            self.player.set_state(Gst.State.NULL)

    def on_sync_message(self, bus, message):
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        if message_name == "prepare-xwindow-id":
            if message.src.get_name() == 'sinkx_overview':
                print('sinkx_overview win_id')
                win_id = self.gstWindowId
            else:
                raise Exception('oh noes')

            assert win_id
            imagesink = message.src
            imagesink.set_xwindow_id(win_id)

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('pyv4l test')

        # top layout
        layout = QHBoxLayout()

        layout.addLayout(self.get_video_layout())

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)
        self.show()

def excepthook(excType, excValue, tracebackobj):
    print('%s: %s' % (excType, excValue))
    traceback.print_tb(tracebackobj)
    os._exit(1)

if __name__ == '__main__':
    '''
    We are controlling a robot
    '''
    sys.excepthook = excepthook
    # Exit on ^C instead of ignoring
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    GObject.threads_init()

    app = QApplication(sys.argv)
    _gui = TestGUI()
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    sys.exit(app.exec_())
