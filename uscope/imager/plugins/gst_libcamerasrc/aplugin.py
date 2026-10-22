from uscope.imager.plugins.aplugin import GstImagerPlugin

import gi

DEFAULT_V4L2_DEVICE = "/dev/video0"

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)


class Plugin(GstImagerPlugin):
    def name(self):
        return "gst-libcamerasrc"

    def source_name(self):
        return "libcamerasrc"

    def get_gst_source(self, name=None):
        return Gst.ElementFactory.make('libcamerasrc', name)
