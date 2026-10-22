#!/usr/bin/env -S uv run
"""Pure analysis tests: centroid, linearity, illumination, settle, usable area."""

import os
import tempfile
import unittest

import numpy as np
from PIL import Image

from uscope.cal.analysis.centroid import find_centroid
from uscope.cal.analysis.fitting import invert_linear, linear_fit, peak_quadratic
from uscope.cal.analysis.focus import settle_time
from uscope.cal.analysis.illumination import (
    IlluminationTables,
    dark_stats,
    gain_map,
    stack_median,
    usable_rect,
)
from uscope.cal.analysis.linearity import fit_response, invert_response
from uscope.cal.calibrations.repeat_xy import RepeatXYCal
from uscope.cal.dataset import Dataset


def _dot_image(w=80, h=80, cx=30.0, cy=40.0, r=6):
    arr = np.zeros((h, w), dtype=np.uint8)
    yy, xx = np.ogrid[:h, :w]
    arr[(xx - cx)**2 + (yy - cy)**2 <= r * r] = 255
    return Image.fromarray(arr)


class TestCentroid(unittest.TestCase):
    def test_finds_dot(self):
        im = _dot_image(cx=30, cy=40, r=6)
        x, y = find_centroid(im)
        self.assertAlmostEqual(x, 30, delta=1.0)
        self.assertAlmostEqual(y, 40, delta=1.0)

    def test_missing_dot(self):
        im = Image.fromarray(np.zeros((40, 40), dtype=np.uint8))
        with self.assertRaises(ValueError):
            find_centroid(im)


class TestFitting(unittest.TestCase):
    def test_linear_and_invert(self):
        xs = [10, 20, 30, 40]
        ys = [5, 15, 25, 35]  # y = x - 5
        m, b = linear_fit(xs, ys)
        self.assertAlmostEqual(m, 1.0, places=5)
        self.assertAlmostEqual(b, -5.0, places=5)
        self.assertAlmostEqual(invert_linear(m, b, 0), 5.0, places=5)

    def test_peak_quadratic(self):
        xs = np.linspace(-2, 2, 21)
        ys = 1.0 - (xs - 0.3)**2
        xp, yp = peak_quadratic(xs, ys)
        self.assertAlmostEqual(xp, 0.3, delta=0.15)
        self.assertGreater(yp, 0.9)


class TestLinearity(unittest.TestCase):
    def test_fit_and_invert(self):
        t = np.array([10, 20, 40, 80, 160], dtype=float)
        v = 0.05 + 0.004 * t  # gamma=1
        model = fit_response(t, v)
        self.assertAlmostEqual(model["gamma"], 1.0, delta=0.15)
        t2 = invert_response(0.05 + 0.004 * 50, model)
        self.assertAlmostEqual(t2, 50, delta=5)


class TestIllumination(unittest.TestCase):
    def test_stack_median_rejects_outlier(self):
        a = np.ones((8, 8), dtype=np.uint8) * 10
        b = np.ones((8, 8), dtype=np.uint8) * 10
        c = np.ones((8, 8), dtype=np.uint8) * 10
        c[0, 0] = 255
        med = stack_median([a, b, c])
        self.assertEqual(med[0, 0], 10)

    def test_gain_and_apply(self):
        flat = np.ones((16, 16), dtype=np.float64) * 100
        flat[:, :8] = 50
        gain = gain_map(flat, reference="max")
        tables = IlluminationTables(gain=gain)
        img = np.ones((16, 16), dtype=np.uint8) * 40
        out = tables.apply(img)
        # Dark half should be boosted ~2x
        self.assertGreater(out[0, 0], out[0, 12])

    def test_dark_stats_hot_pixels(self):
        dark = np.ones((32, 32), dtype=np.float64) * 10
        dark[2, 3] = 200
        mean, std, mask = dark_stats(dark, k_sigma=6)
        self.assertTrue(mask[2, 3])
        self.assertGreater(mask.sum(), 0)

    def test_usable_rect_vignette(self):
        h, w = 64, 80
        yy, xx = np.ogrid[:h, :w]
        cy, cx = h / 2, w / 2
        rel = np.exp(-((xx - cx)**2 + (yy - cy)**2) / (2 * 18**2))
        crop = usable_rect(rel, threshold=0.5, margin=0, even=False)
        self.assertIsNotNone(crop)
        self.assertGreater(crop["top"], 0)
        self.assertGreater(crop["left"], 0)

    def test_tables_save_load(self):
        d = tempfile.mkdtemp()
        fn = os.path.join(d, "t.npz")
        t = IlluminationTables(gain=np.ones((4, 4), dtype=np.float32),
                               dark=np.zeros((4, 4), dtype=np.float32))
        t.save(fn)
        t2 = IlluminationTables.load(fn)
        self.assertEqual(t2.gain.shape, (4, 4))


class TestSettle(unittest.TestCase):
    def test_settle_time(self):
        times = [0.0, 0.1, 0.2, 0.3, 0.4]
        values = [10, 4, 1.2, 1.05, 1.0]
        t, ok = settle_time(times, values, tolerance=0.2)
        self.assertTrue(ok)
        self.assertGreaterEqual(t, 0.2)


class TestRepeatYStatsNotX(unittest.TestCase):
    """The old script computed Y stats from dxs. Guard the new analyzer."""

    def test_analyze_separates_axes(self):
        tmp = tempfile.mkdtemp()
        ds = Dataset.create(tmp, "repeat_xy", meta={
            "objective": {"um_per_pixel": 1.0},
        })
        # Synthetic: X cluster tight, Y cluster loose — but we only have
        # per-axis image folders. Create images with a dot shifted only in X
        # for axis=x and only in Y for axis=y.
        rng = np.random.RandomState(0)
        for i in range(8):
            im = _dot_image(cx=40 + rng.randn() * 0.2, cy=40)
            fn = "x_%02u.tif" % i
            im.save(ds.capture_path(fn))
            ds.manifest["captures"].append({
                "fn": "images/%s" % fn,
                "axis": "x"
            })
        for i in range(8):
            im = _dot_image(cx=40, cy=40 + rng.randn() * 4)
            fn = "y_%02u.tif" % i
            im.save(ds.capture_path(fn))
            ds.manifest["captures"].append({
                "fn": "images/%s" % fn,
                "axis": "y"
            })
        ds.save_manifest()
        result = RepeatXYCal().analyze(ds)
        self.assertTrue(result.ok)
        self.assertLess(result.values["x_std_um"], result.values["y_std_um"])


if __name__ == "__main__":
    unittest.main()
