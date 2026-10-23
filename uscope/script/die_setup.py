"""
Automatic die (chip) panorama setup

Start with the die lower left corner in view: loosely in focus with the die
in the upper right part of the image and substrate in the lower left
The script then:
-Walks the die bottom and left edges to find the lower right and upper left corners
-Adds a border around the die (default 20% of the view)
-Samples auto exposure around the die and locks the median
-Autofocuses each XY3P corner
-Fills in the XY3P corners and optionally starts the scan

The die may be slightly rotated / skewed: XY3P scans a parallelogram

All moves are absolute: the motion layer applies the same backlash
compensation the planner does, so the corners are measured the way they will
be approached during the scan
"""

from uscope.scripting import ScriptingPlugin
from uscope.imagep.die_finder import (
    DieClassifier,
    DieFindError,
    Observation,
    die_summary,
    find_die,
    predict_ur,
    scan_corners,
    xy3p_corners,
)
from uscope.imager.autofocus import AutoStacker
from uscope.planner.planner_util import microscope_to_planner_config

import numpy as np
import statistics
import time

# label => (distance multiplier, step multiplier) as the Advanced tab
STACK_MODES = {
    "None": None,
    "Die normal": (1, 1),
    "Die double distance": (2, 2),
    "Die double steps": (1, 2),
}

AFTER_SET = "Set corners only"
AFTER_ASK = "Set corners, ask to start scan"


