#!/usr/bin/env -S uv run
"""
Log file plumbing: stderr / exceptions tee'd into the app log file
"""

import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest

from uscope import app_log

REPO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))

TS_RE = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}"
# Log file name timestamp
FN_TS_RE = r"\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d"


class AppLogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pyuscope-test-app-log-")
        self.fn = os.path.join(self.tmp, "log.txt")

    def tearDown(self):
        app_log.uninstall()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log_files(self, data_dir=None):
        return sorted(
            glob.glob(os.path.join(data_dir or self.tmp, "log", "*.txt")))

    def read(self, data_dir=None):
        """Read the one log file installed into data_dir"""
        fns = self.log_files(data_dir)
        self.assertEqual(len(fns), 1, fns)
        with open(fns[0]) as f:
            return f.read()

    def read_fn(self):
        with open(self.fn) as f:
            return f.read()


class TestAppLog(AppLogTestCase):
    def test_stream_lines(self):
        log = app_log.AppLog(self.fn)
        log.write_stream("a", "stderr")
        log.write_stream("b\nc\n", "stderr")
        log.write_stream("d\n", "stderr")
        log.close()
        lines = self.read_fn().split("\n")
        self.assertEqual(len(lines), 4)
        self.assertRegex(lines[0], "^" + TS_RE + " stderr: ab$")
        self.assertRegex(lines[1], "^" + TS_RE + " stderr: c$")
        self.assertRegex(lines[2], "^" + TS_RE + " stderr: d$")
        self.assertEqual(lines[3], "")

    def test_log_after_partial(self):
        log = app_log.AppLog(self.fn)
        log.write_stream("partial", "stderr")
        log.write_log("gui message\n")
        log.close()
        self.assertRegex(self.read_fn(),
                         "^" + TS_RE + " stderr: partial\ngui message\n$")

    def test_log_fn(self):
        fn = app_log.log_fn(self.tmp, "lip-m2", "script")
        self.assertEqual(os.path.dirname(fn), os.path.join(self.tmp, "log"))
        self.assertTrue(os.path.isdir(os.path.dirname(fn)))
        self.assertRegex(os.path.basename(fn),
                         "^" + FN_TS_RE + "_lip-m2_script.txt$")
        self.assertRegex(os.path.basename(app_log.log_fn(self.tmp)),
                         "^" + FN_TS_RE + ".txt$")
        # Same timestamp for all files from this process
        self.assertEqual(
            os.path.basename(app_log.log_fn(self.tmp))[:-4],
            os.path.basename(fn).split("_")[0])


