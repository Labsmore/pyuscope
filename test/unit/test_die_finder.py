#!/usr/bin/env -S uv run
"""
Die edge / corner finding (uscope/imagep/die_finder.py) on synthetic images
"""

import math
import unittest

import numpy as np

from uscope.imagep import die_finder
from uscope.imagep.die_finder import (
    DieFindError,
    Observation,
    clean_mask,
    edge_samples,
    find_corner,
    find_die,
    fit_line,
    intersect_lines,
    xy3p_corners,
)


class SyntheticDie:
    """
    Renders camera views of a (possibly rotated / skewed) die on a substrate
    Stage mm, image up = stage +y
    """
    def __init__(self,
                 ll=(10.0, 20.0),
                 width=3.0,
                 height=2.0,
                 rotation=0.0,
                 skew=0.0,
                 wh=(400, 300),
                 mm_per_px=0.002,
                 die_level=0.65,
                 bg_level=0.3,
                 noise=0.05,
                 seed=0):
        self.ll = np.array(ll, dtype=np.float64)
        a = math.radians(rotation)
        self.u = np.array((math.cos(a), math.sin(a))) * width
        b = a + math.radians(90.0 - skew)
        self.v = np.array((math.cos(b), math.sin(b))) * height
        self.wh = wh
        self.mm_per_px = mm_per_px
        self.die_level = die_level
        self.bg_level = bg_level
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        self.views = 0

    def corners(self):
        return {
            "ll": self.ll,
            "lr": self.ll + self.u,
            "ul": self.ll + self.v,
            "ur": self.ll + self.u + self.v,
        }

    def view_wh(self):
        return self.wh[0] * self.mm_per_px, self.wh[1] * self.mm_per_px

    def render(self, center):
        self.views += 1
        w, h = self.wh
        xs = center[0] + (np.arange(w) + 0.5 - w / 2) * self.mm_per_px
        ys = center[1] - (np.arange(h) + 0.5 - h / 2) * self.mm_per_px
        X, Y = np.meshgrid(xs, ys)
        # Die local coordinates
        m = np.array((self.u, self.v)).T
        inv = np.linalg.inv(m)
        dx = X - self.ll[0]
        dy = Y - self.ll[1]
        s = inv[0, 0] * dx + inv[0, 1] * dy
        t = inv[1, 0] * dx + inv[1, 1] * dy
        inside = (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
        # Die: metal lines on a 40 um pitch + dark pads near the edge
        lines = ((np.floor(X / 0.04) + np.floor(Y / 0.04)) % 2) * 0.2
        die = self.die_level + lines - 0.1
        bg = np.full_like(X, self.bg_level)
        im = np.where(inside, die, bg)
        im += self.rng.normal(0.0, self.noise, im.shape)
        im = np.clip(im, 0, 1)
        rgb = np.stack((im, im * 0.9, im * 0.8), axis=2)
        return (rgb * 255).astype(np.uint8)

    def observer(self, classifier):
        def observe(center):
            return Observation(self.render(center),
                               center,
                               self.mm_per_px,
                               classifier=classifier)

        return observe

    def start_observation(self, offset=(0.15, 0.1)):
        """
        View with the lower left corner in the lower left of the image
        """
        vw, vh = self.view_wh()
        center = self.ll + (offset[0] * vw, offset[1] * vh)
        return center, Observation(self.render(center), center,
                                   self.mm_per_px)


class TestGeometry(unittest.TestCase):
    def test_fit_line_outliers(self):
        xs = np.arange(20, dtype=np.float64)
        pts = np.stack((xs, 0.5 * xs + 3), axis=1)
        pts[5] = (5, 40)
        p, d, inliers = fit_line(pts)
        self.assertEqual(len(inliers), 19)
        self.assertAlmostEqual(abs(d[1] / d[0]), 0.5, places=6)
        self.assertAlmostEqual(p[1] - 0.5 * p[0], 3.0, places=6)

    def test_intersect(self):
        p = intersect_lines((0, 1), (1, 0), (2, 0), (0, 1))
        np.testing.assert_allclose(p, (2, 1))
        self.assertIsNone(intersect_lines((0, 0), (1, 0), (0, 1), (2, 0)))

    def test_edge_samples_sides(self):
        mask = np.zeros((40, 60), dtype=bool)
        # Die rows 10..29, cols 20..44
        mask[10:30, 20:45] = True
        bottom = edge_samples(mask, "bottom")
        np.testing.assert_allclose(bottom[:, 1], 30)
        self.assertEqual(bottom[:, 0].min(), 20.5)
        self.assertEqual(bottom[:, 0].max(), 44.5)
        np.testing.assert_allclose(edge_samples(mask, "top")[:, 1], 10)
        np.testing.assert_allclose(edge_samples(mask, "left")[:, 0], 20)
        np.testing.assert_allclose(edge_samples(mask, "right")[:, 0], 45)

    def test_find_corner_mask(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[:25, 15:] = True
        np.testing.assert_allclose(find_corner(mask, "ll"), (15, 25))
        self.assertIsNone(find_corner(mask, "lr"))
        self.assertIsNone(find_corner(mask, "ul"))

    def test_clean_mask(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[:25, 15:] = True
        # Background speck, die hole
        mask[35, 3] = True
        mask[5:8, 30:33] = False
        out = clean_mask(mask)
        self.assertFalse(out[35, 3])
        self.assertTrue(out[6, 31])
        self.assertEqual(out.sum(), 25 * 45)

    def test_xy3p_corners_aligned(self):
        corners = {"ll": (0.0, 0.0), "lr": (4.0, 0.0), "ul": (0.0, 3.0)}
        ret = xy3p_corners(corners, view_wh=(1.0, 0.75), border=(0.2, 0.15))
        # Scan region: x -0.2 to 4.2, y -0.15 to 3.15
        np.testing.assert_allclose(ret["ll"], (-0.2 + 0.5, -0.15 + 0.375))
        np.testing.assert_allclose(ret["lr"], (4.2 - 0.5, -0.15 + 0.375))
        np.testing.assert_allclose(ret["ul"], (-0.2 + 0.5, 3.15 - 0.375))

    def test_xy3p_corners_matches_planner_convention(self):
        """
        Undoing the Argus XY3P widget => planner offsets recovers the bordered die
        """
        corners = {"ll": (1.0, 1.0), "lr": (5.0, 1.2), "ul": (0.9, 4.0)}
        xv, yv = 1.0, 0.75
        ret = xy3p_corners(corners, view_wh=(xv, yv), border=(0, 0))
        planner = {
            "ll": ret["ll"] + (-xv / 2, -yv / 2),
            "ul": ret["ul"] + (-xv / 2, yv / 2),
            "lr": ret["lr"] + (xv / 2, -yv / 2),
        }
        for k in ("ll", "lr", "ul"):
            np.testing.assert_allclose(planner[k], corners[k], atol=1e-12)

    def test_die_summary(self):
        s = die_finder.die_summary({
            "ll": (0, 0),
            "lr": (2 * math.cos(math.radians(3)), 2 * math.sin(math.radians(3))),
            "ul": (0, 1),
        })
        self.assertAlmostEqual(s["width"], 2.0)
        self.assertAlmostEqual(s["height"], 1.0)
        self.assertAlmostEqual(s["rotation"], 3.0)
        self.assertAlmostEqual(s["skew"], 3.0)


class TestSynthetic(unittest.TestCase):
    # Max corner error as a fraction of the view width
    TOL = 0.03

    def assertNear(self, p, q, tol_mm):
        dist = float(np.linalg.norm(np.asarray(p) - np.asarray(q)))
        self.assertLess(dist, tol_mm, f"{p} vs {q}")

    def check_start_view(self, die):
        center, obs = die.start_observation()
        self.assertGreater(obs.classifier.separation, 2.0)
        c = obs.corner("ll")
        self.assertIsNotNone(c)
        self.assertNear(c, die.ll, self.TOL * die.view_wh()[0])
        return obs

    def test_start_view(self):
        self.check_start_view(SyntheticDie())

    def test_dark_die(self):
        """
        Die darker than the substrate: only the texture / level differs
        """
        self.check_start_view(SyntheticDie(die_level=0.3, bg_level=0.7))

    def test_large_image(self):
        die = SyntheticDie(wh=(2400, 1800), mm_per_px=0.002 / 6)
        obs = self.check_start_view(die)
        self.assertGreater(obs.bim.px_per_block, 10)

    def test_no_die(self):
        die = SyntheticDie()
        center = die.ll + (-1.0, -1.0)
        clf = die.start_observation()[1].classifier
        obs = Observation(die.render(center), center, die.mm_per_px, clf)
        self.assertEqual(obs.die_fraction(), 0.0)
        self.assertIsNone(obs.corner("ll"))
        self.assertIsNone(obs.edge("bottom"))

    def run_find_die(self, die, offset=(0.15, 0.1), **kwargs):
        start, obs = die.start_observation(offset=offset)
        found = find_die(die.observer(obs.classifier),
                         start,
                         die.view_wh(),
                         max_size=10.0,
                         **kwargs)
        tol = self.TOL * die.view_wh()[0]
        for k in ("ll", "lr", "ul"):
            self.assertNear(found[k], die.corners()[k], tol)
        return found

    def test_find_die_aligned(self):
        self.run_find_die(SyntheticDie())

    def test_find_die_rotated(self):
        die = SyntheticDie(rotation=4.0)
        found = self.run_find_die(die)
        s = die_finder.die_summary(found)
        self.assertAlmostEqual(s["rotation"], 4.0, delta=0.5)

    def test_find_die_rotated_negative_skewed(self):
        self.run_find_die(SyntheticDie(rotation=-3.0, skew=2.0, seed=3))

    def test_find_die_small(self):
        """
        Die smaller than the view: everything is visible from the start
        """
        self.run_find_die(SyntheticDie(width=0.5, height=0.4))

    def test_find_die_corner_near_frame(self):
        """
        Corner too close to the lower left of the frame to fit edges at first
        """
        self.run_find_die(SyntheticDie(rotation=2.0), offset=(0.4, 0.4))

    def test_find_die_too_big(self):
        die = SyntheticDie(width=20.0)
        start, obs = die.start_observation()
        with self.assertRaises(DieFindError):
            find_die(die.observer(obs.classifier),
                     start,
                     die.view_wh(),
                     max_size=3.0)

    def test_find_die_no_corner_in_start_view(self):
        die = SyntheticDie()
        start = die.ll + (1.0, 1.0)
        _center, obs = die.start_observation()
        with self.assertRaises(DieFindError):
            find_die(die.observer(obs.classifier), start, die.view_wh())


if __name__ == "__main__":
    unittest.main()
