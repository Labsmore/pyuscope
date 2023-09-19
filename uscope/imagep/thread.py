from uscope.imager.autofocus import choose_best_image
from uscope.imager.imager_util import get_scaled
from uscope.imagep.pipeline import process_snapshots

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

    def auto_focus(self, block=False, callback=None):
        j = {
            "type": "auto_focus",
        }
        self.command(j, block=block, callback=callback)

    def move_absolute(self, pos):
        self.microscope.motion_thread.move_absolute(pos, block=True)
        self.microscope.kinematics.wait_imaging_ok()

    def pos(self):
        return self.microscope.motion_thread.pos()

    def auto_focus_pass(self, step_size, step_pm):
        """
        for outer_i in range(3):
            self.log("autofocus: try %u / 3" % (outer_i + 1,))
            # If we are reasonably confident we found the local minima stop
            # TODO: if repeats should bias further since otherwise we are repeating steps
            if abs(step_pm - fni) <= 2:
                self.log("autofocus: converged")
                return
        self.log("autofocus: timed out")
        """

        # Very basic short range
        start_pos = self.pos()["z"]
        steps = step_pm * 2 + 1

        # Doing generator allows easier to process images as movement is done / settling
        def gen_images():
            for focusi in range(steps):
                # FIXME: use backlash compensation direction here
                target_pos = start_pos + -(focusi - step_pm) * step_size
                self.log("autofocus round %u / %u: try %0.6f" %
                         (focusi + 1, steps, target_pos))
                self.move_absolute({"z": target_pos})
                im_pil = self.microscope.imager.get()["0"]
                yield target_pos, im_pil

        target_pos, fni = choose_best_image(gen_images())
        self.log("autofocus: set %0.6f at %u / %u" %
                 (target_pos, fni + 1, steps))
        self.move_absolute({"z": target_pos})

    def _do_auto_focus(self):
        # MVP intended for 20x
        # 2 um is standard focus step size
        self.log("autofocus: coarse")
        self.auto_focus_pass(step_size=0.006, step_pm=3)
        self.log("autofocus: medium")
        self.auto_focus_pass(step_size=0.002, step_pm=3)
        self.log("autofocus: done")

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

        image = process_snapshots([image])

        if "save_filename" in options:
            kwargs = {}
            if "save_quality" in options:
                kwargs["quality"] = options["save_quality"]
            image.save(options["save_filename"], **kwargs)

    def run(self):
        while self.running:
            try:
                j, command_done = self.queue.get(block=True, timeout=0.1)
            except queue.Empty:
                continue
            try:
                if j["type"] == "auto_focus":
                    ret = self._do_auto_focus()
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