class Plugin(ScriptingPlugin):
    def input_config(self):
        return {
            "Border (fraction of view)": {
                "widget": "QLineEdit",
                "key": "border",
                "type": float,
                "default": "0.2",
            },
            "Max die size (mm)": {
                "widget": "QLineEdit",
                "key": "max_size",
                "type": float,
                "default": "30.0",
            },
            "Edge step (fraction of view)": {
                "widget": "QLineEdit",
                "key": "step",
                "type": float,
                "default": "0.6",
            },
            "Autofocus at start": {
                "widget": "QComboBox",
                "key": "autofocus_start",
                "values": ["Yes", "No"],
                "default": "Yes",
            },
            "Autofocus corners": {
                "widget": "QComboBox",
                "key": "autofocus_corners",
                "values": ["Yes", "No"],
                "default": "Yes",
            },
            "Exposure": {
                "widget": "QComboBox",
                "key": "exposure",
                "values": ["Sample median", "Sample minimum", "Keep current"],
                "default": "Sample median",
            },
            "Check upper right corner": {
                "widget": "QComboBox",
                "key": "check_ur",
                "values": ["Yes", "No"],
                "default": "Yes",
            },
            "After setup": {
                "widget": "QComboBox",
                "key": "after",
                "values": [AFTER_ASK, AFTER_SET],
                "default": AFTER_ASK,
            },
            "Scan focus stacking": {
                "widget": "QComboBox",
                "key": "stacking",
                "values": list(STACK_MODES.keys()),
                "default": "Die normal",
            },
            "Scan stack drift correction": {
                "widget": "QComboBox",
                "key": "drift",
                "values": ["Yes", "No"],
                "default": "Yes",
            },
            "Scan name": {
                "widget": "QLineEdit",
                "key": "name",
                "type": str,
                "default": "die",
            },
            "Show debug images": {
                "widget": "QComboBox",
                "key": "debug",
                "values": ["No", "Yes"],
                "default": "No",
            },
        }

    def run_test(self):
        vals = self.get_input()
        self.has_z = self.session.microscope.has_z()
        self.debug = vals["debug"] == "Yes"
        objective = self.get_objective_config()
        x_view = objective["x_view"]
        self.log("Die setup: objective %s, view %0.3f mm" %
                 (objective["name"], x_view))
        if vals["border"] < 0 or vals["step"] <= 0 or vals["step"] > 0.75:
            self.fail("Border must be >= 0 and step 0 to 0.75")

        if self.has_z and vals["autofocus_start"] == "Yes":
            self.log("Autofocusing start position")
            self.autofocus()

        # Edges are classified with the model trained on the first view
        # so exposure must not change during the search
        self.ae_restore = None
        if self.imager_get_auto_exposure():
            self.log("Locking exposure for the edge search")
            self.imager_set_auto_exposure(False)
            self.ae_restore = True

        start = self.position()
        im = self.image()
        self.mm_per_px = x_view / im.width
        view_wh = (x_view, x_view * im.height / im.width)
        obs = Observation(im, (start["x"], start["y"]), self.mm_per_px)
        self.show_debug(obs, "start")
        self.classifier = obs.classifier
        self.log("Die / background separation: %0.1f" %
                 self.classifier.separation)
        if self.classifier.separation < DieClassifier.MIN_SEPARATION:
            self.fail(
                "Can't tell die from background. Is the die lower left corner in view with the die in the upper right?"
            )

        try:
            corners = find_die(self.observe,
                               (start["x"], start["y"]),
                               view_wh,
                               step_frac=vals["step"],
                               max_size=vals["max_size"],
                               log=self.log)
        except DieFindError as e:
            self.log(f"Die search failed: {e}")
            self.log("Returning to start position")
            self.move_absolute(start)
            self.fail(str(e))

        summary = die_summary(corners)
        self.log(
            "Die: %0.3f x %0.3f mm, rotation %0.2f deg, skew %0.2f deg" %
            (summary["width"], summary["height"], summary["rotation"],
             summary["skew"]))
        if vals["check_ur"] == "Yes":
            self.check_ur(corners, view_wh)

        border = (vals["border"] * view_wh[0], vals["border"] * view_wh[1])
        xy3p = xy3p_corners(corners, view_wh, border)
        scan = scan_corners(corners, border)
        positions = {k: {"x": float(v[0]), "y": float(v[1])}
                     for k, v in xy3p.items()}
        # Can't fail later on a corner out of range
        for pos in positions.values():
            self.session.motion.check_valid_position(pos)

        if vals["exposure"] != "Keep current":
            self.sample_exposure(positions, corners,
                                 vals["exposure"] == "Sample minimum")
        elif self.ae_restore:
            self.log(
                "WARNING: auto exposure left off; panoramas need a fixed exposure"
            )
        # Don't turn auto exposure back on
        self.ae_restore = None

        if self.has_z:
            z = start["z"]
            for name in ("ul", "ll", "lr"):
                self.move_absolute(positions[name])
                if vals["autofocus_corners"] == "Yes":
                    self.log(f"Autofocusing {name}")
                    self.autofocus()
                    z = self.position()["z"]
                positions[name]["z"] = z

        for name in ("ll", "ul", "lr"):
            self.log("XY3P %s: %s" %
                     (name, self.position_format(positions[name])))
        self.planner_set_xy3p_corners(positions)
        self.move_absolute(positions["ll"])

        if vals["after"] == AFTER_ASK:
            self.ask_scan(vals, objective, positions, scan)
        self.log("Done")

    def cleanup(self):
        if getattr(self, "ae_restore", None):
            self.imager_set_auto_exposure(True)

    def observe(self, center):
        self.move_absolute({"x": float(center[0]), "y": float(center[1])})
        obs = Observation(self.image(),
                          center,
                          self.mm_per_px,
                          classifier=self.classifier)
        self.show_debug(obs, "view")
        return obs

    def show_debug(self, obs, title):
        if self.debug:
            self.show_image(
                obs.mask_image(), "%s %0.4f, %0.4f" %
                (title, obs.view.center[0], obs.view.center[1]))

    def check_ur(self, corners, view_wh):
        """
        XY3P assumes a parallelogram. Verify the fourth corner
        """
        ur = predict_ur(corners)
        self.log("Checking upper right corner near %0.4f, %0.4f" %
                 (ur[0], ur[1]))
        found = self.observe(ur).corner("ur")
        if found is None:
            self.log("WARNING: upper right corner not found")
            return
        err = ((found[0] - ur[0])**2 + (found[1] - ur[1])**2)**0.5
        self.log("  off by %0.4f mm (%0.1f%% of view)" %
                 (err, 100.0 * err / view_wh[0]))
        if err > 0.1 * view_wh[0]:
            self.log(
                "WARNING: die isn't a parallelogram. Scan may cut off the upper right"
            )

    def wait_auto_exposure(self, timeout=15.0, tolerance=0.03, samples=3):
        """
        Return the exposure once auto exposure stops changing
        """
        history = []
        tstart = time.time()
        while time.time() - tstart < timeout:
            self.sleep(0.5)
            history.append(self.imager_get_exposure())
            recent = history[-samples:]
            if len(recent) == samples and max(recent) <= min(recent) * (
                    1 + tolerance):
                break
        else:
            self.log("  WARNING: auto exposure didn't settle")
        return history[-1]

    def sample_exposure(self, positions, corners, use_min):
        if self.imager_get_auto_exposure() is None or self.imager_get_exposure(
        ) is None:
            self.log("Camera has no auto exposure: keeping current exposure")
            return
        ur = predict_ur(positions_xy(positions))
        center = (corners["lr"] + corners["ul"]) / 2
        points = [positions[k] for k in ("ll", "lr", "ul")]
        points += [{
            "x": float(ur[0]),
            "y": float(ur[1])
        }, {
            "x": float(center[0]),
            "y": float(center[1])
        }]
        self.log("Sampling exposure at %u points" % len(points))
        exposures = []
        self.imager_set_auto_exposure(True)
        try:
            for pos in points:
                self.move_absolute(pos)
                exposure = self.wait_auto_exposure()
                self.log("  %s: %s" % (self.position_format(pos), exposure))
                exposures.append(exposure)
        finally:
            self.imager_set_auto_exposure(False)
        if use_min:
            exposure = min(exposures)
        else:
            exposure = statistics.median(exposures)
        self.log("Locking exposure at %s (range %s to %s)" %
                 (exposure, min(exposures), max(exposures)))
        self.imager_set_exposure(exposure)

    def ask_scan(self, vals, objective, positions, scan):
        stack = STACK_MODES[vals["stacking"]]
        drift = bool(stack) and vals["drift"] == "Yes" and self.has_z
        text = "XY3P corners are set. Start the scan now?"
        text += "\n\nFocus stacking: %s" % vals["stacking"]
        if stack:
            text += "\nStack drift correction: %s" % ("Yes"
                                                      if drift else "No")
        text += "\n\nStitching / other Argus scan settings are not applied."
        text += " Answer No and press Go to scan with them"
        if self.prompt("Start scan?", text, ("Yes", "No")) != "Yes":
            return

        corners = {}
        for name in ("ll", "ul", "lr"):
            corner = {"x": float(scan[name][0]), "y": float(scan[name][1])}
            if "z" in positions[name]:
                corner["z"] = positions[name]["z"]
            corners[name] = corner
        pconfig = microscope_to_planner_config(self.session.microscope,
                                               objective=objective,
                                               corners=corners)
        if stack and self.has_z:
            params = AutoStacker(self.session.microscope).calc_die_parameters(
                objective, stack[0], stack[1])
            # +/- but always add the center plane
            pconfig["points-stacker"] = {
                "number": 1 + 2 * params["pm_steps"],
                "distance": 2 * params["pm_distance"],
            }
            if drift:
                pconfig["stacker-drift"] = {}
        self.log("Starting scan")
        ret = self.run_planner_hconfig({
            "pconfig": pconfig,
            "out_dir_config": {
                "dt_prefix": True,
                "user_basename": vals["name"] or "die",
            },
        })
        self.log("Scan result: %s" % (ret.get("result"), ))
        if ret.get("result") != "ok":
            self.fail("Scan failed")


def positions_xy(positions):
    return {
        k: np.array((v["x"], v["y"]))
        for k, v in positions.items()
    }
