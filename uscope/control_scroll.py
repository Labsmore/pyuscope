from PyQt4.QtGui import *
from PyQt4.QtCore import *

from .control_scroll_base import GstControlScroll

from collections import OrderedDict

from uscope.touptek_widget import TTControlScroll
from uscope.v4l2_widget import V4L2GstControlScroll, V4L2ApiControlScroll

prop_layout = OrderedDict([
    ("Unknown", {
    }),
])

class DummyGstControlScroll(GstControlScroll):
    def __init__(self, vidpip, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  prop_layout=prop_layout,
                                  parent=parent)

def get_control_scroll(vidpip):
    # Need to hide this when not needed
    if vidpip.source_name == "gst-toupcamsrc":
        return TTControlScroll(vidpip)
    elif vidpip.source_name == "gst-v4l2src":
        return V4L2GstControlScroll(vidpip)
    elif vidpip.source_name == "gst-v4l2src-mu800":
        return V4L2ApiControlScroll(vidpip)
    else:
        print("WARNING: no control layout for source %s" %
              (vidpip.source_name, ))
        return DummyGstControlScroll(vidpip)
