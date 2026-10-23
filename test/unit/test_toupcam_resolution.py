#!/usr/bin/env python3
"""
toupcamsrc resolution selection (no camera required)
"""

import json
import os
import shutil
import tempfile
import unittest

from uscope.imager.plugins.gst_toupcamsrc import resolution as tres

INFO = """\
Setting pipeline to PAUSED ...
toupcam debug info for Version(): 55.21541.20221010
  SDK_Brand: toupcam
  MaxBitDepth(): 12
  FanMaxSpeed(): -2147467263
  MaxSpeed(): 2
  MonoMode(): 1
  StillResolutionNumber(): 3
    eSize=0
          StillResolution(): 5440w x 3648h
          PixelSize(): 2.4w x 2.4h um
    eSize=1
          StillResolution(): 2736w x 1824h
          PixelSize(): 4.8w x 4.8h um
    eSize=2
          StillResolution(): 1824w x 1216h
          PixelSize(): 7.2w x 7.2h um
  ExpTimeRange(): min 244, max 15000000, def 40000
  Mode(): 0
  Temperature(): 400
  Revision(): 2
  SerialNumber(): TP2210101618037715CAC72D6E44CF4
  FwVersion(): 3.6.5.20220711
"""

R0 = {"esize": 0, "width": 5440, "height": 3648}
R1 = {"esize": 1, "width": 2736, "height": 1824}
R2 = {"esize": 2, "width": 1824, "height": 1216}
RES = [R0, R1, R2]
SN = "TP2210101618037715CAC72D6E44CF4"


class TestParse(unittest.TestCase):
    def test_parse(self):
        info = tres.parse_info(INFO)
        self.assertEqual(info["serial"], SN)
        self.assertEqual(info["resolutions"], RES)

    def test_parse_empty(self):
        info = tres.parse_info("No ToupCam devices found\n")
        self.assertEqual(info, {"serial": None, "resolutions": []})

    def test_parse_failed_resolution(self):
        # get_StillResolution() failure: no StillResolution line for eSize=1
        text = INFO.replace("          StillResolution(): 2736w x 1824h\n", "")
        info = tres.parse_info(text)
        self.assertEqual(info["resolutions"], [R0, R2])


class TestSelect(unittest.TestCase):
    def test_max(self):
        self.assertEqual(tres.max_resolution([R2, R1, R0]), R0)
        self.assertIsNone(tres.max_resolution([]))
        # Unknown esize (camera not probed)
        r = {"esize": None, "width": 1, "height": 1}
        self.assertEqual(tres.max_resolution([r, r.copy()]), r)
        self.assertEqual(tres.select(RES), (R0, "max"))

    def test_filter_settable(self):
        r3 = {"esize": 3, "width": 640, "height": 480}
        self.assertEqual(tres.filter_settable(RES + [r3], 2), RES)
        self.assertEqual(tres.filter_settable(RES + [r3], None), RES + [r3])

    def test_config(self):
        self.assertEqual(tres.select(RES, config_wh=(2736, 1824)),
                         (R1, "config"))
        self.assertEqual(
            tres.select(RES, config_wh=(2736, 1824), config_esize=1),
            (R1, "config"))
        self.assertEqual(tres.select(RES, config_esize=2), (R2, "config"))
        with self.assertRaises(ValueError):
            tres.select(RES, config_wh=(2736, 1824), config_esize=0)
        with self.assertRaises(ValueError):
            tres.select(RES, config_wh=(1000, 1000))
        with self.assertRaises(ValueError):
            tres.select(RES, config_esize=5)

    def test_config_no_probe(self):
        # Camera couldn't be queried: trust microscope.j5 as before
        r, how = tres.select([], config_wh=(5440, 3648), config_esize=0)
        self.assertEqual(how, "config")
        self.assertEqual(r, R0)
        with self.assertRaises(tres.CameraNotFound):
            tres.select([])

    def test_user(self):
        self.assertEqual(
            tres.select(RES, config_wh=(5440, 3648), user_wh=(1824, 1216)),
            (R2, "user"))
        self.assertEqual(tres.select(RES, user_wh=[2736, 1824]), (R1, "user"))
        # Unsupported user value falls through
        self.assertEqual(tres.select(RES, user_wh=(1, 1)), (R0, "max"))

    def test_label(self):
        self.assertEqual(tres.resolution_label(R0, RES), "5440 x 3648 (max)")
        self.assertEqual(tres.resolution_label(R1, RES), "2736 x 1824")


class TestImagerJ(unittest.TestCase):
    def test_apply_auto(self):
        j = {"source": "gst-toupcamsrc", "scalar": 0.5}
        tres.apply_to_imagerj(j, R1, max_r=R0)
        self.assertEqual((j["width"], j["height"]), (2736, 1824))
        self.assertEqual((j["native_width"], j["native_height"]), (5440, 3648))
        self.assertEqual(j["source_properties"], {"esize": 1})

    def test_apply_config_native(self):
        # Configured width/height keep defining native (calibration basis)
        j = {"width": 2736, "height": 1824}
        tres.apply_to_imagerj(j, R2, max_r=R0)
        self.assertEqual((j["native_width"], j["native_height"]), (2736, 1824))
        self.assertEqual((j["width"], j["height"]), (1824, 1216))

        j = {"native_width": 100, "native_height": 50}
        tres.apply_to_imagerj(j, R2, max_r=R0)
        self.assertEqual((j["native_width"], j["native_height"]), (100, 50))

    def test_apply_unknown_esize(self):
        j = {"width": 5440, "height": 3648}
        tres.apply_to_imagerj(j, {
            "esize": None,
            "width": 5440,
            "height": 3648
        })
        self.assertNotIn("source_properties", j)

    def test_config_resolution(self):
        self.assertEqual(tres.config_resolution({}), (None, None))
        self.assertEqual(
            tres.config_resolution({
                "width": 5440,
                "height": 3648,
                "source_properties": {
                    "esize": 0
                }
            }), ((5440, 3648), 0))

    def test_save_load(self):
        cachej_sn = {}
        tres.save_resolution(cachej_sn, (2736, 1824), SN)
        self.assertEqual(tres.saved_resolution(cachej_sn, SN), (2736, 1824))
        self.assertEqual(tres.saved_resolution(cachej_sn, None), (2736, 1824))
        self.assertIsNone(tres.saved_resolution(cachej_sn, "other"))
        tres.save_resolution(cachej_sn, None, SN)
        self.assertEqual(cachej_sn, {"imager": {}})
        self.assertIsNone(tres.saved_resolution(cachej_sn, SN))


