from uscope.config import get_bc, get_usc
from uscope.motion.plugins import get_motion_hal, configure_motion_hal
from uscope.kinematics import Kinematics
from uscope.imager import gst
import threading
import copy
from collections import OrderedDict
"""
CLI capable
Do not use any Qt concepts?
    Could consider signals / slots w/o GUI though
"""
"""
Initialization passes:
-Create basic objects
-Configure objects

Two phases are required as some parameters depend on others
Ex: timing parameters are generated based on misc factors
"""


# Graceful stop requested
class MicroscopeStop(Exception):
    pass


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
        um_per_pixel_raw_1x = self.microscope.usc.imager.um_per_pixel_raw_1x()
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

    def get_config(self, name):
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


class StopEvent:
    def __init__(self, microscope):
        self.event = threading.Event()
        self.microscope = microscope

    def poll(self):
        if self.event.is_set():
            raise MicroscopeStop()

    def __enter__(self):
        self.microscope.stop_register_event(self.event)
        return self

    def __exit__(self, *args):
        self.microscope.stop_unregister(self.event)


class Microscope:
    def __init__(self, log=None, configure=True, **kwargs):
        self.bc = None
        self.usc = None
        self.imager = None
        self.motion = None
        self.kinematics = None

        if log is None:
            log = print
        self._log = log

        self.init(**kwargs)
        if configure:
            self.configure()
        self.stops = {}

    def log(self, msg):
        self._log(msg)

    def init(
        self,
        bc=None,
        usc=None,
        name=None,
        imager=None,
        make_imager=True,
        kinematics=None,
        make_kinematics=True,
        motion=None,
        make_motion=True,
        joystick=None,
        make_joystick=True,
        imager_cli=False,
        auto=True,
    ):
        if bc is None:
            bc = get_bc()
        self.bc = bc

        if usc is None:
            usc = get_usc(name=name)
        self.usc = usc

        if not auto:
            make_motion = False
            make_imager = False
            make_kinematics = False
            make_joystick = False

        if imager is None and imager_cli and make_imager:
            imager = gst.get_cli_imager_by_config(usc=self.usc,
                                                  microscope=self)
        self.imager = imager
        if self.imager is not None:
            self.imager.microscope = self

        if motion is None and make_motion:
            motion = get_motion_hal(usc=self.usc,
                                    microscope=self,
                                    log=self.log)
        self.motion = motion
        if self.motion is not None:
            self.motion.microscope = self

        if kinematics is None and make_kinematics:
            kinematics = Kinematics(
                microscope=self,
                log=self.log,
            )
        self.kinematics = kinematics
        """
        if joystick is None and make_joystick:
            try:
                joystick = Joystick(ac=self.ac)
            except JoystickNotFound:
                pass
        """
        self.joystick = joystick

    def configure(self):
        if self.motion:
            # self.motion.configure()
            configure_motion_hal(self)
        if self.imager:
            self.imager.configure()
        if self.kinematics:
            self.kinematics.configure()
        if self.joystick:
            self.joystick.configure()

    def get_planner(self, pconfig, out_dir):
        raise Exception("fixme")

    def has_z(self):
        return "z" in self.motion.axes()

    def set_motion(self, motion):
        self.motion = motion

    def set_imager(self, imager):
        self.imager = imager

    def set_kinematics(self, kinematics):
        self.kinematics = kinematics

    def stop(self):
        """
        Stop all operations on the system:
        -Cancel planner
        -Stop running script
        -Stop movement
        """
        for stop in self.stops.values():
            stop()

    def stop_register(self, hashable, function):
        """
        Stop functions must be thread safe: they can be called from any context
        """
        assert hashable not in self.stops
        self.stops[hashable] = function

    def stop_register_event(self, event):
        """
        Register a threading.Event() that will be set when requested
        Once the other side handles the stop it should unset the event
        """
        def stop():
            event.set()

        self.stop_register(event, stop)

    def stop_unregister(self, hashable):
        del self.stops[hashable]

    def get_objectives(self):
        return MicroscopeObjectives(self)


def get_cli_microscope(name=None):
    usc = get_usc(name=name)
    return Microscope(usc=usc, imager_cli=True)


def get_gui_microscope(name=None):
    usc = get_usc(name=name)
    return Microscope(usc=usc, imager_gui=True)


def get_microscope_for_motion(name=None):
    """
    Create a microscope for given microscope configuration without the imager
    """
    return Microscope(name=name, make_motion=True, make_imager=False)


def get_microscope_for_imager(name=None):
    """
    Create a microscope for given microscope configuration without the motion
    """
    return Microscope(name=name, make_motion=False, make_imager=True)
