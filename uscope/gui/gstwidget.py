# from uscope.imager.imager_util import auto_detect_source

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import sys
import traceback
import os
import signal
import faulthandler
import threading
from collections import OrderedDict
from uscope.imager.gst_pipeline import GstImagerPipeline
from uscope import util
from uscope.gui import video_sink

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('GstRtspServer', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# WARNING: importing GdkX11 will cause hard crash (related to Qt)
# fortunately its not needed
# from gi.repository import GdkX11, GstVideo
from gi.repository import Gst

Gst.init(None)
from gi.repository import GstRtspServer
# GstVideo must be loaded for the video overlay interface (set_window_handle)
from gi.repository import GstVideo  # noqa: F401

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


class ArgusVideoWidget(QWidget):
    # Renders into winId() via GstVideoOverlay
    uses_window_handle = True

    def __init__(self, ac=None, player=None, config={}, parent=None):
        super().__init__(parent=parent)
        self.config = config
        self.player = player
        # The actual QWidget
        self.gst_element_name = config["gst_element_name"]
        self.ac = ac


class SinkxZoomableWidget(ArgusVideoWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        incoming_wh = self.config["incoming_wh_hint"]

        policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setSizePolicy(policy)
        self.resize(500, 500)
        # print("after resize", self.width(), self.height())

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

        self.zoom = 1.0
        # The value to restore zoom to when toggling high zoom off
        self.zoom_out = None

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
            incoming_crop_tb += 1
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

        self.capsfilter = Gst.ElementFactory.make("capsfilter")
        self.update_crop_scale()
        player.add(self.capsfilter)

        self.sinkx = Gst.ElementFactory.make("ximagesink",
                                             self.gst_element_name)
        assert self.sinkx
        player.add(self.sinkx)
        src_tee.append(self.videocrop)

        # Do a baseline scale?
        # no too early. widget is not sized yet
        # wait for resize event
        # self.update_crop_scale()

    def gst_link(self):
        assert self.videocrop.link(self.videoscale)
        assert self.videoscale.link(self.capsfilter)
        assert self.capsfilter.link(self.sinkx)


class AppsinkZoomableWidget(SinkxZoomableWidget):
    """
    Same as SinkxZoomableWidget but frames are pulled from an appsink and painted by Qt
    Doesn't need a native X window => works on Wayland / offscreen
    Costs a frame copy + Qt scaling per frame
    """

    uses_window_handle = False
    frame_ready = pyqtSignal()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.image = None
        self.videoconvert = None
        # Newest frame not yet picked up by the GUI thread
        # Only one is kept so a slow GUI drops frames instead of queuing them
        self.pending_image = None
        self.pending_lock = threading.Lock()
        self.frame_ready.connect(self.on_frame_ready)

    def setupWidget(self, parent=None):
        if parent:
            self.setParent(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def create_elements(self, player, src_tee):
        self.videocrop = Gst.ElementFactory.make("videocrop")
        assert self.videocrop
        player.add(self.videocrop)

        self.videoscale = Gst.ElementFactory.make("videoscale")
        assert self.videoscale
        player.add(self.videoscale)

        self.videoconvert = Gst.ElementFactory.make("videoconvert")
        assert self.videoconvert
        player.add(self.videoconvert)

        # BGRx bytes in memory == QImage.Format_RGB32 on little endian
        self.capsfilter = Gst.ElementFactory.make("capsfilter")
        self.capsfilter.props.caps = Gst.Caps.from_string(
            "video/x-raw,format=BGRx")
        self.update_crop_scale()
        player.add(self.capsfilter)

        self.sinkx = Gst.ElementFactory.make("appsink", self.gst_element_name)
        assert self.sinkx
        self.sinkx.set_property("emit-signals", True)
        # Display only: never back up the pipeline
        self.sinkx.set_property("max-buffers", 1)
        self.sinkx.set_property("drop", True)
        self.sinkx.set_property("sync", False)
        self.sinkx.connect("new-sample", self.on_new_sample)
        player.add(self.sinkx)
        src_tee.append(self.videocrop)

    def gst_link(self):
        assert self.videocrop.link(self.videoscale)
        assert self.videoscale.link(self.videoconvert)
        assert self.videoconvert.link(self.capsfilter)
        assert self.capsfilter.link(self.sinkx)

    def on_new_sample(self, appsink):
        """
        GStreamer streaming thread
        """
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            # copy() so the QImage owns its pixels after unmap
            image = QImage(mapinfo.data, info.width, info.height,
                           info.stride[0], QImage.Format_RGB32).copy()
        finally:
            buf.unmap(mapinfo)
        with self.pending_lock:
            notify = self.pending_image is None
            self.pending_image = image
        if notify:
            # Queued to the GUI thread
            self.frame_ready.emit()
        return Gst.FlowReturn.OK

    def on_frame_ready(self):
        with self.pending_lock:
            self.image = self.pending_image
            self.pending_image = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self.image is not None and not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            x = (self.width() - size.width()) // 2
            y = (self.height() - size.height()) // 2
            painter.drawImage(QRect(x, y, size.width(), size.height()),
                              self.image)
        painter.end()


def zoomable_widget_class():
    sink = video_sink.video_sink(QGuiApplication.platformName())
    if sink == "appsink":
        return AppsinkZoomableWidget
    return SinkxZoomableWidget


class GstVideoPipeline(GstImagerPipeline):
    """
    GstImagerPipeline + Qt video widgets (and optional RTSP feed)

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
        ac=None,
        log=None):
        self.ac = ac
        super().__init__(session=ac, log=log)

        if widget_configs is None:
            widget_configs = set()
            # Main window view
            # Placed next to each other
            # Currently these two are more or less identical
            # but convey intent in case they start to diverge
            # FIXME: now that widget sizing is now based on QWidget,
            # simplify this
            if zoomable:
                widget_configs.add("zoomable")
            # For calibrating video feed
            if overview2:
                widget_configs.add("overview2")
            # Stand alone window
            if overview_full_window:
                widget_configs.add("overview_full_window")
        """
        key: gst name
        widget: QWidget
        winid: during ON_SYNC_MESSAGE give the winid to render to
        width/height:
        """
        self.widgets = OrderedDict()
        for widget_name in widget_configs:
            self.create_widget(widget_name)
        self.size_widgets()

        # RTSP
        self.rtsp_bin = None
        self.rtsp_server = None
        self.rtsp_media_factory = None

    def create_widget(self, widget_name):
        t = self.imager_aplugin.get_widget()
        config = {
            "gst_element_name": "sinkx_" + widget_name,
            "incoming_wh_hint": (self.incoming_w, self.incoming_h),
        }
        widget = t(
            ac=self.ac,
            player=self.player,
            config=config,
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
        """
        for widget in self.widgets.values():
            if widget_in and widget_in != widget:
                continue
            if not widget.is_fixed():
                continue
            widget.calc_size()

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
        widget = self.create_widget("overview_full_window")
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
            if not widget.uses_window_handle:
                continue
            widget.winid = widget.winId()
            assert widget.winid, "Need widget_winid by run"

        self.player.set_state(Gst.State.PLAYING)
        if self.microscope.imager:
            self.microscope.imager.device_restarted()

    def remove_full_widget(self):
        assert 0, "FIXME"

    def create_vc_elements(self, vc_tees):
        for widget in self.widgets.values():
            widget.create_elements(self.player, vc_tees)

    def link_vc_elements(self):
        for widget in self.widgets.values():
            widget.gst_link()

    def setup_bus(self, bus):
        # Qt runs a glib main loop on Linux: messages are dispatched there
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def run(self):
        """
        You must have placed widget by now or it will invalidate winid
        """
        for widget in self.widgets.values():
            if not widget.uses_window_handle:
                continue
            widget.winid = widget.winId()
            assert widget.winid, "Need widget_winid by run"
        super().run()

    def gstreamer_to_winid(self, want_name):
        for widget in self.widgets.values():
            if widget.gst_element_name == want_name:
                return widget.winid
        assert 0, "Failed to match widget winid for ximagesink %s" % want_name

    def on_sync_message(self, bus, message):
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        if message_name == "prepare-window-handle":
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

    def enable_rtsp_server(self, enabled):
        # RTSP config vars
        RTSP_SERVER_PORT = 8554
        MOUNT_POINT = "feed"
        if enabled:
            self.player.set_state(Gst.State.PAUSED)
            if not self.rtsp_bin:
                self.rtsp_bin = RtspBin(ac=self.ac,
                                        gst_element_name="rtsp_bin",
                                        incoming_wh=(self.incoming_w,
                                                     self.incoming_h))
                self.rtsp_bin.create_elements()
                self.rtsp_bin.gst_link()
            self.link_tee_dsts(self.tee_vc, [self.rtsp_bin], add=True)

            if not self.rtsp_server:
                # Create RTSP server
                self.rtsp_server = GstRtspServer.RTSPServer.new()
                self.rtsp_server.props.service = f"{RTSP_SERVER_PORT}"
                self.rtsp_media_factory = ARtspMediaFactory(
                    host=self.rtsp_bin.host, port=self.rtsp_bin.port)
                self.rtsp_server.get_mount_points().add_factory(
                    f"/{MOUNT_POINT}", self.rtsp_media_factory)
                self.rtsp_server.attach(None)
            self.player.set_state(Gst.State.PLAYING)
        else:
            # TODO: post server shutdown cleanup
            self.player.remove(self.rtsp_bin)


class RtspBin(Gst.Bin):
    """
    GStreamer defaults are
        host: "localhost"
        port: 5004
    """
    host = "127.0.0.1"
    port = 8554

    def __init__(
        self,
        ac=None,
        gst_element_name=None,
        incoming_wh=None,
        # player=None
    ):
        super().__init__()

        self.ac = ac
        # self.player = player
        self.gst_element_name = gst_element_name
        # gstreamer rendering element
        self.udpsink = None
        # Used to fit incoming stream to window
        self.videoscale = None
        self.videocrop = None
        # Tell the videoscale the window size we need
        self.capsfilter = None
        #
        self.videoflip = None

        # H264 to RTP to UDP
        self.videoconvert = None
        self.openh264enc = None
        self.h264parse = None
        self.rtph264pay = None
        self.udpsink = None

        # Input image may be cropped, don't use the raw w/h for anything
        # Fixed across a microscope run, not currently configurable after startup
        # XXX: would be nice if we could detect these
        self.incoming_w, self.incoming_h = incoming_wh

    def update_crop_scale(self):
        """
        Is this QWidget related or still needed?
        """
        pass

    def create_elements(self):
        self.videocrop = Gst.ElementFactory.make("videocrop")
        assert self.videocrop
        self.add(self.videocrop)

        self.videoscale = Gst.ElementFactory.make("videoscale")
        assert self.videoscale
        self.add(self.videoscale)

        # No videoflip: frames were already flipped upstream of the tee

        self.capsfilter = Gst.ElementFactory.make("capsfilter")
        self.update_crop_scale()
        self.add(self.capsfilter)

        self.videoconvert = Gst.ElementFactory.make("videoconvert")
        self.add(self.videoconvert)

        self.openh264enc = Gst.ElementFactory.make("openh264enc")
        assert self.openh264enc
        self.add(self.openh264enc)
        self.h264parse = Gst.ElementFactory.make("h264parse")
        assert self.h264parse
        self.add(self.h264parse)

        self.rtph264pay = Gst.ElementFactory.make("rtph264pay")
        assert self.rtph264pay
        self.rtph264pay.set_property("name", "pay0")
        self.rtph264pay.set_property("pt", 96)
        self.rtph264pay.set_property("config-interval", 1)
        self.add(self.rtph264pay)

        self.udpsink = Gst.ElementFactory.make("udpsink")
        assert self.udpsink
        self.udpsink.set_property("host", self.host)
        self.udpsink.set_property("port", self.port)
        self.add(self.udpsink)

        # Link videocrop's sink to the bin's sink
        bin_sink_pad = Gst.GhostPad.new("sink",
                                        self.videocrop.get_static_pad("sink"))
        bin_sink_pad.set_active(True)
        self.add_pad(bin_sink_pad)

    def gst_link(self):
        if self.videoflip:
            assert self.videocrop.link(self.videoflip)
            assert self.videoflip.link(self.videoscale)
        else:
            assert self.videocrop.link(self.videoscale)

        assert self.videoscale.link(self.capsfilter)
        assert self.capsfilter.link(self.videoconvert)
        assert self.videoconvert.link(self.openh264enc)
        assert self.openh264enc.link(self.h264parse)
        assert self.h264parse.link(self.rtph264pay)
        assert self.rtph264pay.link(self.udpsink)


class ARtspMediaFactory(GstRtspServer.RTSPMediaFactory):
    def __init__(self, host, port, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.host = host
        self.port = port
        self.set_shared(True)

    def do_create_element(self, url):
        bin = Gst.Bin()
        udpsrc = Gst.ElementFactory.make("udpsrc")
        assert udpsrc
        udpsrc.set_property("name", "pay0")
        udpsrc.set_property("port", self.port)

        def create_caps():
            caps = Gst.Caps.new_empty_simple("application/x-rtp")
            caps.set_value("media", "video")
            caps.set_value("buffer-size", 524288)
            caps.set_value("clock-rate", 90000)
            caps.set_value("encoding-name", "H264")
            caps.set_value("payload", 96)
            return caps

        udpsrc.set_property("caps", create_caps())
        bin.add(udpsrc)
        return bin


def excepthook(excType, excValue, tracebackobj):
    # stderr so that it also goes to the log file (see uscope/app_log.py)
    print("", file=sys.stderr)
    print("excepthook: got exception", file=sys.stderr)
    traceback.print_exception(excType, excValue, tracebackobj)
    # Don't leave the stage jogging
    util.run_emergency_stops()
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
    # Debug aid for hangs: kill -USR1 <pid> dumps all Python thread stacks
    faulthandler.register(signal.SIGUSR1, all_threads=True)

    # GObject.threads_init() has been a no-op since PyGObject 3.11
    # and is removed in newer versions

    try:
        video_sink.setup_qt_platform()
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
    except Exception:
        print("", file=sys.stderr)
        print("main: got exception", file=sys.stderr)
        traceback.print_exc()
        util.run_emergency_stops()
        os._exit(1)
