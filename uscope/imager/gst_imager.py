"""
Qt free imagers driven by a session (ArgusCommon or HeadlessSession)

Session interface used:
    microscope, usc, vidpip, imager_properties, image_processing_thread
    objective_config()
    is_planner_running()
    capture_format_changed(fmt)
"""

from uscope.imager.imager import Imager
from uscope.imager.gst import ImageTimeout
from uscope.imager import pixel_format
from uscope.microscope import MicroscopeStop
from uscope.util import LogTimer

import threading
import time
import traceback


class SessionImager(Imager):
    """
    Common logic for session imagers
    Subclasses implement next_captured_image() to grab a single raw frame
    """

    # Give up after this many consecutive failed captures
    # Enough to ride out a camera restart but not hang a scan forever
    GET_MAX_TRIES = 5
    # Frames already went through the gstreamer videoflip element
    # If not, image processing applies imager.videoflip_method instead
    FRAMES_FLIPPED = False

    def __init__(self, session):
        Imager.__init__(self)
        self.session = session
        self.microscope = session.microscope
        self.usc = self.microscope.usc
        self.width, self.height = self.usc.imager.final_wh()
        self.factor = self.usc.imager.scalar()
        self.videoflip_method = self.usc.imager.videoflip_method()

    def properties(self):
        return self.session.imager_properties

    def wh(self):
        return self.width, self.height

    def next_captured_image(self, timeout=None):
        raise NotImplementedError()

    def _disp_properties_meta(self):
        """
        Best estimate of camera settings for this image
        Ideally we'd pull this off of the image metadata coming over USB
        will be more important if support quick exposure bracketing
        """
        return {
            "disp_properties": self.properties().get_disp_properties_ts(),
        }

    def get(self, recover_errors=True, timeout=None):
        # WARNING: this must be thread safe / may be called from any context
        tries = 0
        while True:
            try:
                # 2023-11-16: we used to do scaling / etc here
                # Now its done in image processing thread
                # This also allows getting "raw" image if needed
                return self.next_captured_image(timeout=timeout)
            except MicroscopeStop:
                raise
            except Exception as e:
                tries += 1
                if not recover_errors or tries >= self.GET_MAX_TRIES:
                    raise
                self.microscope.log(
                    f"WARNING: failed to get image ({type(e).__name__}: {e}). Sleeping then retrying ({tries} / {self.GET_MAX_TRIES})"
                )
                print(traceback.format_exc())
                # If something went badly wrong need to give it some time to recover / restart pipeline
                time.sleep(self.microscope.kinematics.tsettle_video_pipeline *
                           2)

    def get_processed(self,
                      processing_options=None,
                      recover_errors=True,
                      snapshot_timeout=None,
                      processing_timeout=None):
        if processing_options is None:
            processing_options = {}
        if processing_timeout is None:
            processing_timeout = self.usc.imager.processing_timeout()
        with LogTimer("get_processed: net",
                      variable="PYUSCOPE_PROFILE_TIMAGE"):
            # Get relatively unprocessed snapshot
            with LogTimer("get_processed: raw",
                          variable="PYUSCOPE_PROFILE_TIMAGE"):
                capim = self.get(timeout=snapshot_timeout,
                                 recover_errors=recover_errors)

            processed = {}
            ready = threading.Event()

            def callback(command, args, ret_e):
                if isinstance(ret_e, BaseException):
                    processed["exception"] = ret_e
                else:
                    processed["captured_image"] = ret_e
                ready.set()

            options = {}
            options["image"] = capim.image
            options["captured_image"] = capim
            options["objective_config"] = self.session.objective_config()
            options["scale_factor"] = self.usc.imager.scalar()
            options["scale_expected_wh"] = self.usc.imager.final_wh()
            if self.usc.imager.videoflip_method() and not self.FRAMES_FLIPPED:
                options["videoflip_method"] = self.usc.imager.videoflip_method(
                )
            options.update(processing_options)

            self.session.image_processing_thread.process_image(
                options=options, callback=callback)
            with LogTimer("get_processed: waiting",
                          variable="PYUSCOPE_PROFILE_TIMAGE"):
                if not ready.wait(timeout=processing_timeout):
                    raise ImageTimeout(
                        "Failed to get image within processing timeout %0.1f sec"
                        % (processing_timeout, ))
            if "exception" in processed:
                e = processed["exception"]
                if isinstance(e, MicroscopeStop):
                    raise e
                raise Exception(f"failed to process image: {e}") from e
            capim = processed["captured_image"]
            if capim is None:
                raise Exception("failed to process image: no result")
            capim.set_meta_kv("objective_config", options["objective_config"])
            return capim

    def log_planner_header(self, log):
        log("Imager config")
        log("  Image size")
        log("    Raw sensor size: %uw x %uh" % (self.usc.imager.raw_wh()))
        cropw, croph = self.usc.imager.cropped_wh()
        log("    Cropped sensor size: %uw x %uh" %
            (self.usc.imager.cropped_wh()))
        scalar = self.usc.imager.scalar()
        log("    Output scale factor: %0.1f" % scalar)
        log("    Final scaled image: %uw x %uh" %
            (cropw * scalar, croph * scalar))

    def _set_properties(self, vals):
        self.properties().set_disp_properties(vals)

    def _get_properties(self):
        return self.properties().get_disp_properties_ts()

    def get_exposure_cache(self):
        disp_prop = self.properties().get_exposure_disp_property()
        return self.properties().get_disp_property_ts(disp_prop)

    def get_exposure_property(self):
        return self.properties().get_exposure_disp_property()

    def set_exposure(self, value):
        self.set_property(self.get_exposure_property(), value)

    def add_captured_image_meta(self, captured_image):
        self.properties().add_captured_image_meta(captured_image)

    def captured_image_exposure(self, captured_image):
        return self.properties().captured_image_exposure(captured_image)


