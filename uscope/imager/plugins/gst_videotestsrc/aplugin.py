from uscope.imager.plugins.aplugin import GstImagerPlugin
from uscope.imager.plugins.gst_videotestsrc.properties import TestSrcProperties
from uscope.imager import pixel_format

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)


class Plugin(GstImagerPlugin):
    def name(self):
        return "gst-videotestsrc"

    def source_name(self):
        return "videotestsrc"

    def get_properties(self):
        return TestSrcProperties(self.session, plugin=self)

    def get_gst_source(self, name=None):
        return Gst.ElementFactory.make('videotestsrc', name)

    def supported_formats(self):
        # Must be pinned: unconstrained, newer gstreamer negotiates exotic
        # formats (ex: A444_16LE)
        return [
            fmt for fmt in pixel_format.FORMATS
            if not pixel_format.is_raw(fmt)
        ]

    def gst_frame_meta(self, image_dict):
        fmt = image_dict.get("format") or pixel_format.DEFAULT_FORMAT
        image_dict["bit_depth"] = 16 if pixel_format.is_16bit(fmt) else 8

    def gst_decode_image(self, image_dict):
        return pixel_format.decode_frame(
            image_dict["bytes"],
            image_dict.get("format") or pixel_format.DEFAULT_FORMAT,
            image_dict["width"],
            image_dict["height"],
            stride=image_dict.get("stride"))
