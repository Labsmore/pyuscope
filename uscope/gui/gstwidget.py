from uscope.imager.imager_util import auto_detect_source

from PyQt5.Qt import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import sys
import traceback
import os
import pathlib
import signal
from collections import OrderedDict

import gi

DEFAULT_TOUPCAMSRC_ESIZE = 0
DEFAULT_V4L2_DEVICE = "/dev/video0"

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
        command = ['xrandr']
        cmd2 = ['grep', '*']
        p = subprocess.Popen(command, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(cmd2, stdin=p.stdout, stdout=subprocess.PIPE)
        p.stdout.close()

        resolution_string, _junk = p2.communicate()
        resolution = resolution_string.split()[0]
        width, height = resolution.split(b'x')
        return int(width), int(height)


"""
The widget used to render a sinkx winId
"""


class SinkxWidget(QWidget):
    '''
    https://github.com/Labsmore/pyuscope/issues/34
    neither of these got called
    however setUpdatesEnabled(False) seems to have been enough

    def eventFilter(self, obj, event):
        """
        Repaint gets requested as GUI updates
        However only x can repaint the widget
        This results in flickering
        Ignore paint events to keep the old data
        """
        print("SinkxWidget: eventFilter()")
        if event.type() == QEvent.Paint:
            print("SinkxWidget: skip paint")
            return True

        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        print("SinkxWidget: paintEvent()")
        pass
    '''


class GstVideoPipeline:
    """
    Integrates Qt widgets + gstreamer pipelines for easy setup
    Allows teeing off the pipeline for custom post processing
    (ex: saving an image, evaluating focus)

    vidpip = GstVideoPipeline()
    vidpip.setupWidgets()
    vidpip.setupGst()
    vidpip.run()
    """
    def __init__(
        self,
        # Enable overview view?
        overview=True,
        # Enable overview view?
        # hack for second tab displaying overview
        overview2=False,
        # Enable ROI view?
        roi=False,
        # microscope configuration
        usj=None,
        usc=None,
        # Manually specify how many widgets are expected in widest window
        # Applicable if you have multiple tabs / windows
        nwidgets_wide=None):
        if usc is None:
            usc = config.get_usc(usj=usj)
        self.usc = usc
        self.source = None
        self.source_name = None
        self.verbose = os.getenv("USCOPE_GSTWIDGET_VERBOSE") == "Y"

        # x buffer target
        self.overview = overview
        self.overview2 = overview2
        # ROI view
        self.roi = roi
        """
        key: gst name
        widget: QWidget
        winid: during ON_SYNC_MESSAGE give the winid to render to
        width/height:
        """
        self.wigdatas = OrderedDict()
        if self.overview:
            self.wigdatas["overview"] = {
                "type": "overview",
                "name": "sinkx_overview",
            }
        if self.overview2:
            self.wigdatas["overview2"] = {
                "type": "overview",
                "name": "sinkx_overview2",
            }
        if self.roi:
            self.wigdatas["roi"] = {
                "type": "roi",
                "name": "sinkx_roi",
            }

        for wigdata in self.wigdatas.values():
            wigdata["widget"] = None
            wigdata["winid"] = None
            wigdata["width"] = None
            wigdata["height"] = None
            # gst elements
            wigdata["sinkx"] = None
            wigdata["videoscale"] = None
            wigdata["capsfilter"] = None

        # Must have at least one widget
        assert self.overview or self.roi

        # Must not be initialized until after layout is set
        source = self.usc.imager.source()
        if source == "auto":
            source = auto_detect_source()
        self.source_name = source
        self.verbose and print("vidpip source %s" % source)
        # Input image may be cropped, don't use the raw w/h for anything
        # XXX: would be nice if we could detect these
        self.cropped_w, self.cropped_h = usc.imager.cropped_wh()

        # Usable area, not total area
        # XXX: probably should maximize window and take window size
        self.widget_w = 1920
        self.widget_h = 900
        self.roi_zoom = 1

        if not nwidgets_wide:
            nwidgets_wide = 2 if self.overview and self.roi else 1
        self.nwidgets_wide = nwidgets_wide

        self.size_widgets()

        # Needs to be done early so elements can be added before main setup
        self.player = Gst.Pipeline.new("player")

    def get_widget(self, name):
        """
        Called by external user to get the widget to render to
        """
        return self.wigdatas[name]["widget"]

    def size_widgets(self, w=None, h=None, frac=None):
        """
        For now this needs to be called early
        But with some tweaks it can be made dynamic
        
        w/h: total canvas area available for all widgets we need to create
        """

        self.verbose and print("size_widgets(w=%s, h=%s, frac=%s)" %
                               (w, h, frac))
        if frac:
            sw, sh = screen_wh()
            w = int(sw * frac)
            h = int(sh * frac)
        if w:
            self.widget_w = w
        if h:
            self.widget_h = h

        assert self.overview or self.roi
        w, h, ratio = self.fit_pix(self.cropped_w * self.nwidgets_wide,
                                   self.cropped_h)
        w = w / self.nwidgets_wide
        w = int(w)
        h = int(h)
        self.verbose and print(
            "%u widgets, cam %uw x %uh => xwidget %uw x %uh %ur" %
            (self.nwidgets_wide, self.cropped_w, self.cropped_h, w, h, ratio))

        self.overview_widget_ratio = ratio

        for wigdata in self.wigdatas.values():
            self.set_gst_widget_wh(wigdata, w, h)

    def set_gst_widget_wh(self, wigdata, w, h):
        assert wigdata["capsfilter"] is None, "FIXME: handle gst initialized"
        w = int(w)
        h = int(h)
        wigdata["width"] = int(w)
        wigdata["height"] = int(h)
        # might not be initialized yet
        if wigdata["widget"]:
            wigdata["widget"].setMinimumSize(w, h)
            wigdata["widget"].resize(w, h)

    def fit_pix(self, w, h):
        ratio = 1
        while w > self.widget_w or h > self.widget_h:
            w = w / 2
            h = h / 2
            ratio *= 2
        return w, h, ratio

    def set_crop(self, wigdata):
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
        ratio = self.overview_widget_ratio * self.roi_zoom
        keepw = self.cropped_w // ratio
        keeph = self.cropped_h // ratio
        self.verbose and print(
            "crop ratio %u => %u, %uw x %uh" %
            (self.overview_widget_ratio, ratio, keepw, keeph))

        # Divide remaining pixels between left and right
        left = right = (self.cropped_w - keepw) // 2
        top = bottom = (self.cropped_h - keeph) // 2
        border = 1
        wigdata["videocrop"].set_property("top", top - border)
        wigdata["videocrop"].set_property("bottom", bottom - border)
        wigdata["videocrop"].set_property("left", left - border)
        wigdata["videocrop"].set_property("right", right - border)

        finalw = self.cropped_w - left - right
        finalh = self.cropped_h - top - bottom
        if self.verbose:
            print("crop: %u l %u r => %u w" % (left, right, finalw))
            print("crop: %u t %u b => %u h" % (top, bottom, finalh))
            print("crop image ratio: %0.3f" % (finalw / finalh, ))
            print("cam image ratio: %0.3f" %
                  (self.cropped_w / self.cropped_h, ))
            print(
                "cam %uw x %uh %0.1fr => crop (x2) %uw x %uh => %uw x %uh %0.1fr"
                % (self.cropped_w, self.cropped_h, self.cropped_w /
                   self.cropped_h, left, top, finalw, finalh, finalw / finalh))
        # assert 0, self.roi_zoom

    def setupWidgets(self, parent=None):
        for wigdata in self.wigdatas.values():
            # Raw X-windows canvas
            wigdata["widget"] = SinkxWidget(parent=parent)
            wigdata["widget"].setMinimumSize(wigdata["width"],
                                             wigdata["height"])
            wigdata["widget"].resize(wigdata["width"], wigdata["height"])
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            wigdata["widget"].setSizePolicy(policy)
            # https://github.com/Labsmore/pyuscope/issues/34
            # Let x-windows render directly, a clear here will cause flicker
            wigdata["widget"].setUpdatesEnabled(False)

    def prepareSource(self, esize=None):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if self.source_name in ('gst-v4l2src', 'gst-v4l2src-mu800'):
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            self.source.set_property("device", DEFAULT_V4L2_DEVICE)
        elif self.source_name == 'gst-toupcamsrc':
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
            if esize is not None:
                self.source.set_property("esize", esize)
        elif self.source_name == 'gst-videotestsrc':
            self.verbose and print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (self.source_name, ))

        for propk, propv in self.usc.imager.source_properties().items():
            self.verbose and print("Set source %s => %s" % (propk, propv))
            self.source.set_property(propk, propv)

    def link_tee(self, src, dsts, add=0):
        """
        Link src to one or more dsts
        If required, add tee + queues

        dsts will be added to player?
        This makes it easier to link things together dynamically
        """

        assert len(dsts) > 0, "Can't create tee with no sink elements"

        if len(dsts) == 1:
            dst = dsts[0]
            if add:
                # XXX: why isn't this a fatal error?
                try:
                    self.player.add(dst)
                except gi.overrides.Gst.AddError:
                    pass
                    print("WARNING: failed to add %s" % (dst, ))
                    raise
            src.link(dst)
            self.verbose and print("tee simple link %s => %s" % (src, dst))
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
                    # XXX: why isn't this a fatal error?
                    try:
                        self.player.add(dst)
                    except gi.overrides.Gst.AddError:
                        pass
                        print("WARNING: failed to add %s" % (dst, ))
                        raise
                # XXX: why isn't this a fatal error?
                try:
                    assert queue.link(dst)
                except:
                    print("Failed to link %s => %s" % (src, dst))
                    raise
                self.verbose and print("tee queue link %s => %s" % (src, dst))

    def wigdata_create(self, src_tee, wigdata):
        if wigdata["type"] == "overview":
            wigdata["videoscale"] = Gst.ElementFactory.make("videoscale")
            assert wigdata["videoscale"] is not None
            self.player.add(wigdata["videoscale"])
            src_tee.append(wigdata["videoscale"])

            # Unreliable without this => set widget size explicitly
            wigdata["capsfilter"] = Gst.ElementFactory.make("capsfilter")
            wigdata["capsfilter"].props.caps = Gst.Caps(
                "video/x-raw,width=%u,height=%u" %
                (wigdata["width"], wigdata["height"]))
            self.player.add(wigdata["capsfilter"])

            wigdata["sinkx"] = Gst.ElementFactory.make("ximagesink",
                                                       wigdata["name"])
            assert wigdata["sinkx"] is not None
            self.player.add(wigdata["sinkx"])
        elif wigdata["type"] == "roi":
            wigdata["videocrop"] = Gst.ElementFactory.make("videocrop")
            assert wigdata["videocrop"]
            self.set_crop(wigdata)
            self.player.add(wigdata["videocrop"])

            wigdata["videoscale"] = Gst.ElementFactory.make("videoscale")
            assert wigdata["videoscale"]
            self.player.add(wigdata["videoscale"])

            if 1:
                wigdata["capsfilter"] = Gst.ElementFactory.make("capsfilter")
                wigdata["capsfilter"].props.caps = Gst.Caps(
                    "video/x-raw,width=%u,height=%u" %
                    (wigdata["width"], wigdata["height"]))
                self.player.add(wigdata["capsfilter"])
            else:
                wigdata["capsfilter"] = None

            wigdata["sinkx"] = Gst.ElementFactory.make("ximagesink",
                                                       wigdata["name"])
            assert wigdata["sinkx"]
            self.player.add(wigdata["sinkx"])

            src_tee.append(wigdata["videocrop"])
        else:
            assert 0, wigdata["type"]

    def wigdata_link(self, wigdata):
        # Used in roi but not full
        if wigdata["type"] == "roi":
            assert "videocrop" in wigdata
            assert wigdata["videocrop"].link(wigdata["videoscale"])
        if wigdata["capsfilter"]:
            assert wigdata["videoscale"].link(wigdata["capsfilter"])
            assert wigdata["capsfilter"].link(wigdata["sinkx"])
        else:
            wigdata["scale"].link(wigdata["sinkx"])

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

        self.verbose and print(
            "Setting up gstreamer pipeline w/ full=%u, roi=%u, tees-r %u, tees-vc %u"
            % (self.overview, self.roi, len(raw_tees), len(vc_tees)))

        # FIXME: is this needed? seems broken anyway
        #if esize is None:
        #    esize = self.usj["imager"].get("esize", None)
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
        self.raw_capsfilter = Gst.ElementFactory.make("capsfilter")
        # Select the correct resolution from the camera
        # This is pre-crop so it must be the actual resolution
        raw_w, raw_h = self.usc.imager.raw_wh()
        self.raw_capsfilter.props.caps = Gst.Caps(
            "video/x-raw,width=%u,height=%u" % (raw_w, raw_h))
        self.player.add(self.raw_capsfilter)

        if not self.source.link(self.raw_capsfilter):
            raise RuntimeError("Couldn't set capabilities on the source")

        # Hack to use a larger than needed camera sensor
        # Crop out the unused sensor area
        crop = self.usc.imager.crop_tblr()
        if crop:
            self.videocrop = Gst.ElementFactory.make("videocrop")
            assert self.videocrop
            self.videocrop.set_property("top", crop["top"])
            self.videocrop.set_property("bottom", crop["bottom"])
            self.videocrop.set_property("left", crop["left"])
            self.videocrop.set_property("right", crop["right"])
            self.player.add(self.videocrop)
            self.raw_capsfilter.link(self.videocrop)
            raw_element = self.videocrop
        else:
            self.videocrop = None
            raw_element = self.raw_capsfilter

        # This either will be directly forwarded or put into a queue
        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None
        self.player.add(self.videoconvert)

        our_vc_tees = []
        for wigdata in self.wigdatas.values():
            self.wigdata_create(our_vc_tees, wigdata)

        # Note at least one vc tee is garaunteed (either full or roi)
        self.verbose and print("Link raw...")
        raw_tees = [self.videoconvert] + raw_tees
        self.link_tee(raw_element, raw_tees)

        self.verbose and print("Link vc...")
        self.verbose and print("  our", our_vc_tees)
        self.verbose and print("  their", vc_tees)
        vc_tees = our_vc_tees + vc_tees
        self.link_tee(self.videoconvert, vc_tees)

        # Finish linking post vc_tee

        for wigdata in self.wigdatas.values():
            self.wigdata_link(wigdata)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def run(self):
        """
        You must have placed widget by now or it will invalidate winid
        """
        for wigdata in self.wigdatas.values():
            wigdata["winid"] = wigdata["widget"].winId()
            assert wigdata["winid"], "Need widget_winid by run"

        self.verbose and print("Starting gstreamer pipeline")
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

    def gstreamer_to_winid(self, want_name):
        for wigdata in self.wigdatas.values():
            if wigdata["name"] == want_name:
                return wigdata["winid"]
        assert 0, "Failed to match widget winid for ximagesink %s" % want_name

    def on_sync_message(self, bus, message):
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        if message_name == "prepare-window-handle":
            # self.verbose and print("prepare-window-handle", message.src.get_name())
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)
            imagesink.set_window_handle(
                self.gstreamer_to_winid(message.src.get_name()))


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
    # FIXME: becoming unreadable
    # app.setStyleSheet(pathlib.Path(config.GUI.stylesheet_file).read_text())

    kwargs = {}
    if parse_args:
        kwargs = parse_args()
    _mainwin = AQMainWindow(**kwargs)
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    sys.exit(app.exec_())
