#!/usr/bin/env -S uv run
"""Calibration parameter catalog: nominal / calibrated / effective rows and edits"""

import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="pyuscope-cal-params-")
os.environ["PYUSCOPE_DATA_DIR"] = _TMP
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

from uscope import config as uconfig
from uscope.cal import params
from uscope.cal.store import CalEntry
from uscope.microscope import Microscope


class TestCalParams(unittest.TestCase):
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

    def row(self, key, scope=None):
        scope = scope or {}
        for row in params.rows(self.m):
            if row.key == key and row.scope == scope:
                return row
        self.fail("No row %s %s" % (key, scope))

    def test_rows_without_calibration(self):
        rows = params.rows(self.m)
        keys = {row.key for row in rows}
        for param in params.PARAMS:
            self.assertIn(param.key, keys)
        for axis in self.m.usc.motion.axes():
            self.assertEqual(
                self.row("motion.backlash", {
                    "axis": axis
                }).status(), "nominal")
        for name in self.m.objective_state.names():
            row = self.row("kinematics.tsettle_motion", {"objective": name})
            self.assertEqual(row.nominal(), row.effective())
        row = self.row("optics.um_per_pixel_raw_1x")
        self.assertEqual(row.nominal(), self.m.usc.optics.um_per_pixel_raw_1x())

    def test_edit_and_remove(self):
        store = self.m.cal_store
        nominal = self.row("optics.um_per_pixel_raw_1x").nominal()
        params.set_value(store, self.row("optics.um_per_pixel_raw_1x"), 0.25)
        row = self.row("optics.um_per_pixel_raw_1x")
        self.assertEqual(row.status(), "calibrated (user)")
        self.assertEqual(row.user_entry.source, "manual")
        self.assertAlmostEqual(row.effective(), 0.25)
        self.assertAlmostEqual(row.nominal(), nominal)

        params.set_enabled(store, row, False)
        row = self.row("optics.um_per_pixel_raw_1x")
        self.assertEqual(row.status(), "disabled (user)")
        self.assertAlmostEqual(row.effective(), nominal)

        params.remove_user(store, row)
        self.assertEqual(self.row("optics.um_per_pixel_raw_1x").status(),
                         "nominal")

    def test_notes_keep_measurement(self):
        store = self.m.cal_store
        store.set(
            CalEntry(key="imager.software_gain",
                     value=2.0,
                     std=0.1,
                     n=5,
                     source="session"))
        row = self.row("imager.software_gain")
        params.set_value(store, row, notes="checked")
        entry = self.row("imager.software_gain").user_entry
        self.assertEqual(entry.notes, "checked")
        self.assertEqual(entry.source, "session")
        self.assertEqual(entry.std, 0.1)

    def test_backlash_compensation_extra(self):
        store = self.m.cal_store
        row = self.row("motion.backlash", {"axis": "x"})
        params.set_value(store, row, extras={"compensation": 1})
        row = self.row("motion.backlash", {"axis": "x"})
        self.assertEqual(row.extra("compensation"), 1)
        self.assertEqual(self.m.usc.motion.backlash_compensation()["x"], 1)
        # Value starts from nominal when only compensation is set
        self.assertEqual(row.calibrated(), row.nominal())

    def test_fields(self):
        wb = params.get_param("imager.white_balance")
        value = wb.set_fields({"gains": [1, 1, 1], "n": 3}, {
            "r": 1.5,
            "g": 1.0,
            "b": 0.8
        })
        self.assertEqual(value, {"gains": [1.5, 1.0, 0.8], "n": 3})
        with self.assertRaises(ValueError):
            wb.set_fields(None, {"r": 0, "g": 1, "b": 1})
        crop = params.get_param("imager.crop_fractions")
        self.assertEqual(
            crop.set_fields(None, {
                "top": 0.1,
                "bottom": 0.1,
                "left": 0.25,
                "right": 0.25
            }), {
                "top": 0.1,
                "bottom": 0.1,
                "left": 0.25,
                "right": 0.25
            })
        for bad in ({
                "top": -0.1,
                "bottom": 0,
                "left": 0,
                "right": 0
        }, {
                "top": 0,
                "bottom": 0,
                "left": 0.5,
                "right": 0.5
        }):
            with self.assertRaises(ValueError):
                crop.set_fields(None, bad)

    def test_crop_default_and_override(self):
        store = self.m.cal_store
        names = self.m.objective_state.names()
        default = {"top": 0.1, "bottom": 0.1, "left": 0.2, "right": 0.2}
        override = {"top": 0.0, "bottom": 0.0, "left": 0.3, "right": 0.3}
        params.set_value(store, self.row("imager.crop_fractions"), default)
        row = self.row("imager.crop_fractions", {"objective": names[0]})
        self.assertEqual(row.status(), "default")
        self.assertTrue(row.inherits())
        self.assertEqual(row.effective(), default)
        self.assertEqual(self.row("imager.crop_fractions").scope_label(),
                         "default (all objectives)")
        params.set_value(store, row, override)
        row = self.row("imager.crop_fractions", {"objective": names[0]})
        self.assertEqual(row.status(), "calibrated (user)")
        self.assertEqual(row.effective(), override)
        # Pixels at the current (mock 800x750) resolution
        self.assertEqual(row.describe(override),
                         "T 0, B 0, L 240, R 240 pixels at the current 800x750")
        # Clearing the override goes back to the default
        params.remove_user(store, row)
        row = self.row("imager.crop_fractions", {"objective": names[0]})
        self.assertEqual(row.effective(), default)
        self.assertFalse(row.param.needs_restart)

    def test_um_per_pixel_native_basis(self):
        store = self.m.cal_store
        params.set_value(store, self.row("optics.um_per_pixel_raw_1x"), 0.25)
        entry = self.row("optics.um_per_pixel_raw_1x").user_entry
        self.assertEqual(entry.extra["native_wh"],
                         list(self.m.usc.imager.native_wh()))

    def test_factory_shadowed(self):
        store = self.m.cal_store
        store._factory = [CalEntry(key="imager.software_gain", value=3.0)]
        row = self.row("imager.software_gain")
        self.assertEqual(row.status(), "calibrated (factory)")
        params.set_value(store, row, 4.0)
        row = self.row("imager.software_gain")
        self.assertEqual(row.status(), "calibrated (user)")
        self.assertIsNotNone(row.factory_entry)
        params.remove_user(store, row)
        self.assertAlmostEqual(self.row("imager.software_gain").effective(),
                               3.0)

    def test_unknown_key_listed(self):
        self.m.cal_store.set(CalEntry(key="custom.thing", value={"a": 1}))
        row = self.row("custom.thing")
        self.assertEqual(row.param.group, "Other")
        self.assertFalse(row.param.editable)

    def test_scale_change_survives_objective_reload(self):
        state = self.m.objective_state
        name = state.names()[0]
        state.set_global_scalar(None)
        before = state.full_config()[name]["um_per_pixel"]
        self.m.cal_store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x",
                     value=self.m.usc.optics.um_per_pixel_raw_1x() * 2))
        # Global scalar change reloads the objective DB
        state.set_global_scalar(1.0)
        self.assertAlmostEqual(state.full_config()[name]["um_per_pixel"],
                               before * 2)
        self.m.cal_store.delete("optics.um_per_pixel_raw_1x")
        self.assertAlmostEqual(state.full_config()[name]["um_per_pixel"],
                               before)


if __name__ == "__main__":
    unittest.main()
