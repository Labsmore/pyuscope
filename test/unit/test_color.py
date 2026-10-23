#!/usr/bin/env -S uv run
"""Software white balance / gain: gain computation and application."""

import tempfile
import unittest

import numpy as np
from PIL import Image

from uscope.cal.analysis.color import (
    apply_gains,
    apply_gains_pil,
    center_roi,
    channel_levels,
    compute_white_balance,
    effective_gains,
    gains_from_levels,
)
from uscope.cal.calibrations.white_balance import WhiteBalanceCal
from uscope.cal.dataset import Dataset
from uscope.cal.registry import names as cal_names
from uscope.imager.image_sequence import CapturedImage


def _white(rgb, w=40, h=30, dtype=np.uint8):
    arr = np.zeros((h, w, 3), dtype=dtype)
    arr[:, :] = rgb
    return arr


class TestComputeGains(unittest.TestCase):
    def test_center_roi(self):
        arr = np.arange(100).reshape(10, 10)
        roi = center_roi(arr, 0.5)
        self.assertEqual(roi.shape, (5, 5))
        self.assertEqual(roi[0, 0], 22)
        self.assertEqual(center_roi(arr, 1.0).shape, (10, 10))
        with self.assertRaises(ValueError):
            center_roi(arr, 0.0)

    def test_levels_ignore_border(self):
        arr = _white((100, 200, 50))
        # Junk outside of the central ROI
        arr[0:5, :] = 0
        arr[:, 0:5] = 255
        levels, sat = channel_levels(arr, roi_fraction=0.5)
        self.assertEqual(levels, [100.0, 200.0, 50.0])
        self.assertEqual(sat, 0.0)

    def test_levels_exclude_saturated(self):
        arr = _white((100, 200, 50))
        arr[14:16, 18:22] = (255, 255, 255)
        levels, sat = channel_levels(arr, roi_fraction=0.5)
        self.assertEqual(levels, [100.0, 200.0, 50.0])
        self.assertGreater(sat, 0.0)

    def test_levels_percentile(self):
        arr = _white((100, 100, 100))
        arr[15, 20] = (200, 200, 200)
        levels, _sat = channel_levels(arr, percentile=100)
        self.assertEqual(levels, [200.0, 200.0, 200.0])

    def test_gains_green(self):
        gains = gains_from_levels([100, 200, 50], normalize="green")
        np.testing.assert_allclose(gains, [2.0, 1.0, 4.0])

    def test_gains_max_min(self):
        np.testing.assert_allclose(
            gains_from_levels([100, 200, 50], normalize="max"),
            [2.0, 1.0, 4.0])
        np.testing.assert_allclose(
            gains_from_levels([100, 200, 50], normalize="min"),
            [0.5, 0.25, 1.0])
        with self.assertRaises(ValueError):
            gains_from_levels([100, 200, 50], normalize="bogus")
        with self.assertRaises(ValueError):
            gains_from_levels([0, 200, 50])

    def test_compute_16bit(self):
        wb = compute_white_balance(_white((10000, 20000, 40000),
                                          dtype=np.uint16))
        np.testing.assert_allclose(wb["gains"], [2.0, 1.0, 0.5])
        self.assertAlmostEqual(wb["levels_frac"][1], 20000 / 65535)

    def test_gray_rejected(self):
        with self.assertRaises(ValueError):
            compute_white_balance(np.zeros((10, 10), dtype=np.uint8))