class FakeMicroscope:
    cal_store = None


class FakeUSCImager:
    def __init__(self, j):
        self.j = j
        self.microscope = FakeMicroscope()


class TestSelector(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache_fn = os.path.join(self.tmp, "argus.j5")
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_cache(self, wh, camera_sn=SN):
        cachej_sn = {}
        tres.save_resolution(cachej_sn, wh, camera_sn)
        with open(self.cache_fn, "w") as f:
            json.dump({"microscopes": {"scope_sn-1": cachej_sn}}, f)

    def selector(self, j, probe_info=INFO, max_esize=2):
        sel = tres.ResolutionSelector(FakeUSCImager(j),
                                      cache_fn=self.cache_fn,
                                      model_serial_string="scope_sn-1",
                                      log=self.logs.append)

        def probe_func():
            if probe_info is None:
                raise tres.CameraNotFound("no camera")
            return tres.parse_info(probe_info)

        sel.configure(probe_func=probe_func, max_esize_func=lambda: max_esize)
        return sel

    def test_default_max(self):
        j = {"source": "gst-toupcamsrc"}
        sel = self.selector(j)
        self.assertEqual(sel.active, R0)
        self.assertEqual(sel.how, "max")
        self.assertEqual(sel.camera_sn, SN)
        self.assertIsNone(sel.override)
        self.assertEqual((j["width"], j["height"]), (5440, 3648))
        self.assertTrue(sel.can_select())
        self.assertFalse(sel.restart_required())

    def test_saved(self):
        self.write_cache((2736, 1824))
        j = {"width": 5440, "height": 3648}
        sel = self.selector(j)
        self.assertEqual(sel.active, R1)
        self.assertEqual(sel.how, "user")
        self.assertEqual(sel.override, (2736, 1824))
        self.assertEqual((j["width"], j["height"]), (2736, 1824))
        self.assertEqual(j["source_properties"]["esize"], 1)
        # Restoring the default goes back to microscope.j5
        sel.set_override(None)
        self.assertEqual(sel.selected_wh(), (5440, 3648))
        self.assertTrue(sel.restart_required())
        cachej_sn = {"imager": {"format": "RGB"}}
        sel.cache_sn_save(cachej_sn)
        self.assertEqual(cachej_sn, {"imager": {"format": "RGB"}})

    def test_saved_other_camera(self):
        self.write_cache((2736, 1824), camera_sn="other")
        sel = self.selector({})
        self.assertEqual(sel.active, R0)

    def test_saved_pixel_crop(self):
        self.write_cache((2736, 1824))
        j = {
            "width": 5440,
            "height": 3648,
            "crop_pixels": {
                "left": 10,
                "right": 10
            }
        }
        sel = self.selector(j)
        self.assertEqual(sel.active, R0)
        self.assertEqual(sel.how, "config")
        self.assertFalse(sel.can_select())
        with self.assertRaises(ValueError):
            sel.set_override((2736, 1824))

    def test_saved_unsettable(self):
        # Saved resolution the plugin can no longer select
        self.write_cache((1824, 1216))
        sel = self.selector({}, max_esize=1)
        self.assertEqual(sel.active, R0)
        self.assertEqual(sel.resolutions, [R0, R1])

    def test_set_override(self):
        sel = self.selector({})
        with self.assertRaises(ValueError):
            sel.set_override((1, 1))
        sel.set_override([1824, 1216])
        self.assertEqual(sel.override, (1824, 1216))
        self.assertTrue(sel.restart_required())
        cachej_sn = {}
        sel.cache_sn_save(cachej_sn)
        self.assertEqual(cachej_sn["imager"]["resolution"], {
            "width": 1824,
            "height": 1216,
            "camera_sn": SN
        })
        sel.set_override((5440, 3648))
        self.assertFalse(sel.restart_required())

    def test_no_camera(self):
        j = {"width": 5440, "height": 3648, "source_properties": {"esize": 0}}
        sel = self.selector(j, probe_info=None)
        self.assertEqual(sel.how, "config")
        self.assertFalse(sel.can_select())
        self.assertEqual((j["width"], j["height"]), (5440, 3648))
        # Previous selection is kept
        cachej_sn = {"imager": {"resolution": {"width": 1, "height": 1}}}
        sel.cache_sn_save(cachej_sn)
        self.assertEqual(cachej_sn["imager"]["resolution"]["width"], 1)
        with self.assertRaises(tres.CameraNotFound):
            self.selector({}, probe_info=None)


if __name__ == "__main__":
    unittest.main()
