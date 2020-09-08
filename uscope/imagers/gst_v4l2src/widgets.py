from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.control_scroll_base import GstControlScroll

from collections import OrderedDict

groups_gst = OrderedDict([
    ("HSV+", [
        "brightness",
        "contrast",
        "saturation",
        "hue",
    ]),
])


class V4L2GstControlScroll(GstControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=groups_gst,
                                  parent=parent)
