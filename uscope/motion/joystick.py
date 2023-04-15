import math
try:
    import pygame
except ImportError:
    pygame = None

class Joystick(object):
    def __init__(self, parent):
        self.parent = parent
        pygame.init()
        pygame.joystick.init()
        self.threshold = 0.5
        # Use the first controller we find
        self.joystick = pygame.joystick.Joystick(0)

    def execute(self):
        # Get events and perform actions
        pygame.event.get()
        self.set_jog_slider_value()
        self.capture_image()
        self.move_x()
        self.move_y()
        self.move_z()

    def _move(self, axis, val):
        self.parent.motion_thread.jog({axis: val})

    def move_x(self):
        val = self.joystick.get_axis(0)
        if abs(val) < self.threshold:
            return
        val = math.copysign(self.parent.mainTab.motion_widget.slider.get_jog_val(), val)
        self._move('x', val)

    def move_y(self):
        val = self.joystick.get_axis(1)
        if abs(val) < self.threshold:
            return
        val = math.copysign(self.parent.mainTab.motion_widget.slider.get_jog_val(), val)
        self._move('y', val)

    def move_z(self):
        val = self.joystick.get_hat(0)[1]
        # If hat is not pressed skip
        if val == 0:
            return
        # Hat values are either -1 or 1, so we can just multiply for sign
        val = val * self.parent.mainTab.motion_widget.slider.get_jog_val()
        self._move('z', val)

    def capture_image(self):
        if self.joystick.get_button(0):
            self.parent.mainTab.snapshot_widget.take_snapshot()

    def set_jog_slider_value(self, invert=True):
        # The min and max values of the joystick range (it is not
        # always -1 to 1)
        val_min = -1.0
        val_max = 1.0
        # We need to convert the joystick range to what the set jog function
        # expects, which is 0 to 1.
        new_min = 0.0
        new_max = 1.0
        val = -self.joystick.get_axis(3) if invert else self.joystick.get_axis(3)
        old_range = val_max - val_min
        new_range = new_max - new_min
        new_value = (((val - val_min) * new_range) / old_range) + new_min
        self.parent.mainTab.motion_widget.slider.set_jog_slider(new_value)

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

