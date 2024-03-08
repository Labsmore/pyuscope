"""
Start aggregating plugin registration

Motion HAL
Imager HAL
Control Scroll (imager GUI controls)
"""

from uscope.imager.imager import Imager, MockImager
from uscope.util import LogTimer
from uscope.imager.image_sequence import ImageSequence
from uscope.planner.planner_util import get_planner

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import threading
import time
import traceback
from uscope.imager.gst import ImageTimeout
import tempfile
import glob
from PIL import Image
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


class GstGUIImager(Imager):
    def __init__(self, ac):
        Imager.__init__(self)
        self.ac = ac
        self.usc = self.ac.microscope.usc
        self.image_ready = threading.Event()
        self.image_id = None
        # self.emitter = GstGUIImager.Emitter()
        self.width, self.height = self.usc.imager.final_wh()
        self.factor = self.usc.imager.scalar()
        self.videoflip_method = self.usc.imager.videoflip_method()
        self.composite_grabber = CompositeImageGrabber(self.ac, self)

    def get_sn(self):
        if self.ac.vidpip.source_name == "gst-toupcamsrc":
            return self.ac.control_scroll.raw_prop_read("serial-number")
        else:
            return None

    def wh(self):
        return self.width, self.height

    def next_captured_image(self, timeout=None):
        # WARNING: this must be thread safe / may be called from any context
        if timeout is None:
            timeout = self.ac.microscope.usc.imager.snapshot_timeout()
        #self.ac.emit_log('gstreamer imager: taking image to %s' % file_name_out)
        def got_image(image_id):
            # self.ac.emit_log('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.ac.capture_sink.request_image(got_image)
        # self.ac.emit_log('Waiting for next image...')
        if not self.image_ready.wait(timeout=timeout):
            raise ImageTimeout(
                "Failed to get raw image within timeout %0.1f sec" %
                (timeout, ))
        # self.ac.emit_log('Got image %s' % self.image_id)
        capim = self.ac.capture_sink.pop_captured_image(self.image_id)
        # best estimate for now
        # Ideally we'd pull this off of the image metadata coming over USB
        # will be more important if support quick exposure bracketing
        # XXX: originally had this before capture request,
        # but maybe will synchronize slightly better after
        # until we have a need to actually do this per image this is probably more accurate
        meta = {
            #"exposure": self.get_exposure_cache(),
            "disp_properties":
            dict(self.ac.control_scroll.get_disp_properties_ts()),
            "raw_properties":
            dict(self.ac.control_scroll.get_raw_properties_ts()),
        }
        capim.set_meta(meta)
        return capim

    def get(self, recover_errors=True, timeout=None):
        # WARNING: this must be thread safe / may be called from any context
        while True:
            try:
                # 2023-11-16: we used to do scaling / etc here
                # Now its done in image processing thread
                # This also allows getting "raw" image if needed
                return ImageSequence(captured_image=self.next_captured_image(
                    timeout=timeout))
            except Exception as e:
                if not recover_errors:
                    raise
                self.ac.microscope.log(
                    f"WARNING: failed to get image ({type(e)}: {e}). Sleeping then retrying"
                )
                print(traceback.format_exc())
                # If something went badly wrong need to give it some time to recover / restart pipeline
                time.sleep(
                    self.ac.microscope.kinematics.tsettle_video_pipeline * 2)

    def get_processed(self,
                      recover_errors=True,
                      snapshot_timeout=None,
                      processing_timeout=None):

        if processing_timeout is None:
            processing_timeout = self.ac.microscope.usc.imager.processing_timeout(
            )
        with LogTimer("get_processed: net",
                      variable="PYUSCOPE_PROFILE_TIMAGE"):
            # Get relatively unprocessed snapshot
            with LogTimer("get_processed: raw",
                          variable="PYUSCOPE_PROFILE_TIMAGE"):
                meta_image = self.get(timeout=snapshot_timeout,
                                      recover_errors=recover_errors)["0"]

            processed = {}
            ready = threading.Event()

            def callback(command, args, ret_e):
                if type(ret_e) is Exception:
                    processed["exception"] = ret_e
                else:
                    processed["image"] = ret_e
                ready.set()

            options = {}
            options["image"] = meta_image["image"]
            meta_image["type"] = "snapshot"
            options["meta_image"] = meta_image
            options["objective_config"] = self.ac.objective_config()
            options["scale_factor"] = self.ac.usc.imager.scalar()
            options["scale_expected_wh"] = self.ac.usc.imager.final_wh()
            if self.ac.usc.imager.videoflip_method():
                options[
                    "videoflip_method"] = self.ac.usc.imager.videoflip_method(
                    )

            self.ac.image_processing_thread.process_image(options=options,
                                                          callback=callback)
            with LogTimer("get_processed: waiting",
                          variable="PYUSCOPE_PROFILE_TIMAGE"):
                if not ready.wait(timeout=processing_timeout):
                    raise ImageTimeout(
                        "Failed to get image within processing timeout %0.1f sec"
                        % (processing_timeout, ))
            if "exception" in processed:
                raise Exception(
                    f"failed to process image: {processed['exception']}")
            return processed["image"]

    def get_composite(self, **kwargs):
        self.composite_grabber.get_composite(**kwargs)

    def get_by_mode(self, mode=None, **kwargs):
        if mode == "raw":
            return self.get(**kwargs)
        elif mode == "processed":
            return self.get_processed(**kwargs)
        elif mode == "composite":
            return self.get_composite(**kwargs)
        else:
            assert 0, f"bad mode {mode}"

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

    '''
    def get_exposure_cache(self):
        # not thread safe
        # val = self.ac.get_exposure()
        disp_prop = self.ac.control_scroll.get_exposure_disp_property()
        return self.ac.control_scroll.get_disp_property_ts(disp_prop)
    '''

    def make_exif_bytes(self, meta):
        return self.ac.control_scroll.make_exif_bytes(meta)


