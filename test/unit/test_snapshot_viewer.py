#!/usr/bin/env -S uv run
import os
import shutil
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, QCoreApplication
from PyQt5.QtWidgets import QApplication, QMessageBox

from uscope.gui import snapshot_viewer
from uscope.gui.snapshot_viewer import list_images, to_8bit, load_rgb8


def write_images(directory):
    """
    Write one of each supported type with increasing mtime
    Returns paths oldest first
    """
    rgb8 = np.zeros((40, 60, 3), dtype=np.uint8)
    rgb8[:, :, 2] = 255
    rgb16 = np.full((40, 60, 3), 4000, dtype=np.uint16)
    gray16 = np.full((30, 50), 65535, dtype=np.uint16)
    ret = []
    for name, data in (("a.jpg", rgb8), ("b.png", rgb8), ("c.tif", rgb16),
                       ("d.tif", gray16)):
        fn = os.path.join(directory, name)
        assert cv2.imwrite(fn, data)
        ret.append(fn)
    now = time.time()
    for i, fn in enumerate(ret):
        os.utime(fn, (now - 100 + i, now - 100 + i))
    return ret


class TestHelpers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_list_images_newest_first(self):
        fns = write_images(self.dir)
        open(os.path.join(self.dir, "notes.txt"), "w").close()
        os.mkdir(os.path.join(self.dir, "sub.jpg"))
        got = [x[0] for x in list_images(self.dir)]
        self.assertEqual(got, list(reversed(fns)))

    def test_list_images_missing_dir(self):
        self.assertEqual(list_images(os.path.join(self.dir, "nope")), [])

    def test_to_8bit(self):
        a = np.array([0, 255], dtype=np.uint8)
        self.assertIs(to_8bit(a), a)
        # Full 16 bit range
        self.assertEqual(
            to_8bit(np.array([0, 65535], dtype=np.uint16)).tolist(), [0, 255])
        # 12 bit data in a 16 bit container scales by 12 bits
        self.assertEqual(
            to_8bit(np.array([0, 2048, 4095], dtype=np.uint16)).tolist(),
            [0, 128, 255])
        # Small values stay as is
        self.assertEqual(
            to_8bit(np.array([0, 200], dtype=np.uint16)).tolist(), [0, 200])
        self.assertEqual(
            to_8bit(np.array([0.0, 1.0], dtype=np.float32)).tolist(),
            [0, 255])
        self.assertEqual(to_8bit(np.zeros((0, ), dtype=np.uint16)).dtype,
                         np.uint8)

    def test_load_rgb8(self):
        a, b, c, d = write_images(self.dir)
        im = load_rgb8(b)
        self.assertEqual(im.shape, (40, 60, 3))
        self.assertEqual(im.dtype, np.uint8)
        # Written as BGR red => loaded as RGB red
        self.assertEqual(im[0, 0].tolist(), [255, 0, 0])
        im = load_rgb8(c)
        self.assertEqual(im.dtype, np.uint8)
        self.assertEqual(int(im[0, 0, 0]), 4000 >> 4)
        self.assertEqual(load_rgb8(a, max_dim=10).shape, (7, 10, 3))
        im = load_rgb8(d, max_dim=10)
        self.assertEqual(im.shape, (6, 10))
        self.assertEqual(int(im[0, 0]), 255)
        with self.assertRaises(Exception):
            bad = os.path.join(self.dir, "bad.jpg")
            with open(bad, "w") as f:
                f.write("not an image")
            load_rgb8(bad)


    def test_captured_image_save(self):
        """
        Files as written by the Argus snapshot path
        """
        from PIL import Image
        from uscope.imager.image_sequence import CapturedImage
        from uscope.gui.imaging import snapshot_fn

        rgb16 = np.zeros((20, 30, 3), dtype=np.uint16)
        rgb16[:, :, 0] = 65535
        image = Image.new("RGB", (30, 20), (0, 0, 255))
        capim = CapturedImage(image=image, data=rgb16, pixel_format="RGB16")
        fn16 = snapshot_fn(user="x", extension=".tif", parent=self.dir)
        capim.save(fn16)
        capim = CapturedImage(image=image)
        fn8 = snapshot_fn(user="x", extension=".jpg", parent=self.dir)
        capim.save(fn8, quality=90)
        self.assertEqual(sorted(x[0] for x in list_images(self.dir)),
                         sorted([fn16, fn8]))
        self.assertEqual(load_rgb8(fn16)[0, 0].tolist(), [255, 0, 0])
        self.assertEqual(load_rgb8(fn8)[0, 0, 2] > 200, True)


