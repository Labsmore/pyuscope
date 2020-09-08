from uscope.control_scroll import GstControlScroll
from uscope.imagers.gst_toupcamsrc.widgets import TTControlScroll
from uscope.imagers.gst_v4l2src.widgets import V4L2GstControlScroll
from uscope.imagers.gst_v4l2src_mu800.widgets import V4L2MU800ControlScroll

from collections import OrderedDict

from PyQt5.QtGui import *
from PyQt5.QtCore import *

prop_layout = OrderedDict([
    ("Unknown", {}),
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
        return V4L2MU800ControlScroll(vidpip)
    else:
        print("WARNING: no control layout for source %s" %
              (vidpip.source_name, ))
        return DummyGstControlScroll(vidpip)