class TestInstall(AppLogTestCase):
    def test_file_name(self):
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            log = app_log.install(self.tmp, tag="headless")
            self.assertRegex(os.path.basename(log.fn),
                             "^" + FN_TS_RE + "_headless.txt$")
            sys.stderr.write("before\n")
            app_log.set_microscope_name("lip-m2")
            self.assertRegex(os.path.basename(log.fn),
                             "^" + FN_TS_RE + "_lip-m2_headless.txt$")
            # Only the first microscope renames
            app_log.set_microscope_name("other")
            self.assertIn("lip-m2", log.fn)
            sys.stderr.write("after\n")
            app_log.uninstall()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(self.log_files(), [log.fn])
        content = self.read()
        self.assertRegex(content, TS_RE + " stderr: before\n")
        self.assertRegex(content, TS_RE + " stderr: after\n")

    def test_no_clobber(self):
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            first = app_log.install(self.tmp).fn
            app_log.uninstall()
            # Same second, same app
            second = app_log.install(self.tmp).fn
            app_log.uninstall()
        finally:
            sys.stderr = old_stderr
        self.assertNotEqual(first, second)
        self.assertEqual(self.log_files(), sorted([first, second]))

    def test_rename_fail(self):
        terminal = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = terminal
        try:
            log = app_log.install(self.tmp)
            orig = log.fn
            # Occupy the target name
            with open(app_log.log_fn(self.tmp, "lip-m2"), "w") as f:
                f.write("someone else")
            app_log.set_microscope_name("lip-m2")
            self.assertEqual(log.fn, orig)
            sys.stderr.write("still logging\n")
            app_log.uninstall()
        finally:
            sys.stderr = old_stderr
        self.assertIn("WARNING: failed to rename log", terminal.getvalue())
        with open(orig) as f:
            content = f.read()
        self.assertIn("WARNING: failed to rename log", content)
        self.assertIn("still logging", content)

    def test_stderr_tee(self):
        terminal = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = terminal
        try:
            log = app_log.install(self.tmp)
            self.assertIs(app_log.get(), log)
            log.write_log("gui only\n")
            try:
                raise ValueError("boom")
            except ValueError:
                traceback.print_exc()
            app_log.uninstall()
            self.assertIs(sys.stderr, terminal)
        finally:
            sys.stderr = old_stderr
        # Terminal output unchanged
        self.assertNotIn("stderr:", terminal.getvalue())
        self.assertNotIn("gui only", terminal.getvalue())
        self.assertIn("ValueError: boom", terminal.getvalue())
        content = self.read()
        self.assertTrue(content.startswith("gui only\n"))
        self.assertRegex(content, TS_RE + " stderr: Traceback")
        self.assertRegex(content, TS_RE + " stderr: ValueError: boom")

    def test_thread_exception(self):
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            app_log.install(self.tmp)

            def crash():
                raise RuntimeError("thread crash")

            t = threading.Thread(target=crash)
            t.start()
            t.join()
            app_log.uninstall()
        finally:
            sys.stderr = old_stderr
        self.assertIn("RuntimeError: thread crash", self.read())

    def test_stdout_optional(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        no_stdout_dir = os.path.join(self.tmp, "no_stdout")
        stdout_dir = os.path.join(self.tmp, "stdout")
        try:
            app_log.install(no_stdout_dir)
            print("not logged")
            app_log.uninstall()
            app_log.install(stdout_dir, stdout=True)
            print("logged")
            app_log.uninstall()
        finally:
            sys.stdout = old_stdout
        self.assertEqual(self.read(no_stdout_dir), "")
        self.assertRegex(self.read(stdout_dir),
                         "^" + TS_RE + " stdout: logged\n$")

    def test_reentrant_write(self):
        """Something printing to stderr while logging must not recurse"""
        class Chatty(io.StringIO):
            def write(self, s):
                ret = super().write(s)
                if s == "outer\n":
                    sys.stderr.write("inner\n")
                return ret

        old_stderr = sys.stderr
        sys.stderr = Chatty()
        try:
            log = app_log.install(self.tmp)
            orig = log.write_stream

            # A write while the log is busy (ex: a signal handler)
            def write_stream(data, tag):
                sys.stderr.write("outer\n")
                orig(data, tag)

            log.write_stream = write_stream
            sys.stderr.write("hello\n")
            app_log.uninstall()
        finally:
            sys.stderr = old_stderr
        self.assertRegex(self.read(), "^" + TS_RE + " stderr: hello\n$")

    def test_qt_message_handler(self):
        try:
            from PyQt5.QtCore import qWarning, qInstallMessageHandler
        except ImportError:
            self.skipTest("PyQt5 not available")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            app_log.install(self.tmp)
            app_log.install_qt_message_handler()
            qWarning(b"qt says hi")
            app_log.uninstall()
        finally:
            qInstallMessageHandler(None)
            sys.stderr = old_stderr
        self.assertRegex(self.read(),
                         TS_RE + " stderr: Qt warning: qt says hi")


class TestHeadless(unittest.TestCase):
    def test_headless_log(self):
        tmp = tempfile.mkdtemp(prefix="pyuscope-test-app-log-")
        try:
            env = dict(os.environ)
            env["PYUSCOPE_DATA_DIR"] = os.path.join(tmp, "data")
            env["PYTHONPATH"] = REPO_DIR
            script = os.path.join(REPO_DIR, "uscope", "script", "test",
                                  "test_exception.py")
            out = subprocess.run([
                sys.executable,
                os.path.join(REPO_DIR, "app", "headless.py"), "--microscope",
                "mock-grbl", "--imager", "mock", script
            ],
                                 cwd=REPO_DIR,
                                 env=env,
                                 capture_output=True,
                                 text=True,
                                 timeout=120)
            # Terminal output unchanged
            self.assertIn("Traceback", out.stderr)
            self.assertNotIn(" stderr: ", out.stderr)
            fns = glob.glob(os.path.join(tmp, "data", "log", "*.txt"))
            self.assertEqual(len(fns), 1, fns)
            self.assertRegex(os.path.basename(fns[0]),
                             "^" + FN_TS_RE + "_mock-grbl_headless.txt$")
            with open(fns[0]) as f:
                content = f.read()
            self.assertRegex(content, TS_RE + " stderr: Traceback")
            self.assertRegex(content, TS_RE + " stdout: Script completed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
