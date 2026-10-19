"""
Start aggregating plugin registration

Motion HAL
Imager HAL
Control Scroll (imager GUI controls)
"""

from uscope.imager.imager import Imager
from uscope.util import LogTimer
from uscope.planner.planner_util import get_planner, microscope_to_planner_config
from uscope.imager.image_sequence import CapturedImage

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import threading
import time
import traceback
from uscope.imager.gst import ImageTimeout
from uscope.microscope import MicroscopeStop
import tempfile
import glob
"""
WARNING: early on there was some variety in maybe different imagers
for now GUI pretty solidly assumes Gst
This is the intended direction in general
ie if you want something to show up in the GUI write a gstreamer plugin for it

However we might consider some flexibility allowing not to render...TBD
ex: if you are using DSLR with own screen no strict requirement to render here
although could still have a method to snap pictures
"""
"""
FIXME: a lot of this should be some sort of layer on top of planner
is not strictly speaking related to the GUI. Hmm
"""


class GUIImagerBase(Imager):
    """
    Common logic for GUI (Argus) imagers
    Subclasses implement next_captured_image() to grab a single raw frame
    """

    # Give up after this many consecutive failed captures
    # Enough to ride out a camera restart but not hang a scan forever
    GET_MAX_TRIES = 5

    def __init__(self, ac):
        Imager.__init__(self)
        self.ac = ac
        self.usc = self.ac.microscope.usc
        self.width, self.height = self.usc.imager.final_wh()
        self.factor = self.usc.imager.scalar()
        self.videoflip_method = self.usc.imager.videoflip_method()
        self.composite_grabber = CompositeImageGrabber(self.ac, self)

    def get_sn(self):
        return None

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
            "disp_properties":
            dict(self.ac.control_scroll.get_disp_properties_ts()),
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
                self.ac.microscope.log(
                    f"WARNING: failed to get image ({type(e).__name__}: {e}). Sleeping then retrying ({tries} / {self.GET_MAX_TRIES})"
                )
                print(traceback.format_exc())
                # If something went badly wrong need to give it some time to recover / restart pipeline
                time.sleep(
                    self.ac.microscope.kinematics.tsettle_video_pipeline * 2)

    def get_processed(self,
                      processing_options=None,
                      recover_errors=True,
                      snapshot_timeout=None,
                      processing_timeout=None):
        if processing_options is None:
            processing_options = {}
        if processing_timeout is None:
            processing_timeout = self.ac.microscope.usc.imager.processing_timeout(
            )
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
            options["objective_config"] = self.ac.objective_config()
            options["scale_factor"] = self.ac.usc.imager.scalar()
            options["scale_expected_wh"] = self.ac.usc.imager.final_wh()
            if self.ac.usc.imager.videoflip_method():
                options[
                    "videoflip_method"] = self.ac.usc.imager.videoflip_method(
                    )
            options.update(processing_options)

            self.ac.image_processing_thread.process_image(options=options,
                                                          callback=callback)
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
            capim.meta["objective_config"] = options["objective_config"]
            return capim

    def get_composite(self, **kwargs):
        return self.composite_grabber.get_composite(**kwargs)

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

    # FIXME: should maybe actually use low level properties
    # Start with this as PoC since its safer for GUI updates though

    def _set_properties(self, vals):
        self.ac.control_scroll.set_disp_properties(vals)

    def _get_properties(self):
        return self.ac.control_scroll.get_disp_properties()

    def get_exposure_cache(self):
        disp_prop = self.ac.control_scroll.get_exposure_disp_property()
        return self.ac.control_scroll.get_disp_property_ts(disp_prop)

    def get_exposure_property(self):
        return self.ac.control_scroll.get_exposure_disp_property()

    def set_exposure(self, value):
        return self.ac.control_scroll.set_exposure_disp_property(value)

    def add_captured_image_meta(self, captured_image):
        self.ac.control_scroll.add_captured_image_meta(captured_image)

    def captured_image_exposure(self, captured_image):
        return self.ac.control_scroll.captured_image_exposure(captured_image)


