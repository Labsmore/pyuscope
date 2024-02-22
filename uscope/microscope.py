from uscope.config import get_bc, get_usc
from uscope.motion.plugins import get_motion_hal, configure_motion_hal
from uscope.kinematics import Kinematics
from uscope.objective import MicroscopeObjectives
from uscope.imager import gst
from uscope.motion.grbl import grbl_mconfig
import threading
import os
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


class MicroscopeStatistics:
    def __init__(self, microscope):
        self.microscope = microscope
        self.getjs = []

    def add_getj(self, callback):
        self.getjs.append(callback)

    def getj(self):
        """
        Return a JSON compatible object snapshotting all recorded statistics
        Intended for profiling / error handling
        This function should be thread safe
        """
        ret = {}
        for callback in self.getjs:
            callback(ret)
        return ret


"""
By default do not assume Microscope is thread safe
In Argus it is owned by the GUi thread
However, there are major thread safe subsystems by calling associated wrappers
"""


class Microscope:
    def __init__(self, log=None, configure=True, hardware=True, **kwargs):
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
        self.hardware = hardware
        self._last_cachej = None
        #self.instruments = {}
        self.subsystems = {}
        # General purpose data that is passed to Planner and other things
        self.calibration = {}

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
        serial=None,
        mconfig={},
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

        # Name may be auto selected from GRBL, etc
        # Must be done early and in special / careful nammer

        if mconfig is None:
            mconfig = {}
        if name:
            mconfig["name"] = name
        if serial:
            mconfig["serial"] = serial
        if not mconfig.get("name") or not mconfig.get("serial"):
            self.default_microscope_config(mconfig, overwrite=False)
            print("using microscope auto config", mconfig)
        self.name = mconfig["name"]
        self._serial = mconfig.get("serial", None)
        if usc is None:
            usc = get_usc(microscope=self)
        self.usc = usc

        self.objectives = None

        if not auto or virtual:
            make_motion = False
            make_imager = False
            make_kinematics = False
            make_joystick = False

        self.statistics = MicroscopeStatistics(self)

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

    def default_microscope_config(self, mconfig={}, overwrite=False):
        if not mconfig.get("name"):
            name = os.getenv("PYUSCOPE_MICROSCOPE")
            if name:
                mconfig["name"] = name

        # TODO: put mock flag in config file
        is_mock = mconfig.get("name") in ("mock", "mock-grbl")
        # TODO: if we want to revive touptek s/n here we could
        if not mconfig.get("name") or not mconfig.get(
                "serial") and self.hardware and not is_mock:
            # Try to do aggressive GRBL auto-config
            grbl_mconfig(mconfig, overwrite=overwrite)

        # Default to mock GRBL
        if not mconfig.get("name"):
            print(
                "WARNING: failed to find a microscope. Defaulting to mock-grbl"
            )
            # raise Exception("Must specify microscope")
            # Microscope of last resort
            # Generally mock-grbl is better than mock
            mconfig["name"] = "mock-grbl"
            is_mock = True
        if is_mock:
            mconfig["serial"] = "1234"
        if not mconfig.get("serial"):
            print("WARNING: no microscope serial number. Files may conflict")

        return mconfig

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
        Currently the same as the config file name
        """
        return self.name

    def config_name(self):
        """
        Return the config name
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

    def model_serial_string(self):
        """
        Used for various config items
        """
        if self._serial:
            return self.name + "_sn-" + self._serial
        else:
            return self.name

    """
    Argus hack bindings
    See common.py
    """

    def jog_fractioned_lazy(self):
        assert 0, "hack: overriden by GUI"

    def jog_cancel(self):
        assert 0, "hack: overriden by GUI"

    def motion_stop(self):
        assert 0, "hack: overriden by GUI"

    def get_jog_controller(self):
        assert 0, "hack: overriden by GUI"

    def image_save_extension(self):
        assert 0, "hack: overriden by GUI"

    def get_active_objective(self):
        assert 0, "hack: overriden by GUI"

    def set_active_objective(self, objective):
        assert 0, "hack: overriden by GUI"

    '''
    def get_instrument(self, name):
        return self.instruments[name]

    def get_instrument_default(self, name, default=None):
        return self.instruments.get(name, default)

    def add_instrument(self, instrument, name=None):
        if not name:
            name = instrument.name()
        assert name not in self.instruments
        self.instruments[name] = instrument

        # Load configuration
        assert self._last_cachej is not None
        instrumentsj = self._last_cachej.get("instruments", {})
        instancesj = instrumentsj.get("instances", {})
        instrument.cache_load(instancesj.get(name, {}))
    '''

    def get_subsystem(self, name):
        return self.subsystems[name]

    def get_subsystem_default(self, name, default=None):
        return self.subsystems.get(name, default)

    def add_subsystem(self, subsystem, name=None):
        if not name:
            name = subsystem.name()
        assert name not in self.subsystems
        self.subsystems[name] = subsystem

        # Load configuration
        # FIXME: subsystem getting added before init
        # need to make sure this gets deferred later for early adds
        if self._last_cachej is not None:
            subsystemsj = self._last_cachej.get("subsystems", {})
            subsystem.cache_load(subsystemsj.get(name, {}))

    def subsystem_functions(self):
        ret = {}
        for name, subsystem in self.subsystems.items():
            ret[name] = subsystem.functions()
        return ret

    def subsystem_function_ts(self, subsystem, function, kwargs):
        self.subsystems[subsystem].function_ts(function, kwargs)

    def cache_save(self, cachej):
        """
        Argus hook to save configuration cache
        In the future we might reverse such that we call argu
        """
        '''
        instrumentsj = cachej.setdefault("instruments", {})
        # TODO: might have other config here (ex: paths)
        instancesj = instrumentsj.setdefault("instances", {})
        for name, instrument in self.instruments.items():
            instancesj[name] = instrument.cache_save()
        '''
        subsystemsj = cachej.setdefault("subsystems", {})
        for name, subsystem in self.subsystems.items():
            subj = subsystemsj.get(name, {})
            subsystem.cache_save(cachej, subj)
            if len(subj) == 0:
                if name in subsystemsj:
                    del subsystemsj[name]
            else:
                self.subsystems[name] = subj
        self._last_cachej = cachej

    def cache_load(self, cachej):
        """
        Argus hook to load configurationcache
        In the future we might reverse such that we call argu
        """
        self._last_cachej = cachej
        '''
        instrumentsj = cachej.get("instruments", {})
        # TODO: might have other config here (ex: paths)
        instancesj = instrumentsj.get("instances", {})
        for name, instrument in self.instruments.items():
            instrument.cache_load(instancesj.get(name, {}))
        '''
        subsystemsj = cachej.setdefault("subsystems", {})
        for name, subsystem in self.subsystems.items():
            subsystem.cache_load(cachej, subsystemsj.get(name, {}))

    def cache_sn_save(self, cachej):
        """
        Argus hook to save configuration cache
        In the future we might reverse such that we call argu
        """
        '''
        instrumentsj = cachej.setdefault("instruments", {})
        # TODO: might have other config here (ex: paths)
        instancesj = instrumentsj.setdefault("instances", {})
        for name, instrument in self.instruments.items():
            instancesj[name] = instrument.cache_sn_save()
        '''
        subsystemsj = cachej.setdefault("subsystems", {})
        for name, subsystem in self.subsystems.items():
            # Only add to JSON if actually used
            # Keeps structure lean but API easy to use
            subj = subsystemsj.get(name, {})
            subsystem.cache_sn_save(cachej, subj)
            if len(subj) == 0:
                if name in subsystemsj:
                    del subsystemsj[name]
            else:
                self.subsystems[name] = subj
        self._last_cachej = cachej

    def cache_sn_load(self, cachej):
        """
        Argus hook to load configurationcache
        In the future we might reverse such that we call argu
        """
        self._last_cachej = cachej
        self.calibration = cachej.get("calibration", {})
        '''
        instrumentsj = cachej.get("instruments", {})
        # TODO: might have other config here (ex: paths)
        instancesj = instrumentsj.get("instances", {})
        for name, instrument in self.instruments.items():
            instrument.cache_sn_load(instancesj.get(name, {}))
        '''
        subsystemsj = cachej.setdefault("subsystems", {})
        for name, subsystem in self.subsystems.items():
            subsystem.cache_sn_load(cachej, subsystemsj.get(name, {}))

    def system_status_ts(self):
        """
        Get info about various subsystems
        Thread safe
        """
        ret = {}
        self.imager.system_status_ts(ret, ret.setdefault("imager", {}))
        self.motion.system_status_ts(ret, ret.setdefault("motion", {}))

        # maybe instruments as subsystem would make more sense?
        '''
        instruments_j = ret.setdefault("instruments")
        for instrument in self.instruments.values():
            instrument.system_status_ts(instruments_j).setdefault(instrument.name(), {}))
        '''

        # Notably argus subsystem
        subsystemsj = ret.setdefault("subsystems", {})
        for name, subsystem in self.subsystems.items():
            subj = subsystemsj.get(name, {})
            subsystem.system_status_ts(ret, subj)
            if len(subj) == 0:
                if name in subsystemsj:
                    del subsystemsj[name]
            else:
                self.subsystems[name] = subj
        return ret


def get_cli_microscope(name=None):
    return Microscope(imager_cli=True, name=name)


def get_gui_microscope(name=None):
    return Microscope(imager_gui=True, name=name)


def get_mconfig(name=None, serial=None, mconfig=None):
    if mconfig is None:
        mconfig = {}
    if name:
        mconfig["name"] = name
    if serial:
        mconfig["serial"] = serial
    return mconfig


# used by stitcher
# also used by get_microscope_info.py
def get_virtual_microscope(mconfig=None):
    return Microscope(auto=False,
                      configure=False,
                      hardware=False,
                      mconfig=mconfig)


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
