"""
Qt free gstreamer camera pipeline

source ! capsfilter (resolution, pixel format) [! videocrop] [! videoflip] ! tee
    tee ! queue ! capturesink
    tee ! queue ! videoconvert ! tee (display consumers, ex: Argus widgets)

Front ends add display consumers by overriding create_vc_elements() / link_vc_elements()
(see uscope/gui/gstwidget.py)
"""

from uscope.gst_util import Gst, CaptureSink
from uscope.imager import pixel_format
from uscope.imager.plugins.aplugins import get_imager_aplugin

import gi
import os
import threading


class GstImagerPipeline:
    def __init__(self, session, log=None):
        self.session = session
        self.microscope = session.microscope
        self.usc = self.microscope.usc
        self.verbose = os.getenv("USCOPE_GSTWIDGET_VERBOSE") == "Y"
        if log is None:
            log = print
        self.log = log

        self.source = None
        self.videoflip = None
        self.videocrop = None
        self.videoconvert = None
        self.raw_capsfilter = None
        self.raw_element = None
        self.tee_raw = None
        self.tee_vc = None
        self.setting_up = True
        # Requested source pixel format (gstreamer format string)
        # None => don't constrain (source default)
        self.capture_format = None
        # Clear if anything bad happens and shouldn't be trusted
        self.ok = True
        self._bus_thread = None
        self._bus_running = threading.Event()

        self.player = Gst.Pipeline.new("player")
        source = self.usc.imager.source()
        if source == "auto":
            raise ValueError("imager source auto: currently not supported")
        self.source_name = source
        self.verbose and print("vidpip source %s" % source)
        # Arbitrates source specific pipeline, properties, etc
        self.imager_aplugin = get_imager_aplugin(session=session,
                                                 source_name=self.source_name)
        self.incoming_w, self.incoming_h = self.usc.imager.cropped_wh()
        self.capture_sink = CaptureSink(session=session,
                                        width=self.incoming_w,
                                        height=self.incoming_h,
                                        source_type=self.source_name)
        self.player.add(self.capture_sink)

    """
    Source + capture format
    """

    def prepareSource(self, properties=None):
        properties = dict(properties or {})
        self.source = self.imager_aplugin.get_gst_source()
        assert self.source, "Failed to load camera source"
        # Override with microscope.j5 values
        properties.update(self.usc.imager.source_properties())
        for propk, propv in properties.items():
            self.verbose and print("Set source %s => %s" % (propk, propv))
            self.source.set_property(propk, propv)

    def raw_caps(self):
        """
        Caps forced onto the source: resolution and (optionally) pixel format
        """
        raw_w, raw_h = self.usc.imager.raw_wh()
        s = "video/x-raw,width=%u,height=%u" % (raw_w, raw_h)
        if self.capture_format:
            s += ",format=%s" % self.capture_format
        return Gst.Caps(s)

    def supported_formats(self):
        """
        Formats the plugin supports that the installed source element can produce
        Ex: older toupcamsrc builds only output RGB
        None if the source doesn't support selecting pixel format
        """
        formats = self.imager_aplugin.supported_formats()
        if formats is None:
            return None
        source = self.source or self.imager_aplugin.get_gst_source()
        template = source.get_static_pad("src").get_pad_template_caps()
        return [
            fmt for fmt in formats
            if template.can_intersect(Gst.Caps("video/x-raw,format=%s" % fmt))
        ]

    def set_capture_format(self, fmt):
        """
        Change the pixel format negotiated with the source
        fmt: gstreamer format string (see uscope.imager.pixel_format)

        Updating the capsfilter triggers renegotiation
        The source (ex: toupcamsrc) reconfigures + restarts the camera
        Safe to call from any thread (gstreamer properties are thread safe)
        but shouldn't be called while a scan is running
        """
        pixel_format.validate(fmt)
        supported = self.supported_formats()
        if supported is None:
            raise ValueError(
                f"Source {self.source_name} does not support selecting pixel format"
            )
        if fmt not in supported:
            raise ValueError(
                f"Source {self.source_name} does not support format {fmt}")
        if fmt == self.capture_format:
            return
        self.verbose and print("vidpip: capture format %s => %s" %
                               (self.capture_format, fmt))
        self.capture_format = fmt
        if self.raw_capsfilter is not None:
            self.raw_capsfilter.props.caps = self.raw_caps()

    def get_capture_format(self):
        """
        Pixel format requested from the source
        None if not constrained (source default)
        """
        return self.capture_format

    """
    Linking
    """

    def link_next_raw_element(self, element):
        """
        Add an element into the pipeline immediately before raw_tee
        """
        assert element
        if not self.setting_up:
            assert self.tee_vc
            self.raw_element.unlink(self.tee_vc)
            self.player.set_state(Gst.State.PAUSED)
        else:
            # not strictly true but close
            assert self.tee_vc is None

        self.player.add(element)
        assert self.raw_element.link(element)
        if self.tee_vc:
            assert element.link(self.tee_vc)
        self.raw_element = element

        if not self.setting_up:
            self.player.set_state(Gst.State.PLAYING)

    def link_tee(self, src, dsts, add=False):
        """
        Link src to one or more dsts through a tee + queues
        """
        assert len(dsts) > 0, "Can't create tee with no sink elements"
        tee = Gst.ElementFactory.make("tee")
        self.player.add(tee)
        assert src.link(tee)
        self.link_tee_dsts(tee, dsts, add=add)
        return tee

    def link_tee_dsts(self, tee, dsts, add=False):
        for dst in dsts:
            assert dst is not None
            queue = Gst.ElementFactory.make("queue")
            self.player.add(queue)
            assert tee.link(queue)
            if add:
                try:
                    self.player.add(dst)
                except gi.overrides.Gst.AddError:
                    print("WARNING: failed to add %s" % (dst, ))
                    raise
            try:
                assert queue.link(dst)
            except Exception:
                print(f"Failed to link {queue} => {dst}")
                raise

    def create_vc_elements(self, vc_tees):
        """
        Add display consumers (after videoconvert) to vc_tees
        """
        pass

    def link_vc_elements(self):
        pass

    def setupGst(self, raw_tees=None, vc_tees=None):
        """
        raw_tees: additional consumers of the raw (cropped, flipped) stream
        vc_tees: additional consumers after videoconvert
        """
        self.tee_vc = None
        raw_tees = list(raw_tees or [])
        vc_tees = list(vc_tees or [])

        self.prepareSource()
        self.player.add(self.source)
        self.raw_element = self.source
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
        # Also selects the pixel format (8 bit RGB, 16 bit, raw, ...)
        # if the source supports it
        supported = self.supported_formats()
        if supported is not None:
            self.capture_format = self.usc.imager.capture_format()
            if self.capture_format not in supported:
                self.log(
                    f"WARNING: {self.source_name} can't capture {self.capture_format}"
                    f" (supports {supported}). Using {pixel_format.DEFAULT_FORMAT}"
                )
                self.capture_format = pixel_format.DEFAULT_FORMAT
        self.raw_capsfilter.props.caps = self.raw_caps()
        self.link_next_raw_element(self.raw_capsfilter)

        # Hack to use a larger than needed camera sensor
        # Crop out the unused sensor area
        crop = self.usc.imager.crop_tblr()
        if crop:
            self.videocrop = Gst.ElementFactory.make("videocrop")
            assert self.videocrop
            for k in ("top", "bottom", "left", "right"):
                self.videocrop.set_property(k, crop[k])
            self.link_next_raw_element(self.videocrop)

        # Use hardware acceleration if present
        # Otherwise can soft flip feeds when / if needed
        videoflip_method = self.usc.imager.videoflip_method()
        if videoflip_method:
            self.videoflip = Gst.ElementFactory.make("videoflip")
            assert self.videoflip
            self.videoflip.set_property("method", videoflip_method)
            self.link_next_raw_element(self.videoflip)

        our_vc_tees = []
        self.create_vc_elements(our_vc_tees)
        vc_tees = our_vc_tees + vc_tees
        raw_tees = [self.capture_sink] + raw_tees
        # Only convert if something wants to display it
        if vc_tees:
            self.videoconvert = Gst.ElementFactory.make('videoconvert')
            assert self.videoconvert is not None
            self.player.add(self.videoconvert)
            raw_tees = [self.videoconvert] + raw_tees

        self.verbose and print("Link raw...")
        self.tee_raw = self.link_tee(self.raw_element, raw_tees)
        if vc_tees:
            self.verbose and print("Link vc...")
            self.tee_vc = self.link_tee(self.videoconvert, vc_tees)
        self.link_vc_elements()

        self.setup_bus(self.player.get_bus())
        self.setting_up = False

    """
    Running
    """

    def setup_bus(self, bus):
        """
        No main loop is required: poll the bus from a thread
        Front ends with a glib main loop (Qt on Linux) may use a signal watch instead
        """
        self._bus_running.set()
        self._bus_thread = threading.Thread(target=self._bus_loop,
                                            args=(bus, ),
                                            name="gst-bus",
                                            daemon=True)
        self._bus_thread.start()

    def _bus_loop(self, bus):
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS
        while self._bus_running.is_set():
            message = bus.timed_pop_filtered(100 * Gst.MSECOND, mask)
            if message is not None:
                self.on_message(bus, message)

    def run(self):
        self.verbose and print("Starting gstreamer pipeline")
        self.player.set_state(Gst.State.PLAYING)
        if self.source_name == 'gst-toupcamsrc':
            assert self.source.get_property(
                "devicepresent"), "camera not found"

    def wait_playing(self, timeout=30.0):
        """
        Block until the pipeline is running (or fails to)
        """
        ret, state, _pending = self.player.get_state(int(timeout *
                                                         Gst.SECOND))
        if ret == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
            raise Exception(f"Video pipeline failed to start ({ret}, {state})")

    def stop(self):
        self._bus_running.clear()
        self.player.set_state(Gst.State.NULL)
        if self._bus_thread:
            self._bus_thread.join(timeout=1.0)
            self._bus_thread = None

    def on_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL)
            self.log("GstVP: End of stream")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.log("GstVP error: %s %s" % (err, debug))
            self.player.set_state(Gst.State.NULL)
            self.ok = False

    def recover_video_crash(self):
        """
        Goal: recover from transient camera errors
        At a high level:
        -Stop pipeline
        -Deconstruct the old video source / remove from pipeline
        -Create a new video source and add to pipeline
        -Start pipeline
        Caller restores properties (exposure, etc)
        """
        # Stop pipeline and remove the bad element
        self.ok = False
        self.player.set_state(Gst.State.NULL)
        self.source.unlink(self.raw_capsfilter)
        self.player.remove(self.source)
        self.source = None

        # Allocate a new source
        self.prepareSource()
        self.player.add(self.source)
        # Now insert it back into the pipeline
        if not self.source.link(self.raw_capsfilter):
            raise RuntimeError("Couldn't set capabilities on the source")
        # Go go go!
        # Hopefully we'll come back up
        # If not we'll crash in short order
        self.player.set_state(Gst.State.PLAYING)
        imager = self.microscope.imager
        if imager:
            imager.device_restarted()
        self.ok = True
