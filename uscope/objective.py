import copy
from collections import OrderedDict
"""
Sample usage:
with StopEvent(microscope) as se:
    do_stuff()
    se.poll()
    do_stuff()
"""


class MicroscopeObjectives:
    def __init__(self, microscope):
        self.microscope = microscope
        """
        Return objectives w/ automatic sizing (if applicable) applied

        returns:
        x_view: in final scaled image how many mm wide
        um_per_pixel: in final scaled image how many micrometers each pixel represents
        magnification: optional
        na: optional
        """
        # Copy so we can start filling in data
        objectives = copy.deepcopy(
            self.microscope.usc.get_uncalibrated_objectives(
                microscope=self.microscope))

        # Start by filling in missing metdata from DB
        self.microscope.usc.bc.objective_db.set_defaults(objectives)
        # Now apply system specific sizing / calibration
        self.scale_objectives_1x(objectives)
        # Derrive kinematics parameters
        # (ie slower settling at higher mag)
        self.apply_objective_tsettle(objectives)

        final_w, final_h = self.microscope.usc.imager.final_wh()
        for objective in objectives:
            if "um_per_pixel" not in objective:
                if "x_view" not in objective:
                    raise Exception(
                        "Failed to calculate objective um_per_pixel: need x_view. Microscope missing um_per_pixel_raw_1x?"
                    )
                # mm to um
                objective[
                    "um_per_pixel"] = objective["x_view"] / final_w * 1000

        # Sanity check required parameters
        names = set()
        for objectivei, objective in enumerate(objectives):
            # last ditch name
            if "name" not in objective:
                if "magnification" in objective:
                    if "series" in objective:
                        objective["name"] = "%s %uX" % (
                            objective["series"], objective["magnification"])
                    else:
                        objective["name"] = "%uX" % objective["magnification"]
                else:
                    objective["name"] = "Objective %u" % objectivei
            assert "name" in objective, objective
            assert objective[
                "name"] not in names, f"Duplicate objective name {objective}"
            names.add(objective["name"])
            assert "x_view" in objective, objective
            assert "um_per_pixel" in objective, objective
            assert "tsettle_motion" in objective, objective

        # Used to be list by index
        # Lets make this a dictionary by name
        objectivesd = OrderedDict()
        for objective in objectives:
            objectivesd[objective["name"]] = objective
        self.objectives = objectivesd

    def scale_objectives_1x(self, objectives):
        # In raw sensor pixels before scaling
        # That way can adjust scaling w/o adjusting
        # This is the now preferred way to set configuration
        um_per_pixel_raw_1x = self.microscope.usc.optics.um_per_pixel_raw_1x()
        if not um_per_pixel_raw_1x:
            return

        # crop_w, _crop_h = self.imager.cropped_wh()
        final_w, final_h = self.microscope.usc.imager.final_wh()
        # Objectives must support magnification to scale
        for objective in objectives:
            if "um_per_pixel" not in objective:
                objective["um_per_pixel"] = um_per_pixel_raw_1x / objective[
                    "magnification"] / self.microscope.usc.imager.scalar()
            if "x_view" not in objective:
                # um to mm
                objective[
                    "x_view"] = final_w * um_per_pixel_raw_1x / self.microscope.usc.imager.scalar(
                    ) / objective["magnification"] / 1000
            if "y_view" not in objective:
                # um to mm
                objective[
                    "y_view"] = final_h * um_per_pixel_raw_1x / self.microscope.usc.imager.scalar(
                    ) / objective["magnification"] / 1000

    def apply_objective_tsettle(self, objectives):
        reference_tsettle_motion = self.microscope.usc.kinematics.tsettle_motion_na1(
        )
        reference_na = 1.0
        # Objectives must support magnification to scale
        for objective in objectives:
            if "tsettle_motion" in objective:
                continue
            tsettle_motion = 0.0
            # Ex: 2.0 sec sleep at 100x 1.0 NA => 20x 0.42 NA => 0.84 sec sleep
            # Assume conservative NA (high power oil objective) if not specified
            HIGHEST_NA = 1.4
            tsettle_motion = reference_tsettle_motion * objective.get(
                "na", HIGHEST_NA) / reference_na
            objective["tsettle_motion"] = tsettle_motion

    def names(self):
        return [objective["name"] for objective in self.objectives.values()]

    def get_full_config(self):
        """
        Return JSON config structure for all objectives
        """
        return self.objectives

    def get_config(self, name):
        """
        Return JSON config structure for given objectives
        """
        return self.objectives[name]

    def default_name(self):
        # First name
        return list(self.objectives.keys())[0]

    def set_global_scalar(self, magnification):
        """
        Set a magnification factor
        Intended to:
        -Support barlow lens
        -Support swapping relay lens
        -Correcting a systematic offset

        In the future we will probably also support per objective correction
        """
        assert magnification
        for objective in self.objectives.values():
            # Higher magnification means each pixel sees fewer um
            objective["um_per_pixel"] /= magnification
            # Similarly field of view is reduced
            objective["x_view"] /= magnification
            objective["y_view"] /= magnification
