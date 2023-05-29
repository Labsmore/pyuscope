import math
import pygame


class JoystickNotFound(Exception):
    pass


# FIXME: low level joystick object should not depend on Argus
class Joystick:
    def __init__(self, microscope):
        self.microscope = microscope
        pygame.init()
        pygame.joystick.init()
        try:
            self.joystick = pygame.joystick.Joystick(
                self.microscope.bc.joystick.device_number())
        except pygame.error:
            raise JoystickNotFound()

        print("Joystick: detected %s" % (self.joystick.get_name(), ))
        # This init is required by some systems.
        pygame.joystick.init()
        self.joystick.init()
        self.joystick_fn_map = self.microscope.bc.joystick.function_map(
            model=self.joystick.get_name())
        self.axis_threshold = {
            "x": 0.1,
            "y": 0.1,
            "z": 0.1,
        }
        self.axis_scalars = {
            "x": 1.0,
            "y": 1.0,
            "z": 1.0,
        }
        self.hat_scalars = {
            "x": 1.0,
            "y": 1.0,
            "z": 1.0,
        }
        # Used to know when joystick is idle to cancel jogs
        self._last_skips = {}

    def set_axis_scalars(self, scalars):
        self.axis_scalars = scalars

    def set_hat_scalars(self, scalars):
        self.hat_scalars = scalars

    def configure(self):
        pass

    def execute(self):
        # Get events and perform actions
        pygame.event.get()
        # Run through all the mapped funcs specified in the config
        # Expected format of config is:
        #     dict(fn_name(dict(fn_args))
        # Call the fn with provided args
        for fn in self.joystick_fn_map:
            getattr(self, fn)(**self.joystick_fn_map[fn])

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

    def _jog(self, axis, val):
        # self.ac.motion_thread.jog_lazy({axis: val})
        self.microscope.jog_lazy({axis: val})

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

    def process_skip(self, axis, function, val):
        skip = abs(val) < self.axis_threshold[axis]
        last_skip = self._last_skips.get(function)
        self._last_skips[function] = skip
        if skip:
            # Need to cancel jog to clear queue
            if not last_skip:
                self.microscope.cancel_jog()
        return skip

    def axis_move_x(self, id):
        val = self.joystick.get_axis(id)
        if self.process_skip("x", "axis_move_x", val):
            return
        self._jog("x", self.axis_scalars["x"] * val)

    def axis_move_y(self, id):
        val = self.joystick.get_axis(id)
        if self.process_skip("y", "axis_move_y", val):
            return
        self._jog("y", self.axis_scalars["y"] * val)

    def axis_move_z(self, id):
        val = self.joystick.get_axis(id)
        if self.process_skip("z", "axis_move_z", val):
            return
        self._jog("z", self.axis_scalars["z"] * val)

    def hat_move_z(self, id, idx):
        val = self.joystick.get_hat(id)[idx]
        # If hat is not pressed skip
        if self.process_skip("z", "hat_move_z", val):
            return
        # Hat values are either -1 or 1, so we can just multiply for sign
        self._jog("z", self.hat_scalars["z"] * val)

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
