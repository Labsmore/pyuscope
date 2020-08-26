from PyQt4.Qt import Qt
from PyQt4.QtGui import QSizePolicy, QApplication
from PyQt4.QtGui import QWidget, QLabel

import sys
import traceback
import os
import signal

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# WARNING: importing GdkX11 will cause hard crash (related to Qt)
# fortunately its not needed
# from gi.repository import GdkX11, GstVideo
from gi.repository import GstVideo

from gi.repository import Gst
Gst.init(None)
from gi.repository import GstBase, GObject


class GstVideoPipeline:
    def __init__(self):
        self.gstWindowId = None
        self.source = None
        self.widget = None

    def setupWidgets(self, parent=None):
        # Raw X-windows canvas
        self.widget = QWidget(parent=parent)
        # Allows for convenient keyboard control by clicking on the video
        self.widget.setFocusPolicy(Qt.ClickFocus)
        w, h = 5440 / 4, 3648 / 4
        self.widget.setMinimumSize(w, h)
        self.widget.resize(w, h)
        policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.widget.setSizePolicy(policy)

    def prepareSource(self, source=None):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if source is None:
            # XXX: is there a way to see if a camera is attached?
            source = 'gst-toupcamsrc'
        self.source_name = source
        if source == 'gst-v4l2src':
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            self.source.set_property("device", "/dev/video0")
        elif source == 'gst-toupcamsrc':
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None
        elif source == 'gst-videotestsrc':
            print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (source, ))

    def setupGst(self, source=None, tee=None):
        self.prepareSource(source=source)
        print("Setting up gstreamer pipeline")

        self.player = Gst.Pipeline("player")
        self.sinkx = Gst.ElementFactory.make("ximagesink", 'sinkx_overview')
        assert self.sinkx is not None
        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None
        #caps = Gst.caps_from_string('video/x-raw,format=rgb')
        #assert caps is not None
        self.resizer = Gst.ElementFactory.make("videoscale")
        assert self.resizer is not None

        # Video render stream
        self.player.add(self.source)

        self.player.add(self.videoconvert, self.resizer, self.sinkx)
        if tee:
            self.tee = Gst.ElementFactory.make("tee")
            self.player.add(self.tee)
            assert self.source.link(self.tee)

            self.queue_us = Gst.ElementFactory.make("queue")
            self.player.add(self.queue_us)
            assert self.tee.link(self.queue_us)
            self.queue_us.link(self.videoconvert)

            self.queue_them = Gst.ElementFactory.make("queue")
            self.player.add(self.queue_them)
            assert self.tee.link(self.queue_them)
            self.player.add(tee)
            assert self.queue_them.link(tee)
        else:
            assert self.source.link(self.videoconvert)
        self.videoconvert.link(self.resizer)
        assert self.resizer.link(self.sinkx)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def run(self):
        """
        You must have placed widget by now or it will invalidate winid
        """
        self.gstWindowId = self.widget.winId()
        assert self.gstWindowId, "Need gstWindowId by run"
        if self.gstWindowId:
            print("Starting gstreamer pipeline")
            self.player.set_state(Gst.State.PLAYING)
            if self.source_name == 'gst-toupcamsrc':
                assert self.source.get_property(
                    "devicepresent"), "camera not found"

    def on_message(self, bus, message):
        t = message.type

        # print("on_message", message, t)
        if t == Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL)
            print("End of stream")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print("Error: %s" % err, debug)
            self.player.set_state(Gst.State.NULL)
        elif t == Gst.MessageType.STATE_CHANGED:
            pass
            # assert self.vidpip.source.get_property("devicepresent")
            # self.player.get_state()
            #print("present", self.source.get_property("devicepresent"))

    def on_sync_message(self, bus, message):
        print("sync1", message.src.get_name())
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        print("sync2", message_name, self.gstWindowId)
        if message_name == "prepare-window-handle":
            assert message.src.get_name() == 'sinkx_overview'
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)
            assert self.gstWindowId, "Need gstWindowId by sync"
            imagesink.set_window_handle(self.gstWindowId)


def excepthook(excType, excValue, tracebackobj):
    print('%s: %s' % (excType, excValue))
    traceback.print_tb(tracebackobj)
    os._exit(1)


def default_parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    parser.add_argument('source', nargs="?", default=None)
    args = parser.parse_args()

    return vars(args)


def gstwidget_main(AQMainWindow, parse_args=default_parse_args):
    '''
    We are controlling a robot
    '''
    sys.excepthook = excepthook
    # Exit on ^C instead of ignoring
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    GObject.threads_init()

    app = QApplication(sys.argv)
    kwargs = {}
    if parse_args:
        kwargs = parse_args()
    _mainwin = AQMainWindow(**kwargs)
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    sys.exit(app.exec_())


class CbSink(GstBase.BaseSink):
    __gstmetadata__ = ('CustomSink','Sink', \
                      'Custom test sink element', 'John McMaster')

    __gsttemplates__ = Gst.PadTemplate.new("sink", Gst.PadDirection.SINK,
                                           Gst.PadPresence.ALWAYS,
                                           Gst.Caps.new_any())

    def __init__(self, *args, **kwargs):
        GstBase.BaseSink.__init__(self, *args, **kwargs)
        self.cb = None

        # self.sinkpad.set_chain_function(self.chainfunc)
        # self.sinkpad.set_event_function(self.eventfunc)

    def chainfunc(self, pad, buffer):
        # print("got buffer, size %u" % len(buffer))
        print("chaiunfun %s" % (buffer, ))
        return Gst.FlowReturn.OK

    def eventfunc(self, pad, event):
        return True

    def do_render(self, buffer):
        print("do_render(), %s" % (buffer, ))

        (result, mapinfo) = buffer.map(Gst.MapFlags.READ)
        assert result

        try:
            # type: bytes
            if self.cb:
                self.cb(mapinfo.data)
        finally:
            buffer.unmap(mapinfo)

        return Gst.FlowReturn.OK


# XXX: these aren't properly registering anymore, but good enough
GObject.type_register(CbSink)
__gstelementfactory__ = ("cbsink", Gst.Rank.NONE, CbSink)
