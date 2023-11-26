from uscope.imager.imager_util import auto_detect_source
from uscope.v4l2_util import find_device

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
import math

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
    def __init__(self,
                 gst_name=None,
                 config=None,
                 incoming_wh=None,
                 player=None,
                 parent=None):
        super().__init__(parent=parent)

        self.player = player
        policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setSizePolicy(policy)
        self.resize(500, 500)
        # print("after resize", self.width(), self.height())

        # The actual QWidget
        self.gst_name = gst_name
        self.config = config
        # gstreamer rendering element
        self.sinkx = None
        # Window ID from the sinkx element
        # Passed to kernel so it knows where to render
        self.winid = None
        # Used to fit incoming stream to window
        self.videoscale = None
        self.videocrop = None
        # Tell the videoscale the window size we need
        self.capsfilter = None

        # old method...
        self.screen_w = None
        self.screen_h = None

        # Input image may be cropped, don't use the raw w/h for anything
        # Fixed across a microscope run, not currently configurable after startup
        # XXX: would be nice if we could detect these
        self.incoming_w, self.incoming_h = incoming_wh

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
    """
    def resizeEvent(self, event):
        print("resized")
    """

    def sizeHint(self):
        return QSize(500, 500)

    def setupWidget(self, parent=None):
        if parent:
            self.setParent(parent)
        # Stop paint events from causing flicker on the raw x buffer
        self.setUpdatesEnabled(False)


