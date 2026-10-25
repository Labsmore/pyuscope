#!/usr/bin/env -S uv run
"""
Crop as fractions: pixel conversion, precedence, migrations
Pixel scale (um / pixel) following the camera resolution
"""

import glob
import json
import os
import shutil
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="pyuscope-crop-")
os.environ["PYUSCOPE_DATA_DIR"] = _TMP
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

from uscope import config as uconfig
from uscope.cal.store import CalEntry, CalStore
from uscope.config import USCImager
from uscope.imager import crop
from uscope.imager.plugins.gst_toupcamsrc import resolution as tres
from uscope.microscope import Microscope
from uscope.util import readj

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

# E3ISPM20000KPA modes
MODES = [(5440, 3648), (2736, 1824), (1824, 1216)]

# Crops as they were in pixels before conversion to fractions
CONFIG_CROP_PIXELS = {
    "lip-a2": {
        "top": 400,
        "bottom": 400,
        "left": 1296,
        "right": 1296
    },
    "lip-m1-beta": {
        "top": 500,
        "bottom": 500,
        "left": 1396,
        "right": 1396
    },
    "lip-m2": {
        "top": 200,
        "bottom": 200,
        "left": 1096,
        "right": 1096
    },
    "ls-hvy-1": {
        "top": 0,
        "bottom": 0,
        "left": 896,
        "right": 896
    },
    # Second (last) crop wins
    "ls-hvy-2": {
        "top": 224,
        "bottom": 224,
        "left": 1120,
        "right": 1120
    },
    "lip-vm1": {
        "top": 0,
        "bottom": 0,
        "left": 160,
        "right": 160
    },
    "lip-x1": {
        "top": 393,
        "bottom": 393,
        "left": 856,
        "right": 856
    },
}


class FakeBC:
    def timeout_scalar_scale(self, v):
        return v


class FakeMicroscope:
    def __init__(self, cal_store=None, objective=None):
        self.bc = FakeBC()
        self.cal_store = cal_store
        self.objective = objective

    def get_active_objective(self):
        return self.objective


def imager(j, **kwargs):
    return USCImager(j=j, microscope=FakeMicroscope(**kwargs))


