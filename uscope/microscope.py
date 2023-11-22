from uscope.config import get_bc, get_usc, default_microscope_name
from uscope.motion.plugins import get_motion_hal, configure_motion_hal
from uscope.kinematics import Kinematics
from uscope.objective import MicroscopeObjectives
from uscope.imager import gst
import threading
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
        # Thread safe version
        self._imager_ts = None
        self.motion = None
        # Thread safe version
        self._motion_ts = None
        self.kinematics = None
        # Thread safe version
        self._kinematics_ts = None

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
        virtual=False,
    ):
        if bc is None:
            bc = get_bc()
        self.bc = bc

        if usc is None:
            usc = get_usc(name=name)
        self.usc = usc
        self.name = default_microscope_name()
        self._serial = None

        self.objectives = None

        if not auto or virtual:
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

    def set_imager_ts(self, imager):
        self._imager_ts = imager

    def imager_ts(self):
        assert self._imager_ts
        return self._imager_ts

    def set_motion_ts(self, motion):
        self._motion_ts = motion

    def motion_ts(self):
        assert self._motion_ts
        return self._motion_ts

    def set_kinematics_ts(self, kinematics):
        self._kinematics_ts = kinematics

    def kinematics_ts(self):
        assert self._kinematics_ts
        return self._kinematics_ts

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
        if not self.objectives:
            self.objectives = MicroscopeObjectives(self)
        return self.objectives

    def model(self):
        """
        Return microscope model number
        Same as the config file name
        """
        return self.name

    def serial(self):
        """
        From GRBL
        May not be present and return None
        """
        return self._serial

    def set_serial(self, serial):
        self._serial = serial


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
