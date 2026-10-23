#!/usr/bin/env -S uv run
"""
Log file plumbing: stderr / exceptions tee'd into the app log file
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import traceback
import unittest

from uscope import app_log

TS_RE = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}"


class AppLogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pyuscope-test-app-log-")
        self.fn = os.path.join(self.tmp, "log.txt")

    def tearDown(self):
        app_log.uninstall()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self):
        with open(self.fn) as f:
            return f.read()


class TestAppLog(AppLogTestCase):
    def test_stream_lines(self):
        log = app_log.AppLog(self.fn)
        log.write_stream("a", "stderr")
        log.write_stream("b\nc\n", "stderr")
        log.write_stream("d\n", "stderr")
        log.close()
        lines = self.read().split("\n")
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
        self.assertRegex(self.read(),
                         "^" + TS_RE + " stderr: partial\ngui message\n$")


class TestInstall(AppLogTestCase):
    def test_stderr_tee(self):
        terminal = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = terminal
        try:
            log = app_log.install(self.fn)
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
            app_log.install(self.fn)

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
        try:
            app_log.install(self.fn)
            print("not logged")
            app_log.uninstall()
            app_log.install(self.fn, stdout=True)
            print("logged")
            app_log.uninstall()
        finally:
            sys.stdout = old_stdout
        self.assertRegex(self.read(), "^" + TS_RE + " stdout: logged\n$")

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
            log = app_log.install(self.fn)
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
            app_log.install(self.fn)
            app_log.install_qt_message_handler()
            qWarning(b"qt says hi")
            app_log.uninstall()
        finally:
            qInstallMessageHandler(None)
            sys.stderr = old_stderr
        self.assertRegex(self.read(),
                         TS_RE + " stderr: Qt warning: qt says hi")


if __name__ == "__main__":
    unittest.main()
