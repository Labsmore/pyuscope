#!/usr/bin/env -S uv run
"""
Kinematics frame sync vs imager property changes (ex: HDR exposure)
No hardware: the imager simulates frames in flight when a property is written
"""

import unittest
import time

from uscope.config import USCKinematics
from uscope.imager.imager import Imager
from uscope.imager.properties import MockImagerProperties
from uscope.kinematics import Kinematics


class LaggyImager(Imager):
    """
    Each frame reports the exposure it was taken with
    The next latency frames after a write were already exposing / in flight
    and still have the old value
    """
    def __init__(self, latency):
        Imager.__init__(self)
        self.latency = latency
        self.exposure = 0
        self.in_flight = []
        self.gets = 0
        # Called at the start of get() to inject events mid flush
        self.on_get = None

    def _set_properties(self, vals):
        self.exposure = vals["exposure"]

    def _get_properties(self):
        return {"exposure": self.exposure}

    def get(self, **_kwargs):
        if self.on_get:
            self.on_get()
        self.gets += 1
        while len(self.in_flight) <= self.latency:
            self.in_flight.append(self.exposure)
        return self.in_flight.pop(0)


class FakeUSC:
    def __init__(self, kinematics_j):
        self.kinematics = USCKinematics(kinematics_j)


class FakeMicroscope:
    def __init__(self, imager, kinematics_j):
        self.imager = imager
        self.motion = None
        self.usc = FakeUSC(kinematics_j)
        imager.microscope = self

    def imager_ts(self):
        return self.imager


class FakeSession:
    def __init__(self, microscope):
        self.microscope = microscope


def make_kinematics(imager, **kinematics_j):
    microscope = FakeMicroscope(imager, kinematics_j)
    kinematics = Kinematics(microscope=microscope, log=lambda s="": None)
    kinematics.configure(tsettle_motion=0.0,
                         tsettle_hdr=0.0,
                         tsettle_autofocus=0.0)
    return kinematics


class TestFrameSync(unittest.TestCase):
    def hdr_bracket(self, imager, kinematics, exposures):
        got = []
        for exposure in exposures:
            imager.set_properties({"exposure": exposure})
            kinematics.wait_imaging_ok()
            got.append(imager.get())
        return got

    def test_hdr_gets_new_exposure(self):
        """Default config skips the frames in flight during the write"""
        imager = LaggyImager(latency=2)
        kinematics = make_kinematics(imager)
        exposures = [100, 400, 1600, 100]
        self.assertEqual(self.hdr_bracket(imager, kinematics, exposures),
                         exposures)

    def test_single_flush_frame_is_stale(self):
        """The old single frame flush isn't enough for a 2 frame latency"""
        imager = LaggyImager(latency=2)
        kinematics = make_kinematics(imager, frame_sync_frames_hdr=1)
        got = self.hdr_bracket(imager, kinematics, [100, 400, 1600])
        self.assertNotEqual(got, [100, 400, 1600])

    def test_no_change_no_flush(self):
        imager = LaggyImager(latency=2)
        kinematics = make_kinematics(imager)
        kinematics.wait_imaging_ok()
        gets = imager.gets
        kinematics.wait_imaging_ok()
        self.assertEqual(imager.gets, gets)

    def test_change_during_flush_resyncs(self):
        """
        A property written while the flush is in progress (ex: another
        thread) must not be covered by that flush
        """
        imager = LaggyImager(latency=2)
        kinematics = make_kinematics(imager)
        kinematics.wait_imaging_ok()

        def racing_set():
            imager.on_get = None
            time.sleep(0.01)
            imager.set_properties({"exposure": 800})
            # Flush frames come back after the write
            time.sleep(0.01)

        imager.set_properties({"exposure": 200})
        imager.on_get = racing_set
        kinematics.wait_imaging_ok()
        gets = imager.gets
        kinematics.wait_imaging_ok()
        self.assertGreater(imager.gets, gets)
        self.assertEqual(imager.get(), 800)

    def test_frame_sync_disabled(self):
        imager = LaggyImager(latency=2)
        kinematics = make_kinematics(imager, frame_sync=False)
        imager.set_properties({"exposure": 100})
        kinematics.wait_imaging_ok()
        self.assertEqual(imager.gets, 0)


class SlowWriteImager(LaggyImager):
    def __init__(self):
        LaggyImager.__init__(self, latency=0)
        self.t_written = None

    def _set_properties(self, vals):
        time.sleep(0.05)
        LaggyImager._set_properties(self, vals)
        self.t_written = time.time()


class TestPropertyChangeTime(unittest.TestCase):
    def test_set_properties_stamps_after_write(self):
        imager = SlowWriteImager()
        imager.set_properties({"exposure": 100})
        self.assertGreaterEqual(imager.last_properties_change,
                                imager.t_written)

    def test_wait_hdr_measured_from_write(self):
        """Time spent in the write must not count toward tsettle_hdr"""
        imager = SlowWriteImager()
        kinematics = make_kinematics(imager, frame_sync=False)
        kinematics.set_tsettle_hdr(0.1)
        imager.set_properties({"exposure": 100})
        kinematics.wait_imaging_ok()
        self.assertGreaterEqual(time.time() - imager.t_written, 0.1)

    def test_raw_prop_write_stamps(self):
        """Writes bypassing set_properties() (ex: GUI slider) still count"""
        imager = LaggyImager(latency=0)
        microscope = FakeMicroscope(imager, {})
        properties = MockImagerProperties(FakeSession(microscope))
        imager.last_properties_change = 0.0
        tbefore = time.time()
        properties.raw_prop_write("expotime", 100)
        self.assertGreaterEqual(imager.last_properties_change, tbefore)


if __name__ == "__main__":
    unittest.main()
