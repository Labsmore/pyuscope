#!/usr/bin/env -S uv run
"""Cal store overlays nominal microscope.j5 values. Empty store is a no-op."""

import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="pyuscope-cal-apply-")
os.environ["PYUSCOPE_DATA_DIR"] = _TMP
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

from uscope import config as uconfig
from uscope.cal.store import CalEntry
from uscope.microscope import Microscope


class TestCalApply(unittest.TestCase):
    def _scope(self):
        uconfig.usc = None
        m = Microscope(name="mock-grbl",
                       log=lambda *a, **k: None,
                       make_imager=False,
                       hardware=False)
        m.cal_store._user = []
        if m.cal_store.path and os.path.exists(m.cal_store.path):
            os.unlink(m.cal_store.path)
        m.calibration = m.cal_store.as_dict()
        return m

    def test_nominal_without_store_entries(self):
        m = self._scope()
        nominal = m.usc.optics.um_per_pixel_raw_1x()
        self.assertTrue(nominal)
        self.assertIsNone(m.cal_store.get("optics.um_per_pixel_raw_1x"))
        self.assertEqual(m.usc.optics.um_per_pixel_raw_1x(), nominal)

    def test_um_per_pixel_overlay(self):
        m = self._scope()
        m.cal_store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x", value=0.123, unit="um"))
        self.assertAlmostEqual(m.usc.optics.um_per_pixel_raw_1x(), 0.123)

    def test_disabled_uses_nominal(self):
        m = self._scope()
        nominal = m.usc.optics.um_per_pixel_raw_1x()
        m.cal_store.set(
            CalEntry(key="optics.um_per_pixel_raw_1x",
                     value=0.123,
                     enabled=False))
        self.assertEqual(m.usc.optics.um_per_pixel_raw_1x(), nominal)

    def test_backlash_overlay(self):
        m = self._scope()
        before = m.usc.motion.backlash()
        m.cal_store.set(
            CalEntry(key="motion.backlash",
                     value=0.042,
                     scope={"axis": "x"},
                     unit="mm",
                     extra={"compensation": +1}))
        after = m.usc.motion.backlash()
        self.assertAlmostEqual(after["x"], 0.042)
        # Other axes unchanged
        if "y" in before:
            self.assertEqual(after["y"], before["y"])
        comp = m.usc.motion.backlash_compensation()
        self.assertEqual(comp["x"], 1)

    def test_reconfigure_backlash(self):
        uconfig.usc = None
        m = Microscope(name="mock-grbl",
                       log=lambda *a, **k: None,
                       hardware=False)
        m.cal_store.set(
            CalEntry(key="motion.backlash",
                     value=0.05,
                     scope={"axis": "x"},
                     unit="mm"))
        if m.motion is not None:
            m.motion.reconfigure_backlash()
            self.assertIn("backlash", m.motion.modifiers)

    def test_calibration_view_for_planner(self):
        m = self._scope()
        self.assertEqual(m.calibration.get("entries"), [])
        m.cal_store.set(CalEntry(key="optics.um_per_pixel_raw_1x", value=0.5))
        self.assertTrue(m.calibration["entries"])


if __name__ == "__main__":
    unittest.main()
