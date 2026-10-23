"""
Application log file (ex: data/log.txt) shared by the GUI log window and
stderr

The GUI log window intentionally doesn't show tracebacks
However they are critical for debugging, so stderr is tee'd into the log file
(timestamped, "stderr: " prefix) while still going to the terminal as before
This catches traceback.print_exc() in worker threads, sys.excepthook,
threading.excepthook and (see install_qt_message_handler()) Qt warnings

Only Python level writes to sys.stderr are captured
Native code writing straight to fd 2 (ex: GStreamer warnings) is not
"""

import datetime
import sys
import threading

# The AppLog installed by install(), if any
_app_log = None


class AppLog:
    def __init__(self, fn, mode="w"):
        self.fn = fn
        self.fd = open(fn, mode)
        self.lock = threading.Lock()
        # Is the file at the start of a line?
        self.nl = True

    def _write(self, s):
        if s:
            self.fd.write(s)
            self.nl = s[-1] == "\n"

    def write_log(self, s):
        """Write GUI log text as is"""
        with self.lock:
            # Don't glue onto a partial stderr line
            if not self.nl:
                self._write("\n")
            self._write(s)
            self.fd.flush()

    def write_stream(self, data, tag):
        """Write stream output, prefixing each new line with time and tag"""
        with self.lock:
            prefix = "%s %s: " % (datetime.datetime.now().isoformat(
                sep=" ", timespec="milliseconds"), tag)
            for line in data.splitlines(keepends=True):
                if self.nl:
                    self._write(prefix)
                self._write(line)
            self.fd.flush()

    def close(self):
        with self.lock:
            self.fd.close()


class StreamTee:
    """
    Wrap a text stream (ex: sys.stderr): write through to it as before and
    also copy to an AppLog
    """
    def __init__(self, stream, app_log, tag):
        self.stream = stream
        self.app_log = app_log
        self.tag = tag
        # Set while copying to the log so that anything printed in the process
        # (ex: a signal handler) goes to the terminal only instead of
        # recursing / deadlocking on the AppLog lock
        self.local = threading.local()

    def write(self, data):
        ret = self.stream.write(data)
        if getattr(self.local, "busy", False):
            return ret
        self.local.busy = True
        try:
            self.app_log.write_stream(data, self.tag)
        except Exception:
            # Never break the caller (ex: disk full, file closed at exit)
            pass
        finally:
            self.local.busy = False
        return ret

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        # fileno(), isatty(), encoding, etc
        return getattr(self.stream, name)


def get():
    return _app_log


def install(fn, stdout=False, mode="w"):
    """
    Open the log file and tee stderr (and optionally stdout) into it
    Returns the AppLog
    """
    global _app_log

    assert _app_log is None, "app log already installed"
    _app_log = AppLog(fn, mode=mode)
    if sys.stderr is not None:
        sys.stderr = StreamTee(sys.stderr, _app_log, "stderr")
    if stdout and sys.stdout is not None:
        sys.stdout = StreamTee(sys.stdout, _app_log, "stdout")
    return _app_log


def uninstall():
    global _app_log

    if _app_log is None:
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, StreamTee) and stream.app_log is _app_log:
            setattr(sys, name, stream.stream)
    _app_log.close()
    _app_log = None


def install_qt_message_handler():
    """
    Route Qt warnings through sys.stderr so they also get logged
    By default Qt writes to fd 2 directly
    """
    from PyQt5.QtCore import qInstallMessageHandler, QtMsgType

    names = {
        QtMsgType.QtDebugMsg: "debug",
        QtMsgType.QtInfoMsg: "info",
        QtMsgType.QtWarningMsg: "warning",
        QtMsgType.QtCriticalMsg: "critical",
        QtMsgType.QtFatalMsg: "fatal",
    }

    def handler(msg_type, _context, msg):
        try:
            sys.stderr.write("Qt %s: %s\n" %
                             (names.get(msg_type, msg_type), msg))
        except Exception:
            pass

    qInstallMessageHandler(handler)