class TestApplyGains(unittest.TestCase):
    def test_effective(self):
        self.assertIsNone(effective_gains(None, 1.0))
        self.assertIsNone(effective_gains([1.0, 1.0, 1.0], 1.0))
        self.assertEqual(effective_gains(None, 2.0), [2.0, 2.0, 2.0])
        self.assertEqual(effective_gains([2.0, 1.0, 0.5], 2.0),
                         [4.0, 2.0, 1.0])
        with self.assertRaises(ValueError):
            effective_gains([1.0, 1.0], 1.0)
        with self.assertRaises(ValueError):
            effective_gains(None, 0.0)

    def test_roundtrip_balances(self):
        arr = _white((100, 200, 50))
        wb = compute_white_balance(arr)
        out = apply_gains(arr, wb["gains"])
        self.assertEqual(out.dtype, np.uint8)
        self.assertTrue(np.all(out == 200))

    def test_clip_8bit(self):
        arr = _white((100, 200, 250))
        out = apply_gains(arr, [2.0, 1.0, 1.5])
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(tuple(out[0, 0]), (200, 200, 255))
        # Input untouched
        self.assertEqual(tuple(arr[0, 0]), (100, 200, 250))

    def test_rounding(self):
        arr = _white((3, 5, 7))
        out = apply_gains(arr, [0.5, 0.5, 0.5])
        # np.rint: round half to even
        self.assertEqual(tuple(out[0, 0]), (2, 2, 4))

    def test_clip_16bit(self):
        arr = _white((10000, 40000, 60000), dtype=np.uint16)
        out = apply_gains(arr, [2.0, 1.5, 0.5])
        self.assertEqual(out.dtype, np.uint16)
        self.assertEqual(tuple(out[0, 0]), (20000, 60000, 30000))
        out = apply_gains(arr, [7.0, 2.0, 2.0])
        self.assertEqual(tuple(out[0, 0]), (65535, 65535, 65535))

    def test_float(self):
        arr = _white((0.25, 0.5, 0.75), dtype=np.float32)
        out = apply_gains(arr, [2.0, 1.0, 2.0])
        self.assertEqual(out.dtype, np.float32)
        np.testing.assert_allclose(out[0, 0], [0.5, 0.5, 1.0])

    def test_gray_uses_mean_gain(self):
        arr = np.full((4, 4), 100, dtype=np.uint8)
        out = apply_gains(arr, [1.0, 2.0, 3.0])
        self.assertTrue(np.all(out == 200))

    def test_alpha_untouched(self):
        arr = np.full((4, 4, 4), 100, dtype=np.uint8)
        out = apply_gains(arr, [2.0, 2.0, 2.0])
        self.assertTrue(np.all(out[:, :, 0:3] == 200))
        self.assertTrue(np.all(out[:, :, 3] == 100))

    def test_none_is_copy(self):
        arr = _white((1, 2, 3))
        out = apply_gains(arr, None)
        self.assertTrue(np.array_equal(out, arr))
        self.assertIsNot(out, arr)

    def test_pil(self):
        im = Image.fromarray(_white((100, 200, 50)))
        out = apply_gains_pil(im, [2.0, 1.0, 4.0])
        self.assertEqual(out.mode, "RGB")
        self.assertEqual(out.getpixel((0, 0)), (200, 200, 200))

        im = Image.fromarray(np.full((4, 4), 1000, dtype=np.uint16))
        self.assertEqual(im.mode, "I;16")
        out = apply_gains_pil(im, [3.0, 3.0, 3.0])
        self.assertEqual(out.mode, "I;16")
        self.assertEqual(out.getpixel((0, 0)), 3000)

        im = Image.new("I", (4, 4), 30000)
        out = apply_gains_pil(im, [3.0, 3.0, 3.0])
        self.assertEqual(out.getpixel((0, 0)), 65535)


class _FakeCtx:
    def __init__(self, image, data=None):
        self.captured = CapturedImage(image=image, data=data)

    def check_running(self):
        pass

    def progress(self, frac, text):
        pass

    def get_disp_properties(self):
        return {"White Balance Temperature": 4600, "Exposure": 100}

    def image_captured(self, mode=None):
        assert mode == "raw"
        return self.captured


class TestWhiteBalanceCal(unittest.TestCase):
    def _run(self, ctx):
        cal = WhiteBalanceCal()
        ds = Dataset.create(tempfile.mkdtemp(prefix="pyuscope-wb-"),
                            cal.name)
        cal.collect(ctx, ds)
        return cal.analyze(Dataset.load(ds.path))

    def test_registered(self):
        self.assertIn("white_balance", cal_names())

    def test_8bit(self):
        im = Image.fromarray(_white((100, 200, 50)))
        result = self._run(_FakeCtx(im))
        self.assertTrue(result.ok, result.message)
        entry = result.entries[0]
        self.assertEqual(entry.key, "imager.white_balance")
        self.assertEqual(entry.scope, {})
        np.testing.assert_allclose(entry.value["gains"], [2.0, 1.0, 4.0])
        self.assertEqual(entry.value["imager_properties"],
                         {"White Balance Temperature": 4600})

    def test_16bit_data(self):
        im = Image.fromarray(_white((1, 2, 3)))
        data = _white((10000, 20000, 40000), dtype=np.uint16)
        result = self._run(_FakeCtx(im, data=data))
        self.assertTrue(result.ok, result.message)
        np.testing.assert_allclose(result.entries[0].value["gains"],
                                   [2.0, 1.0, 0.5])

    def test_raw_mosaic_uses_image(self):
        im = Image.fromarray(_white((100, 200, 50)))
        data = np.zeros((30, 40), dtype=np.uint16)
        result = self._run(_FakeCtx(im, data=data))
        np.testing.assert_allclose(result.entries[0].value["gains"],
                                   [2.0, 1.0, 4.0])

    def test_saturated_fails(self):
        im = Image.fromarray(_white((255, 255, 200)))
        result = self._run(_FakeCtx(im))
        self.assertFalse(result.ok)
        self.assertIn("saturated", result.message)

    def test_dark_fails(self):
        im = Image.fromarray(_white((2, 4, 3)))
        result = self._run(_FakeCtx(im))
        self.assertFalse(result.ok)
        self.assertIn("dark", result.message)

    def test_black_channel_fails(self):
        im = Image.fromarray(_white((0, 100, 100)))
        result = self._run(_FakeCtx(im))
        self.assertFalse(result.ok)
        self.assertFalse(result.entries)


if __name__ == "__main__":
    unittest.main()
