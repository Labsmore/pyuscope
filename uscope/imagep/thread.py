from uscope.imager.autofocus import choose_best_image
from uscope.imager.imager_util import get_scaled
from uscope.imagep.pipeline import CSImageProcessor
from uscope.imager.autofocus import Autofocus
from uscope.threads import CommandThreadBase
from uscope.imagep.util import find_qr_code_match

import threading
import queue
import traceback
from PIL import Image
from uscope.microscope import MicroscopeStop
import os.path


class ImageProcessingThreadBase(CommandThreadBase):
    def __init__(self, microscope):
        super().__init__(microscope)
        self.command_map = {
            "auto_focus": self._do_auto_focus,
            "process_image": self._do_process_image,
        }

        self.autofocus_running = False
        self.ip = None
        self.ip = CSImageProcessor(microscope=microscope)
        self.ip.start()
        # nothing to process => can try to shutdown before it starts
        self.ip.ready.wait(1.0)

    def shutdown_request(self):
        # Stop requests first
        super().shutdown_request()
        # Then lower level engine
        self.ip.shutdown_request()

    def shutdown_join(self, timeout=3.0):
        # Stop requests first
        super().shutdown_join(timeout=timeout)
        # Then lower level engine
        self.ip.shutdown_join(timeout=timeout)

    def auto_focus(self, objective_config, block=False, done=None):
        j = {
            #"type": "auto_focus",
            "objective_config": objective_config,
        }
        self.command("auto_focus", j, block=block, done=done)

    def _do_auto_focus(self, j):
        try:
            self.autofocus_running = True
            af = Autofocus(
                self.microscope,
                move_absolute=self.microscope.motion_thread.move_absolute,
                pos=self.microscope.motion_thread.pos,
                imager=self.microscope.imager,
                kinematics=self.microscope.kinematics,
                log=self.log)
            af.coarse(j["objective_config"])
        except MicroscopeStop:
            self.log("Autofocus cancelled")
            raise
        finally:
            self.autofocus_running = False

    def process_image(self, options, block=False, callback=None):
        # if "objective_config" not in options:
        #    options["objective_config"] = self.ac.objective_config()
        assert "objective_config" in options, options
        j = {
            #"type": "process_snapshot",
            "options": options,
        }
        self.command("process_image", j, block=block, callback=callback)

    # TODO: move more of this to the image processing thread
    # rotate, scaling
    def _do_process_image(self, j):
        options = j["options"]
        capim = options["captured_image"]
        capim.image = get_scaled(capim.image,
                                 options["scale_factor"],
                                 filt=Image.NEAREST)

        if "scale_expected_wh" in options:
            expected_wh = options["scale_expected_wh"]
            assert expected_wh[0] == capim.image.size[0] and expected_wh[
                1] == capim.image.size[
                    1], "Unexpected image size: expected %s, got %s" % (
                        expected_wh, capim.image.size)

        videoflip_method = options.get("videoflip_method")
        if videoflip_method:
            assert videoflip_method == "rotate-180"
            capim.image = capim.image.rotate(180)

        try:
            capim.image = self.ip.process_snapshot(capim.image,
                                                   options=options)
        except Exception as e:
            traceback.print_exc()
            self.log(f"WARNING; snapshot processing crashed: {e}")
            return None

        if "save_filename" in options:
            if "qr_regex" in options:
                qr_match = find_qr_code_match(capim.image,
                                              options.get("qr_regex"))
                if qr_match:
                    base_name, ext = os.path.splitext(options["save_filename"])
                    save_filename = base_name + "_" + qr_match + ext
                    options["save_filename"] = save_filename
            kwargs = {}
            if "save_quality" in options:
                kwargs["quality"] = options["save_quality"]
            capim.save(options["save_filename"], **kwargs)
            capim.meta["save_filename"] = options["save_filename"]

        return capim


class SimpleImageProcessingThreadBase(ImageProcessingThreadBase,
                                      threading.Thread):
    pass
