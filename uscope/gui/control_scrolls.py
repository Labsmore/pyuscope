from uscope.gui.control_scroll import GstControlScroll
from uscope.gui.imager.gst_toupcamsrc.widgets import TTControlScroll
from uscope.gui.imager.gst_v4l2src.widgets import V4L2GstControlScroll
from uscope.gui.imager.gst_v4l2src_mu800.widgets import V4L2MU800ControlScroll

from collections import OrderedDict

from PyQt5.QtGui import *
from PyQt5.QtCore import *

prop_layout = OrderedDict([
    ("Unknown", {}),
])


class DummyGstControlScroll(GstControlScroll):
    def __init__(self, vidpip, usc=None, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=prop_layout,
                                  usc=usc,
                                  parent=parent)


def get_control_scroll(vidpip, usc):
    # Need to hide this when not needed
    if vidpip.source_name == "gst-toupcamsrc":
        return TTControlScroll(vidpip, usc=usc)
    elif vidpip.source_name == "gst-v4l2src":
        return V4L2GstControlScroll(vidpip, usc=usc)
    elif vidpip.source_name == "gst-v4l2src-mu800":
        return V4L2MU800ControlScroll(vidpip, usc=usc)
    else:
        print("WARNING: no control layout for source %s" %
              (vidpip.source_name, ))
        return DummyGstControlScroll(vidpip, usc=usc)