class GstGUIImager(GUIImagerBase):
    def get_sn(self):
        if self.ac.vidpip.source_name == "gst-toupcamsrc":
            return self.ac.control_scroll.raw_prop_read("serial-number")
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
        formats = self.ac.vidpip.imager_aplugin.supported_formats()
        if not formats:
            return []
        # Filter out 16 bit formats on 8 bit cameras
        max_bit_depth = None
        source = self.ac.vidpip.source
        if source is not None and source.find_property("max-bit-depth"):
            try:
                max_bit_depth = source.get_property("max-bit-depth")
            except Exception:
                max_bit_depth = None
        if max_bit_depth and max_bit_depth <= 8:
            from uscope.imager import pixel_format
            formats = [f for f in formats if not pixel_format.is_16bit(f)]
        return list(formats)

    def get_capture_format(self):
        """
        Currently requested capture format (gstreamer format string)
        None if the source doesn't support selecting it
        """
        return self.ac.vidpip.get_capture_format()

    def set_capture_format(self, fmt):
        """
        Switch the capture pixel format
        The camera is stopped, reconfigured and restarted by the source
        Not allowed while a scan is running
        Thread safe
        """
        # planner_thread is cleared when a scan completes
        if self.ac.planner_thread is not None:
            raise RuntimeError(
                "Refusing to change capture format while scan is running")
        if fmt not in self.supported_capture_formats():
            raise ValueError(
                f"Capture format {fmt} not supported by this camera. Supported: {self.supported_capture_formats()}"
            )
        if fmt == self.get_capture_format():
            return
        self.ac.vidpip.set_capture_format(fmt)
        self.ac.microscope.log(f"Capture format: {fmt}")
        # Let the GUI (menu, control scroll) follow along
        self.ac.captureFormatChanged.emit(fmt)

    def next_captured_image(self, timeout=None):
        # WARNING: this must be thread safe / may be called from any context
        if timeout is None:
            timeout = self.ac.microscope.usc.imager.snapshot_timeout()

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
            self.ac.capture_sink.request_image(got_image)
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
            got_format = self.ac.capture_sink.get_image(image_id).get(
                "format")
            if got_format is None or got_format == want_format:
                break
            self.ac.capture_sink.del_image(image_id)
        capim = self.ac.capture_sink.pop_captured_image(image_id)
        # XXX: originally had this before capture request,
        # but maybe will synchronize slightly better after
        # until we have a need to actually do this per image this is probably more accurate
        capim.set_meta(self._disp_properties_meta())
        return capim


class Picam2GUIImager(GUIImagerBase):
    """Picamera2 (Raspberry Pi Camera) imager backend"""

    def next_captured_image(self, timeout=None):
        """Capture an image using Picamera2's still capture mode"""
        if timeout is None:
            timeout = self.ac.microscope.usc.imager.snapshot_timeout()

        image_ready = threading.Event()
        got = {}

        def got_image(job):
            got["req"] = self.ac.capture_pc2.wait(job)
            image_ready.set()

        capture_config = self.ac.capture_pc2.create_still_configuration(
            display=None)
        self.ac.capture_pc2.switch_mode_and_capture_request(
            capture_config, signal_function=got_image)

        if not image_ready.wait(timeout=timeout):
            raise ImageTimeout(
                "Failed to get raw image within timeout %0.1f sec" %
                (timeout, ))
        image = got["req"].make_image("main")

        capim = CapturedImage(image=image)
        capim.set_meta(self._disp_properties_meta())
        return capim


