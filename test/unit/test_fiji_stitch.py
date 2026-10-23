#!/usr/bin/env -S uv run
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from uscope import fiji_stitch
from uscope.scan_util import index_scan_images
from uscope.util import writej, readj


def touch_exe(fn):
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(fn, 0o755)


def make_uscan(cols=2, rows=2, step_mm=1.0, um_per_pixel=2.0, hdrs=0):
    """
    Stage y up: row 0 at the largest y
    """
    files = {}
    for col in range(cols):
        for row in range(rows):
            x = 10.0 + col * step_mm
            y = 50.0 - row * step_mm
            if hdrs:
                for hdr in range(hdrs):
                    files[f"c{col:03}_r{row:03}_h{hdr:02}.jpg"] = {
                        "col": col,
                        "row": row,
                        # small jitter between captures
                        "position": {
                            "x": x + 0.001 * hdr,
                            "y": y,
                            "z": 0.0
                        },
                    }
            else:
                files[f"c{col:03}_r{row:03}.jpg"] = {
                    "col": col,
                    "row": row,
                    "position": {
                        "x": x,
                        "y": y,
                        "z": 0.0
                    },
                }
    return {
        "files": files,
        "image-save": {
            "extension": ".jpg"
        },
        "pconfig": {
            "app": {
                "objective": {
                    "um_per_pixel": um_per_pixel
                }
            }
        },
    }


def write_images(d, names):
    os.makedirs(d, exist_ok=True)
    for name in names:
        Image.new("RGB", (8, 8)).save(os.path.join(d, name))


