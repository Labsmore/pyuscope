#!/usr/bin/env -S uv run
import os
import shutil
import tempfile
import unittest

from uscope.scan_util import index_scan_images
from uscope.imagep.pipeline import get_open_set


class TestOpenSet(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def touch(self, fn):
        open(os.path.join(self.dir, fn), "w").close()

    def test_grid_missing_tile(self):
        for fn in ("c000_r000.jpg", "c001_r000.jpg", "c000_r001.jpg"):
            self.touch(fn)
        self.assertEqual(get_open_set(index_scan_images(self.dir)), {(1, 1)})

    def test_singleton(self):
        # Composite snapshot output
        self.touch("image.jpg")
        self.assertEqual(get_open_set(index_scan_images(self.dir)), set())


if __name__ == "__main__":
    unittest.main()
