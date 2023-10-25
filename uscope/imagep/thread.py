from uscope.imager.autofocus import choose_best_image
from uscope.imager.imager_util import get_scaled
from uscope.imagep.pipeline import process_snapshots
from uscope.imager.autofocus import Autofocus

import threading
import queue
import traceback
from PIL import Image


class ImageProcessingThreadBase:
    def __init__(self, microscope):
        self.queue = queue.Queue()
        self.running = threading.Event()
        self.running.set()
        self.microscope = microscope

    def log(self, msg):
        self.log_msg.emit(msg)

    def shutdown(self):
        self.running.clear()

    def command(self, command, block=False, callback=None):
        command_done = None
        if block or callback:
            ready = threading.Event()
            ret = []

            def command_done(command, ret_e):
                ret.append(ret_e)
                ready.set()
                if callback:
                    callback(command, ret_e)

        self.queue.put((command, command_done))
        if block:
            ready.wait()
            ret = ret[0]
            if type(ret) is Exception:
                raise Exception("oopsie: %s" % (ret, ))
            return ret

    def auto_focus(self, objective_config, block=False, callback=None):
        j = {
            "type": "auto_focus",
            "objective_config": objective_config,
        }
        self.command(j, block=block, callback=callback)

    def pos(self):
        return self.microscope.motion_thread.pos()

    def _do_auto_focus(self, objective_config):
        af = Autofocus(
            move_absolute=self.microscope.motion_thread.move_absolute,
            pos=self.pos,
            imager=self.microscope.imager,
            kinematics=self.microscope.kinematics,
            log=self.log)
        af.coarse(objective_config)

    def process_snapshot(self, options, block=False, callback=None):
        j = {
            "type": "process_snapshot",
            "options": options,
        }
        self.command(j, block=block, callback=callback)

    def _do_process_snapshot(self, options):
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

        image = process_snapshots([image])

        if "save_filename" in options:
            kwargs = {}
            if "save_quality" in options:
                kwargs["quality"] = options["save_quality"]
            image.save(options["save_filename"], **kwargs)

        return image

    def run(self):
        while self.running:
            try:
                j, command_done = self.queue.get(block=True, timeout=0.1)
            except queue.Empty:
                continue
            try:
                if j["type"] == "auto_focus":
                    ret = self._do_auto_focus(j["objective_config"])
                elif j["type"] == "process_snapshot":
                    ret = self._do_process_snapshot(j["options"])
                else:
                    assert 0, j

                if command_done:
                    command_done(j, ret)

            except Exception as e:
                self.log('WARNING: image processing thread crashed: %s' %
                         str(e))
                traceback.print_exc()
                if command_done:
                    command_done(j, e)
            finally:
                # self.stitcherDone.emit()
                pass


class SimpleImageProcessingThreadBase(ImageProcessingThreadBase,
                                      threading.Thread):
    pass
