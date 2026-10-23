#!/usr/bin/env -S uv run
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from uscope import temp_file


class TestProcessTempRoot(unittest.TestCase):
    def setUp(self):
        self.parent = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.parent, ignore_errors=True)

    def test_stale_root_removed(self):
        # A pid that existed but has exited
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        stale = os.path.join(self.parent,
                             f"{temp_file.PROCESS_ROOT_PREFIX}{p.pid}-abc")
        os.makedirs(os.path.join(stale, "sub"))
        live = os.path.join(self.parent,
                            f"{temp_file.PROCESS_ROOT_PREFIX}{os.getppid()}-abc")
        os.mkdir(live)
        other = os.path.join(self.parent, f"{temp_file.PROCESS_ROOT_PREFIX}x")
        os.mkdir(other)
        temp_file.cleanup_stale_process_roots(self.parent)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(live))
        self.assertTrue(os.path.exists(other))

    def test_temporary_directory_in_root(self):
        d = temp_file.TemporaryDirectory()
        try:
            self.assertEqual(os.path.dirname(d.name),
                             temp_file.process_temp_root())
            self.assertIn(f"-{os.getpid()}-", d.name)
        finally:
            d.cleanup()


if __name__ == "__main__":
    unittest.main()
