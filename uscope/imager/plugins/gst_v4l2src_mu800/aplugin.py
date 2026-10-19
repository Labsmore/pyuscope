from uscope.imager.plugins.gst_v4l2src.aplugin import Plugin as V4l2Plugin
from .widgets import V4L2MU800ControlScroll

import gi

DEFAULT_V4L2_DEVICE = "/dev/video0"

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)

VID_TOUPTEK = 0x0547
PID_MU800 = 0x6801


class Plugin(V4l2Plugin):
    def name(self):
        return "gst-v4l2src-mu800"

    def get_control_scroll(self):
        return V4L2MU800ControlScroll

    def detect_sources(self):
        """
        Auto detection is not supported for this legacy source

        Historically this checked for the modified touptek kernel module
        (lsmod) + a matching USB VID, but that flow is obsolete
        """
        return []
