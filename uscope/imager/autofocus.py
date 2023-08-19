import cv2 as cv
import numpy as np


def choose_best_image(images_iter, log=None):
    if not log:

        def log(s):
            print(s)

    scores = {}
    log(" AF choose")
    for fni, (imagek, im_pil) in enumerate(images_iter):

        def get_score(image, blur=9):
            filtered = cv.medianBlur(image, blur)
            laplacian = cv.Laplacian(filtered, cv.CV_64F)
            return laplacian.var()

        def image_pil2cv(im):
            return np.array(im)[:, :, ::-1].copy()

        im_cv = image_pil2cv(im_pil)
        score = get_score(im_cv)
        log("  AF choose %u (%0.6f): %0.3f" % (fni, imagek, score))
        scores[score] = imagek, fni
    _score, (k, fni) = sorted(scores.items())[-1]
    log(" AF choose winner: %s" % k)
    return k, fni


class Autofocus:
    # FIXME: pass in a Microscope object
    def __init__(self, move_absolute, pos, imager, kinematics, log, poll):
        self.log = log
        self.move_absolute = move_absolute
        self.pos = pos
        self.imager = imager
        self.kinematics = kinematics
        self.poll = poll

    def move_absolute_wait(self, pos):
        self.motion_thread.move_absolute(pos, block=True)
        self.kinematics.wait_imaging_ok()

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
                self.poll()
                # FIXME: use backlash compensation direction here
                target_pos = start_pos + -(focusi - step_pm) * step_size
                self.log("autofocus round %u / %u: try %0.6f" %
                         (focusi + 1, steps, target_pos))
                self.move_absolute_wait({"z": target_pos})
                im_pil = self.imager.get()["0"]
                yield target_pos, im_pil

        self.poll()
        target_pos, fni = choose_best_image(gen_images())
        self.log("autofocus: set %0.6f at %u / %u" %
                 (target_pos, fni + 1, steps))
        self.poll()
        self.move_absolute_wait({"z": target_pos})

    def coarse(self):
        # MVP intended for 20x
        # 2 um is standard focus step size
        self.log("autofocus: coarse")
        self.auto_focus_pass(step_size=0.006, step_pm=3)
        self.log("autofocus: medium")
        self.auto_focus_pass(step_size=0.002, step_pm=3)
        self.log("autofocus: done")
