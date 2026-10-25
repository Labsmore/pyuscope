"""
Application log file shared by the GUI log window and stderr

One file per launch: data/log/<launch time>[_<microscope>][_<tag>].txt
ex: data/log/2026-08-19T01-18-16_lip-m2.txt
The microscope name isn't known until the config is resolved (possibly via
GRBL auto config), so the file is opened without it and renamed once known

The GUI log window intentionally doesn't show tracebacks
However they are critical for debugging, so stderr is tee'd into the log file
(timestamped, "stderr: " prefix) while still going to the terminal as before
This catches traceback.print_exc() in worker threads, sys.excepthook,
threading.excepthook and (see install_qt_message_handler()) Qt warnings

Only Python level writes to sys.stderr are captured
Native code writing straight to fd 2 (ex: GStreamer warnings) is not
"""

import datetime
import os
import sys
import threading

# The AppLog installed by install(), if any
_app_log = None
# Shared by all log files from this process so they can be matched up
_launch_ts = None


def launch_ts():
    global _launch_ts

    if _launch_ts is None:
        _launch_ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return _launch_ts


def log_dir(data_dir, mkdir=True):
    ret = os.path.join(data_dir, "log")
    if mkdir:
        os.makedirs(ret, exist_ok=True)
    return ret


def log_fn(data_dir, microscope=None, tag=None, mkdir=True):
    """
    ex: log_fn("data", "lip-m2", "script")
    => data/log/2026-08-19T01-18-16_lip-m2_script.txt
    """
    parts = [launch_ts()]
    if microscope:
        parts.append(microscope.replace(os.sep, "-"))
    if tag:
        parts.append(tag)
    return os.path.join(log_dir(data_dir, mkdir=mkdir),
                        "_".join(parts) + ".txt")


class AppLog:
    def __init__(self, fn, mode="w"):
        self.fn = fn
        self.fd = open(fn, mode)
        self.lock = threading.Lock()
        # Is the file at the start of a line?
        self.nl = True
        # Set by install() so the file can be renamed once the microscope is known
        self.data_dir = None
        self.tag = None
        self.microscope = None

    def rename(self, fn):
        """Move the (open) log file. Writes continue to the same file"""
        with self.lock:
            if os.path.exists(fn):
                raise FileExistsError(fn)
            os.replace(self.fn, fn)
            self.fn = fn

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


def install(data_dir, tag=None, stdout=False):
    """
    Open a new log file in data_dir/log and tee stderr (and optionally stdout)
    into it
    tag: distinguishes apps that may run at the same time (ex: "headless")
    Returns the AppLog
    """
    global _app_log

    assert _app_log is None, "app log already installed"
    try:
        log = AppLog(log_fn(data_dir, tag=tag), mode="x")
    except FileExistsError:
        # Another launch of the same app in the same second
        tag = "_".join(filter(None, (tag, "pid%u" % os.getpid())))
        log = AppLog(log_fn(data_dir, tag=tag), mode="x")
    log.data_dir = data_dir
    log.tag = tag
    _app_log = log
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


def set_microscope_name(name):
    """
    Add the microscope name to the installed log's file name
    Only the first call renames: later microscopes in the same process are ignored
    """
    log = _app_log
    if log is None or log.data_dir is None or log.microscope or not name:
        return
    log.microscope = name
    fn = log_fn(log.data_dir, microscope=name, tag=log.tag)
    try:
        log.rename(fn)
    except Exception as e:
        print("WARNING: failed to rename log %s => %s: %s" % (log.fn, fn, e),
              file=sys.stderr)


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
