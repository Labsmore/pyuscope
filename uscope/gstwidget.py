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
    """
    Integrates Qt widgets + gstreamer pipelines for easy setup
    Allows teeing off the pipeline for custom post processing

    vidpip = GstVideoPipeline()
    vidpip.setupWidgets()
    vidpip.setupGst()
    vidpip.run()
    """
    def __init__(self):
        self.source = None
        # x buffer target
        self.widget = None
        self.widget_winid = None
        # ROI view, if any
        self.widget_roi = None
        self.widget_roi_winid = None
        self.roi = False

        # TODO: auto calc these or something better
        self.camw = 5440
        self.camh = 3648
        # Usable area, not total area
        self.screenw = 1920
        self.screenh = 900

    def fit_pix(self, w, h):
        ratio = 1
        while w > self.screenw and h > self.screenh:
            w = w / 2
            h = h / 2
            ratio *= 2
        return w, h, ratio

    def set_crop(self):
        """
        Zoom 2x or something?

        TODO: make this more automagic
        w, h = 3264/8, 2448/8 => 408, 306
        Want 3264/2, 2448,2 type resolution
        Image is coming in raw at this point which menas we need to end up with
        408*2, 306*2 => 816, 612
        since its centered crop the same amount off the top and bottom:
        (3264 - 816)/2, (2448 - 612)/2 => 1224, 918

        self.videocrop_roi.set_property("top", 918)
        self.videocrop_roi.set_property("bottom", 918)
        self.videocrop_roi.set_property("left", 1224)
        self.videocrop_roi.set_property("right", 1224)
        """
        ratio = self.widget_ratio * 1
        # ratio = 1
        keepw = self.camw // ratio
        keeph = self.camh // ratio
        print("crop ratio %u => %u, %uw x %uh" %
              (self.widget_ratio, ratio, keepw, keeph))

        # Divide remaining pixels between left and right
        left = right = (self.camw - keepw) // 2
        top = bottom = (self.camh - keeph) // 2
        self.videocrop_roi.set_property("top", top)
        self.videocrop_roi.set_property("bottom", bottom)
        self.videocrop_roi.set_property("left", left)
        self.videocrop_roi.set_property("right", right)

        finalw = self.camw - left - right
        finalh = self.camh - top - bottom
        print("cam %uw x %uh %0.1fr => crop (x2) %uw x %uh => %uw x %uh %0.1fr" %
              (self.camw, self.camh, self.camw / self.camh, left, top, finalw, finalh, finalw / finalh))

    def setupWidgets(self, parent=None, roi=False):
        # Raw X-windows canvas
        self.widget = QWidget(parent=parent)
        if roi:
            # probably horizontal layout...
            w, h, ratio = self.fit_pix(self.camw * 2, self.camh)
            w = w / 2
        else:
            w, h, ratio = self.fit_pix(self.camw, self.camh)
        print("cam %uw x %uh => xwidget %uw x %uh %ur" %
              (self.camw, self.camh, w, h, ratio))
        self.widget_w = w
        self.widget_h = h
        self.widget_ratio = ratio
        self.widget.setMinimumSize(w, h)
        self.widget.resize(w, h)
        policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.widget.setSizePolicy(policy)

        # Hack: allows for convenient keyboard control by clicking on the video
        # TODO: review if this is a good idea
        self.widget.setFocusPolicy(Qt.ClickFocus)

        if roi:
            self.widget_roi = QWidget(parent=parent)
            self.widget_roi.setMinimumSize(w, h)
            self.widget_roi.resize(w, h)
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.widget_roi.setSizePolicy(policy)

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

    def setupGst(self, source=None, tees=None):
        """
        TODO: clean up queue architecture
        Probably need to add a seperate (optional) tee before and after videoconvert
        This will allow raw imaging but also share encoding for main + ROI
        """

        print("Setting up gstreamer pipeline")

        self.player = Gst.Pipeline("player")
        self.prepareSource(source=source)
        self.player.add(self.source)

        # This either will be directly forwarded or put into a queue
        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None

        #caps = Gst.caps_from_string('video/x-raw,format=rgb')
        #assert caps is not None

        self.sinkx_roi = None
        if 0 and self.widget_roi:
            self.videoconvert_roi = Gst.ElementFactory.make('videoconvert')
            assert self.videoconvert_roi

            self.videocrop_roi = Gst.ElementFactory.make("videocrop")
            assert self.videocrop_roi
            self.set_crop()
            self.player.add(self.videocrop_roi)

            self.scale_roi = Gst.ElementFactory.make("videoscale")
            assert self.scale_roi
            self.player.add(self.scale_roi)

            self.sinkx_roi = Gst.ElementFactory.make("ximagesink", 'sinkx_roi')
            assert self.sinkx_roi
            self.player.add(self.sinkx_roi)

            if tees is None:
                tees = []
            tees.append(self.videoconvert_roi)

        if tees is not None:
            print("Linking tees")
            self.tee = Gst.ElementFactory.make("tee")
            self.player.add(self.tee)
            assert self.source.link(self.tee)

            # Link ours first
            tees = [self.videoconvert] + tees

            # self.queues = []
            for tee in tees:
                queue = Gst.ElementFactory.make("queue")
                # self.queues.append(queue)
                self.player.add(queue)
                assert self.tee.link(queue)
                self.player.add(tee)
                assert queue.link(tee)
        else:
            self.player.add(self.videoconvert)
            assert self.source.link(self.videoconvert)

        if self.widget_roi:
            self.videoconvert_roi.link(self.videocrop_roi)
            self.videocrop_roi.link(self.scale_roi)
            self.scale_roi.link(self.sinkx_roi)

        self.scale = Gst.ElementFactory.make("videoscale")
        assert self.scale is not None
        self.player.add(self.scale)
        self.videoconvert.link(self.scale)

        self.sinkx = Gst.ElementFactory.make("ximagesink", 'sinkx_overview')
        assert self.sinkx is not None
        self.player.add(self.sinkx)
        assert self.scale.link(self.sinkx)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def run(self):
        """
        You must have placed widget by now or it will invalidate winid
        """
        self.widget_winid = self.widget.winId()
        assert self.widget_winid, "Need widget_winid by run"
        if self.widget_roi:
            self.widget_roi_winid = self.widget_roi.winId()
            assert self.widget_roi_winid, "Need widget_winid by run"
        if self.widget_winid:
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
        print("sync2", message_name, self.widget_winid)
        if message_name == "prepare-window-handle":
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)
            assert self.widget_winid, "Need widget_winid by sync"
            if message.src.get_name() == 'sinkx_overview':
                imagesink.set_window_handle(self.widget_winid)
            elif message.src.get_name() == 'sinkx_roi':
                imagesink.set_window_handle(self.widget_roi_winid)
            else:
                assert 0, message.src.get_name()


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
