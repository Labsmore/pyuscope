import math
import pygame
from uscope import config
import json5
from collections import OrderedDict
import os


class JoystickNotFound(Exception):
    pass


"""
Configuration more related to machine / user than a specific microscope
"""
"""
If the joystick is plugged in use this
But by default don't require it
maybe add an option to fault if not found


Sample uscope.j5 entry:
{
    ...
    "joystick": {
        //(float) in seconds, to query/run the joystick actions
        "scan_secs": 0.2
        "fn_map": {
            //"func_from_joystick_file": dict(keyword args for the function),
            'axis_set_jog_slider_value': {'id': 3},
            'btn_capture_image': {'id': 0},
            'axis_move_x': {'id': 0},
            'axis_move_y': {'id': 1},
            'hat_move_z': {'id': 0, 'idx': 1}
          }
      }
}

The function names specified in "fn_map" are the functions to be
mapped/triggered, and correspond to a function exposed within
uscope.motion.joystick. The value for the function name is
a dictionary of key:vals corresponding to the required arguments
for the chosen function.

See the docs in uscope/joystick.py for available functions
"""


# Originally this was in config
# given close dependence between calibration and configure
# moved here and making distinct object
class JoystickConfig:
    def __init__(self, jbc={}):
        self.jbc = jbc
        # Early configurable not related to model or
        # needed to get model
        self._scan_secs = self.jbc.get("scan_secs", 0.2)
        self._device_number = self.jbc.get("device_number", 0)
        self._volatile_scalars = None
        self._function_map = None
        self.model = None
        self.guid = None

    # Late configuration after model is selected
    def configure(self, model, guid):
        with open(os.path.join(config.get_configs_dir(), "joystick.j5")) as f:
            jdb = json5.load(f, object_pairs_hook=OrderedDict)

        self.model = model
        self.guid = guid
        self._function_map = self.make_function_map(model, guid, jdb)
        self._volatile_scalars = {}
        """
        # Things that can take a calibration constant
        self._function_unsigned_axes = set()
        self._function_signed_axes = set()
        for function in self._function_map:
            if "axis_move_" in function or "hat_move_" in function:
                self._function_signed_axes.add(function)
            # A 0 to 100 scalar
            # No sign convention as min and max is explicit
            if "axis_set_jog_slider_value" == function:
                self._function_unsigned_axes.add(function)
        """

    # User scalars: user supplied calibration
    # Non-volatile / can be saved

    def reset_user_scalars(self):
        for config in self._function_map.values():
            config["user_scalar"] = 1.0

    def set_user_scalars(self, scalars):
        # External tuning not implicit to configuration
        # ex: if user wants to increase or decrease sensitivity
        # self._user_scalars = scalars
        for function, val in scalars:
            config = self._function_map[function]
            config["user_scalar"] = val

    def device_number(self):
        return self._device_number

    def set_volatile_scalars(self, volatile_scalars):
        self._volatile_scalars = volatile_scalars

    def volatile_scalars(self):
        return self._volatile_scalars

    def scan_secs(self):
        # assert 0, "hard coded loop time assumptions? tread carefully"
        return self._scan_secs

    def function_map(self):
        """
        Return the raw function map structure
        """
        return self._function_map

    def process_axis(self, function, axis, value):
        config = self._function_map[function]
        threshold = config.get("threshold")
        if threshold:
            if abs(value) < threshold:
                return 0.0
        return value * config.get("scalar", 1.0) * config.get(
            "user_scalar", 1.0) * self._volatile_scalars.get(axis, 1.0)

    def process_hat(self, function, axis, value):
        config = self._function_map[function]
        # Hat values are either -1 or 1, so we can just multiply for sign
        return value * config.get("scalar", 1.0) * config.get(
            "user_scalar", 1.0) * self._volatile_scalars.get(axis, 1.0)

    def make_function_map(self, model, guid, jdb):
        # If user manually specifies just take that
        ret = self.jbc.get("function_map", None)
        if ret:
            return ret
        # Auto detection requires model
        if guid is None:
            raise Exception("guid required for auto detection")

        model = model.upper()
        joystick_config = jdb.get(guid)
        if joystick_config is None:
            raise ValueError(
                f"Unsupported joystick GUID {guid} ({model}). Consider filing a bug and/or submitting a PR to joystick.j5"
            )

        # Some models have multiple names
        # should ideally only be one level of indirection here
        while True:
            alias = joystick_config.get("alias")
            if alias:
                try:
                    joystick_config = jdb[alias]
                except KeyError:
                    raise Exception(f"bad alias {alias}")
            break

        return joystick_config["function_map"]


