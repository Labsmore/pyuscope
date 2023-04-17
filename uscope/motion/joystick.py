import math
try:
    import pygame
except ImportError:
    pygame = None

class Joystick(object):
    _default_axis_threshold = 0.5

    def __init__(self, parent):
        self.parent = parent
        pygame.init()
        pygame.joystick.init()
        self.joystick_cfg = self.parent.bc.argus_joystick_cfg()
        self.joystick = pygame.joystick.Joystick(self.joystick_cfg.get('device_num', 0))
        self.joystick_fn_map = self.joystick_cfg.get('fn_map', {})

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

    def _move(self, axis, val):
        self.parent.motion_thread.jog({axis: val})

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

    def axis_move_x(self, id, threshold=_default_axis_threshold):
        val = self.joystick.get_axis(id)
        if abs(val) < threshold:
            return
        val = math.copysign(self.parent.mainTab.motion_widget.slider.get_jog_val(), val)
        self._move('x', val)

    def axis_move_y(self, id, threshold=_default_axis_threshold):
        val = self.joystick.get_axis(id)
        if abs(val) < threshold:
            return
        val = math.copysign(self.parent.mainTab.motion_widget.slider.get_jog_val(), val)
        self._move('y', val)

    def hat_move_z(self, id, idx):
        val = self.joystick.get_hat(id)[idx]
        # If hat is not pressed skip
        if val == 0:
            return
        # Hat values are either -1 or 1, so we can just multiply for sign
        val = val * self.parent.mainTab.motion_widget.slider.get_jog_val()
        self._move('z', val)

    def btn_capture_image(self, id):
        if self.joystick.get_button(id):
            self.parent.mainTab.snapshot_widget.take_snapshot()

    def axis_set_jog_slider_value(self, id, invert=True):
        # The min and max values of the joystick range (it is not
        # always -1 to 1)
        val_min = -1.0
        val_max = 1.0
        # We need to convert the joystick range to what the set jog function
        # expects, which is 0 to 1.
        new_min = 0.0
        new_max = 1.0
        val = -self.joystick.get_axis(id) if invert else self.joystick.get_axis(id)
        old_range = val_max - val_min
        new_range = new_max - new_min
        new_value = (((val - val_min) * new_range) / old_range) + new_min
        self.parent.mainTab.motion_widget.slider.set_jog_slider(new_value)

