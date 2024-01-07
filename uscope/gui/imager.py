"""
Start aggregating plugin registration

Motion HAL
Imager HAL
Control Scroll (imager GUI controls)
"""

from uscope.imager.imager import Imager, MockImager
from uscope.util import LogTimer

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import threading
import time
import traceback
from uscope.imager.gst import ImageTimeout
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
    class Emitter(QObject):
        change_properties = pyqtSignal(dict)

    def __init__(self, ac, usc):
        Imager.__init__(self)
        self.ac = ac
        self.usc = usc
        self.image_ready = threading.Event()
        self.image_id = None
        self.emitter = GstGUIImager.Emitter()
        self.width, self.height = self.usc.imager.final_wh()
        self.factor = self.usc.imager.scalar()
        self.videoflip_method = self.usc.imager.videoflip_method()

    def get_sn(self):
        if self.ac.vidpip.source_name == "gst-toupcamsrc":
            return self.ac.control_scroll.raw_prop_read("serial-number")
        else:
            return None

    def wh(self):
        return self.width, self.height

    def next_image(self, timeout=None):
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
        return self.ac.capture_sink.pop_image(self.image_id)

    def get(self, recover_errors=True, timeout=None):
        # WARNING: this must be thread safe / may be called from any context
        while True:
            try:
                # 2023-11-16: we used to do scaling / etc here
                # Now its done in image processing thread
                # This also allows getting "raw" image if needed
                return {"0": self.next_image(timeout=timeout)}
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

    # FIXME: clean this up
    # maybe start by getting all parties to call this
    # then can move things into main get function?
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
                image = self.get(timeout=snapshot_timeout,
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
            options["image"] = image
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


def get_gui_imager(source, gui):
    # WARNING: only gst- sources are supported
    # This indirection may be eliminated
    if source == 'mock':
        return MockImager()
    elif source.find("gst-") == 0:
        ret = GstGUIImager(gui, usc=gui.usc)
        # For HDR which needs in situ control
        ret.emitter.change_properties.connect(
            gui.control_scroll.set_disp_properties)
        return ret
    else:
        raise Exception('Invalid imager type %s' % source)
