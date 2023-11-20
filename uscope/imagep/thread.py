from uscope.imager.autofocus import choose_best_image
from uscope.imager.imager_util import get_scaled
from uscope.imagep.pipeline import process_snapshots
from uscope.imager.autofocus import Autofocus
from uscope.threads import CommandThreadBase

import threading
import queue
import traceback
from PIL import Image
from uscope.microscope import MicroscopeStop


class ImageProcessingThreadBase(CommandThreadBase):
    def __init__(self, microscope):
        super().__init__(microscope)
        self.command_map = {
            "auto_focus": self._do_auto_focus,
            "process_image": self._do_process_image,
        }

    def auto_focus(self, objective_config, block=False, done=None):
        j = {
            #"type": "auto_focus",
            "objective_config": objective_config,
        }
        self.command("auto_focus", j, block=block, done=done)

    def _do_auto_focus(self, j):
        try:
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

    def process_image(self, options, block=False, callback=None):
        j = {
            #"type": "process_snapshot",
            "options": options,
        }
        self.command("process_image", j, block=block, callback=callback)

    # TODO: move more of this to the image processing thread
    # rotate, scaling
    def _do_process_image(self, j):
        options = j["options"]
        image = get_scaled(options["image"],
                           options["scale_factor"],
                           filt=Image.NEAREST)

        if "scale_expected_wh" in options:
            expected_wh = options["scale_expected_wh"]
            assert expected_wh[0] == image.size[0] and expected_wh[
                1] == image.size[
                    1], "Unexpected image size: expected %s, got %s" % (
                        expected_wh, image.size)

        videoflip_method = options.get("videoflip_method")
        if videoflip_method:
            assert videoflip_method == "rotate-180"
            image = image.rotate(180)

        image = process_snapshots([image], options=options)

        if "save_filename" in options:
            kwargs = {}
            if "save_quality" in options:
                kwargs["quality"] = options["save_quality"]
            image.save(options["save_filename"], **kwargs)

        return image


class SimpleImageProcessingThreadBase(ImageProcessingThreadBase,
                                      threading.Thread):
    pass