class GstImager(SessionImager):
    """
    Captures from a GstImagerPipeline (session.vidpip)
    Thread safe
    """
    # capturesink is downstream of the pipeline's videoflip
    FRAMES_FLIPPED = True

    def capture_sink(self):
        return self.session.vidpip.capture_sink

    def get_sn(self):
        if self.session.vidpip.source_name == "gst-toupcamsrc":
            return self.properties().raw_prop_read("serial-number")
        else:
            return None

    """
    Capture pixel format
    ex: 8 bit RGB vs 16 bit vs sensor raw
    Negotiated with the source via gstreamer caps
    """

    def supported_capture_formats(self):
        """
        Formats the current camera can be switched to
        Empty list if the source doesn't support switching
        """
        vidpip = self.session.vidpip
        formats = vidpip.supported_formats()
        if not formats:
            return []
        # Filter out 16 bit formats on 8 bit cameras
        max_bit_depth = None
        source = vidpip.source
        if source is not None and source.find_property("max-bit-depth"):
            try:
                max_bit_depth = source.get_property("max-bit-depth")
            except Exception:
                max_bit_depth = None
        if max_bit_depth and max_bit_depth <= 8:
            formats = [f for f in formats if not pixel_format.is_16bit(f)]
        return list(formats)

    def get_capture_format(self):
        """
        Currently requested capture format (gstreamer format string)
        None if the source doesn't support selecting it
        """
        return self.session.vidpip.get_capture_format()

    def set_capture_format(self, fmt):
        """
        Switch the capture pixel format
        The camera is stopped, reconfigured and restarted by the source
        Not allowed while a scan is running
        Thread safe
        """
        if self.session.is_planner_running():
            raise RuntimeError(
                "Refusing to change capture format while scan is running")
        if fmt not in self.supported_capture_formats():
            raise ValueError(
                f"Capture format {fmt} not supported by this camera. Supported: {self.supported_capture_formats()}"
            )
        if fmt == self.get_capture_format():
            return
        self.session.vidpip.set_capture_format(fmt)
        self.microscope.log(f"Capture format: {fmt}")
        self.properties().capture_format_changed(fmt)
        self.session.capture_format_changed(fmt)

    def next_captured_image(self, timeout=None):
        # WARNING: this must be thread safe / may be called from any context
        if timeout is None:
            timeout = self.usc.imager.snapshot_timeout()
        capture_sink = self.capture_sink()

        # Per call state: concurrent callers (ex: GUI snapshot during a
        # script) must not clobber each other
        image_ready = threading.Event()
        got = {}

        def got_image(image_id):
            got["image_id"] = image_id
            image_ready.set()

        # Format we expect frames to arrive in
        # None if the source doesn't negotiate formats (legacy sources)
        want_format = self.get_capture_format()
        tstart = time.time()
        while True:
            got.pop("image_id", None)
            image_ready.clear()
            capture_sink.request_image(got_image)
            remaining = timeout - (time.time() - tstart)
            if remaining <= 0 or not image_ready.wait(timeout=remaining):
                raise ImageTimeout(
                    "Failed to get raw image within timeout %0.1f sec" %
                    (timeout, ))
            image_id = got["image_id"]
            if want_format is None:
                break
            # After a capture format switch frames already in the pipeline
            # (queues between the source and us) are still in the old format
            # Discard them until the camera restarts in the new format
            got_format = capture_sink.get_image(image_id).get("format")
            if got_format is None or got_format == want_format:
                break
            capture_sink.del_image(image_id)
        capim = capture_sink.pop_captured_image(image_id)
        # XXX: originally had this before capture request,
        # but maybe will synchronize slightly better after
        # until we have a need to actually do this per image this is probably more accurate
        capim.set_meta(self._disp_properties_meta())
        return capim
