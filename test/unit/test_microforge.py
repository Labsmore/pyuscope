#!/usr/bin/env -S uv run
"""
New Microscope Wizard (./app/microforge.py) config generation
No hardware is touched: generated configs are loaded with hardware=False

Run with:
    ./test/unit/test_microforge.py
"""

import os
import shutil
import tempfile

# Keep test data out of the working tree
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-microforge-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")

import json
import json5
import unittest
from unittest import mock

from uscope import microforge as mf
from uscope import config as uconfig
from uscope.microscope import Microscope

# Generated configs go here instead of the real configs/
_CONFIGS_DIR = os.path.join(_TMP_ROOT, "configs")
os.makedirs(_CONFIGS_DIR)
shutil.copy(os.path.join(uconfig.get_configs_dir(), "objectives.j5"),
            _CONFIGS_DIR)


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


def quiet_log(msg="", *args, **kwargs):
    pass


def plain(j):
    """OrderedDict / tuple => plain json types for comparison"""
    return json.loads(json.dumps(j))


class RenderTestCase(unittest.TestCase):
    def test_round_trip_all_sources(self):
        for source in mf.SOURCES:
            with self.subTest(source=source):
                params = mf.default_params("rt-" + source, source)
                self.assertEqual(mf.validate_params(params), [])
                text = mf.render_j5(params)
                self.assertIn("//Guess", text)
                self.assertEqual(plain(json5.loads(text)),
                                 plain(mf.build_config(params)))

    def test_comments_and_description(self):
        params = mf.default_params("desc")
        params["description"] = "Line one\nhas a */ in it"
        params["rc_pre_home"] = ["$100=800.000", "G10 L2 P1 X0 Y0 Z0"]
        params["objectives"] = [{
            "name": "Mystery 7X",
            "magnification": 7,
            "na": 0.2
        }]
        text = mf.render_j5(params)
        self.assertIn("Line one", text)
        self.assertIn("calibration", text.lower())
        usj = json5.loads(text)
        self.assertEqual(usj["motion"]["grbl"]["rc_pre_home"],
                         params["rc_pre_home"])
        self.assertEqual(usj["systems"][0]["objectives"][0]["name"],
                         "Mystery 7X")

    def test_toupcam_autodetect_wh(self):
        usj = mf.build_config(mf.default_params("tc", "gst-toupcamsrc"))
        self.assertNotIn("width", usj["imager"])
        self.assertNotIn("height", usj["imager"])

    def test_xy_drops_z(self):
        params = mf.default_params("xy")
        params["axes"] = "xy"
        params["scalars"] = {"x": 2.0, "y": 1.0, "z": 5.0}
        motion = mf.build_config(params)["motion"]
        self.assertEqual(motion["axes"], "xy")
        self.assertEqual(dict(motion["scalars"]), {"x": 2.0})
        self.assertNotIn("zmin", motion["soft_limits"])

    def test_all_one_scalars_omitted(self):
        usj = mf.build_config(mf.default_params("s"))
        self.assertNotIn("scalars", usj["motion"])

    def test_guesses(self):
        self.assertEqual(mf.scalar_guess(5440, 3648), 0.5)
        self.assertEqual(mf.scalar_guess(1920, 1080), 1.0)
        self.assertEqual(mf.scalar_guess(None, None), 1.0)
        self.assertEqual(mf.um_per_pixel_guess(None), mf.UM_PER_PIXEL_DEFAULT)
        self.assertAlmostEqual(mf.um_per_pixel_guess(2.4, 0.5), 4.8)
        self.assertAlmostEqual(mf.um_per_pixel_guess(2.0, 1.0, 3840, 1280),
                               6.0)
        db = uconfig.ObjectiveDB(os.path.join(_CONFIGS_DIR, "objectives.j5"))
        self.assertEqual(mf.tube_length_guess(mf.DEFAULT_OBJECTIVES_DB, db),
                         200)
        self.assertEqual(
            mf.tube_length_guess(["vendor: Olympus, model: NeoSPlan 10X"],
                                 db), 180)

    def test_validate(self):
        self.assertIsNone(mf.validate_name("my-scope_2.0"))
        for bad in ("", "a/b", "../x", "-x", "has space"):
            self.assertIsNotNone(mf.validate_name(bad), bad)
        params = mf.default_params("v", "gst-v4l2src")
        params["width"] = None
        params["height"] = None
        params["objectives_db"] = []
        params["soft_limits"]["xmin"] = 500.0
        errors = mf.validate_params(params)
        self.assertEqual(len(errors), 3, errors)

    def test_from_existing(self):
        for name in ("mock-grbl", "grbl", "ls-hvy-2", "lip-x1", "lip-vm1"):
            with self.subTest(name=name):
                params = mf.params_from_usj(mf.load_usj(name))
                params["name"] = name + "-copy"
                self.assertEqual(mf.validate_params(params), [])
                json5.loads(mf.render_j5(params))
        params = mf.params_from_usj(mf.load_usj("ls-hvy-2"))
        self.assertEqual(params["source"], "gst-toupcamsrc")
        self.assertEqual((params["width"], params["height"]), (5440, 3648))
        self.assertEqual(params["axes"], "xy")
        self.assertEqual(params["limit_switches"], False)
        self.assertEqual(params["infinity_tube_length"], 180)
        self.assertAlmostEqual(params["um_per_pixel_raw_1x"], 3.262867647)
        self.assertTrue(params["rc_pre_home"])
        # image_width_1x_mm => um_per_pixel_raw_1x
        params = mf.params_from_usj(mf.load_usj("lip-vm1"))
        self.assertAlmostEqual(params["um_per_pixel_raw_1x"],
                               6.6 * 1000 / 1280)