# TODO: consider doing this in memory
# a bit of a hack to write to filesystem
class CompositeImageGrabber:
    def __init__(self, ac, imager):
        self.ac = ac
        self.imager = imager

    # FIXME: timeouts aren't being respected
    def get_composite(self,
                      recover_errors=True,
                      snapshot_timeout=None,
                      processing_timeout=None):
        """
        Ideally would like this to do focus stacking, HDR, etc
        Whatever is active
        """
        hdr_pconfig = self.ac.imagerTab.hdr_pconfig()
        stacker_pconfig = self.ac.advancedtab.stacker_pconfig()
        image_stabilization_pconfig = self.ac.advancedtab.image_stabilization_pconfig(
        )

        # No advanced options?
        # Just do a simple capture
        if hdr_pconfig is None and stacker_pconfig is None and image_stabilization_pconfig is None:
            return self.imager.get_processed(
                recover_errors=recover_errors,
                snapshot_timeout=snapshot_timeout,
                processing_timeout=processing_timeout)

        if hdr_pconfig is not None or stacker_pconfig is not None or image_stabilization_pconfig is not None:
            #if self.ac.auto_exposure_enabled():
            pconfig = {
                "points-stacker": stacker_pconfig,
                "image-stabilization": image_stabilization_pconfig,
                "imager": {
                    # prevent recursion
                    "get_mode": "processed",
                    # Temp file
                    # Run lossless
                    "save_extension": ".tif",
                    "hdr": hdr_pconfig,
                }
            }
            with tempfile.TemporaryDirectory() as out_dir_temp:
                # Collect images but running a lightweight planner
                planner = get_planner(microscope=self.ac.microscope,
                                      pconfig=pconfig,
                                      out_dir=out_dir_temp,
                                      dry=False)
                _meta = planner.run()

                # Now process them with minimal settings
                ippj = {
                    "cloud_stitch": False,
                    "write_html_viewer": False,
                    "write_quick_pano": False,
                    "write_snapshot_grid": False,
                    "keep_intermediates": False,
                }
                self.ac.stitchingTab.stitcher_thread.imagep_add(
                    directory=out_dir_temp,
                    ippj=ippj,
                )

                # Scrape the output image
                out_fn = glob.glob(out_dir_temp + "/*.tif")
                if len(out_fn) != 1:
                    raise Exception(
                        "Expected exactly one image (image processing failed?)"
                    )
                with open(out_fn[0], "rb") as f:
                    im_out = Image.open(f)
                    im_out.load()


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

    def _set_properties(self, vals):
        # self.ac.control_scroll.set_disp_properties(vals)
        # self.imager.change_properties.emit(vals)
        self.imager.ac.control_scroll.set_disp_properties_ts(vals)

    def _get_properties(self):
        # return self.ac.control_scroll.get_disp_properties()
        # assert 0, "FIXME"
        return self.imager.ac.control_scroll.get_disp_properties_ts()


'''
class MockGUIImager(MockImager):
    pass
'''
