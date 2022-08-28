#!/usr/bin/env python3

import unittest
import os
import json5
from uscope.motion.hal import MockHal
from uscope.imager.imager import MockImager
from uscope.imager import gst
import uscope.planner
from uscope.util import printj


class TestCase(unittest.TestCase):
    def setUp(self):
        """Call before every test case."""
        self.verbose = int(os.getenv("VERBOSE", "0"))

    def tearDown(self):
        """Call after every test case."""
        pass

    def test_planer_dry(self):
        """blah"""
        if self.verbose:
            log = print
        else:
            log = lambda x: None
        motion = MockHal(log=log)
        imager = MockImager(width=150, height=50)
        pconfig = {
            # "microscope": json5.load(open("configs/mock/microscope.j5", "r")),
            "motion": {
                "origin": "ll",
            },
            "imager": {
                "scalar": 0.5,
                "x_view": 1.0,
            },
            "contour": {
                "start": {
                    "x": 0.0,
                    "y": 0.0,
                },
                "end": {
                    "x": 2.0,
                    "y": 1.0,
                },
            },
        }
        out_dir = "/tmp/pyuscope/planner"
        if os.path.exists(out_dir):
            os.rmdir(out_dir)
        planner = uscope.planner.Planner(pconfig,
                                         motion=motion,
                                         imager=imager,
                                         out_dir=out_dir,
                                         progress_cb=None,
                                         dry=True,
                                         log=log,
                                         verbosity=2)
        meta = planner.run()
        # printj(meta)
        expect_images = 12
        self.assertEqual(expect_images, meta["planner"]["pictures_taken"])
        self.assertEqual(expect_images, meta["planner"]["pictures_to_take"])
        self.assertEqual(expect_images, len(meta["images"]))


if __name__ == "__main__":
    unittest.main()