class TestConvert(unittest.TestCase):
    def test_round(self):
        fractions = {"top": 0.1, "bottom": 0.1, "left": 0.25, "right": 0.125}
        self.assertEqual(crop.fractions_to_pixels(fractions, (5440, 3648)), {
            "top": 365,
            "bottom": 365,
            "left": 1360,
            "right": 680
        })
        self.assertEqual(crop.fractions_to_pixels(fractions, (2736, 1824)), {
            "top": 182,
            "bottom": 182,
            "left": 684,
            "right": 342
        })
        self.assertEqual(crop.fractions_to_pixels(fractions, (1824, 1216)), {
            "top": 122,
            "bottom": 122,
            "left": 456,
            "right": 228
        })

    def test_round_even(self):
        fractions = {"top": 0.1, "bottom": 0.1, "left": 0.25, "right": 0.125}
        for wh in MODES:
            pixels = crop.fractions_to_pixels(fractions, wh, even=True)
            exact = crop.fractions_to_pixels(fractions, wh)
            for k in crop.SIDES:
                self.assertEqual(pixels[k] % 2, 0, (wh, k))
                self.assertLessEqual(abs(pixels[k] - exact[k]), 1)
        # 364.8 => 364 (nearest even), 182.5 => 182
        self.assertEqual(
            crop.fractions_to_pixels(fractions, (5440, 3648),
                                     even=True)["top"], 364)
        self.assertEqual(
            crop.fractions_to_pixels({"left": 182.5 / 1824}, (1824, 1216),
                                     even=True)["left"], 182)

    def test_half_rounds_up(self):
        # No banker's rounding
        self.assertEqual(
            crop.fractions_to_pixels({"left": 0.5 / 1824}, (1824, 1216))["left"],
            1)

    def test_validate(self):
        self.assertEqual(crop.validate_fractions({"left": 0.1}), {
            "top": 0.0,
            "bottom": 0.0,
            "left": 0.1,
            "right": 0.0
        })
        for bad in ({
                "left": -0.1
        }, {
                "left": 1.0
        }, {
                "left": 0.5,
                "right": 0.5
        }, {
                "top": 0.6,
                "bottom": 0.4
        }, {
                "middle": 0.1
        }):
            with self.assertRaises(ValueError):
                crop.validate_fractions(bad)

    def test_pixels_round_trip(self):
        for wh in MODES:
            for p in range(0, wh[1] // 2, 7):
                pixels = {"top": p, "bottom": p, "left": p, "right": p}
                fractions = crop.pixels_to_fractions(pixels, wh)
                self.assertEqual(crop.fractions_to_pixels(fractions, wh),
                                 pixels)


class TestConfig(unittest.TestCase):
    def test_none(self):
        im = imager({"width": 5440, "height": 3648})
        self.assertIsNone(im.crop_fractions())
        self.assertIsNone(im.crop_tblr())
        self.assertEqual(im.cropped_wh(), (5440, 3648))

    def test_follows_resolution(self):
        j = {
            "width": 5440,
            "height": 3648,
            "crop_fractions": {
                "top": 0.1,
                "bottom": 0.1,
                "left": 0.25,
                "right": 0.25
            }
        }
        im = imager(j)
        self.assertEqual(im.cropped_wh(), (2720, 2918))
        tres.apply_to_imagerj(j, {
            "esize": 1,
            "width": 2736,
            "height": 1824
        },
                              max_r={
                                  "esize": 0,
                                  "width": 5440,
                                  "height": 3648
                              })
        self.assertEqual(im.crop_tblr(), {
            "top": 182,
            "bottom": 182,
            "left": 684,
            "right": 684
        })
        self.assertEqual(im.cropped_wh(), (1368, 1460))

    def test_raw_format_even(self):
        j = {
            "width": 5440,
            "height": 3648,
            "format": "GRAY16_LE",
            "crop_fractions": {
                "top": 0.1,
                "left": 0.25
            }
        }
        im = imager(j)
        self.assertTrue(im.crop_even())
        self.assertEqual(im.crop_tblr()["top"], 364)
        # Runtime capture format (Argus override) wins
        im.active_capture_format = "RGB"
        self.assertEqual(im.crop_tblr()["top"], 365)
        im.active_capture_format = "GRAY8"
        self.assertEqual(im.crop_tblr()["top"], 364)

    def test_crop_pixels_legacy(self):
        j = {
            "width": 5440,
            "height": 3648,
            "crop_pixels": {
                "top": 400,
                "bottom": 400,
                "left": 1296,
                "right": 1296,
            }
        }
        im = imager(j)
        self.assertEqual(im.crop_tblr(), j["crop_pixels"])
        # Authored for the configured size, not the camera max
        j = {
            "native_width": 3840,
            "native_height": 2160,
            "width": 1280,
            "height": 960,
            "crop_pixels": {
                "left": 160,
                "right": 160
            }
        }
        im = imager(j)
        self.assertEqual(im.crop_fractions()["left"], 0.125)
        self.assertEqual(im.cropped_wh(), (960, 960))
        with self.assertRaises(ValueError):
            imager({
                "width": 100,
                "height": 100,
                "crop_pixels": {
                    "left": 1
                },
                "crop_fractions": {
                    "left": 0.01
                }
            }).crop_fractions()

    def test_repo_configs(self):
        """
        Converted microscope.j5 crops give the same pixels as before
        """
        seen = set()
        for fn in sorted(
                glob.glob(os.path.join(REPO, "configs", "*",
                                       "microscope.j5"))):
            name = os.path.basename(os.path.dirname(fn))
            imagerj = readj(fn).get("imager", {})
            self.assertNotIn("crop_pixels", imagerj, name)
            if name not in CONFIG_CROP_PIXELS:
                self.assertNotIn("crop_fractions", imagerj, name)
                continue
            seen.add(name)
            im = imager(imagerj)
            self.assertEqual(im.crop_tblr(), CONFIG_CROP_PIXELS[name], name)
        self.assertEqual(seen, set(CONFIG_CROP_PIXELS))


class TestPrecedence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = CalStore(os.path.join(self.tmp, "calibration.j5"))
        self.j = {
            "width": 1000,
            "height": 800,
            "crop_fractions": {
                "left": 0.01,
                "right": 0.01
            }
        }
        self.im = imager(self.j, cal_store=self.store, objective="10X")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_precedence(self):
        im = self.im
        # microscope.j5
        self.assertEqual(im.crop_tblr()["left"], 10)
        # Default
        self.store.set(
            CalEntry(key="imager.crop_fractions",
                     value={
                         "top": 0.1,
                         "left": 0.02
                     }))
        self.assertEqual(im.crop_tblr(), {
            "top": 80,
            "bottom": 0,
            "left": 20,
            "right": 0
        })
        # Override for another objective doesn't apply
        self.store.set(
            CalEntry(key="imager.crop_fractions",
                     value={"left": 0.3},
                     scope={"objective": "50X"}))
        self.assertEqual(im.crop_tblr()["left"], 20)
        # Override replaces the whole crop (no per side merge)
        self.store.set(
            CalEntry(key="imager.crop_fractions",
                     value={"left": 0.05},
                     scope={"objective": "10X"}))
        self.assertEqual(im.crop_tblr(), {
            "top": 0,
            "bottom": 0,
            "left": 50,
            "right": 0
        })
        im.microscope.objective = "50X"
        self.assertEqual(im.crop_tblr()["left"], 300)
        # nominal ignores the store
        self.assertEqual(im.crop_tblr(nominal=True)["left"], 10)
        # No objective selected: default
        im.microscope.objective = None
        self.assertEqual(im.crop_tblr()["left"], 20)
        # Nothing at all
        self.store.delete("imager.crop_fractions")
        del self.j["crop_fractions"]
        self.assertIsNone(im.crop_tblr())


class TestStoreMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fn = os.path.join(self.tmp, "calibration.j5")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_migrate(self):
        # Measured at a known resolution (dataset manifest)
        ds_dir = os.path.join(self.tmp, "session", "usable_area")
        os.makedirs(ds_dir)
        with open(os.path.join(ds_dir, "manifest.json"), "w") as f:
            json.dump({"imager": {"width": 2736, "height": 1824}}, f)
        store = CalStore(self.fn)
        store.set(
            CalEntry(key="imager.crop_pixels",
                     value={
                         "top": 400,
                         "bottom": 400,
                         "left": 1296,
                         "right": 1296
                     }))
        store.set(
            CalEntry(key="imager.crop_pixels",
                     value={
                         "top": 0,
                         "bottom": 0,
                         "left": 684,
                         "right": 684
                     },
                     scope={"objective": "10X"},
                     source="session/usable_area"))

        store = CalStore(self.fn)
        store.load()
        self.assertEqual(store.migrate_crop_pixels(default_wh=(5440, 3648)),
                         2)
        default = store.find_exact("imager.crop_fractions", {})
        self.assertEqual(
            crop.fractions_to_pixels(default.value, (5440, 3648)), {
                "top": 400,
                "bottom": 400,
                "left": 1296,
                "right": 1296
            })
        override = store.find_exact("imager.crop_fractions",
                                    {"objective": "10X"})
        self.assertEqual(override.value["left"], 0.25)
        self.assertEqual(override.source, "session/usable_area")
        # Old entries kept but disabled
        for scope in ({}, {"objective": "10X"}):
            old = store.find_exact("imager.crop_pixels", scope)
            self.assertIsNotNone(old)
            self.assertFalse(old.enabled)
        self.assertIsNone(store.get("imager.crop_pixels", {"objective": "10X"}))

        # Saved and idempotent
        store = CalStore(self.fn)
        store.load()
        self.assertEqual(store.migrate_crop_pixels(default_wh=(5440, 3648)),
                         0)
        self.assertEqual(
            len([
                e for e in store.entries()
                if e.key == "imager.crop_fractions"
            ]), 2)
        # Old entry re-enabled by hand: disabled again, new entry kept
        store.set_enabled("imager.crop_pixels", {}, True)
        self.assertEqual(store.migrate_crop_pixels(default_wh=(100, 100)), 1)
        self.assertEqual(
            store.find_exact("imager.crop_fractions", {}).value, default.value)
        self.assertFalse(store.find_exact("imager.crop_pixels", {}).enabled)

    def test_unknown_resolution(self):
        store = CalStore(self.fn)
        store.set(CalEntry(key="imager.crop_pixels", value={"left": 10}))
        self.assertEqual(store.migrate_crop_pixels(), 0)
        self.assertTrue(store.find_exact("imager.crop_pixels", {}).enabled)
        self.assertEqual(store.migrate_crop_pixels(default_wh=(1000, 1000)),
                         1)


class TestPixelScale(unittest.TestCase):
    def setUp(self):
        uconfig.usc = None
        self.m = Microscope(name="mock-grbl",
                            log=lambda *a, **k: None,
                            make_imager=False,
                            hardware=False)
        store = self.m.cal_store
        store._user = []
        store._factory = []
        if store.path and os.path.exists(store.path):
            os.unlink(store.path)

    def set_resolution(self, w, h):
        tres.apply_to_imagerj(self.m.usc.imager.j, {
            "esize": None,
            "width": w,
            "height": h
        })
        self.m.objective_state.reload()

    def test_scales_with_resolution(self):
        m = self.m
        optics = m.usc.optics
        # mock-grbl: 800x750, 5.0 um/pix
        self.assertEqual(m.usc.imager.native_wh(), (800, 750))
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 5.0)
        name = m.objective_state.names()[0]
        before = dict(m.objective_state.full_config()[name])
        self.set_resolution(400, 375)
        self.assertEqual(m.usc.imager.native_wh(), (800, 750))
        self.assertAlmostEqual(m.usc.imager.hardware_resolution_scalar(), 0.5)
        self.assertAlmostEqual(optics.um_per_pixel_native_1x(), 5.0)
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 10.0)
        after = m.objective_state.full_config()[name]
        self.assertAlmostEqual(after["um_per_pixel"],
                               before["um_per_pixel"] * 2)
        # Field of view doesn't depend on resolution
        self.assertAlmostEqual(after["x_view"], before["x_view"])
        self.assertAlmostEqual(after["y_view"], before["y_view"])

    def test_cal_store_native(self):
        m = self.m
        optics = m.usc.optics
        # Calibrated at half resolution, stored at native
        self.set_resolution(400, 375)
        m.cal_store.set(
            CalEntry(**optics.um_per_pixel_cal_entry_kwargs(8.0)))
        entry = m.cal_store.get("optics.um_per_pixel_raw_1x")
        self.assertAlmostEqual(entry.value, 4.0)
        self.assertEqual(entry.extra["native_wh"], [800, 750])
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 8.0)
        self.set_resolution(800, 750)
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 4.0)
        # Older entries (no native_wh) were measured at the configured size
        m.cal_store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x", value=3.0, unit="um"))
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 3.0)
        self.set_resolution(400, 375)
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 6.0)

    def test_scripting_override_native(self):
        state = self.m.objective_state
        self.set_resolution(400, 375)
        state.set_um_per_pixel_raw_1x(12.0)
        self.assertAlmostEqual(state.um_per_pixel_native_1x, 6.0)
        self.set_resolution(800, 750)
        name = state.names()[0]
        mag = state.full_config()[name]["magnification"]
        scalar = self.m.usc.imager.scalar()
        self.assertAlmostEqual(state.full_config()[name]["um_per_pixel"],
                               6.0 / mag / scalar)

    def test_authored_differs_from_native(self):
        """
        ex: lip-va1: v4l2 camera, native 3840 wide but run (and calibrated) at 1280
        """
        j = self.m.usc.imager.j
        j["native_width"], j["native_height"] = 3200, 3000
        optics = self.m.usc.optics
        self.assertEqual(self.m.usc.imager.authored_wh(), (800, 750))
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 5.0)
        self.assertAlmostEqual(optics.um_per_pixel_native_1x(), 1.25)

    def test_image_width_fallback(self):
        optics = self.m.usc.optics
        del optics.j["um_per_pixel_raw_1x"]
        optics.j["image_width_1x_mm"] = 4.0
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 5.0)
        self.set_resolution(400, 375)
        self.assertAlmostEqual(optics.um_per_pixel_raw_1x(), 10.0)


if __name__ == "__main__":
    unittest.main()
