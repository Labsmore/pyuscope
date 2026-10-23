#!/usr/bin/env -S uv run
"""Software white balance / gain through the snapshot processing pipeline."""

import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="pyuscope-color-")
os.environ["PYUSCOPE_DATA_DIR"] = _TMP
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

import numpy as np
from PIL import Image

from uscope import config as uconfig
from uscope.cal.store import CalEntry
from uscope.imagep.pipeline import process_snapshot
from uscope.imager.image_sequence import CapturedImage
from uscope.microscope import Microscope


class TestColorPipeline(unittest.TestCase):
    def setUp(self):
        uconfig.usc = None
        self.m = Microscope(name="mock-grbl",
                            log=lambda *a, **k: None,
                            make_imager=False,
                            hardware=False)
        self.store = self.m.cal_store
        self.store._user = []
        if self.store.path and os.path.exists(self.store.path):
            os.unlink(self.store.path)
        self.usc = uconfig.get_usc(microscope=self.m)
        self.usc.imager.j.pop("software_white_balance", None)
        self.usc.imager.j.pop("software_gain", None)

    def _process(self, rgb, data=None):
        im = Image.fromarray(np.full((8, 10, 3), rgb, dtype=np.uint8))
        capim = CapturedImage(image=im, data=data)
        out = process_snapshot(im,
                               microscope=self.m,
                               nthreads=1,
                               options={"captured_image": capim})
        return out, capim

    def _set_wb(self, gains, enabled=True):
        self.store.set(
            CalEntry(key="imager.white_balance",
                     value={"gains": gains},
                     enabled=enabled))

    def test_no_cal_is_noop(self):
        self.assertIsNone(self.usc.imager.white_balance_gains())
        self.assertEqual(self.usc.imager.software_gain(), 1.0)
        out, capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (100, 200, 50))
        self.assertEqual(capim.meta["corrections"], [])

    def test_white_balance(self):
        self._set_wb([2.0, 1.0, 4.0])
        self.assertEqual(self.usc.imager.white_balance_gains(),
                         [2.0, 1.0, 4.0])
        out, capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (200, 200, 200))
        # Lets scan post processing know not to apply it again
        self.assertEqual(capim.meta["corrections"], ["correct-wb"])

    def test_disabled_entry(self):
        self._set_wb([2.0, 1.0, 4.0], enabled=False)
        out, _capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (100, 200, 50))

    def test_config_disable(self):
        self._set_wb([2.0, 1.0, 4.0])
        self.usc.imager.j["software_white_balance"] = False
        out, _capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (100, 200, 50))

    def test_software_gain_config_and_store(self):
        self.usc.imager.j["software_gain"] = 2.0
        out, _capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (200, 255, 100))
        # Store overrides config
        self.store.set(CalEntry(key="imager.software_gain", value=0.5))
        out, _capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (50, 100, 25))

    def test_gain_and_wb_combine(self):
        self._set_wb([2.0, 1.0, 4.0])
        self.store.set(CalEntry(key="imager.software_gain", value=0.5))
        out, _capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (100, 100, 100))

    def test_native_data(self):
        self._set_wb([2.0, 1.0, 4.0])
        data = np.full((8, 10, 3), (10000, 20000, 5000), dtype=np.uint16)
        _out, capim = self._process((100, 200, 50), data=data)
        self.assertEqual(capim.data.dtype, np.uint16)
        self.assertEqual(tuple(capim.data[0, 0]), (20000, 20000, 20000))
        # Raw mosaic only gets the scalar gain
        self.store.set(CalEntry(key="imager.software_gain", value=2.0))
        data = np.full((8, 10), 40000, dtype=np.uint16)
        _out, capim = self._process((100, 200, 50), data=data)
        self.assertTrue(np.all(capim.data == 65535))

    def test_bad_gains_ignored(self):
        self._set_wb([0.0, 1.0, 4.0])
        out, _capim = self._process((100, 200, 50))
        self.assertEqual(out.getpixel((0, 0)), (100, 200, 50))


if __name__ == "__main__":
    unittest.main()