# Use QWidget size policy
# Crop stream if needed to fit size?
# Unclear what happens if its not a perfect match
class SinkxZoomableWidget(SinkxWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.zoom = 1.0
        # The value to restore zoom to when toggling high zoom off
        self.zoom_out = None
        self.videoflip = None

    def is_fixed(self):
        return False

    def calc_size(self):
        pass

    def roi_zoom_plus(self):
        # FIXME: high zoom levels cause crash
        # prohibit for now
        zoom = self.zoom * 2
        if zoom >= 32.0:
            zoom = 32.0
        self.change_roi_zoom(zoom=zoom)

    def roi_zoom_minus(self):
        zoom = self.zoom // 2
        if zoom <= 1.0:
            zoom = 1.0
        self.change_roi_zoom(zoom=zoom)

    def zoomable_high_toggle(self):
        """
        Toggle between "general zoom" and a pre-set high zoom mode
        """
        # Zoomed in => zoom out
        if self.zoom_out:
            self.change_roi_zoom(self.zoom_out)
            self.zoom_out = None
        # Zoomed out => zoom in
        else:
            self.zoom_out = self.zoom
            self.change_roi_zoom(self.calc_zoom_magnified())

    def resizeEvent(self, event):
        self.update_crop_scale()

    def calc_zoom_magnified(self):
        """
        Return the zoom level required to display camera feed at 2x the screen resolution
        """
        widget_width = self.width()
        """
        widget_height = self.height()
        if widget_width / widget_height >= self.incoming_w / self.incoming_h:
            screen_w_1x = widget_height * self.incoming_w / self.incoming_h
        else:
            screen_w_1x = widget_width
        pix_incoming_to_screen_1x = screen_w_1x / self.incoming_w
        """

        # Display 2 screen pixels for every camera pixel
        # TODO: HiDPI scale?
        factor = 4.0
        # Ex: widget 200 pixels wide, factor=2 => 100 incoming pixels used
        incoming_used_w = int(widget_width / factor)
        zoom = self.incoming_w / incoming_used_w
        """
        print("Widget pixels: %uw x %uh" % (widget_width, widget_height))
        print("Incoming size: %uw x %uh" % (self.incoming_w, self.incoming_h))
        print("Used screen width at 1x: %uw" % (screen_w_1x,))
        print("calc_zoom_magnified: settle zoom %0.3f" % zoom)
        """
        return zoom

    def update_crop_scale(self):
        """
        Set videoscale, videocrop based on current widget size + zoom level
        By definition, fit the entire video feed in the widget at zoom level 1
            possibly cropping width or height
        At zoom level 2 half as much of the view is visible but in more detail

        on keeping aspect ratio
        https://stackoverflow.com/questions/36489794/how-to-change-aspect-ratio-with-gstreamer
        looks like add-borders=true is default => will not change aspect ratio
        lets see how this look
        """
        widget_width = self.width()
        widget_height = self.height()
        verbose = False
        verbose and print("update_crop_scale")
        verbose and print(f"  widget {widget_width}w x {widget_height}h")
        verbose and print(
            f"  incoming {self.incoming_w}w x {self.incoming_h}h")
        verbose and print("  zoom: %0.1f" % self.zoom)
        if widget_width == 0 or widget_height == 0:
            print("WARNING: widget not ready yet for pipeline rescale")
            return
        assert widget_width and widget_height
        """
        What would zoom level 1 look like?
        Is the incoming stream more constrained by width or height?
        """
        if widget_width / widget_height >= self.incoming_w / self.incoming_h:
            screen_w_1x = widget_height * self.incoming_w / self.incoming_h
            screen_h_1x = widget_height
        else:
            screen_w_1x = widget_width
            screen_h_1x = widget_width * self.incoming_h / self.incoming_w
        verbose and print(
            f"  Screen: 1x render {screen_w_1x}w x {screen_h_1x}h")
        pix_screen_to_incoming_1x = self.incoming_w / screen_w_1x
        """
        Now figure out the maximum video size supported at this zoom level
        """
        incoming_used_w = int(screen_w_1x * pix_screen_to_incoming_1x /
                              self.zoom)
        incoming_used_h = int(screen_h_1x * pix_screen_to_incoming_1x /
                              self.zoom)
        # Now that we know what can fit,
        # See how much we need to crop off
        # Keep centered => round size down if needed
        # Not strictly necessary, could push to l or r though
        incoming_crop_lr = int(max(0, self.incoming_w - incoming_used_w)) // 2
        incoming_crop_tb = int(max(0, self.incoming_h - incoming_used_h)) // 2
        if incoming_crop_lr % 2 == 1:
            incoming_crop_lr += 1
            incoming_used_w -= 1
        if incoming_crop_tb % 2 == 1:
            incoming_crop_lr += 1
            incoming_used_h -= 1
        verbose and print(
            f"  Incoming: zoomed size {incoming_used_w}w x {incoming_used_h}h")
        verbose and print(
            f"  Incoming crop {incoming_crop_lr}lr x {incoming_crop_tb}tb")

        assert incoming_crop_lr >= 0
        assert incoming_crop_tb >= 0
        self.crop = {
            "left": incoming_crop_lr,
            "right": incoming_crop_lr,
            "top": incoming_crop_tb,
            "bottom": incoming_crop_tb,
        }
        verbose and print("crop", self.crop)

        self.videocrop.set_property("top", self.crop["top"])
        self.videocrop.set_property("bottom", self.crop["bottom"])
        self.videocrop.set_property("left", self.crop["left"])
        self.videocrop.set_property("right", self.crop["right"])

        # Hmm seems to be optional
        # By default centers to full size
        # Does it know the window size and this is redundant?
        # Gets centered if I make it really small
        if 0:
            # Set to full widget size (as opposed to usable widget area) so that border is added to center view
            # see caps filter add-borders=true
            self.capsfilter.props.caps = Gst.Caps(
                "video/x-raw,width=%u,height=%u" %
                (widget_width, widget_height))
            # New caps => must reconfigure
            # doesn't seem to be required?
            self.player.send_event(Gst.Event.new_reconfigure())

    def change_roi_zoom(self, zoom):
        """
        Widget size is fixed
        Get the video feed to fit into whatever we have
        """
        assert zoom >= 1.0
        self.zoom = zoom
        self.update_crop_scale()

    def create_elements(self, player, src_tee):
        self.videocrop = Gst.ElementFactory.make("videocrop")
        assert self.videocrop
        player.add(self.videocrop)

        self.videoscale = Gst.ElementFactory.make("videoscale")
        assert self.videoscale
        player.add(self.videoscale)

        # Use hardware acceleration if present
        # Otherwise can soft flip feeds when / if needed
        # videoflip_method = self.parent.usc.imager.videoflip_method()
        videoflip_method = config.get_usc().imager.videoflip_method()
        if videoflip_method:
            self.videoflip = Gst.ElementFactory.make("videoflip")
            assert self.videoflip
            self.videoflip.set_property("method", videoflip_method)
            player.add(self.videoflip)

        self.capsfilter = Gst.ElementFactory.make("capsfilter")
        self.update_crop_scale()
        player.add(self.capsfilter)

        self.sinkx = Gst.ElementFactory.make("ximagesink", self.gst_name)
        assert self.sinkx
        player.add(self.sinkx)
        src_tee.append(self.videocrop)

        # Do a baseline scale?
        # no too early. widget is not sized yet
        # wait for resize event
        # self.update_crop_scale()

    def gst_link(self):
        if self.videoflip:
            assert self.videocrop.link(self.videoflip)
            assert self.videoflip.link(self.videoscale)
        else:
            assert self.videocrop.link(self.videoscale)
        assert self.videoscale.link(self.capsfilter)
        assert self.capsfilter.link(self.sinkx)


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
        overview=False,
        # Enable ROI view?
        overview_roi=False,
        zoomable=False,
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
            # Currently these two are more or less identical
            # but convey intent in case they start to diverge
            # FIXME: now that widget sizing is now based on QWidget,
            # simplify this
            if zoomable:
                widget_configs["zoomable"] = {
                    "type": "zoomable",
                }
            # For calibrating video feed
            if overview2:
                widget_configs["overview2"] = {
                    "type": "zoomable",
                }
            # Stand alone window
            if overview_full_window:
                widget_configs["overview_full_window"] = {
                    "type": "zoomable",
                }
        # Needs to be done early so elements can be added before main setup
        self.player = Gst.Pipeline.new("player")
        """
        key: gst name
        widget: QWidget
        winid: during ON_SYNC_MESSAGE give the winid to render to
        width/height:
        """
        self.incoming_w, self.incoming_h = usc.imager.cropped_wh()
        self.widgets = OrderedDict()
        for widget_name, widget_config in widget_configs.items():
            self.create_widget(widget_name, widget_config)

        # Must not be initialized until after layout is set
        source = self.usc.imager.source()
        if source == "auto":
            source = auto_detect_source()
        self.source_name = source
        self.verbose and print("vidpip source %s" % source)
        self.size_widgets()

        # Clear if anything bad happens and shouldn't be trusted
        self.ok = True

        if log is None:

            def log(s):
                print(s)

        self.log = log

    def create_widget(self, widget_name, widget_config):
        t = {
            # "full": SinkxWidgetOverviewFixed,
            # "roi": SinkxWidgetROIFixed,
            "zoomable": SinkxZoomableWidget,
        }[widget_config["type"]]
        widget = t(
            gst_name="sinkx_" + widget_name,
            config=dict(widget_config),
            incoming_wh=(self.incoming_w, self.incoming_h),
            player=self.player,
        )
        self.widgets[widget_name] = widget
        return widget

    def get_widget(self, name):
        """
        Called by external user to get the widget to render to
        """
        return self.widgets[name]

    def size_widget(self, widget):
        if widget.is_fixed():
            self.size_widgets(widget_in=widget)

    def size_widgets(self, widget_in=None):
        """
        TODO: could we size these based on Qt widget policy?
        ie set to expanding and see how much room is availible
        Then shrink down based on that

        For now this needs to be called early
        But with some tweaks it can be made dynamic
        
        w/h: total canvas area available for all widgets we need to create
        """

        # print("Sizing widgets for screen %u w x %u h" % (screen_w, screen_h))

        for widget in self.widgets.values():
            """
            self.verbose and print("size_widgets(w=%s, h=%s, frac=%s)" %
                                   (w, h, frac))
            """
            if widget_in and widget_in != widget:
                continue
            if not widget.is_fixed():
                continue
            self.widget.calc_size()

    def setupWidgets(self):
        for widget in self.widgets.values():
            widget.setupWidget()

    def zoomable_plus(self):
        self.widgets["zoomable"].roi_zoom_plus()

    def zoomable_minus(self):
        self.widgets["zoomable"].roi_zoom_minus()

    def zoomable_high_toggle(self):
        self.widgets["zoomable"].zoomable_high_toggle()

    def change_roi_zoom(self, zoom):
        self.widgets["zoomable"].change_roi_zoom(zoom)

    def add_full_widget(self):
        # Don't think we have to pause first but might as well
        self.player.set_state(Gst.State.PAUSED)
        widget_config = {
            "type": "zoomable",
        }
        widget = self.create_widget("overview_full_window", widget_config)
        widget.setupWidget()
        vc_dsts = []
        widget.create_elements(self.player, vc_dsts)
        self.link_tee_dsts(self.tee_vc, vc_dsts, add=False)
        widget.gst_link()
        # moved to after creating
        # seems to be unreliable here
        # maybe needs to be after window is made?
        # self.full_restart_pipeline()
        return widget

    def full_restart_pipeline(self):
        for widget in self.widgets.values():
            widget.winid = widget.winId()
            assert widget.winid, "Need widget_winid by run"

        self.player.set_state(Gst.State.PLAYING)

    def remove_full_widget(self):
        assert 0, "FIXME"

    def prepareSource(self, esize=None):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        properties = {}

        is_v4l2 = self.source_name == 'gst-v4l2src' or self.source_name.find(
            'gst-v4l2src-') == 0
        if is_v4l2:
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
        elif self.source_name == 'gst-toupcamsrc':
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
            if esize is not None:
                properties["esize"] = esize
        elif self.source_name == 'gst-libcamerasrc':
            self.source = Gst.ElementFactory.make('libcamerasrc', None)
            assert self.source is not None, "Failed to load libcamerasrc"
        elif self.source_name == 'gst-videotestsrc':
            self.verbose and print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (self.source_name, ))

        # Override with user specified values
        properties.update(self.usc.imager.source_properties())

        # Set default v4l2 device, if not given
        if is_v4l2 and not properties.get("device"):
            properties["device"] = DEFAULT_V4L2_DEVICE
            # TODO: scrape info one way or the other to identify preferred device
            name = self.usc.imager.j.get("v4l2_name")
            if name:
                device = find_device(name)
                print(f"Camera '{name}': selected {device}")
                properties["device"] = device

        for propk, propv in properties.items():
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
        for widget in self.widgets.values():
            widget.create_elements(self.player, our_vc_tees)

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
        for widget in self.widgets.values():
            widget.gst_link()

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def run(self):
        """
        You must have placed widget by now or it will invalidate winid
        """
        for widget in self.widgets.values():
            widget.winid = widget.winId()
            assert widget.winid, "Need widget_winid by run"

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
        for widget in self.widgets.values():
            if widget.gst_name == want_name:
                return widget.winid
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
            name = message.src.get_name()
            winid = self.gstreamer_to_winid(name)
            # FIXME: transiet error while restarting pipeline
            # for now hide the intended window and let it float
            if winid is None:
                print(f"  WARNING: ignoring bad winid for name {name}")
            else:
                imagesink.set_window_handle(winid)


def excepthook(excType, excValue, tracebackobj):
    print("")
    print("excepthook: got exception")
    print("%s: %s" % (excType, excValue))
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

    try:
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
    # in some instances excepthook isn't triggering
    # (has to be an error?)
    # so explicitly handle to ensure clean exit
    # notably homing fail
    except Exception as e:
        print("")
        print("main: got exception")
        traceback.print_exc()
        os._exit(1)
