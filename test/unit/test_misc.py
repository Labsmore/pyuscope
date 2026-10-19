#!/usr/bin/env -S uv run
"""
Hardware GRBL tests

WARNING: these move the stage (by a small amount, 0.01 mm per axis)
They are skipped unless TEST_GRBL=Y is set
Make sure the stage is homed and clear of obstructions first

For headless / mock coverage see test_mock.py
"""

import unittest
import os
import time
import glob

from uscope.motion.grbl import GRBL, GrblHal

# A small amount to move
DELTA = 0.01
# A reasonable feedrate
FEEDRATE = 100


def hardware_tests_enabled():
    return os.getenv("TEST_GRBL", "N") == "Y"


def find_grbl_port():
    ports = glob.glob("/dev/serial/by-id/usb-*_USB_Serial-if00-port0")
    if not ports:
        return None
    return ports[0]


def get_grbl(verbose=False):
    port = find_grbl_port()
    if port is None:
        print("GRBL: no plausible GRBL")
        return None
    ret = GRBL(port=port, verbose=verbose)
    print("GRBL: open ok")
    return ret


class GrblTestCommon(unittest.TestCase):
    def setUp(self):
        print("")
        print("Start " + self._testMethodName)
        if not hardware_tests_enabled():
            self.skipTest("Set TEST_GRBL=Y to run stage moving GRBL tests")
        self.grbl = get_grbl(verbose=int(os.getenv("TEST_VERBOSE", "0")))
        if not self.grbl:
            self.skipTest("No GRBL")

    def tearDown(self):
        if getattr(self, "grbl", None):
            self.grbl.close()


class GrblTestCase(GrblTestCommon):
    def test_stop(self):
        self.grbl.stop()

    def test_qstatus(self):
        status = self.grbl.qstatus()
        self.assertIn("status", status)
        self.assertIn("MPos", status)

    def test_mpos(self):
        mpos = self.grbl.mpos()
        self.assertEqual(set(mpos.keys()), {"x", "y", "z"})

    def test_move_absolute(self):
        mpos = self.grbl.mpos()
        for axis in "xyz":
            pos0 = mpos[axis]
            self.grbl.move_absolute(pos={axis: pos0 + DELTA}, f=FEEDRATE)
            self.grbl.move_absolute(pos={axis: pos0}, f=FEEDRATE)

    def test_hard_move_relative(self):
        for axis in "xyz":
            self.grbl.move_relative(pos={axis: +DELTA}, f=FEEDRATE, soft=False)
            self.grbl.move_relative(pos={axis: -DELTA}, f=FEEDRATE, soft=False)

    def test_soft_move_relative(self):
        for axis in "xyz":
            self.grbl.move_relative(pos={axis: +DELTA}, f=FEEDRATE, soft=True)
            self.grbl.move_relative(pos={axis: -DELTA}, f=FEEDRATE, soft=True)

    def test_wait_idle(self):
        self.grbl.wait_idle(timeout=5.0)

    def test_jog(self):
        self.grbl.jog_rel({"x": +DELTA}, rate=FEEDRATE)
        time.sleep(0.1)
        self.grbl.jog_cancel()
        self.grbl.jog_rel({"x": -DELTA}, rate=FEEDRATE)
        time.sleep(0.1)
        self.grbl.jog_cancel()
        self.grbl.wait_idle(timeout=5.0)

    def test_jog_cancel(self):
        self.grbl.jog_cancel()


class GrblHalTestCase(GrblTestCommon):
    def setUp(self):
        super().setUp()
        # Takes ownership of self.grbl
        self.gh = GrblHal(grbl=self.grbl)
        self.gh.configure({})

    def tearDown(self):
        if getattr(self, "gh", None):
            self.gh.close()
        super().tearDown()

    def test_pos(self):
        pos = self.gh.pos()
        self.assertEqual(set(pos.keys()), {"x", "y", "z"})

    def test_move_absolute(self):
        pos0s = self.gh.pos()
        for axis in "xyz":
            pos0 = pos0s[axis]
            self.gh.move_absolute(pos={axis: pos0 + DELTA})
            self.gh.move_absolute(pos={axis: pos0})

    def test_move_relative(self):
        for axis in "xyz":
            self.gh.move_relative(pos={axis: +DELTA})
            self.gh.move_relative(pos={axis: -DELTA})


if __name__ == "__main__":
    unittest.main()