# FIXME: low level joystick object should not depend on Argus
class Joystick:
    def __init__(self, microscope):
        self.microscope = microscope
        self.was_jogging = False
        pygame.init()
        pygame.joystick.init()

        self.config = JoystickConfig(
            jbc=self.microscope.bc.j.get("joystick", {}))
        try:
            self.joystick = pygame.joystick.Joystick(
                self.config.device_number())
        except pygame.error:
            raise JoystickNotFound()

        # This init is required by some systems.
        pygame.joystick.init()
        self.joystick.init()
        model = self.joystick.get_name()
        try:
            guid = self.joystick.get_guid()
        except AttributeError:
            raise ImportError(
                "require pygame 2.0.0dev11 or later. try: sudo pip3 install pygame --upgrade"
            )
        print(
            f"pygame version {pygame.version.ver} detected GUID {guid} ({model})"
        )
        self.config.configure(model, guid)
        # version 2.5.2
        # joystick Logitech Extreme 3D

    def configure(self):
        # 0.2 default
        # self._jog_fractioned_period = config.get_bc().joystick.scan_secs()
        # self._jog_fractioned_period = 0.2
        self.jog_controller = self.microscope.get_jog_controller(period=0.2)

    def execute(self):
        # Get events and perform actions
        pygame.event.get()
        # Run through all the mapped funcs specified in the config
        # Expected format of config is:
        #     dict(fn_name(dict(fn_args))
        # Call the fn with provided args

        # jogs across multiple axes need to be issued together
        # otherwise it will override the previous jog
        self._jog_queue = {}
        for fn, config in self.config._function_map.items():
            getattr(self, fn)(**config["args"])
        self.jog_controller.update(self._jog_queue)
        self._jog_queue = None

    def debug_dump(self):
        pygame.event.get()
        print("Joystick debug dump")
        print("Joystick name: {}".format(self.joystick.get_name()))
        for i in range(self.joystick.get_numaxes()):
            print("Axis({}): {}".format(i, self.joystick.get_axis(i)))
        for i in range(self.joystick.get_numbuttons()):
            print("Button({}): {}".format(i, self.joystick.get_button(i)))
        for i in range(self.joystick.get_numhats()):
            print("Hat({}): {}".format(i, self.joystick.get_hat(i)))

    def _jog_add_queue(self, axis, val):
        # import time
        # hack: set in ac
        # self.ac.motion_thread.jog_lazy({axis: val})
        # print("jog joystick", time.time())
        # self.microscope.jog_fractioned_lazy({axis: val}, self._jog_fractioned_period)
        self._jog_queue[axis] = val

    # The following functions can be specified in the fn_map of the
    # joystick configuration files.
    #
    # They are separated into mappings for controller axis (axis_),
    # buttons (btn_) and hats (hat_).
    #
    # In the config file, you specify the function you want to
    # poll/trigger, and provide the necessary arguments for the
    # function. Common arguments:
    #   "id": (int) corresponds to the id of the button/hat/axis
    # that should trigger this function.
    #   "idx": (int) used by hats. sub-id of the hat's
    # axis, usually 0 or 1.
    #   "threshold": (float) used by axis, specifies the
    # the minimum threshold value of the axis before we execute
    # the function.
    #
    # Note, because of the different data available if the trigger is
    # a button, a hat or an axis, we could provide a different function
    # for each type (e.g. axis_move_x, btn_move_x_left and btn_move_x_right).
    # Not all functions need to be mapped to triggers, and potentially
    # multiple triggers could be mapped (use buttons or axis to move x).

    def axis_move_x(self, id):
        val = self.joystick.get_axis(id)
        val = self.config.process_axis("axis_move_x", "x", val)
        self._jog_add_queue("x", +val)

    def axis_move_y(self, id):
        val = self.joystick.get_axis(id)
        val = self.config.process_axis("axis_move_y", "y", val)
        self._jog_add_queue("y", +val)

    def axis_move_z(self, id):
        val = self.joystick.get_axis(id)
        val = self.config.process_axis("axis_move_z", "z", val)
        self._jog_add_queue("z", +val)

    def hat_move_z(self, id, idx):
        val = self.joystick.get_hat(id)[idx]
        val = self.config.process_hat("hat_move_z", "z", val)
        self._jog_add_queue("z", +val)

    def btn_capture_image(self, id):
        if self.joystick.get_button(id):
            # self.ac.mainTab.snapshot_widget.take_snapshot()
            self.microscope.take_snapshot()

    def axis_set_jog_slider_value(self, id, invert=True):
        # The min and max values of the joystick range (it is not
        # always -1 to 1)
        val_min = -1.0
        val_max = 1.0
        # We need to convert the joystick range to what the set jog function
        # expects, which is 0 to 1.
        new_min = 0.0
        new_max = 1.0
        val = -self.joystick.get_axis(
            id) if invert else self.joystick.get_axis(id)
        old_range = val_max - val_min
        new_range = new_max - new_min
        new_value = (((val - val_min) * new_range) / old_range) + new_min
        # self.ac.mainTab.motion_widget.slider.set_jog_slider(new_value)
        self.microscope.set_jog_scale(new_value)
