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

import platform
"""
def screen_wh():
    return width, height
"""
if platform.system() == 'Windows':
    import ctypes

    def screen_wh():
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
else:
    import subprocess

    def screen_wh():
        cmd = ['xrandr']
        cmd2 = ['grep', '*']
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(cmd2, stdin=p.stdout, stdout=subprocess.PIPE)
        p.stdout.close()

        resolution_string, _junk = p2.communicate()
        resolution = resolution_string.split()[0]
        width, height = resolution.split(b'x')
        return int(width), int(height)


class GstVideoPipeline:
    """
    Integrates Qt widgets + gstreamer pipelines for easy setup
    Allows teeing off the pipeline for custom post processing

    vidpip = GstVideoPipeline()
    vidpip.setupWidgets()
    vidpip.setupGst()
    vidpip.run()
    """
    def __init__(self, source=None, full=True, roi=False):
        self.source = None
        self.source_name = None

        # x buffer target
        self.full = full
        self.full_widget = None
        self.full_widget_winid = None

        # ROI view
        self.roi = roi
        self.roi_widget = None
        self.roi_widget_winid = None

        # Must have at least one widget
        assert self.full or self.roi

        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if source is None:
            # XXX: is there a way to see if a camera is attached?
            source = 'gst-toupcamsrc'
        self.source_name = source

        # TODO: auto calc these or something better
        self.camw = 5440
        self.camh = 3648
        # Usable area, not total area
        # XXX: probably should maximize window and take window size
        self.screenw = 1920
        self.screenh = 900

        # Needs to be done early so elements can be added before main setup
        self.player = Gst.Pipeline("player")

        self.size_widgets()

    def size_widgets(self, w=None, h=None, frac=None):
        if frac:
            sw, sh = screen_wh()
            w = int(sw * frac)
            h = int(sh * frac)
        if w:
            self.screenw = w
        if h:
            self.screenh = h

        assert self.full or self.roi
        if self.full and self.roi:
            # probably horizontal layout...
            w, h, ratio = self.fit_pix(self.camw * 2, self.camh)
            w = w / 2
        else:
            w, h, ratio = self.fit_pix(self.camw, self.camh)
        print("cam %uw x %uh => xwidget %uw x %uh %ur" %
              (self.camw, self.camh, w, h, ratio))

        self.full_widget_ratio = ratio

        if self.full:
            self.full_widget_w = w
            self.full_widget_h = h

        if self.roi:
            self.roi_widget_w = w
            self.roi_widget_h = h

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

        self.roi_videocrop.set_property("top", 918)
        self.roi_videocrop.set_property("bottom", 918)
        self.roi_videocrop.set_property("left", 1224)
        self.roi_videocrop.set_property("right", 1224)
        """
        ratio = self.full_widget_ratio * 1
        # ratio = 1
        keepw = self.camw // ratio
        keeph = self.camh // ratio
        print("crop ratio %u => %u, %uw x %uh" %
              (self.full_widget_ratio, ratio, keepw, keeph))

        # Divide remaining pixels between left and right
        left = right = (self.camw - keepw) // 2
        top = bottom = (self.camh - keeph) // 2
        self.roi_videocrop.set_property("top", top)
        self.roi_videocrop.set_property("bottom", bottom)
        self.roi_videocrop.set_property("left", left)
        self.roi_videocrop.set_property("right", right)

        finalw = self.camw - left - right
        finalh = self.camh - top - bottom
        print(
            "cam %uw x %uh %0.1fr => crop (x2) %uw x %uh => %uw x %uh %0.1fr" %
            (self.camw, self.camh, self.camw / self.camh, left, top, finalw,
             finalh, finalw / finalh))

    def setupWidgets(self, parent=None):
        if self.full:
            # Raw X-windows canvas
            self.full_widget = QWidget(parent=parent)
            self.full_widget.setMinimumSize(self.full_widget_w,
                                            self.full_widget_h)
            self.full_widget.resize(self.full_widget_w, self.full_widget_h)
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.full_widget.setSizePolicy(policy)

        if self.roi:
            self.roi_widget = QWidget(parent=parent)
            self.roi_widget.setMinimumSize(self.roi_widget_w,
                                           self.roi_widget_h)
            self.roi_widget.resize(self.roi_widget_w, self.roi_widget_h)
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.roi_widget.setSizePolicy(policy)

    def prepareSource(self):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if self.source_name == 'gst-v4l2src':
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            self.source.set_property("device", "/dev/video0")
        elif self.source_name == 'gst-toupcamsrc':
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
        elif self.source_name == 'gst-videotestsrc':
            print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (self.source_name, ))

    def link_tee(self, src, dsts, add=0):
        """
        Link src to one or more dsts
        If required, add tee + queues

        dsts will be added to player?
        This makes it easier to link things together dynamically
        """

        assert len(dsts) > 0, "Can't create tee with no sink elements"
        print(dsts)

        if len(dsts) == 1:
            dst = dsts[0]
            if add:
                try:
                    self.player.add(dst)
                except gi.overrides.Gst.AddError:
                    pass
            src.link(dst)
            print("tee simple link %s => %s" % (src, dst))
        else:
            tee = Gst.ElementFactory.make("tee")
            self.player.add(tee)
            assert src.link(tee)

            for dst in dsts:
                queue = Gst.ElementFactory.make("queue")
                # self.queues.append(queue)
                self.player.add(queue)
                assert tee.link(queue)
                if add:
                    try:
                        self.player.add(dst)
                    except gi.overrides.Gst.AddError:
                        pass
                assert queue.link(dst)
                print("tee queue link %s => %s" % (src, dst))

    def setupGst(self, source=None, raw_tees=None, vc_tees=None):
        """
        TODO: clean up queue architecture
        Probably need to add a seperate (optional) tee before and after videoconvert
        This will allow raw imaging but also share encoding for main + ROI
        
        
        toupcamsource ! 
        """

        if raw_tees is None:
            raw_tees = []
        if vc_tees is None:
            vc_tees = []

        print(
            "Setting up gstreamer pipeline w/ full=%u, roi=%u, tees-r %u, tees-vc %u"
            % (self.full, self.roi, len(raw_tees), len(vc_tees)))

        self.prepareSource()
        self.player.add(self.source)

        # This either will be directly forwarded or put into a queue
        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None
        self.player.add(self.videoconvert)

        #caps = Gst.caps_from_string('video/x-raw,format=rgb')
        #assert caps is not None

        our_vc_tees = []
        self.full_sinkx = None
        if self.full:
            self.full_scale = Gst.ElementFactory.make("videoscale")
            assert self.full_scale is not None
            self.player.add(self.full_scale)
            our_vc_tees.append(self.full_scale)

            # Unreliable without this => set widget size explicitly
            full_capsfilter = Gst.ElementFactory.make("capsfilter")
            full_capsfilter.props.caps = Gst.Caps(
                "video/x-raw,width=%u,height=%u" %
                (self.full_widget_w, self.full_widget_h))
            self.player.add(full_capsfilter)

            self.full_sinkx = Gst.ElementFactory.make("ximagesink",
                                                      'sinkx_overview')
            assert self.full_sinkx is not None
            self.player.add(self.full_sinkx)

        self.roi_sinkx = None
        if self.roi:
            self.roi_videocrop = Gst.ElementFactory.make("videocrop")
            assert self.roi_videocrop
            self.set_crop()
            self.player.add(self.roi_videocrop)

            self.roi_scale = Gst.ElementFactory.make("videoscale")
            assert self.roi_scale
            self.player.add(self.roi_scale)

            roi_capsfilter = Gst.ElementFactory.make("capsfilter")
            roi_capsfilter.props.caps = Gst.Caps(
                "video/x-raw,width=%u,height=%u" %
                (self.roi_widget_w, self.roi_widget_h))
            self.player.add(roi_capsfilter)

            self.roi_sinkx = Gst.ElementFactory.make("ximagesink", 'sinkx_roi')
            assert self.roi_sinkx
            self.player.add(self.roi_sinkx)

            our_vc_tees.append(self.roi_videocrop)

        # Note at least one vc tee is garaunteed (either full or roi)
        print("Link raw...")
        raw_tees = [self.videoconvert] + raw_tees
        self.link_tee(self.source, raw_tees)

        print("Link vc...")
        print("our", our_vc_tees)
        print("their", vc_tees)
        vc_tees = our_vc_tees + vc_tees
        self.link_tee(self.videoconvert, vc_tees)

        # Finish linking post vc_tee

        if self.full:
            assert self.full_scale.link(full_capsfilter)
            assert full_capsfilter.link(self.full_sinkx)

        if self.roi:
            assert self.roi_videocrop.link(self.roi_scale)
            assert self.roi_scale.link(roi_capsfilter)
            assert roi_capsfilter.link(self.roi_sinkx)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def run(self):
        """
        You must have placed widget by now or it will invalidate winid
        """
        if self.full:
            self.full_widget_winid = self.full_widget.winId()
            assert self.full_widget_winid, "Need widget_winid by run"
        if self.roi:
            self.roi_widget_winid = self.roi_widget.winId()
            assert self.roi_widget_winid, "Need widget_winid by run"
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
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        if message_name == "prepare-window-handle":
            print("prepare-window-handle", message.src.get_name(),
                  self.full_widget_winid, self.roi_widget_winid)
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)
            if message.src.get_name() == 'sinkx_overview':
                1 and imagesink.set_window_handle(self.full_widget_winid)
            elif message.src.get_name() == 'sinkx_roi':
                1 and imagesink.set_window_handle(self.roi_widget_winid)
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
