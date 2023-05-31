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
        # Enable ROI view?
        overview_roi=False,
        # Enable overview view?
        # hack for second tab displaying overview
        overview2=False,
        overview_full_window=False,
        widget_configs=None,
        # microscope configuration
        usj=None,
        usc=None,
        log=None):
        if usc is None:
            usc = config.get_usc(usj=usj)
        self.usc = usc
        self.source = None
        self.source_name = None
        self.verbose = os.getenv("USCOPE_GSTWIDGET_VERBOSE") == "Y"

        if widget_configs is None:
            widget_configs = OrderedDict()
            # Main window view
            # Placed next to each other
            if overview:
                widget_configs["overview"] = {
                    "type": "overview",
                    "group": "overview1"
                }
            if overview_roi:
                widget_configs["overview_roi"] = {
                    "type": "roi",
                    "group": "overview1"
                }

            # For calibrating video feed
            if overview2:
                widget_configs["overview2"] = {
                    "type": "overview",
                    "size": "small"
                }
            # Stand alone window
            if overview_full_window:
                widget_configs["overview_full_window"] = {
                    "type": "overview",
                    "size": "max"
                }
        """
        key: gst name
        widget: QWidget
        winid: during ON_SYNC_MESSAGE give the winid to render to
        width/height:
        """
        self.wigdatas = OrderedDict()
        for widget_name, widget_config in widget_configs.items():
            self.create_wigdata(widget_name, widget_config)

        # Must not be initialized until after layout is set
        source = self.usc.imager.source()
        if source == "auto":
            source = auto_detect_source()
        self.source_name = source
        self.verbose and print("vidpip source %s" % source)
        # Input image may be cropped, don't use the raw w/h for anything
        # XXX: would be nice if we could detect these
        self.cam_cropped_w, self.cam_cropped_h = usc.imager.cropped_wh()
        self.size_widgets()

        # Needs to be done early so elements can be added before main setup
        self.player = Gst.Pipeline.new("player")
        # Clear if anything bad happens and shouldn't be trusted
        self.ok = True

        if log is None:

            def log(s):
                print(s)

        self.log = log

    def create_wigdata(self, widget_name, widget_config):
        wigdata = dict(widget_config)
        wigdata["gst_name"] = "sinkx_" + widget_name
        wigdata["widget"] = None
        wigdata["winid"] = None
        wigdata["screen_w"] = None
        wigdata["screen_h"] = None
        # gst elements
        wigdata["sinkx"] = None
        wigdata["videoscale"] = None
        wigdata["capsfilter"] = None
        self.wigdatas[widget_name] = wigdata
        return wigdata

    def get_widget(self, name):
        """
        Called by external user to get the widget to render to
        """
        return self.wigdatas[name]["widget"]

    def group_widgets(self):
        """
        Figure out which widgets will be displayed in the same window
        This will be used to manually allocate window space
        """
        group_configs = {
            None: {
                "widgets_h": 1,
                "widgets_v": 1,
            }
        }
        groups = set(
            [wigdata.get("group") for wigdata in self.wigdatas.values()])
        for group_name in groups:
            if not group_name:
                continue
            # assume all h for now
            widgets_h = 0
            for wigdata in self.wigdatas.values():
                if wigdata.get("group") == group_name:
                    widgets_h += 1
            # assert widgets_h >= 2
            group_configs[group_name] = {
                "widgets_h": widgets_h,
                "widgets_v": 1,
            }
        return group_configs

    def size_overview(self, wigdata):
        """
        Halve the sensor size until it fits on screen
        Its unclear if we actually need to do in multiple of 2s though
        Maybe we can do fractional scaling?

        20MP: 5440 x 3648
            5440: 6 divs
            3648: 6 divs
        25MP: 4928 x 4928
            4928: 5 divs
        But we are cropping, so hmm does it matter?
        Maybe crop needs to obey this rule for now as well?
        """
        fast_scaling = False
        if fast_scaling:
            """
            Divide by even multiple of pixels
            Hardware scaling *might* be faster but can really waste screen area
            """
            ratio = 1
            w = self.cam_cropped_w
            h = self.cam_cropped_h
            while w > wigdata["screen_w_max"] or h > wigdata["screen_h_max"]:
                assert w % 2 == 0 and h % 2 == 0, "Failed to fit video feed: scaling would introduce rounding error"
                w = w // 2
                h = h // 2
                ratio *= 2
            wigdata["screen_w"] = w
            wigdata["screen_h"] = h
            wigdata["ratio"] = ratio
            wigdata["scalar"] = 1 / ratio
        else:
            """
            Just squish to the largest size we can fit
            
            """
            wigdata["screen_h_max"]
            # Screen is really wide compared to camera => fill h and crop w
            # XXX: worry about ratio here? How do rounding errors affect things?
            if wigdata["screen_w_max"] / wigdata[
                    "screen_h_max"] >= self.cam_cropped_w / self.cam_cropped_h:
                w = wigdata[
                    "screen_h_max"] * self.cam_cropped_w / self.cam_cropped_h
                h = wigdata["screen_h_max"]
            else:
                w = wigdata["screen_w_max"]
                h = wigdata[
                    "screen_w_max"] * self.cam_cropped_h / self.cam_cropped_w
            wigdata["screen_w"] = int(w)
            wigdata["screen_h"] = int(h)

    def size_roi(self, wigdata):
        """
        Zoom into the middle of the video feed
        Ideally its a 1:1 or even 1:2 ratio with the screen size
        Whereas the other one may be scaled to fit

        NOTE: there are two crops applied here
        First is camera sensor (self.cam_cropped_w) to get rid of unusable sensor area
        Second is this which is to zoom in on an ROI
        """

        # Apply zoom factor
        # Ex: zoom 2 means 100 pixel wide widget only can display 50 cam pixels
        zoom_default = wigdata.setdefault("zoom")
        zoom = zoom_default or 2
        while True:
            cam_max_w = wigdata["screen_w_max"] // zoom
            cam_max_h = wigdata["screen_h_max"] // zoom
            # Calculate crop based on the full size
            # Divide remaining pixels between left and right
            cam_crop_lr = self.cam_cropped_w - cam_max_w
            cam_crop_tb = self.cam_cropped_h - cam_max_h
            if cam_crop_lr < 0 or cam_crop_tb < 0:
                if zoom_default:
                    assert 0, (cam_crop_lr, cam_crop_tb)
                zoom *= 2
                print("FIXME: zoom hack", cam_crop_lr, cam_crop_tb)
                continue
            break
        screen_w = wigdata["screen_w_max"]
        if cam_crop_lr % 2 == 1:
            cam_crop_lr += 1
            screen_w -= 1
        screen_h = wigdata["screen_h_max"]
        if cam_crop_tb % 2 == 1:
            cam_crop_tb += 1
            screen_h -= 1
        crop_lr = cam_crop_lr // 2
        crop_tb = cam_crop_tb // 2
        wigdata["crop"] = {
            "left": crop_lr,
            "right": crop_lr,
            "top": crop_tb,
            "bottom": crop_tb,
        }
        # How much of the sensor is actually being used
        wigdata["screen_w"] = screen_w
        wigdata["screen_h"] = screen_h
        wigdata["cam_w"] = self.cam_cropped_w - crop_lr * 2
        wigdata["cam_h"] = self.cam_cropped_h - crop_tb * 2
        if 0 or self.verbose:
            print("size_roi() for %s" % wigdata["type"])
            print("  Final widget size: %u w x %u h pix" %
                  (wigdata["screen_w"], wigdata["screen_h"]))
            print("  crop filter: %s" % (wigdata["crop"], ))
            print("  zoom: %u" % zoom)
            print("  available cam size: %u w x %u h pix" %
                  (self.cam_cropped_w, self.cam_cropped_h))
            print("  cam ROI size: %u w x %u h pix" %
                  (wigdata["cam_w"], wigdata["cam_h"]))
            print("  widget max size: %u w x %u h pix" %
                  (wigdata["screen_w_max"], wigdata["screen_h_max"]))
            print("  Cam candidate ROI size: %u w x %u h pix" %
                  (cam_max_w, cam_max_h))
        assert wigdata["screen_w"] > 0 and wigdata["screen_h"] > 0, (
            wigdata["screen_w"], wigdata["screen_h"])

    def size_widget(self, wigdata):
        self.size_widgets(wigdata_in=wigdata)

    def size_widgets(self, wigdata_in=None):
        """
        TODO: could we size these based on Qt widget policy?
        ie set to expanding and see how much room is availible
        Then shrink down based on that

        For now this needs to be called early
        But with some tweaks it can be made dynamic
        
        w/h: total canvas area available for all widgets we need to create
        """
        group_configs = self.group_widgets()
        screen_w, screen_h = screen_wh()
        # print("Sizing widgets for screen %u w x %u h" % (screen_w, screen_h))

        for wigdata in self.wigdatas.values():
            """
            self.verbose and print("size_widgets(w=%s, h=%s, frac=%s)" %
                                   (w, h, frac))
            """
            if wigdata_in and wigdata_in != wigdata:
                continue
            group_config = group_configs[wigdata.get("group")]
            # If explicit size is not given assign it
            if not wigdata.get("screen_w_max"):
                # FIXME: rethink these hard coded constraints a bit better
                # Let a widget take up most of the horizontal space
                if wigdata.get("size") == "max":
                    default_frac_h = 0.90
                else:
                    default_frac_h = 0.35
                wigdata["screen_w_max"] = int(
                    screen_w * wigdata.get("screen_scalar_w", 0.90) /
                    group_config["widgets_h"])
                wigdata["screen_h_max"] = int(
                    screen_h * wigdata.get("screen_scalar_h", default_frac_h) /
                    group_config["widgets_v"])

            if wigdata["type"] == "overview":
                self.size_overview(wigdata)
            elif wigdata["type"] == "roi":
                self.size_roi(wigdata)
            else:
                assert 0, wigdata["type"]

    def set_crop(self, wigdata):
        crop = wigdata["crop"]
        wigdata["videocrop"].set_property("top", crop["top"])
        wigdata["videocrop"].set_property("bottom", crop["bottom"])
        wigdata["videocrop"].set_property("left", crop["left"])
        wigdata["videocrop"].set_property("right", crop["right"])

    def setupWidget(self, wigdata, parent=None):
        #print("widget for %s: set %u w, %u h" %
        #      (wigdata["type"], wigdata["screen_w"], wigdata["screen_h"]))
        # Raw X-windows canvas
        wigdata["widget"] = SinkxWidget(parent=parent)
        wigdata["widget"].setMinimumSize(wigdata["screen_w"],
                                         wigdata["screen_h"])
        wigdata["widget"].resize(wigdata["screen_w"], wigdata["screen_h"])
        policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        wigdata["widget"].setSizePolicy(policy)
        # https://github.com/Labsmore/pyuscope/issues/34
        # Let x-windows render directly, a clear here will cause flicker
        wigdata["widget"].setUpdatesEnabled(False)

    def setupWidgets(self, parent=None):
        for wigdata in self.wigdatas.values():
            self.setupWidget(wigdata, parent=parent)

    def roi_zoom_plus(self):
        wigdata = self.wigdatas["overview_roi"]
        self.change_roi_zoom(zoom=wigdata["zoom"] * 2)

    def roi_zoom_minus(self):
        wigdata = self.wigdatas["overview_roi"]
        # https://github.com/Labsmore/pyuscope/issues/171
        # For some reason zoom level 1 causes issue even though still cropped
        if wigdata["zoom"] == 2:
            return
        self.change_roi_zoom(zoom=wigdata["zoom"] // 2)

    def change_roi_zoom(self, zoom):
        """
        Simple experiment to dynamically change video stream
        """
        wigdata = self.wigdatas["overview_roi"]
        wigdata["zoom"] = zoom
        self.size_widget(wigdata)
        self.set_crop(wigdata)

        wigdata["widget"].setMinimumSize(wigdata["screen_w"],
                                         wigdata["screen_h"])
        wigdata["widget"].resize(wigdata["screen_w"], wigdata["screen_h"])

    def add_full_widget(self):
        """
        Experiment to dynamically add a full screen widget after pipeline is running
        Note this doesn't handle groups / assumes its the only widget on the screen
        """
        widget_config = {"type": "overview", "size": "max"}
        wigdata = self.create_wigdata("overview_full_window", widget_config)
        self.size_widget(wigdata)
        self.setupWidget(wigdata)
        vc_dsts = []
        self.wigdata_create(vc_dsts, wigdata)
        self.link_tee_dsts(self.tee_vc, vc_dsts, add=False)
        self.wigdata_link(wigdata)
        # Restart pipeline to get winid
        return wigdata["widget"]

    def full_restart_pipeline(self):
        wigdata = self.wigdatas["overview_full_window"]
        wigdata["winid"] = wigdata["widget"].winId()
        assert wigdata["winid"], "Need widget_winid by run"

        self.player.set_state(Gst.State.PAUSED)
        self.player.set_state(Gst.State.PLAYING)

    def remove_full_widget(self):
        assert 0, "FIXME"

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

    def link_tee(self, src, dsts, add=False):
        """
        Link src to one or more dsts
        If required, add tee + queues

        dsts will be added to player?
        This makes it easier to link things together dynamically
        """

        assert len(dsts) > 0, "Can't create tee with no sink elements"

        # playing with dynamic linking
        # this becomes a bad idea, make sure the tee is always there
        if 0 and len(dsts) == 1:
            dst = dsts[0]
            if add:
                try:
                    self.player.add(dst)
                except gi.overrides.Gst.AddError:
                    print("WARNING: failed to add %s" % (dst, ))
                    raise
            assert src.link(dst)
            self.verbose and print("tee simple link %s => %s" % (src, dst))
            return None
        else:
            tee = Gst.ElementFactory.make("tee")
            self.player.add(tee)
            assert src.link(tee)
            self.link_tee_dsts(tee, dsts, add=add)
            return tee

    def link_tee_dsts(self, tee, dsts, add=False):
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
                # print("Failed to link %s => %s" % (src, dst))
                raise
            # self.verbose and print("tee queue link %s => %s" % (src, dst))

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
                (wigdata["screen_w"], wigdata["screen_h"]))
            self.player.add(wigdata["capsfilter"])

            wigdata["sinkx"] = Gst.ElementFactory.make("ximagesink",
                                                       wigdata["gst_name"])
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
                    (wigdata["screen_w"], wigdata["screen_h"]))
                self.player.add(wigdata["capsfilter"])
            else:
                wigdata["capsfilter"] = None

            wigdata["sinkx"] = Gst.ElementFactory.make("ximagesink",
                                                       wigdata["gst_name"])
            assert wigdata["sinkx"]
            self.player.add(wigdata["sinkx"])

            src_tee.append(wigdata["videocrop"])
        else:
            assert 0, wigdata["type"]

    def wigdata_link(self, wigdata):
        # Used in roi but not full
        if wigdata["type"] == "roi":
            assert wigdata["videocrop"].link(wigdata["videoscale"])
        if wigdata["capsfilter"]:
            assert wigdata["videoscale"].link(wigdata["capsfilter"])
            assert wigdata["capsfilter"].link(wigdata["sinkx"])
        else:
            assert wigdata["scale"].link(wigdata["sinkx"])

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
        self.tee_raw = self.link_tee(raw_element, raw_tees)

        self.verbose and print("Link vc...")
        self.verbose and print("  our", our_vc_tees)
        self.verbose and print("  their", vc_tees)
        vc_tees = our_vc_tees + vc_tees
        self.tee_vc = self.link_tee(self.videoconvert, vc_tees)

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
            print("GstVP: End of stream")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print("GstVP error: %s" % err, debug)
            self.player.set_state(Gst.State.NULL)
            self.ok = False
        elif t == Gst.MessageType.STATE_CHANGED:
            pass

    def gstreamer_to_winid(self, want_name):
        for wigdata in self.wigdatas.values():
            if wigdata["gst_name"] == want_name:
                return wigdata["winid"]
        assert 0, "Failed to match widget winid for ximagesink %s" % want_name

    def on_sync_message(self, bus, message):
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        if message_name == "prepare-window-handle":
            # self.verbose and print("prepare-window-handle", message.src.get_name())
            # print("prepare-window-handle", message.src.get_name())
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)
            winid = self.gstreamer_to_winid(message.src.get_name())
            # FIXME: transiet error while restarting pipeline
            # for now hide the intended window and let it float
            if winid is None:
                print("  WARNING: ignoring bad winid")
            else:
                imagesink.set_window_handle(winid)


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
