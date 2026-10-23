#!/usr/bin/env -S uv run
"""Measure tab label placement and line hit testing geometry."""

import unittest

from uscope.gui.measure_util import point_segment_distance, label_normal, label_rect


class TestPointSegmentDistance(unittest.TestCase):
    def test_perpendicular(self):
        self.assertAlmostEqual(point_segment_distance((5, 3), (0, 0), (10, 0)),
                               3)

    def test_past_end(self):
        self.assertAlmostEqual(
            point_segment_distance((13, 4), (0, 0), (10, 0)), 5)

    def test_vertical_and_horizontal(self):
        # Axis aligned lines have a zero area bounding box
        self.assertAlmostEqual(point_segment_distance((1, 5), (0, 0), (0, 10)),
                               1)
        self.assertAlmostEqual(point_segment_distance((5, 0), (0, 0), (10, 0)),
                               0)

    def test_degenerate(self):
        self.assertAlmostEqual(point_segment_distance((3, 4), (0, 0), (0, 0)),
                               5)


class TestLabelNormal(unittest.TestCase):
    def test_horizontal_is_up(self):
        self.assertEqual(label_normal((0, 0), (10, 0)), (0, -1))
        self.assertEqual(label_normal((10, 0), (0, 0)), (0, -1))

    def test_vertical_is_right(self):
        self.assertEqual(label_normal((0, 0), (0, 10)), (1, 0))
        self.assertEqual(label_normal((0, 10), (0, 0)), (1, 0))

    def test_diagonal_independent_of_direction(self):
        n1 = label_normal((0, 0), (10, 10))
        n2 = label_normal((10, 10), (0, 0))
        self.assertAlmostEqual(n1[0], n2[0])
        self.assertAlmostEqual(n1[1], n2[1])
        self.assertLess(n1[1], 0)


class TestLabelRect(unittest.TestCase):
    def assertRect(self, got, expected):
        for g, e in zip(got, expected):
            self.assertAlmostEqual(g, e)

    def test_horizontal_centered_above(self):
        r = label_rect((0, 100), (100, 100), (40, 20), offset=5)
        # Centered on x=50, bottom edge 5 above the line
        self.assertRect(r, (30, 75, 40, 20))

    def test_vertical_centered_right(self):
        r = label_rect((100, 0), (100, 100), (40, 20), offset=5)
        self.assertRect(r, (105, 40, 40, 20))

    def test_direction_independent(self):
        self.assertRect(label_rect((0, 0), (80, 60), (40, 20)),
                        label_rect((80, 60), (0, 0), (40, 20)))

    def test_diagonal_clears_line(self):
        a = (0, 0)
        b = (100, 100)
        x, y, w, h = label_rect(a, b, (40, 20), offset=5)
        corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
        nearest = min(point_segment_distance(c, a, b) for c in corners)
        self.assertGreaterEqual(nearest, 5 - 1e-9)

    def test_flips_at_top_edge(self):
        bounds = (0, 0, 200, 200)
        r = label_rect((0, 2), (100, 2), (40, 20), offset=5, bounds=bounds)
        # No room above so goes below
        self.assertRect(r, (30, 7, 40, 20))

    def test_clamped_inside(self):
        bounds = (0, 0, 200, 200)
        r = label_rect((190, 2), (190, 198), (40, 20), offset=5, bounds=bounds)
        x, y, w, h = r
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + w, 200)
        # Right side is out of bounds, so label sits to the left
        self.assertAlmostEqual(x, 190 - 5 - 40)

    def test_clamped_when_nothing_fits(self):
        bounds = (0, 0, 30, 30)
        x, y, w, h = label_rect((0, 15), (30, 15), (40, 20), bounds=bounds)
        self.assertEqual((x, y), (0, 0))


if __name__ == "__main__":
    unittest.main()
