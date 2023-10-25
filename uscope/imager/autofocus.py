import cv2 as cv
import numpy as np
from uscope.imagep.util import RC_CONST


def choose_best_image(images_iter, log=None, verbose=False):
    take_center = True

    if not log:

        def log(s):
            print(s)

    scores = {}
    verbose and log(" AF choose")
    for fni, (imagek, im_pil) in enumerate(images_iter):

        def get_score(image, blur=9):
            filtered = cv.medianBlur(image, blur)
            laplacian = cv.Laplacian(filtered, cv.CV_64F)
            return laplacian.var()

        def image_pil2cv(im):
            return np.array(im)[:, :, ::-1].copy()

        if take_center:
            width, height = im_pil.size

            left = (width - width / 3) / 2
            top = (height - height / 3) / 2
            right = (width + width / 3) / 2
            bottom = (height + height / 3) / 2

            # Crop the center of the image
            im_pil = im_pil.crop((left, top, right, bottom))

        im_cv = image_pil2cv(im_pil)
        score = get_score(im_cv)
        verbose and log("  AF choose %u (%0.6f): %0.3f" % (fni, imagek, score))
        scores[score] = imagek, fni
    _score, (k, fni) = sorted(scores.items())[-1]
    verbose and log(" AF choose winner: %s" % k)
    return k, fni


class Autofocus:
    # FIXME: pass in a Microscope object
    def __init__(self, move_absolute, pos, imager, kinematics, log, poll=None):
        self.log = log
        self.move_absolute = move_absolute
        self.pos = pos
        self.imager = imager
        self.kinematics = kinematics
        self.poll = poll

    def move_absolute_wait(self, pos):
        self.move_absolute(pos, block=True)
        self.kinematics.wait_autofocus()

    def auto_focus_pass(self,
                        step_size,
                        step_pm,
                        move_target=True,
                        start_pos=None):
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
        if start_pos is None:
            start_pos = self.pos()["z"]
        steps = step_pm * 2 + 1

        # Doing generator allows easier to process images as movement is done / settling
        def gen_images():
            for focusi in range(steps):
                if self.poll:
                    self.poll()
                # FIXME: use backlash compensation direction here
                target_pos = start_pos + -(focusi - step_pm) * step_size
                self.log("autofocus round %u / %u: try %0.6f" %
                         (focusi + 1, steps, target_pos))
                self.move_absolute_wait({"z": target_pos})
                im_pil = self.imager.get()["0"]
                yield target_pos, im_pil

        if self.poll:
            self.poll()
        target_pos, fni = choose_best_image(gen_images())
        self.log("autofocus: set %0.6f at %u / %u" %
                 (target_pos, fni + 1, steps))
        if self.poll:
            self.poll()
        if move_target:
            self.move_absolute_wait({"z": target_pos})
        return target_pos

    def calc_die_normal_step(self, objective_config):
        na = objective_config["na"]
        # convert nm to mm
        resolution400 = RC_CONST * 400 / (2 * na) / 1e6
        machine_epsilon = self.kinematics.microscope.motion.epsilon()["z"]
        ideal_move = resolution400 * 3.5
        # Now round to nearest machine step
        # Don't go below machine min step size
        steps = max(1, round(ideal_move / machine_epsilon))
        rounded_move = steps * machine_epsilon
        return rounded_move

    def coarse_parameters(self, objective_config):
        base_step = self.calc_die_normal_step(objective_config)
        return {
            "step_size": base_step * 3,
            "step_pm": 3,
        }

    def fine_parameters(self, objective_config):
        base_step = self.calc_die_normal_step(objective_config)
        return {
            "step_size": base_step,
            "step_pm": 3,
        }

    def coarse(self, objective_config):
        # MVP intended for 20x
        # 2 um is standard focus step size
        self.log("autofocus: coarse")
        parameters = self.coarse_parameters(objective_config)
        coarse_z = self.auto_focus_pass(step_size=parameters["step_size"],
                                        step_pm=parameters["step_pm"],
                                        move_target=False)
        self.log("autofocus: medium")
        parameters = self.fine_parameters(objective_config)
        self.auto_focus_pass(step_size=parameters["step_size"],
                             step_pm=parameters["step_pm"],
                             start_pos=coarse_z)
        self.log("autofocus: done")


class AutoStacker:
    def __init__(self, microscope):
        self.microscope = microscope

    """
        I've found roughly a 3 to 4x multiplier on resolution is a good rule of thumb
        Results in a decent chip image without excessive pictures
        Example: 20x objective is 0.42 NA and I use a 2 um step size
        Resolution @ 400 nm: 1.22 * 400 / (2 * 0.42) = 581 nm
        2000 / 581 = 3.44
        Let's target a ballpark and then round based on machine step size
        Better to round down or to nearest step?
        """

    def calc_die_normal_step(self, objective_config):
        na = objective_config["na"]
        # convert nm to mm
        resolution400 = RC_CONST * 400 / (2 * na) / 1e6
        machine_epsilon = self.microscope.motion.epsilon()["z"]
        ideal_move = resolution400 * 3.5
        # Now round to nearest machine step
        # Don't go below machine min step size
        steps = max(1, round(ideal_move / machine_epsilon))
        rounded_move = steps * machine_epsilon
        return rounded_move

    def calc_die_parameters(self,
                            objective_config,
                            distance_mult=1,
                            step_mult=1):
        """
            For a typical chip
            Assumes very planar with layers at around 1 um vertical spacing
            Might not apply to other things
            """
        normal_step = self.calc_die_normal_step(objective_config)
        normal_steps = 3
        # We calculated per step, but GUI displays a range
        # Normalize the baseline to total distance
        pm_distance = normal_steps * normal_step * distance_mult
        pm_steps = normal_steps * step_mult
        return {
            "pm_distance": pm_distance,
            "pm_steps": pm_steps,
        }
