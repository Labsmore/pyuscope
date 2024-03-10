from uscope.imager.plugins.gst_v4l2src.aplugin import Plugin as V4l2Plugin
from .widgets import V4L2YW500U3MControlScroll
from uscope.v4l2_util import find_device

import gi

DEFAULT_V4L2_DEVICE = "/dev/video0"

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)


class Plugin(V4l2Plugin):
    def name(self):
        return "gst-v4l2src-yw500u3m"

    def get_control_scroll(self):
        return V4L2YW500U3MControlScroll
