#!/usr/bin/env python3

import unittest
import os
import json5
from uscope.motion.hal import MockHal
from uscope.imager.imager import MockImager
from uscope.imager import gst
import uscope.planner
from uscope.config import get_usj
from uscope.util import printj
from uscope.planner import microscope_to_planner
import shutil
import time


class PlannerTestCase(unittest.TestCase):
    def setUp(self):
        """Call before every test case."""
        self.verbose = int(os.getenv("TEST_VERBOSE", "0"))
        self.planner_dir = "/tmp/pyuscope/planner"
        if os.path.exists("/tmp/pyuscope"):
            shutil.rmtree("/tmp/pyuscope")
        os.mkdir("/tmp/pyuscope")

    def tearDown(self):
        """Call after every test case."""
        pass

    def simple_planner(self, pconfig, dry=False):
        if self.verbose:
            log = print
        else:
            log = lambda x: None
        motion = MockHal(log=log)
        imager = MockImager(width=150, height=50)
        planner = uscope.planner.Planner(pconfig,
                                         motion=motion,
                                         imager=imager,
                                         out_dir=self.planner_dir,
                                         progress_cb=None,
                                         dry=dry,
                                         log=log,
                                         verbosity=2)
        return planner.run()

    def simple_config(self):
        """
        Simple scan config
        4 images wide
        3 images tall
        """
        return {
            # "microscope": json5.load(open("configs/mock/microscope.j5", "r")),
            # "motion": {
            #    "origin": "ll",
            #},
            "imager": {
                #    "scalar": 0.5,
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

    def test_dry(self):
        """blah"""
        meta = self.simple_planner(pconfig=self.simple_config(), dry=True)
        # printj(meta)
        expect_images = 12
        self.assertEqual(expect_images, meta["planner"]["pictures_taken"])
        self.assertEqual(expect_images, meta["planner"]["pictures_to_take"])
        self.assertEqual(expect_images, len(meta["images"]))

    def test_simple(self):
        meta = self.simple_planner(pconfig=self.simple_config(), dry=False)
        # printj(meta)
        expect_images = 12
        self.assertEqual(expect_images, meta["planner"]["pictures_taken"])
        self.assertEqual(expect_images, meta["planner"]["pictures_to_take"])
        self.assertEqual(expect_images, len(meta["images"]))

    def test_tsettle(self):
        """
        test pconfig["tsettle"]
        This controls how long to wait between movement and snapping a picture
        """

        pconfig = self.simple_config()
        pconfig["tsettle"] = 0.0
        # get a baseline without
        tstart = time.time()
        self.simple_planner(pconfig=pconfig)
        d0 = time.time() - tstart

        pconfig["tsettle"] = 0.01
        tstart = time.time()
        meta = self.simple_planner(pconfig=pconfig)
        d1 = time.time() - tstart

        # should be 12 images and added 0.1 sec per image
        # so should have increased by at least a second
        # make it quicker...I'm impatient
        self.assertEqual(12, meta["planner"]["pictures_taken"])
        d = d1 - d0
        self.verbose and print("delta: %0.2f" % d)
        assert d > 0.1

    def test_scalar(self):
        pconfig = self.simple_config()
        pconfig["imager"]["scalar"] = 0.5
        self.simple_planner(pconfig=pconfig)

    def test_backlash(self):
        pconfig = self.simple_config()
        pconfig.setdefault("motion", {})["backlash"] = 0.1
        self.simple_planner(pconfig=pconfig)

    def test_origin_ll(self):
        pconfig = self.simple_config()
        pconfig.setdefault("motion", {})["origin"] = "ll"
        self.simple_planner(pconfig=pconfig)

    def test_origin_ul(self):
        pconfig = self.simple_config()
        pconfig.setdefault("motion", {})["origin"] = "ul"
        self.simple_planner(pconfig=pconfig)

    def test_exclude(self):
        pconfig = self.simple_config()
        pconfig["exclude"] = [{"r0": 0, "c0": 0, "r1": 1, "c1": 1}]
        self.simple_planner(pconfig=pconfig)

    def test_microscope_to_planner(self):
        usj = get_usj(name="mock")
        contour = {
            "start": {
                "x": 0.0,
                "y": 0.0,
            },
            "end": {
                "x": 2.0,
                "y": 1.0,
            }
        }
        microscope_to_planner(usj, objectivei=0, contour=contour)


class GstTestCase(unittest.TestCase):
    def setUp(self):
        """Call before every test case."""
        self.verbose = int(os.getenv("TEST_VERBOSE", "0"))
        self.planner_dir = "/tmp/pyuscope/planner"
        if os.path.exists("/tmp/pyuscope"):
            shutil.rmtree("/tmp/pyuscope")
        os.mkdir("/tmp/pyuscope")

    def tearDown(self):
        """Call after every test case."""
        pass

    def test_mock(self):
        usj = get_usj(name="mock")
        gst.get_cli_imager_by_config(usj)


if __name__ == "__main__":
    unittest.main()
