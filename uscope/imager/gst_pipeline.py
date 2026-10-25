"""
Qt free gstreamer camera pipeline

source ! capsfilter (resolution, pixel format) ! videocrop [! videoflip] ! tee
    tee ! queue ! capturesink
    tee ! queue ! videoconvert ! tee (display consumers, ex: Argus widgets)

Front ends add display consumers by overriding create_vc_elements() / link_vc_elements()
(see uscope/gui/gstwidget.py)
"""

from uscope.gst_util import Gst, CaptureSink
from uscope.imager.gst_recorder import GstVideoRecorder
from uscope.imager import pixel_format
from uscope.imager.plugins.aplugins import get_imager_aplugin
from uscope.imager.plugins.gst_toupcamsrc import resolution as toupcam_resolution
from uscope.imager import camera_check
from uscope.imager.crop import SIDES as CROP_SIDES
from uscope.imager.geometry import GeometryBusy

import copy
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
        self.videocrop_linked = False
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
        # Set by run() if the camera doesn't look like the configured one
        # Front ends may show it to the user
        self.camera_mismatch = None
        self._bus_thread = None
        self._bus_running = threading.Event()
        self.recorder = None
        # Serializes source restarts (crash recovery, geometry changes)
        self.restart_lock = threading.RLock()

        self.player = Gst.Pipeline.new("player")
        source = self.usc.imager.source()
        if source == "auto":
            raise ValueError("imager source auto: currently not supported")
        self.source_name = source
        self.verbose and print("vidpip source %s" % source)
        # Arbitrates source specific pipeline, properties, etc
        self.imager_aplugin = get_imager_aplugin(session=session,
                                                 source_name=self.source_name)
        # Sizes below depend on the selected resolution
        self.imager_aplugin.configure_resolution(log=self.log)
        # Pixel crops that couldn't be converted before the camera was probed
        store = getattr(self.microscope, "cal_store", None)
        if store is not None:
            store.migrate_crop_pixels()
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
        # May change crop rounding: session follows up with reconfigure_geometry()
        self.usc.imager.active_capture_format = fmt
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
            self.usc.imager.active_capture_format = self.capture_format
        self.raw_capsfilter.props.caps = self.raw_caps()
        self.link_next_raw_element(self.raw_capsfilter)

        # Crop out the unused sensor area (ex: outside the image circle)
        # Only linked in while cropping: videocrop can't negotiate some formats (ARGB64)
        # Kept in the pipeline so the crop can change at runtime
        self.videocrop = Gst.ElementFactory.make("videocrop")
        assert self.videocrop
        crop = self.usc.imager.crop_tblr()
        self.set_videocrop(crop)
        if crop:
            self.link_next_raw_element(self.videocrop)
            self.videocrop_linked = True
        else:
            self.player.add(self.videocrop)
            self.videocrop_linked = False

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

    def video_recorder(self):
        """
        Records the raw (cropped, flipped) stream
        """
        if self.recorder is None:
            assert self.tee_raw, "setupGst() first"
            # Logs from gstreamer threads: self.log may not be thread safe
            self.recorder = GstVideoRecorder(
                self.player,
                self.tee_raw,
                log=lambda msg: self.microscope.log(msg),
                wh=(self.incoming_w, self.incoming_h))
        return self.recorder

    def stop_recording(self):
        """
        Finalize any recording before the pipeline stops
        """
        if self.recorder and self.recorder.is_recording():
            self.recorder.stop(wait=True)

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
        ret = self.player.set_state(Gst.State.PLAYING)
        self.check_camera(ret)

    def check_camera(self, state_ret=None):
        """
        Raise CameraNotFound if the source couldn't open a camera
        Otherwise log a warning and set self.camera_mismatch if the camera
        doesn't look like the one in the microscope config
        Requires the source to be started (READY => PAUSED)
        """
        microscope = self.microscope
        config_reason = getattr(microscope, "config_reason", "unknown")
        present = self.imager_aplugin.camera_present(self.source)
        if present is None:
            present = state_ret != Gst.StateChangeReturn.FAILURE
        if not present:
            msg = camera_check.not_found_message(
                microscope.name, config_reason, self.source_name,
                self.imager_aplugin.camera_not_found_detail(self.source))
            self.log("ERROR: " + msg)
            raise camera_check.CameraNotFound(msg)

        found = self.imager_aplugin.probe_camera(self.source)
        caps = self.source.get_static_pad("src").query_caps(None)
        caps_str = caps.to_string()
        found["sizes"] = camera_check.caps_sizes(caps_str)
        raw_w, raw_h = self.usc.imager.raw_wh()
        if caps.is_any() or caps.is_empty():
            found["wh_supported"] = None
        else:
            want = camera_check.size_caps_str(caps_str, raw_w, raw_h)
            found["wh_supported"] = caps.can_intersect(Gst.Caps(want))
        expected = {
            "wh": (raw_w, raw_h),
            "model": self.usc.imager.model(),
            "v4l2_name": self.usc.imager.v4l2_name(),
        }
        self.log("Camera: " + camera_check.describe_camera(found))
        issues = camera_check.check_camera(expected, found)
        if issues:
            self.camera_mismatch = camera_check.mismatch_message(
                microscope.name, config_reason, self.source_name, found,
                issues)
            self.log("WARNING: " + self.camera_mismatch)

    def wait_playing(self, timeout=30.0):
        """
        Block until the pipeline is running (or fails to)
        """
        ret, state, _pending = self.player.get_state(int(timeout *
                                                         Gst.SECOND))
        if ret == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
            raise Exception(f"Video pipeline failed to start ({ret}, {state})")

    def stop(self):
        self.stop_recording()
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
            if self.camera_mismatch:
                self.log(
                    "GstVP: likely caused by the microscope config / camera mismatch reported above"
                )
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
        with self.restart_lock:
            self._recover_video_crash()

    def _recover_video_crash(self):
        # Stop pipeline and remove the bad element
        self.ok = False
        self.stop_recording()
        self.player.set_state(Gst.State.NULL)
        self.replace_source()
        # Go go go!
        # Hopefully we'll come back up
        # If not we'll crash in short order
        self.player.set_state(Gst.State.PLAYING)
        imager = self.microscope.imager
        if imager:
            imager.device_restarted()
        self.ok = True

    def replace_source(self):
        """
        Pipeline must be stopped (NULL)
        New source picks up source_properties (ex: toupcamsrc esize)
        """
        self.source.unlink(self.raw_capsfilter)
        self.player.remove(self.source)
        self.source = None

        # Allocate a new source
        self.prepareSource()
        self.player.add(self.source)
        # Now insert it back into the pipeline
        if not self.source.link(self.raw_capsfilter):
            raise RuntimeError("Couldn't set capabilities on the source")

    """
    Image geometry (resolution, crop)
    """

    def set_videocrop(self, crop):
        crop = crop or {}
        for k in CROP_SIDES:
            self.videocrop.set_property(k, crop.get(k, 0))

    def link_videocrop(self, linked):
        """
        Insert / remove videocrop between the capsfilter and the next element
        Pipeline must be stopped (NULL)
        """
        if linked == self.videocrop_linked:
            return
        next_element = self.videoflip or self.tee_raw
        if linked:
            self.raw_capsfilter.unlink(next_element)
            assert self.raw_capsfilter.link(self.videocrop)
            assert self.videocrop.link(next_element)
        else:
            self.raw_capsfilter.unlink(self.videocrop)
            self.videocrop.unlink(next_element)
            assert self.raw_capsfilter.link(next_element)
        self.videocrop_linked = linked

    def videocrop_tblr(self):
        """
        Crop currently applied (None if none)
        """
        if not self.videocrop_linked:
            return None
        ret = {k: self.videocrop.get_property(k) for k in CROP_SIDES}
        if not any(ret.values()):
            return None
        return ret

    def geometry_busy(self):
        """
        Why the image geometry can't be changed right now, or None
        """
        if self.recorder is not None and self.recorder.is_recording():
            return "video recording"
        session = self.session
        if session.is_planner_running():
            return "scan"
        thread = getattr(session, "image_processing_thread", None)
        if thread is not None and thread.autofocus_running:
            return "autofocus"
        taking_snapshot = getattr(session, "is_taking_snapshot", None)
        if taking_snapshot is not None and taking_snapshot():
            return "snapshot"
        return None

    def reconfigure_geometry(self, resolution=None):
        """
        Apply a new camera resolution and / or the current crop (crop_tblr())
        to the running pipeline
        resolution: {"width", "height", "esize"} (see ResolutionSelector), None => keep

        Returns True if anything changed
        Raises GeometryBusy if a scan, snapshot, recording or autofocus is active
        If the camera doesn't come back the previous geometry is restored
        and the error is re-raised
        Call from the thread that runs the pipeline / video widgets (Argus: GUI thread)
        """
        with self.restart_lock:
            return self._reconfigure_geometry(resolution)

    def _reconfigure_geometry(self, resolution):
        busy = self.geometry_busy()
        if busy:
            raise GeometryBusy(f"Can't change image geometry during {busy}")
        imagerj = self.usc.imager.j
        old_imagerj = copy.deepcopy(imagerj)
        old_raw_wh = self.usc.imager.raw_wh()
        new_source = False
        if resolution is not None:
            new_wh = (resolution["width"], resolution["height"])
            old_esize = imagerj.get("source_properties", {}).get("esize")
            new_source = new_wh != old_raw_wh or (
                resolution.get("esize") is not None
                and resolution["esize"] != old_esize)
        if new_source:
            self.apply_resolution(resolution)
        try:
            crop = self.usc.imager.crop_tblr()
            self.usc.imager.cropped_wh()
        except Exception:
            imagerj.clear()
            imagerj.update(old_imagerj)
            raise
        if not new_source and crop == self.videocrop_tblr():
            return False

        properties = getattr(self.session, "imager_properties", None)
        prop_cache = properties.get_prop_cache() if properties else None
        try:
            self._restart_geometry(new_source)
        except Exception as e:
            raw_w, raw_h = self.usc.imager.raw_wh()
            self.log(
                f"ERROR: camera failed at {raw_w}x{raw_h}: {e}. Reverting to {old_raw_wh[0]}x{old_raw_wh[1]}"
            )
            imagerj.clear()
            imagerj.update(old_imagerj)
            self._restart_geometry(new_source)
            if properties:
                properties.recover_video_crash(prop_cache)
            raise
        if properties:
            properties.recover_video_crash(prop_cache)
        return True

    def apply_resolution(self, resolution):
        """
        Update the imager config (width / height / esize) for the next source start
        """
        selector = self.imager_aplugin.resolution_selector()
        if selector is not None:
            selector.apply(self.usc.imager.j, resolution)
        else:
            toupcam_resolution.apply_to_imagerj(self.usc.imager.j, resolution)

    def _restart_geometry(self, new_source):
        self.stop_recording()
        self.player.set_state(Gst.State.NULL)
        if new_source:
            self.replace_source()
        self.raw_capsfilter.props.caps = self.raw_caps()
        crop = self.usc.imager.crop_tblr()
        self.set_videocrop(crop)
        self.link_videocrop(bool(crop))
        self.incoming_w, self.incoming_h = self.usc.imager.cropped_wh()
        self.capture_sink.width = self.incoming_w
        self.capture_sink.height = self.incoming_h
        if self.recorder is not None:
            self.recorder.wh = (self.incoming_w, self.incoming_h)
        self.geometry_applied()
        ret = self.player.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("video pipeline failed to start")
        self.check_camera(ret)
        self.wait_playing(timeout=10.0)
        imager = self.microscope.imager
        if imager:
            imager.device_restarted()

    def geometry_applied(self):
        """
        Pipeline is stopped and the new geometry (incoming_w / incoming_h) is set
        Front ends update their display elements here
        """
        pass
