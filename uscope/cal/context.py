"""Hardware/GUI facade used by Calibration.collect()."""

import time


class CalContext:
    def __init__(self,
                 plugin=None,
                 microscope=None,
                 log=None,
                 quality="full",
                 prompt_fn=None,
                 progress_fn=None):
        self.plugin = plugin
        if microscope is None and plugin is not None:
            microscope = plugin.session.microscope
        self.microscope = microscope
        self.log = log or (plugin.log if plugin is not None else print)
        self.quality = quality
        self.prompt_fn = prompt_fn
        self.progress_fn = progress_fn
        self.origin = None

    def check_running(self):
        if self.plugin is not None:
            self.plugin.check_running()

    def sleep(self, t):
        if self.plugin is not None:
            self.plugin.sleep(t)
        else:
            time.sleep(t)

    def progress(self, frac, text):
        self.log(text)
        if self.progress_fn:
            self.progress_fn(frac, text)

    def prompt(self, title, text, buttons=("OK", "Skip")):
        """Block until the user answers. Returns the chosen button label."""
        if self.prompt_fn:
            return self.prompt_fn(title, text, buttons)
        self.log("%s: %s" % (title, text))
        self.sleep(3)
        return buttons[0]

    def confirm_clear(self, seconds=5):
        ans = self.prompt("Clear the area",
                          "Make sure the stage path is clear, then continue.",
                          buttons=("Continue", "Cancel"))
        if ans == "Cancel":
            raise RuntimeError("User cancelled")
        if seconds:
            self.sleep(seconds)

    def pos(self):
        return self.plugin.pos()

    def move_absolute(self, pos, block=True):
        self.plugin.move_absolute(pos, block=block)

    def move_relative(self, pos, block=True):
        self.plugin.move_relative(pos, block=block)

    def image(self, **kwargs):
        return self.plugin.image(**kwargs)

    def image_captured(self, **kwargs):
        return self.plugin.image_captured(**kwargs)

    def autofocus(self, block=True):
        self.plugin.autofocus(block=block)

    def backlash_disable(self):
        self.plugin.backlash_disable()

    def backlash_enable(self):
        self.plugin.backlash_enable()

    def objective_config(self):
        if self.plugin is not None:
            return self.plugin.get_objective_config()
        name = None
        try:
            name = self.microscope.get_active_objective()
        except Exception:
            name = None
        if not name:
            return None
        return self.microscope.get_objectives().get_config(name)

    def objectives(self):
        if self.plugin is not None:
            return self.plugin.get_objectives_config()
        return self.microscope.get_objectives().get_full_config()

    def capture_format(self):
        if self.plugin is None:
            return None
        try:
            return self.plugin.imager_get_capture_format()
        except Exception:
            return None

    def set_capture_format(self, fmt):
        if fmt:
            self.plugin.imager_set_capture_format(fmt)

    def imager_wh(self):
        try:
            return self.microscope.usc.imager.raw_wh()
        except Exception:
            return None

    def has_z(self):
        try:
            return self.microscope.has_z()
        except Exception:
            return False

    def motion_epsilon(self):
        try:
            return self.microscope.motion.epsilon()
        except Exception:
            return {"x": 0.001, "y": 0.001, "z": 0.001}

    def save_origin(self):
        self.origin = self.pos()
        return self.origin

    def restore_origin(self):
        if self.origin is not None:
            try:
                self.move_absolute(self.origin)
            except Exception:
                self.log("WARNING: failed to restore origin")

    def wait_imaging_ok(self):
        if self.plugin is not None:
            self.plugin.wait_imaging_ok()

    def set_exposure(self, value):
        self.plugin.imager().set_exposure(value)

    def get_exposure(self):
        try:
            cap = self.plugin.image_captured(mode="raw")
            return cap.exposure()
        except Exception:
            try:
                return self.plugin.imager().get_exposure_cache()
            except Exception:
                return None

    def set_disp_property(self, name, value):
        self.plugin.imager_set_disp_property(name, value)

    def get_disp_properties(self):
        try:
            return self.plugin.imager_get_disp_properties()
        except Exception:
            return {}

    def show_image(self, im, title="Image"):
        if self.plugin is not None:
            self.plugin.show_image(im, title=title)
