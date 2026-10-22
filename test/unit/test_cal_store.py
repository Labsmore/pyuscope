#!/usr/bin/env -S uv run
"""CalStore scope matching, enable/disable, save/load."""

import os
import tempfile
import unittest

from uscope.cal.store import CalEntry, CalStore


class TestCalStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pyuscope-cal-")
        self.path = os.path.join(self.tmpdir, "calibration.j5")
        self.store = CalStore(self.path)

    def test_empty_get_is_none(self):
        self.assertIsNone(self.store.get("optics.um_per_pixel_raw_1x"))

    def test_set_get_roundtrip(self):
        self.store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x", value=0.512, unit="um"))
        e = self.store.get("optics.um_per_pixel_raw_1x")
        self.assertAlmostEqual(e.value, 0.512)
        self.assertTrue(e.enabled)

    def test_scope_specificity(self):
        self.store.set(
            CalEntry(key="imager.illumination", value="unit", file="a.npz"))
        self.store.set(
            CalEntry(key="imager.illumination",
                     value="obj",
                     file="b.npz",
                     scope={"objective": "20X"}))
        e = self.store.get("imager.illumination", {"objective": "20X"})
        self.assertEqual(e.value, "obj")
        e = self.store.get("imager.illumination", {"objective": "5X"})
        self.assertEqual(e.value, "unit")

    def test_disabled_falls_through(self):
        self.store.set(
            CalEntry(key="motion.backlash",
                     value=0.01,
                     scope={"axis": "x"},
                     enabled=False))
        self.assertIsNone(self.store.get("motion.backlash", {"axis": "x"}))
        e = self.store.get("motion.backlash", {"axis": "x"},
                           include_disabled=True)
        self.assertIsNotNone(e)
        self.store.set_enabled("motion.backlash", {"axis": "x"}, True)
        self.assertAlmostEqual(
            self.store.get("motion.backlash", {"axis": "x"}).value, 0.01)

    def test_delete_reverts(self):
        self.store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x", value=1.0))
        self.assertTrue(self.store.delete("optics.um_per_pixel_raw_1x"))
        self.assertIsNone(self.store.get("optics.um_per_pixel_raw_1x"))

    def test_save_load(self):
        self.store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x", value=0.4, unit="um"))
        other = CalStore(self.path)
        other.load()
        self.assertAlmostEqual(
            other.get_value("optics.um_per_pixel_raw_1x"), 0.4)

    def test_factory_shadowed_by_user(self):
        factory = os.path.join(self.tmpdir, "factory.j5")
        fac = CalStore(factory)
        fac.set(CalEntry(key="optics.um_per_pixel_raw_1x", value=1.0))
        store = CalStore(self.path, factory_path=factory)
        store.load()
        self.assertAlmostEqual(store.get_value("optics.um_per_pixel_raw_1x"),
                               1.0)
        store.set(CalEntry(key="optics.um_per_pixel_raw_1x", value=2.0))
        self.assertAlmostEqual(store.get_value("optics.um_per_pixel_raw_1x"),
                               2.0)

    def test_entry_does_not_match_if_query_omits_required_key(self):
        self.store.set(
            CalEntry(key="imager.crop_pixels",
                     value={"top": 10},
                     scope={"objective": "20X"}))
        self.assertIsNone(self.store.get("imager.crop_pixels", {}))


if __name__ == "__main__":
    unittest.main()
