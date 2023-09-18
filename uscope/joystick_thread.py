from uscope.joystick import Joystick, JoystickNotFound

import threading
import time
import traceback
import queue


class JoystickThreadBase:
    def __init__(self, microscope):
        self.joystick = None
        self.queue = queue.Queue()
        self.running = threading.Event()
        self.running.set()
        try:
            self.joystick = Joystick(microscope=microscope)
        except JoystickNotFound:
            raise JoystickNotFound()

    def log_info(self):
        self.log("Joystick")
        self.log(f"  Name: {self.joystick.joystick.name}")
        self.log(f"  Axes: {self.joystick.joystick.numaxes}")
        self.log(f"  Trackballs: {self.joystick.joystick.numballs}")
        self.log(f"  Hats: {self.joystick.joystick.numhats}")
        self.log(f"  Buttons: {self.joystick.joystick.numbuttons}")

    def log(self, msg):
        print(msg)

    def shutdown(self):
        self.running.clear()

    def run(self):
        while self.running:
            try:
                time.sleep(0.2)
                # It is important to check that the button is both enabled and
                # active before performing actions. This allows us to preserve
                # state by disabling and enabling the button only during scans.
                self.joystick.execute()
            except Exception as e:
                self.log('WARNING: joystick thread crashed: %s' % str(e))
                traceback.print_exc()


class SimpleJoystickThread(JoystickThreadBase, threading.Thread):
    pass
