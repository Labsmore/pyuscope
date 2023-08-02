#!/usr/bin/env python3
"""
Running the full suite:
-GRBL controller attached (no microscope)
-E3ISPM20000KPA camera attached
-v4levice as /dev/video0 that supports 640x480 video
    Ex: my X1 carbon has this as built in web camera
"""

import unittest
import os
import json5
import shutil
import time
import glob
import subprocess


class TestCommon(unittest.TestCase):
    def setUp(self):
        """Call before every test case."""
        print("")
        print("")
        print("")
        print("Start " + self._testMethodName)
        self.verbose = os.getenv("VERBOSE", "N") == "Y"
        self.verbose = int(os.getenv("TEST_VERBOSE", "0"))
        self.planner_dir = "/tmp/pyuscope/planner"
        if os.path.exists("/tmp/pyuscope"):
            shutil.rmtree("/tmp/pyuscope")
        os.mkdir("/tmp/pyuscope")

    def tearDown(self):
        """Call after every test case."""

    def cs_auto(self, directory):
        subprocess.check_call(f"rm -rf {directory}/hdr", shell=True)
        subprocess.check_call(f"rm -rf {directory}/stack", shell=True)
        subprocess.check_call(f"rm -rf {directory}/correct", shell=True)
        subprocess.check_call(f"./utils/cs_auto.py --no-upload {directory}",
                              shell=True)
        subprocess.check_call(f"rm -rf {directory}/hdr", shell=True)
        subprocess.check_call(f"rm -rf {directory}/correct", shell=True)
        subprocess.check_call(f"rm -rf {directory}/stack", shell=True)

    def test_hdr(self):
        self.cs_auto("test/stitch/test-hdr_img1-hdr3")
        self.cs_auto("test/stitch/test-hdr_img4-hdr3")

    def test_stack(self):
        self.cs_auto("test/stitch/test-stack_img1-stack2")
        self.cs_auto("test-stack_img1-stack10")


if __name__ == "__main__":
    unittest.main()
