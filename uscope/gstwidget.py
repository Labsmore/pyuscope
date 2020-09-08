from uscope.img_util import auto_detect_source

from PyQt5.Qt import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

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
from gi.repository import Gst
Gst.init(None)
from gi.repository import GstBase, GObject, GstVideo

from uscope import config

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
    def __init__(self, source=None, full=True, roi=False, usj=True):
        self.usj = usj
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

        if self.usj:
            # TODO: auto calc these or something better
            usj = config.get_usj()
            self.camw = usj["imager"]["width"]
            self.camh = usj["imager"]["height"]
        # maybe just make this required
        else:
            # Could query
            assert 0, "fixme?"

        # Must not be initialized until after layout is set
        if source is None:
            source = usj["imager"].get("source", "auto")
        if source == "auto":
            source = auto_detect_source()
        self.source_name = source
        print("vidpip source %s" % source)

        # Usable area, not total area
        # XXX: probably should maximize window and take window size
        self.screenw = 1920
        self.screenh = 900

        self.full_capsfilter = None
        self.roi_capsfilter = None
        self.size_widgets()

        # Needs to be done early so elements can be added before main setup
        self.player = Gst.Pipeline.new("player")

    def size_widgets(self, w=None, h=None, frac=None):
        """
        For now this needs to be called early
        But with some tweaks it can be made dynamic
        
        w/h: total canvas area available for all widgets we need to create
        """

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
            self.set_full_widget_wh(w, h)

        if self.roi:
            self.set_roi_widget_wh(w, h)

    def set_full_widget_wh(self, w, h):
        assert self.full_capsfilter is None, "FIXME: handle gst initialized"

        self.full_widget_w = w
        self.full_widget_h = h

        if self.full_widget:
            self.full_widget.setMinimumSize(self.full_widget_w,
                                            self.full_widget_h)
            self.full_widget.resize(self.full_widget_w, self.full_widget_h)

    def set_roi_widget_wh(self, w, h):
        assert self.roi_capsfilter is None, "FIXME: handle gst initialized"

        self.roi_widget_w = w
        self.roi_widget_h = h

        if self.roi_widget:
            self.roi_widget.setMinimumSize(self.roi_widget_w,
                                           self.roi_widget_h)
            self.roi_widget.resize(self.roi_widget_w, self.roi_widget_h)

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

    def prepareSource(self, esize=None):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if self.source_name in ('gst-v4l2src', 'gst-v4l2src-mu800'):
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            self.source.set_property("device", "/dev/video0")
        elif self.source_name == 'gst-toupcamsrc':
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
            if esize is not None:
                self.source.set_property("esize", esize)
        elif self.source_name == 'gst-videotestsrc':
            print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (self.source_name, ))

        if self.usj:
            usj = config.get_usj()
            properties = usj["imager"].get("source_properties", {})
            for propk, propv in properties.items():
                print("Set source %s => %s" % (propk, propv))
                self.source.set_property(propk, propv)

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
                    print("WARNING: failed to add %s" % (dst, ))
            src.link(dst)
            print("tee simple link %s => %s" % (src, dst))
        else:
            tee = Gst.ElementFactory.make("tee")
            self.player.add(tee)
            assert src.link(tee)

            for dst in dsts:
                assert dst is not None
                queue = Gst.ElementFactory.make("queue")
                # self.queues.append(queue)
                self.player.add(queue)
                assert tee.link(queue)
                if add:
                    try:
                        self.player.add(dst)
                    except gi.overrides.Gst.AddError:
                        pass
                        print("WARNING: failed to add %s" % (dst, ))
                try:
                    assert queue.link(dst)
                except:
                    print("Failed to link %s => %s" % (src, dst))
                    raise
                print("tee queue link %s => %s" % (src, dst))

    def setupGst(self, raw_tees=None, vc_tees=None, esize=None):
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

        self.prepareSource(esize=esize)
        self.player.add(self.source)
        """
        observation:
        -adding caps negotation on v4l2src fixed lots of issues (although roi still not working)
            workaround: disable roi on v4l2src
        -adding caps negotation on toupcamsrc caused roi issue
            workaround: disable raw caps negotation on toupcamsrc
        update: toupcamsrc failed due to bad config file setting incorrect caps negotation
        """
        usj = config.get_usj()
        self.raw_capsfilter = Gst.ElementFactory.make("capsfilter")
        self.raw_capsfilter.props.caps = Gst.Caps(
            "video/x-raw,width=%u,height=%u" %
            (usj["imager"]["width"], usj["imager"]["height"]))
        self.player.add(self.raw_capsfilter)

        if not self.source.link(self.raw_capsfilter):
            raise RuntimeError("Couldn't set capabilities on the source")
        raw_element = self.raw_capsfilter

        # This either will be directly forwarded or put into a queue
        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None
        self.player.add(self.videoconvert)

        our_vc_tees = []
        self.full_sinkx = None
        if self.full:
            self.full_scale = Gst.ElementFactory.make("videoscale")
            assert self.full_scale is not None
            self.player.add(self.full_scale)
            our_vc_tees.append(self.full_scale)

            # Unreliable without this => set widget size explicitly
            self.full_capsfilter = Gst.ElementFactory.make("capsfilter")
            self.full_capsfilter.props.caps = Gst.Caps(
                "video/x-raw,width=%u,height=%u" %
                (self.full_widget_w, self.full_widget_h))
            self.player.add(self.full_capsfilter)

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

            if 1:
                self.roi_capsfilter = Gst.ElementFactory.make("capsfilter")
                self.roi_capsfilter.props.caps = Gst.Caps(
                    "video/x-raw,width=%u,height=%u" %
                    (self.roi_widget_w, self.roi_widget_h))
                self.player.add(self.roi_capsfilter)
            else:
                self.roi_capsfilter = None

            self.roi_sinkx = Gst.ElementFactory.make("ximagesink", 'sinkx_roi')
            assert self.roi_sinkx
            self.player.add(self.roi_sinkx)

            our_vc_tees.append(self.roi_videocrop)

        # Note at least one vc tee is garaunteed (either full or roi)
        print("Link raw...")
        raw_tees = [self.videoconvert] + raw_tees
        self.link_tee(raw_element, raw_tees)

        print("Link vc...")
        print("our", our_vc_tees)
        print("their", vc_tees)
        vc_tees = our_vc_tees + vc_tees
        self.link_tee(self.videoconvert, vc_tees)

        # Finish linking post vc_tee

        if self.full:
            if self.full_capsfilter:
                assert self.full_scale.link(self.full_capsfilter)
                assert self.full_capsfilter.link(self.full_sinkx)
            else:
                self.full_scale.link(self.full_sinkx)

        if self.roi:
            assert self.roi_videocrop.link(self.roi_scale)
            if self.roi_capsfilter:
                assert self.roi_scale.link(self.roi_capsfilter)
                assert self.roi_capsfilter.link(self.roi_sinkx)
            else:
                self.roi_scale.link(self.roi_sinkx)

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