class LoadTestCase(unittest.TestCase):
    """Load generated configs with the real config machinery"""
    def load(self, params):
        out_dir = os.path.join(_CONFIGS_DIR, params["name"])
        fn = mf.write_config(params, out_dir)
        self.assertEqual(fn, os.path.join(out_dir, "microscope.j5"))
        with mock.patch.object(uconfig, "get_configs_dir",
                               return_value=_CONFIGS_DIR):
            microscope = Microscope(name=params["name"],
                                    hardware=False,
                                    virtual=True,
                                    log=quiet_log)
            objectives = microscope.get_objectives()
        self.assertEqual(microscope.usc.get_config_dir(), out_dir)
        return microscope, objectives

    def test_load_videotestsrc(self):
        params = mf.default_params("mf-videotestsrc", "gst-videotestsrc")
        params["port"] = "mock"
        microscope, objectives = self.load(params)
        usc = microscope.usc
        self.assertEqual(usc.imager.source(), "gst-videotestsrc")
        self.assertEqual(usc.imager.raw_wh(), (800, 750))
        self.assertEqual(usc.imager.scalar(), 1.0)
        self.assertEqual(usc.motion.hal(), "grbl-ser")
        self.assertEqual(usc.motion.axes(), set("xyz"))
        self.assertEqual(usc.motion.soft_limits(), {
            "x": (0.0, 100.0),
            "y": (0.0, 100.0),
            "z": (-25.0, 0.0)
        })
        self.assertEqual(usc.motion.backlash(nominal=True), {
            "x": 0.05,
            "y": 0.05,
            "z": 0.05
        })
        self.assertEqual(usc.optics.um_per_pixel_raw_1x_nominal(),
                         mf.UM_PER_PIXEL_DEFAULT)
        self.assertEqual(usc.kinematics.tsettle_motion_base(), 0.25)
        self.assertEqual(len(objectives.names()), 3)
        for name in objectives.names():
            self.assertGreater(objectives.get_config(name)["um_per_pixel"],
                               0)

    def test_load_toupcam_custom_objectives(self):
        params = mf.default_params("mf-toupcam", "gst-toupcamsrc")
        params.update({
            "width": 5440,
            "height": 3648,
            "native_pixel_pitch_um": 2.4,
            "scalar": 0.5,
            "hal": "mock",
            "axes": "xy",
            "objectives_db": ["vendor: Olympus, model: NeoSPlan 10X"],
            "objectives": [{
                "name": "Custom 3X",
                "magnification": 3,
                "na": 0.08
            }],
        })
        params["um_per_pixel_raw_1x"] = mf.um_per_pixel_guess_params(params)
        microscope, objectives = self.load(params)
        usc = microscope.usc
        self.assertEqual(usc.imager.raw_wh(), (5440, 3648))
        self.assertEqual(usc.motion.hal(), "mock")
        self.assertEqual(usc.motion.axes(), set("xy"))
        self.assertNotIn("z", usc.motion.soft_limits())
        self.assertAlmostEqual(usc.optics.um_per_pixel_raw_1x_nominal(), 2.4)
        self.assertEqual(len(objectives.names()), 2)

    def test_toupcam_no_wh(self):
        params = mf.default_params("mf-toupcam-auto", "gst-toupcamsrc")
        out_dir = os.path.join(_CONFIGS_DIR, params["name"])
        mf.write_config(params, out_dir)
        with mock.patch.object(uconfig, "get_configs_dir",
                               return_value=_CONFIGS_DIR):
            microscope = Microscope(name=params["name"],
                                    hardware=False,
                                    virtual=True,
                                    log=quiet_log)
        # Filled in from the camera at runtime
        with self.assertRaises(ValueError):
            microscope.usc.imager.raw_wh()
        objectivejs = microscope.usc.get_uncalibrated_objectives()
        uconfig.ObjectiveDB(os.path.join(
            _CONFIGS_DIR, "objectives.j5")).set_defaults_list(objectivejs)
        self.assertEqual([o["magnification"] for o in objectivejs],
                         [5, 10, 20])


if __name__ == "__main__":
    unittest.main()