class FakeAConfig:
    def __init__(self, directory):
        self.directory = directory

    def snapshot_dir(self):
        return self.directory


class FakeAC(QObject):
    snapshotCaptured = pyqtSignal(dict)

    def __init__(self, directory):
        super().__init__()
        self.aconfig = FakeAConfig(directory)
        self.logs = []

    def initUI(self):
        pass

    def log(self, s):
        self.logs.append(s)


class FakeCapim:
    def __init__(self, fn):
        self.meta = {"save_filename": fn}


def wait_for(cond, timeout=10.0):
    tend = time.time() + timeout
    while time.time() < tend:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class TestSnapshotViewerTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.fns = write_images(self.dir)
        self.ac = FakeAC(self.dir)
        self.tab = snapshot_viewer.SnapshotViewerTab(ac=self.ac)
        self.tab.initUI()
        self.tab.post_ui_init()
        self.tab.resize(1200, 800)
        # Refresh is deferred until shown
        self.assertEqual(self.tab.list.count(), 0)
        self.tab.show()
        self.assertTrue(wait_for(lambda: self.tab.list.count() > 0))

    def tearDown(self):
        self.tab.shutdown_request(phase=0)
        self.tab.shutdown_join()
        self.tab.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def paths(self):
        return [
            self.tab.list.item(i).data(snapshot_viewer.Qt.UserRole)
            for i in range(self.tab.list.count())
        ]

    def test_browse(self):
        self.assertEqual(self.paths(), list(reversed(self.fns)))
        self.assertEqual(self.tab.selected_path(), self.fns[-1])
        # Thumbnails load in the background
        self.assertTrue(
            wait_for(lambda: len(self.tab.thumb_cache) == len(self.fns)))
        self.assertTrue(wait_for(lambda: self.tab.preview.has_image()))
        self.assertTrue(self.tab.preview.fit)
        self.assertIn("50 x 30", self.tab.info_label.text())

        self.tab.list.setCurrentRow(1)
        self.assertTrue(
            wait_for(lambda: "60 x 40" in self.tab.info_label.text()))
        self.tab.zoom_in_pb.click()
        self.assertFalse(self.tab.preview.fit)
        self.tab.zoom_1_pb.click()
        self.assertAlmostEqual(self.tab.preview.zoom_factor(), 1.0)
        self.tab.fit_pb.click()
        self.assertTrue(self.tab.preview.fit)

    def test_scroll_keeps_timer_interval(self):
        self.tab.list.verticalScrollBar().valueChanged.emit(1234)
        self.assertEqual(self.tab.thumb_timer.interval(), 50)

    def test_hidden_deferred(self):
        self.tab.hide()
        fn = os.path.join(self.dir, "g.jpg")
        cv2.imwrite(fn, np.zeros((10, 10, 3), dtype=np.uint8))
        self.ac.snapshotCaptured.emit({"captured_image": FakeCapim(fn)})
        wait_for(lambda: False, timeout=0.5)
        self.assertNotIn(fn, self.paths())
        self.tab.show()
        self.assertTrue(wait_for(lambda: self.tab.selected_path() == fn))

    def test_new_snapshot_selected(self):
        fn = os.path.join(self.dir, "e.jpg")
        cv2.imwrite(fn, np.zeros((10, 10, 3), dtype=np.uint8))
        self.ac.snapshotCaptured.emit({"captured_image": FakeCapim(fn)})
        self.assertTrue(wait_for(lambda: self.tab.selected_path() == fn))
        self.assertEqual(self.paths()[0], fn)

    def test_watcher_refresh(self):
        fn = os.path.join(self.dir, "f.png")
        cv2.imwrite(fn, np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertTrue(wait_for(lambda: fn in self.paths()))

    def test_delete(self):
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            self.tab.list.setCurrentRow(1)
            victim = self.tab.selected_path()
            self.tab.delete_pb.click()
        finally:
            QMessageBox.question = orig
        self.assertFalse(os.path.exists(victim))
        self.assertNotIn(victim, self.paths())
        # Selection moves to the next (older) image
        self.assertEqual(self.tab.selected_path(), self.fns[1])

    def test_corrupt_file(self):
        bad = os.path.join(self.dir, "z.jpg")
        with open(bad, "w") as f:
            f.write("garbage")
        self.tab.refresh()
        self.tab.list.setCurrentRow(0)
        self.assertEqual(self.tab.selected_path(), bad)
        self.assertTrue(
            wait_for(lambda: "failed to load" in self.tab.info_label.text()))


if __name__ == "__main__":
    unittest.main()
