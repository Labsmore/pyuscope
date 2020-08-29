from PyQt4.QtGui import *
from PyQt4.QtCore import *

from . control_scroll_base import GstControlScroll

from collections import OrderedDict

prop_layout = OrderedDict([
    ("HSV+", {
        "brightness",
        "contrast",
        "saturation",
        "hue",
    }),
])

class V4L2GstControlScroll(GstControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, parent=None):
        GstControlScroll.__init__(self, vidpip=vidpip, prop_layout=prop_layout, parent=parent)

"""
acts on file descriptor directly via v4l2 API
(like on old GUI)

class V4L2ApiControlScroll(QScrollArea):
    def __init__(self, vidpip, parent=None):
        QScrollArea.__init__(self, parent=parent)
"""