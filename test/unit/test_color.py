#!/usr/bin/env -S uv run
"""Software white balance / gain: gain computation and application."""

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


if __name__ == "__main__":
    unittest.main()