class TestFindFiji(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        os.environ.pop(fiji_stitch.ENV_FIJI, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explicit_dir_and_exe(self):
        app = os.path.join(self.tmp, "Fiji.app")
        exe = os.path.join(app, "ImageJ-linux64")
        touch_exe(exe)
        self.assertEqual(fiji_stitch.find_fiji(app, use_config=False), exe)
        self.assertEqual(fiji_stitch.find_fiji(exe, use_config=False), exe)

    def test_new_launcher_name(self):
        app = os.path.join(self.tmp, "Fiji")
        exe = os.path.join(app, "fiji-linux-x64")
        touch_exe(exe)
        self.assertEqual(fiji_stitch.find_fiji(app, use_config=False), exe)

    def test_env(self):
        exe = os.path.join(self.tmp, "Fiji.app", "ImageJ-linux64")
        touch_exe(exe)
        os.environ[fiji_stitch.ENV_FIJI] = exe
        self.assertEqual(
            fiji_stitch.find_fiji(use_config=False,
                                  install_dirs=[],
                                  which=False), exe)

    def test_explicit_missing_is_error(self):
        with self.assertRaises(ValueError):
            fiji_stitch.find_fiji(os.path.join(self.tmp, "nope"),
                                  use_config=False)

    def test_install_dirs(self):
        app = os.path.join(self.tmp, "Fiji.app")
        exe = os.path.join(app, "ImageJ-linux64")
        touch_exe(exe)
        self.assertEqual(
            fiji_stitch.find_fiji(use_config=False,
                                  install_dirs=[
                                      os.path.join(self.tmp, "missing"), app
                                  ],
                                  which=False), exe)

    def test_not_found(self):
        self.assertIsNone(
            fiji_stitch.find_fiji(use_config=False,
                                  install_dirs=[self.tmp],
                                  which=False))


class TestTilePositions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_positions(self):
        uscan = make_uscan(cols=2, rows=2, step_mm=1.0, um_per_pixel=2.0)
        write_images(self.tmp, uscan["files"].keys())
        positions = fiji_stitch.tile_positions(uscan,
                                               index_scan_images(self.tmp))
        # 500 pixels / mm, y flipped so row 0 is at the top
        self.assertEqual(
            dict(positions), {
                "c000_r000.jpg": (0.0, 0.0),
                "c001_r000.jpg": (500.0, 0.0),
                "c000_r001.jpg": (0.0, 500.0),
                "c001_r001.jpg": (500.0, 500.0),
            })
        # Row major
        self.assertEqual(list(positions)[:2],
                         ["c000_r000.jpg", "c001_r000.jpg"])

    def test_processed_dir_averages_captures(self):
        uscan = make_uscan(cols=2, rows=1, um_per_pixel=1.0, hdrs=3)
        write_images(self.tmp, ["c000_r000.jpg", "c001_r000.jpg"])
        positions = fiji_stitch.tile_positions(uscan,
                                               index_scan_images(self.tmp))
        self.assertAlmostEqual(positions["c000_r000.jpg"][0], 0.0)
        self.assertAlmostEqual(positions["c001_r000.jpg"][0], 1000.0)

    def test_points_fallback(self):
        uscan = make_uscan(cols=2, rows=1)
        del uscan["files"]
        del uscan["pconfig"]
        uscan["points-xy2p"] = {
            "axes": {
                "x": {
                    "pixels_per_mm": 100.0
                }
            },
            "points": {
                "c000_r000": {
                    "col": 0,
                    "row": 0,
                    "x": 1.0,
                    "y": 2.0
                },
                "c001_r000": {
                    "col": 1,
                    "row": 0,
                    "x": 3.0,
                    "y": 2.0
                },
            },
        }
        write_images(self.tmp, ["c000_r000.jpg", "c001_r000.jpg"])
        positions = fiji_stitch.tile_positions(uscan,
                                               index_scan_images(self.tmp))
        self.assertEqual(positions["c001_r000.jpg"], (200.0, 0.0))

    def test_missing_tile(self):
        uscan = make_uscan(cols=1, rows=1)
        write_images(self.tmp, ["c000_r000.jpg", "c001_r000.jpg"])
        with self.assertRaises(ValueError):
            fiji_stitch.tile_positions(uscan, index_scan_images(self.tmp))

    def test_not_flat(self):
        uscan = make_uscan(cols=1, rows=1, hdrs=2)
        write_images(self.tmp, uscan["files"].keys())
        with self.assertRaises(ValueError):
            fiji_stitch.tile_positions(uscan, index_scan_images(self.tmp))

    def test_format_tile_configuration(self):
        out = fiji_stitch.format_tile_configuration({
            "c000_r000.jpg": (0.0, 0.0),
            "c001_r000.jpg": (12.3456, 7.0),
        })
        lines = out.splitlines()
        self.assertIn("dim = 2", lines)
        self.assertIn("c000_r000.jpg; ; (0.000, 0.000)", lines)
        self.assertIn("c001_r000.jpg; ; (12.346, 7.000)", lines)


class TestFindImageDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flat_top(self):
        write_images(self.tmp, ["c000_r000.jpg", "c001_r000.jpg"])
        write_images(os.path.join(self.tmp, "summary"), ["quick_pano.jpg"])
        write_images(os.path.join(self.tmp, "fiji"), ["stitched.jpg"])
        self.assertEqual(fiji_stitch.find_image_dir(self.tmp), self.tmp)

    def test_deepest_processed(self):
        write_images(self.tmp, [
            "c000_r000_h00.jpg", "c000_r000_h01.jpg", "c001_r000_h00.jpg",
            "c001_r000_h01.jpg"
        ])
        hdr = os.path.join(self.tmp, "hdr")
        ff1 = os.path.join(hdr, "ff1")
        write_images(hdr, ["c000_r000.jpg", "c001_r000.jpg"])
        write_images(ff1, ["c000_r000.jpg", "c001_r000.jpg"])
        self.assertEqual(fiji_stitch.find_image_dir(self.tmp), ff1)

    def test_none(self):
        write_images(self.tmp, ["c000_r000_h00.jpg", "c000_r000_h01.jpg"])
        with self.assertRaises(ValueError):
            fiji_stitch.find_image_dir(self.tmp)


class TestMacro(unittest.TestCase):
    def test_macro(self):
        macro = fiji_stitch.format_macro("/data/scan/a b", "/out/s.tif")
        self.assertIn('run("Grid/Collection stitching"', macro)
        self.assertIn("type=[Positions from file]", macro)
        self.assertIn("directory=[/data/scan/a b]", macro)
        self.assertIn("layout_file=[TileConfiguration.txt]", macro)
        self.assertIn("compute_overlap", macro)
        self.assertIn('saveAs("Tiff", "/out/s.tif")', macro)

    def test_no_compute_overlap(self):
        macro = fiji_stitch.format_macro("/d", "/o.tif", compute_overlap=False)
        self.assertNotIn("compute_overlap", macro)

    def test_path_escape(self):
        self.assertEqual(fiji_stitch.macro_path('/a/b"c'), '/a/b\\"c')
        with self.assertRaises(ValueError):
            fiji_stitch.macro_path("/a/[b]")

    def test_command(self):
        self.assertEqual(
            fiji_stitch.fiji_command("/f/ImageJ-linux64", "/m.ijm", mem="8g"),
            [
                "/f/ImageJ-linux64", "--headless", "--console", "--mem=8g",
                "-macro", "/m.ijm"
            ])


class TestStitchDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.exe = os.path.join(self.tmp, "Fiji.app", "ImageJ-linux64")
        touch_exe(self.exe)
        self.scan = os.path.join(self.tmp, "scan")
        uscan = make_uscan(cols=2, rows=1)
        write_images(self.scan, uscan["files"].keys())
        writej(os.path.join(self.scan, "uscan.json"), uscan)
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_run(self, create=True, rc=0):
        def run(args, log=None, log_fn=None, timeout=None):
            self.calls.append(args)
            if create:
                macro_fn = args[args.index("-macro") + 1]
                with open(macro_fn) as f:
                    macro = f.read()
                out = macro.split('saveAs("Tiff", "')[1].split('"')[0]
                with open(out, "w") as f:
                    f.write("tif")
            return rc

        return run

    def test_stitch(self):
        out = fiji_stitch.stitch_scan_dir(self.scan,
                                          fiji=self.exe,
                                          log=lambda s: None,
                                          run=self.fake_run())
        out_dir = os.path.join(self.scan, "fiji")
        self.assertEqual(out, os.path.join(out_dir, "stitched.tif"))
        self.assertTrue(os.path.exists(out))
        self.assertTrue(
            os.path.exists(os.path.join(self.scan, "TileConfiguration.txt")))
        self.assertTrue(os.path.exists(os.path.join(out_dir, "stitch.ijm")))
        self.assertEqual(
            readj(os.path.join(out_dir, "fiji_stitch.json"))["fiji"],
            self.exe)
        self.assertEqual(self.calls[0][:3],
                         [self.exe, "--headless", "--console"])

        # lazy: don't run again
        fiji_stitch.stitch_scan_dir(self.scan,
                                    fiji=self.exe,
                                    lazy=True,
                                    log=lambda s: None,
                                    run=self.fake_run())
        self.assertEqual(len(self.calls), 1)

    def test_no_output_is_failure(self):
        with self.assertRaises(Exception):
            fiji_stitch.stitch_scan_dir(self.scan,
                                        fiji=self.exe,
                                        log=lambda s: None,
                                        run=self.fake_run(create=False))

    def test_bad_exit_code_is_failure(self):
        with self.assertRaises(Exception):
            fiji_stitch.stitch_scan_dir(self.scan,
                                        fiji=self.exe,
                                        log=lambda s: None,
                                        run=self.fake_run(rc=1))

    def test_dry(self):
        fiji_stitch.stitch_scan_dir(self.scan,
                                    fiji=self.exe,
                                    dry=True,
                                    log=lambda s: None,
                                    run=self.fake_run())
        self.assertEqual(self.calls, [])
        self.assertTrue(
            os.path.exists(os.path.join(self.scan, "fiji", "stitch.ijm")))


if __name__ == "__main__":
    unittest.main()
