#!/usr/bin/env -S uv run
"""
Flat field (FF) correction must be applied to scan images exactly once
Processed captures are corrected by SnapshotCSIP => DirCSIP must not redo it

mock-grbl gst-videotestsrc outputs a flat gray frame
A synthetic vignette FF cal makes each application visible as edge gain
"""

import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="pyuscope-ff-once-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

import glob
import json
import threading
import unittest

import numpy as np
from PIL import Image

from uscope import config as uconfig
from uscope.imagep.pipeline import CSImageProcessor
from uscope.imagep.streams import scan_capture_corrections
from uscope.microscope import Microscope
from uscope.planner.planner_util import microscope_to_planner_config
from uscope.scan_runner import ScanRunnerListener
from uscope.session import HeadlessSession

GRAY = 80
# Corner brightness relative to center in the FF cal
VIGNETTE = 0.6


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def write_ff_cal():
    m = Microscope(name="mock-grbl",
                   log=lambda *a, **k: None,
                   make_imager=False,
                   hardware=False)
    w, h = m.usc.imager.final_wh()
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = (((xx - w / 2) / (w / 2))**2 + ((yy - h / 2) / (h / 2))**2) / 2
    flat = np.clip((1.0 - (1.0 - VIGNETTE) * r2) * 250, 0,
                   255).astype(np.uint8)
    fn = m.usc.imager.ff_cal_fn()
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    Image.fromarray(np.dstack([flat] * 3)).save(fn)
    uconfig.usc = None


def corner_gain(fn):
    """Corner / center brightness"""
    with Image.open(fn) as im:
        a = np.array(im.convert("L"), dtype=float)
    h, w = a.shape
    center = a[h // 2 - 10:h // 2 + 10, w // 2 - 10:w // 2 + 10].mean()
    return a[:10, :10].mean() / center


class TestScanCaptureCorrections(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_TMP)

    def write_scanj(self, j):
        with open(os.path.join(self.dir, "uscan.json"), "w") as f:
            json.dump(j, f)

    def test_not_a_scan(self):
        self.assertIsNone(scan_capture_corrections(self.dir))

    def test_recorded(self):
        self.write_scanj({
            "image-capture": {
                "corrections": ["correct-ff1", "correct-wb"]
            },
            "time_end": "2026-10-23T00:00:00",
        })
        self.assertEqual(scan_capture_corrections(self.dir),
                         ["correct-ff1", "correct-wb"])

    def test_recorded_none(self):
        self.write_scanj({
            "image-capture": {
                "corrections": []
            },
            "time_end": "2026-10-23T00:00:00",
        })
        self.assertEqual(scan_capture_corrections(self.dir), [])

    def test_legacy_processed(self):
        self.write_scanj({
            "image-capture": {
                "captured": 4
            },
            "pconfig": {
                "imager": {}
            },
            "time_end": "2024-05-01T12:00:00.123456",
        })
        self.assertEqual(scan_capture_corrections(self.dir), ["correct-ff1"])

    def test_legacy_raw(self):
        self.write_scanj({
            "pconfig": {
                "imager": {
                    "get_mode": "raw"
                }
            },
            "time_end": "2024-05-01T12:00:00",
        })
        self.assertEqual(scan_capture_corrections(self.dir), [])

    def test_legacy_before_processed_capture(self):
        self.write_scanj({
            "pconfig": {
                "imager": {}
            },
            "time_end": "2023-09-01T12:00:00",
        })
        self.assertEqual(scan_capture_corrections(self.dir), [])


class TestFFOnce(unittest.TestCase):
    session = None

    @classmethod
    def setUpClass(cls):
        # Before any image processor loads the calibration
        write_ff_cal()
        color = 0xff000000 | (GRAY << 16) | (GRAY << 8) | GRAY
        cls.session = HeadlessSession(microscope_name="mock-grbl",
                                      imager_config={
                                          "source_properties": {
                                              "pattern": "solid-color",
                                              "foreground-color": color,
                                          }
                                      },
                                      log=lambda *a, **k: None)
        assert cls.session.usc.imager.has_ff_cal()

    @classmethod
    def tearDownClass(cls):
        cls.session.shutdown()

    def scan(self, basename, get_mode=None):
        done = threading.Event()

        class Listener(ScanRunnerListener):
            def queue_done(self, results):
                done.set()

        listener = Listener()
        runner = self.session.scan_runner
        runner.add_listener(listener)
        self.addCleanup(runner.listeners.remove, listener)
        pconfig = microscope_to_planner_config(self.session.microscope,
                                               objectivei=0,
                                               contour={
                                                   "start": {
                                                       "x": 0.0,
                                                       "y": 0.0
                                                   },
                                                   "end": {
                                                       "x": 0.5,
                                                       "y": 0.5
                                                   },
                                               })
        if get_mode:
            pconfig["imager"]["get_mode"] = get_mode
        hconfig = {
            "pconfig": pconfig,
            "out_dir_config": {
                "user_basename": basename
            },
        }
        runner.run([hconfig])
        self.assertTrue(done.wait(120), "scan did not finish")
        runner.join(timeout=5)
        return hconfig["out_dir"]

    def process(self, d, configj={}):
        ip = CSImageProcessor(nthreads=1,
                              microscope=self.session.microscope,
                              log=lambda *a, **k: None)
        ip.start()
        try:
            self.assertTrue(ip.ready.wait(5.0))
            configj = dict(configj,
                           write_html_viewer=False,
                           keep_intermediates=False)
            ip.process_dir(d, upload=False, configj=configj, verbose=False)
        finally:
            ip.shutdown()
        fns = glob.glob(os.path.join(d, "c*_r*.jpg"))
        self.assertTrue(fns)
        return [corner_gain(fn) for fn in fns]

    def scan_corrections(self, d):
        with open(os.path.join(d, "uscan.json")) as f:
            return json.load(f)["image-capture"].get("corrections")

    def assert_once(self, gains):
        for gain in gains:
            self.assertAlmostEqual(gain, 1 / VIGNETTE, delta=0.15)

    def test_processed_scan(self):
        d = self.scan("processed")
        self.assertEqual(self.scan_corrections(d), ["correct-ff1"])
        self.assert_once(self.process(d))

    def test_raw_scan(self):
        d = self.scan("raw", get_mode="raw")
        self.assertEqual(self.scan_corrections(d), [])
        for fn in glob.glob(os.path.join(d, "c*_r*.jpg")):
            self.assertAlmostEqual(corner_gain(fn), 1.0, delta=0.05)
        self.assert_once(self.process(d))

    def test_legacy_processed_scan(self):
        d = self.scan("legacy")
        fn = os.path.join(d, "uscan.json")
        with open(fn) as f:
            j = json.load(f)
        del j["image-capture"]["corrections"]
        del j["image-capture"]["mode"]
        with open(fn, "w") as f:
            json.dump(j, f)
        self.assert_once(self.process(d))

    def test_force_ff(self):
        d = self.scan("force")
        gains = self.process(d, {"ff_correction": True})
        for gain in gains:
            self.assertGreater(gain, 1 / VIGNETTE**2 - 0.3)


if __name__ == "__main__":
    unittest.main()