# TODO: consider doing this in memory
# a bit of a hack to write to filesystem
class CompositeImageGrabber:
    def __init__(self, ac, imager):
        self.ac = ac
        self.imager = imager
        # delete on exit
        self.temp_dirs = []

    # FIXME: timeouts aren't being respected
    def get_composite(self,
                      processing_options=None,
                      recover_errors=True,
                      snapshot_timeout=None,
                      processing_timeout=None):
        """
        Ideally would like this to do focus stacking, HDR, etc
        Whatever is active
        """
        if processing_options is None:
            processing_options = {}
        hdr_pconfig = self.ac.mw.imagerTab.hdr_pconfig()
        stacker_pconfig = self.ac.mw.advancedTab.stacker_pconfig()
        image_stabilization_pconfig = self.ac.advancedTab.image_stabilization_pconfig(
        )

        # No advanced options?
        # Just do a simple capture
        if hdr_pconfig is None and stacker_pconfig is None and image_stabilization_pconfig is None:
            return self.imager.get_processed(
                processing_options=processing_options,
                recover_errors=recover_errors,
                snapshot_timeout=snapshot_timeout,
                processing_timeout=processing_timeout)

        objective = self.ac.mw.mainTab.objective_widget.get_objective_meta_cache(
        )
        pconfig = microscope_to_planner_config(microscope=self.ac.microscope,
                                               objective=objective)

        pconfig["imager"].update({
            # prevent recursion
            "get_mode": "processed",
            # Temp file
            # Run lossless
            # FIXME: https://github.com/Labsmore/pyuscope/issues/464
            # "save_extension": ".tif",
            "save_extension": ".jpg",
        })
        modes = []
        if hdr_pconfig is not None:
            pconfig["imager"]["hdr"] = hdr_pconfig
            modes.append("HDR")
        if stacker_pconfig is not None:
            pconfig["points-stacker"] = stacker_pconfig
            modes.append("stacker")
        if image_stabilization_pconfig is not None:
            pconfig["image-stabilization"] = image_stabilization_pconfig
            modes.append("stabilization")
        modes = ",".join(modes)
        save_filename = processing_options.get("save_filename")
        if save_filename:
            self.ac.microscope.log(f"Composite snapshot: requested w/ {modes}")

        out_dir_temp = tempfile.TemporaryDirectory()
        try:
            # Collect images but running a lightweight planner
            planner = get_planner(microscope=self.ac.microscope,
                                  pconfig=pconfig,
                                  out_dir=out_dir_temp.name,
                                  dry=False)
            _meta = planner.run()

            if save_filename and self.ac.bc.dev_mode():
                self.ac.microscope.log("Composite snapshot: processing images")

            # Now process them with minimal settings
            ippj = {
                "cloud_stitch": False,
                "write_html_viewer": False,
                "write_quick_pano": False,
                "write_snapshot_grid": False,
                "keep_intermediates": False,
                "snapshot": True,
            }
            self.ac.stitchingTab.stitcher_thread.imagep_add(
                directory=out_dir_temp.name, ippj=ippj, block=True)

            # Scrape the output image
            out_fn = glob.glob(out_dir_temp.name +
                               "/*.tif") + glob.glob(out_dir_temp.name +
                                                     "/*.jpg")
            if len(out_fn) != 1:
                raise Exception(
                    "Expected exactly one image (image processing failed?)")
            capim = CapturedImage.load(out_fn)
            # Now save it and/or return it
            if save_filename is not None:
                # XXX: might re-compress things
                capim.save(save_filename)
                capim.set_meta_kv("save_filename", save_filename)
                # self.ac.microscope.log(f"Composite snapshot: saved to {save_filename}")
            capim.set_meta_kv("objective_config", self.ac.objective_config())
            return capim
        finally:
            if self.ac.microscope.bc.dev_mode():
                self.temp_dirs.append(out_dir_temp)
            else:
                del out_dir_temp


# Thread safe Imager
# Ex: called from scripting context
class GstGUIImagerTS(Imager):
    def __init__(self, imager, verbose=False):
        Imager.__init__(self, verbose=verbose)
        self.imager = imager

    def get(self, *args, **kwargs):
        return self.imager.get(*args, **kwargs)

    def get_processed(self, *args, **kwargs):
        return self.imager.get_processed(*args, **kwargs)

    def get_composite(self, *args, **kwargs):
        return self.imager.get_composite(*args, **kwargs)

    def get_by_mode(self, *args, **kwargs):
        return self.imager.get_by_mode(*args, **kwargs)

    def supported_capture_formats(self):
        return self.imager.supported_capture_formats()

    def get_capture_format(self):
        return self.imager.get_capture_format()

    def set_capture_format(self, *args, **kwargs):
        return self.imager.set_capture_format(*args, **kwargs)

    def _set_properties(self, vals):
        # self.ac.control_scroll.set_disp_properties(vals)
        # self.imager.change_properties.emit(vals)
        self.imager.ac.control_scroll.set_disp_properties_ts(vals)

    def _get_properties(self):
        # return self.ac.control_scroll.get_disp_properties()
        # assert 0, "FIXME"
        return self.imager.ac.control_scroll.get_disp_properties_ts()

    def wh(self):
        return self.imager.wh()

    def set_exposure(self, value):
        disp_prop = self.imager.ac.control_scroll.get_exposure_disp_property()
        self.set_property(disp_prop, value)

    def since_properties_change(self):
        return self.imager.since_properties_change()


'''
class MockGUIImager(MockImager):
    pass
'''
