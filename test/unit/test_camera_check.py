#!/usr/bin/env -S uv run
"""
Microscope config vs attached camera sanity checks
Uses gst-videotestsrc only: no camera hardware is opened
"""

import os
import shutil
import tempfile

# Keep test data out of the working tree
# Must be set before uscope.config is imported
_TMP_ROOT = tempfile.mkdtemp(prefix="pyuscope-test-camera-check-")
os.environ["PYUSCOPE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")

import types
import unittest

from uscope.imager import camera_check
from uscope.imager.camera_check import CameraNotFound
from uscope.microscope import Microscope


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


class CameraCheckLogicTestCase(unittest.TestCase):
    def test_caps_sizes(self):
        caps = ("video/x-raw, format=(string)RGB, width=(int)5440, "
                "height=(int)3648, framerate=(fraction)0/1; "
                "video/x-raw, format=(string)GRAY8, width=(int)5440, "
                "height=(int)3648; "
                "video/x-raw, format=(string)YUY2, width=(int)640, "
                "height=(int)480")
        self.assertEqual(camera_check.caps_sizes(caps), [(5440, 3648),
                                                         (640, 480)])
        ranges = ("video/x-raw, width=(int)[ 1, 2147483647 ], "
                  "height=(int)[ 1, 2147483647 ]")
        self.assertEqual(camera_check.caps_sizes(ranges), [])

    def test_size_caps_str(self):
        from uscope.gst_util import Gst
        caps = Gst.Caps("video/x-raw,format=RGB,width=5440,height=3648; "
                        "video/x-bayer,format=rggb,width=5440,height=3648")
        caps_str = caps.to_string()
        self.assertTrue(
            caps.can_intersect(
                Gst.Caps(camera_check.size_caps_str(caps_str, 5440, 3648))))
        self.assertFalse(
            caps.can_intersect(
                Gst.Caps(camera_check.size_caps_str(caps_str, 3704, 2778))))

    def test_match(self):
        expected = {"wh": (5440, 3648), "model": "E3ISPM20000KPA"}
        found = {
            "model": "E3ISPM20000KPA",
            "serial": "TP123",
            "wh_supported": True,
            "sizes": [(5440, 3648)],
        }
        self.assertEqual(camera_check.check_camera(expected, found), [])

    def test_model_substring_case_insensitive(self):
        self.assertTrue(
            camera_check.model_matches("e3ispm20000kpa",
                                       "ToupTek E3ISPM20000KPA"))
        self.assertFalse(camera_check.model_matches("E3ISPM20000KPA", "MU800"))

    def test_model_mismatch(self):
        issues = camera_check.check_camera(
            {
                "wh": (5440, 3648),
                "model": "E3ISPM20000KPA"
            }, {
                "model": "MU800",
                "wh_supported": True
            })
        self.assertEqual(len(issues), 1)
        self.assertIn("E3ISPM20000KPA", issues[0])
        self.assertIn("MU800", issues[0])

    def test_no_model_configured(self):
        # Configs don't have to specify a model
        self.assertEqual(
            camera_check.check_camera({"wh": (800, 750)}, {
                "model": "MU800",
                "wh_supported": None
            }), [])

    def test_size_mismatch(self):
        issues = camera_check.check_camera({"wh": (5440, 3648)}, {
            "wh_supported": False,
            "sizes": [(3704, 2778)]
        })
        self.assertEqual(len(issues), 1)
        self.assertIn("5440x3648", issues[0])
        self.assertIn("3704x2778", issues[0])

    def test_v4l2_card_mismatch(self):
        issues = camera_check.check_camera(
            {
                "wh": (1280, 960),
                "v4l2_name": "Hayear Camera: HY-800B"
            }, {
                "card": "Integrated Camera",
                "wh_supported": True
            })
        self.assertEqual(len(issues), 1)
        self.assertIn("Integrated Camera", issues[0])

    def test_messages(self):
        found = {"model": "MU800", "serial": "TP1", "sizes": [(3264, 2448)]}
        msg = camera_check.mismatch_message("lip-a1", "GRBL metadata",
                                            "gst-toupcamsrc", found,
                                            ["some problem"])
        for s in ("lip-a1", "GRBL metadata", "gst-toupcamsrc", "MU800", "TP1",
                  "3264x2448", "some problem", "--microscope",
                  "PYUSCOPE_MICROSCOPE", "mock-grbl"):
            self.assertIn(s, msg)
        msg = camera_check.not_found_message("lip-a1", "caller",
                                             "gst-toupcamsrc", "unplugged?")
        for s in ("lip-a1", "unplugged?", "--microscope",
                  "PYUSCOPE_MICROSCOPE"):
            self.assertIn(s, msg)


class ConfigReasonTestCase(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.pop("PYUSCOPE_MICROSCOPE", None)

    def tearDown(self):
        os.environ.pop("PYUSCOPE_MICROSCOPE", None)
        if self.old_env is not None:
            os.environ["PYUSCOPE_MICROSCOPE"] = self.old_env

    def test_name(self):
        m = Microscope(name="mock", auto=False, configure=False)
        self.assertEqual(m.name, "mock")
        self.assertIn("caller", m.config_reason)

    def test_env(self):
        os.environ["PYUSCOPE_MICROSCOPE"] = "mock"
        m = Microscope(auto=False, configure=False)
        self.assertEqual(m.name, "mock")
        self.assertIn("PYUSCOPE_MICROSCOPE", m.config_reason)

    def test_default(self):
        # hardware=False: don't probe for GRBL
        m = Microscope(auto=False, configure=False, hardware=False)
        self.assertEqual(m.name, "mock-grbl")
        self.assertIn("default", m.config_reason)


class PipelineCheckTestCase(unittest.TestCase):
    """
    Run a real gst-videotestsrc pipeline but fake what the plugin reports
    """

    def make_pipeline(self, imager_config=None):
        from uscope.imager.gst_pipeline import GstImagerPipeline
        microscope = Microscope(name="mock-grbl", auto=False, configure=False)
        # get_usc() caches the config across Microscope instances
        j = microscope.usc.imager.j
        orig = dict(j)
        self.addCleanup(lambda: (j.clear(), j.update(orig)))
        j.update(imager_config or {})
        session = types.SimpleNamespace(microscope=microscope,
                                        usc=microscope.usc)
        self.logs = []
        vidpip = GstImagerPipeline(session, log=self.logs.append)
        session.vidpip = vidpip
        vidpip.setupGst()
        self.addCleanup(vidpip.stop)
        return vidpip

    def test_ok(self):
        vidpip = self.make_pipeline()
        vidpip.run()
        self.assertIsNone(vidpip.camera_mismatch)

    def test_not_found(self):
        vidpip = self.make_pipeline()
        vidpip.imager_aplugin.camera_present = lambda source: False
        with self.assertRaises(CameraNotFound) as cm:
            vidpip.run()
        self.assertIn("mock-grbl", str(cm.exception))
        self.assertIn("--microscope", str(cm.exception))

    def test_model_mismatch(self):
        vidpip = self.make_pipeline({"model": "E3ISPM20000KPA"})
        vidpip.imager_aplugin.probe_camera = lambda source: {
            "model": "MU800",
            "serial": "TP1"
        }
        vidpip.run()
        self.assertIsNotNone(vidpip.camera_mismatch)
        self.assertIn("MU800", vidpip.camera_mismatch)
        self.assertTrue(any("WARNING" in log for log in self.logs))


if __name__ == "__main__":
    unittest.main()
