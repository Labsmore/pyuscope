#!/usr/bin/env -S uv run
import unittest

from uscope.imager.imager_util import hdr_exposure_sequence, format_exposure_csv


class TestHdrSequence(unittest.TestCase):
    def test_integer_stops(self):
        self.assertEqual(hdr_exposure_sequence(1000, 1, 2),
                         [250.0, 1000.0, 4000.0])

    def test_fractional_stops(self):
        seq = hdr_exposure_sequence(1000, 2, 0.5)
        self.assertEqual(len(seq), 5)
        self.assertAlmostEqual(seq[0], 500.0)
        self.assertAlmostEqual(seq[1], 1000 / 2**0.5)
        self.assertAlmostEqual(seq[2], 1000.0)
        self.assertAlmostEqual(seq[4], 2000.0)
        self.assertEqual(seq, sorted(seq))

    def test_zero_brackets(self):
        self.assertEqual(hdr_exposure_sequence(123, 0, 1), [123])

    def test_invalid(self):
        with self.assertRaises(ValueError):
            hdr_exposure_sequence(1000, 1, 0)
        with self.assertRaises(ValueError):
            hdr_exposure_sequence(1000, -1, 1)

    def test_format_large(self):
        self.assertEqual(format_exposure_csv([250.4, 1000.0, 4000.6]),
                         "250,1000,4001")

    def test_format_small_keeps_distinct(self):
        # Ex: V4L2 exposure in 100 us units
        seq = hdr_exposure_sequence(1, 1, 0.5)
        s = format_exposure_csv(seq)
        vals = [float(x) for x in s.split(",")]
        self.assertEqual(len(set(vals)), 3)


if __name__ == "__main__":
    unittest.main()
