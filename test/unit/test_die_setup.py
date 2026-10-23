#!/usr/bin/env -S uv run
"""
Dry run of uscope/script/die_setup.py against a headless mock microscope
whose camera renders a synthetic die at the stage position
"""

import os
import shutil
import tempfile

# Keep test data out of the working tree
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-die-setup-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

import unittest

import numpy as np
from PIL import Image

from uscope.imager.imager import CapturedImage, MockImager
from uscope.imager.properties import MockImagerProperties
from uscope.scripting import (HeadlessScriptHost, ScriptRunner,
                              input_defaults)
from uscope.session import HeadlessSession

from test_die_finder import SyntheticDie

REPO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_DIR, "uscope", "script", "die_setup.py")


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


class DieImager(MockImager):
    """
    Renders self.die at the current stage position
    Auto exposure: exposure follows a position dependent "ideal" value
    """
    def __init__(self, width, height):
        super().__init__(width=width, height=height)
        self.die = None
        self.exposure = 1000
        self.auto_exposure = False

    def ideal_exposure(self, pos):
        return int(1000 + 100 * pos["x"])

    def pos(self):
        return self.microscope.motion_thread.get_pos_cache()

    def get(self, **_kwargs):
        pos = self.pos()
        if self.auto_exposure:
            self.exposure = self.ideal_exposure(pos)
        im = self.die.render((pos["x"], pos["y"]))
        return CapturedImage(image=Image.fromarray(im))

    def _set_properties(self, vals):
        for k, v in vals.items():
            if k == "exposure":
                self.exposure = int(v)
            elif k == "auto_exposure":
                self.auto_exposure = bool(v)
            else:
                super()._set_properties({k: v})

    def _get_properties(self):
        ret = super()._get_properties()
        if self.auto_exposure:
            self.exposure = self.ideal_exposure(self.pos())
        ret["exposure"] = self.exposure
        ret["auto_exposure"] = self.auto_exposure
        return ret


class DieImagerProperties(MockImagerProperties):
    def get_exposure_disp_property(self):
        return "exposure"

    def get_auto_exposure_disp_property(self):
        return "auto_exposure"

    def auto_exposure_enabled(self):
        return self.session.imager.auto_exposure


class TestDieSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imager = DieImager(800, 750)
        cls.session = HeadlessSession(microscope_name="mock-grbl",
                                      imager=cls.imager,
                                      argus_state=False)
        cls.session.imager_properties = DieImagerProperties(cls.session)

    @classmethod
    def tearDownClass(cls):
        cls.session.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_TMP_ROOT)
        self.logs = []
        self.host = HeadlessScriptHost(image_dir=os.path.join(
            self.tmp, "images"),
                                       log_fn=self.logs.append)
        self.runner = ScriptRunner(session=self.session, host=self.host)
        self.objective = self.session.objective_config()
        self.x_view = self.objective["x_view"]
        self.mm_per_px = self.x_view / 800

    def setup_die(self, **kwargs):
        die = SyntheticDie(wh=(800, 750), mm_per_px=self.mm_per_px, **kwargs)
        self.imager.die = die
        self.imager.auto_exposure = False
        self.imager.exposure = 1000
        vw, vh = die.view_wh()
        start = die.ll + (0.15 * vw, 0.1 * vh)
        self.session.motion_thread.move_absolute(
            {
                "x": float(start[0]),
                "y": float(start[1])
            }, block=True)
        return die

    def run_script(self, overrides={}, timeout=300):
        plugin = self.runner.load(SCRIPT)
        input_ = input_defaults(plugin.input_config())
        # Autofocus on a flat synthetic image is slow and meaningless
        input_.update({
            "autofocus_start": "No",
            "autofocus_corners": "No",
        })
        input_.update(overrides)
        self.runner.run(input_)
        self.assertTrue(self.host.done_event.wait(timeout), "script hung")
        plugin.join(timeout=5)
        return plugin

    def expected_xy3p(self, die, border=0.2):
        vw, vh = die.view_wh()
        c = die.corners()
        u = (c["lr"] - c["ll"]) / np.linalg.norm(c["lr"] - c["ll"])
        v = (c["ul"] - c["ll"]) / np.linalg.norm(c["ul"] - c["ll"])
        bx, by = border * vw, border * vh
        return {
            "ll": c["ll"] - bx * u - by * v + (vw / 2, vh / 2),
            "lr": c["lr"] + bx * u - by * v + (-vw / 2, vh / 2),
            "ul": c["ul"] - bx * u + by * v + (vw / 2, -vh / 2),
        }

    def check_corners(self, die):
        got = self.session.planner_xy3p_corners
        self.assertIsNotNone(got, "\n".join(self.logs))
        expected = self.expected_xy3p(die)
        for name in ("ll", "lr", "ul"):
            p = np.array((got[name]["x"], got[name]["y"]))
            err = np.linalg.norm(p - expected[name])
            self.assertLess(err, 0.03 * self.x_view,
                            f"{name}: {p} vs {expected[name]}")
            self.assertIn("z", got[name])

    def test_rotated_die(self):
        self.session.planner_xy3p_corners = None
        die = self.setup_die(ll=(20.0, 30.0),
                             width=2.0,
                             height=1.5,
                             rotation=2.5,
                             skew=1.0)
        plugin = self.run_script({"after": "Set corners only"})
        self.assertTrue(plugin.succeeded(),
                        "%s\n%s" % (plugin.result_message, "\n".join(
                            self.logs)))
        self.check_corners(die)
        # Exposure: sampled with auto exposure then locked
        self.assertFalse(self.imager.auto_exposure)
        self.assertNotEqual(self.imager.exposure, 1000)
        self.assertTrue(any("Locking exposure at" in l for l in self.logs))
        self.assertTrue(any("off by" in l for l in self.logs), self.logs)

    def test_no_die(self):
        self.session.planner_xy3p_corners = None
        self.setup_die(ll=(20.0, 30.0))
        self.imager.die = SyntheticDie(ll=(50.0, 50.0),
                                       wh=(800, 750),
                                       mm_per_px=self.mm_per_px)
        start = self.session.motion_thread.get_pos_cache()
        plugin = self.run_script()
        self.assertFalse(plugin.succeeded())
        self.assertIsNone(self.session.planner_xy3p_corners)
        pos = self.session.motion_thread.get_pos_cache()
        self.assertAlmostEqual(pos["x"], start["x"], places=3)
        self.assertAlmostEqual(pos["y"], start["y"], places=3)

    def test_start_scan_declined(self):
        """
        Non-interactive host answers the prompt with the safe choice (No)
        """
        self.session.planner_xy3p_corners = None
        die = self.setup_die(ll=(20.0, 30.0), width=1.2, height=1.0)
        plugin = self.run_script({"exposure": "Keep current"})
        self.assertTrue(plugin.succeeded(), "\n".join(self.logs))
        self.check_corners(die)
        self.assertTrue(any("PROMPT Start scan?" in l for l in self.logs))
        self.assertFalse(any("Starting scan" in l for l in self.logs))

    def test_start_scan(self):
        self.session.planner_xy3p_corners = None
        die = self.setup_die(ll=(20.0, 30.0), width=1.2, height=1.0)
        self.host.assume_yes = True
        basename = "die_" + os.path.basename(self.tmp)
        plugin = self.run_script({
            "exposure": "Keep current",
            "name": basename,
        })
        self.assertTrue(plugin.succeeded(), "\n".join(self.logs))
        self.check_corners(die)
        scan_dirs = [
            d for d in os.listdir(self.session.scan_dir())
            if d.endswith(basename)
        ]
        self.assertEqual(len(scan_dirs), 1)
        out_dir = os.path.join(self.session.scan_dir(), scan_dirs[0])
        images = [fn for fn in os.listdir(out_dir) if fn.endswith(".jpg")]
        self.assertGreater(len(images), 4)
        with open(os.path.join(out_dir, "uscan.log")) as f:
            log = f.read()
        # Focus stacking on by default
        self.assertIn("XY3P", log)


if __name__ == "__main__":
    unittest.main()
