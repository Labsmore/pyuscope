"""
Argus (GUI) imagers
Capture itself is Qt free (see uscope/imager/gst_imager.py)
This adds compositing (HDR, stacking, ...) configured from the GUI
"""

from uscope.imager.gst_imager import SessionImager, GstImager
from uscope.planner.planner_util import get_planner, microscope_to_planner_config
from uscope.imager.image_sequence import CapturedImage
from uscope.imager.gst import ImageTimeout
from uscope.imagep.util import TaskBarrier

import threading
from uscope import temp_file
import glob


class GstGUIImager(GstImager):
    def __init__(self, ac):
        super().__init__(session=ac)
        self.ac = ac
        self.composite_grabber = CompositeImageGrabber(ac, self)

    def get_composite(self, **kwargs):
        return self.composite_grabber.get_composite(**kwargs)


class Picam2GUIImager(SessionImager):
    """Picamera2 (Raspberry Pi Camera) imager backend"""
    def __init__(self, ac):
        super().__init__(session=ac)
        self.ac = ac
        self.composite_grabber = CompositeImageGrabber(ac, self)

    def get_composite(self, **kwargs):
        return self.composite_grabber.get_composite(**kwargs)

    def next_captured_image(self, timeout=None):
        """Capture an image using Picamera2's still capture mode"""
        if timeout is None:
            timeout = self.usc.imager.snapshot_timeout()

        image_ready = threading.Event()
        got = {}

        def got_image(job):
            got["req"] = self.session.capture_pc2.wait(job)
            image_ready.set()

        capture_config = self.session.capture_pc2.create_still_configuration(
            display=None)
        self.session.capture_pc2.switch_mode_and_capture_request(
            capture_config, signal_function=got_image)

        if not image_ready.wait(timeout=timeout):
            raise ImageTimeout(
                "Failed to get raw image within timeout %0.1f sec" %
                (timeout, ))
        image = got["req"].make_image("main")

        capim = CapturedImage(image=image)
        capim.set_meta(self._disp_properties_meta())
        return capim


class CompositeImageGrabber:
    def __init__(self, ac, imager):
        self.ac = ac
        self.imager = imager
        # delete on exit
        self.temp_dirs = []

    def run_request_plugins(self, capim, processing_options):
        ip = self.ac.image_processing_thread.ip
        for plugin in processing_options.get("plugins", {}):
            options = {"objective_config": self.ac.objective_config()}
            plugin_options = processing_options["plugins"]
            if isinstance(plugin_options, dict):
                options.update(plugin_options.get(plugin) or {})
            tb = TaskBarrier()
            data_out = ip.queue_1_to_1_plugin(plugin=plugin,
                                              im_in=capim.image,
                                              want_im_out=True,
                                              options=options,
                                              tb=tb)
            tb.wait()
            capim.image = data_out["image"].get_im()

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

        objective = self.ac.objective_config()
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

        out_dir_temp = temp_file.TemporaryDirectory()
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
            capim = CapturedImage.load(out_fn[0],
                                       microscope=self.ac.microscope)
            # Per request plugins (ex: scalebar) only apply to the final image
            # The planner captures above run without them
            self.run_request_plugins(capim, processing_options)
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
