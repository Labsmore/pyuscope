from uscope.imager.plugins.aplugin import ArgusGstImagerPlugin
from uscope.imager.plugins.gst_videotestsrc.widgets import TestSrcScroll
# from uscope.imager.plugins.gst_videotestsrc.widgets import TestSrcScroll

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)


class Plugin(ArgusGstImagerPlugin):
    def name(self):
        return "gst-videotestsrc"

    def source_name(self):
        return "videotestsrc"

    def get_control_scroll(self):
        return TestSrcScroll

    def get_gst_source(self, name=None):
        # self.verbose and print('WARNING: using test source')
        return Gst.ElementFactory.make('videotestsrc', name)
