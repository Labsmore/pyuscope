"""
Pick how the live video gets onto the screen

ximagesink renders into a native X window handle (QWidget.winId()).
That only works when Qt itself is talking X11 ("xcb" platform).
On Wayland (or offscreen) winId() is not an X window and ximagesink dies with
"X Error of failed request: BadWindow"

Environment overrides:
    PYUSCOPE_VIDEO_SINK=auto|ximagesink|appsink
        auto (default): ximagesink when Qt runs on xcb, appsink otherwise (Linux)
    PYUSCOPE_WAYLAND=xwayland|native
        xwayland (default): on a Wayland session run Qt through XWayland
        native: leave Qt on Wayland (video then uses appsink)
"""

import os
import sys

VIDEO_SINKS = ("auto", "ximagesink", "appsink")


def is_wayland_session(env=None):
    if env is None:
        env = os.environ
    if env.get("WAYLAND_DISPLAY"):
        return True
    return env.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def wayland_qt_platform(env=None):
    """
    Return the QT_QPA_PLATFORM to force before QApplication is created,
    or None to leave Qt alone
    """
    if env is None:
        env = os.environ
    # User picked explicitly: respect it
    if env.get("QT_QPA_PLATFORM"):
        return None
    if not is_wayland_session(env):
        return None
    mode = env.get("PYUSCOPE_WAYLAND", "xwayland").lower()
    if mode == "native":
        return None
    if mode != "xwayland":
        raise ValueError(f"Bad PYUSCOPE_WAYLAND={mode}")
    # No XWayland => xcb can't start
    if not env.get("DISPLAY"):
        return None
    return "xcb"


def setup_qt_platform(env=None):
    """
    Call before QApplication is constructed
    """
    if env is None:
        env = os.environ
    platform = wayland_qt_platform(env)
    if platform:
        print(
            f"Wayland session detected: using QT_QPA_PLATFORM={platform} (XWayland)"
        )
        print("  set PYUSCOPE_WAYLAND=native to run Qt on Wayland directly")
        env["QT_QPA_PLATFORM"] = platform


def video_sink(qt_platform, env=None, os_platform=None):
    """
    qt_platform: QGuiApplication.platformName() of the running app
    Return "ximagesink" or "appsink"
    """
    if env is None:
        env = os.environ
    if os_platform is None:
        os_platform = sys.platform
    sink = env.get("PYUSCOPE_VIDEO_SINK", "auto").lower()
    if sink not in VIDEO_SINKS:
        raise ValueError(
            f"Bad PYUSCOPE_VIDEO_SINK={sink}, need one of {VIDEO_SINKS}")
    if sink != "auto":
        return sink
    # Keep historical behavior everywhere except Linux on a non-X11 Qt
    if os_platform.startswith("linux") and qt_platform != "xcb":
        return "appsink"
    return "ximagesink"
