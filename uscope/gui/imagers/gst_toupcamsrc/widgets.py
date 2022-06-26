from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from collections import OrderedDict

from uscope.control_scroll import GstControlScroll

groups_gst = OrderedDict([
    ("Black balance", [
        "bb-r",
        "bb-g",
        "bb-b",
    ]),
    ("White balance", [
        "wb-r",
        "wb-g",
        "wb-b",
    ]),
    ("HSV+", [
        "hue",
        "saturation",
        "brightness",
        "contrast",
        "gamma",
    ]),
    ("AWB", [
        "awb_rgb",
        "awb_tt",
    ]),
    ("Exposure", [
        "auto-exposure",
        "expotime",
    ]),
    ("Flip", [
        "hflip",
        "vflip",
    ]),
    (
        "Misc",
        [
            #"name": "esize", "ro": True,
        ]),
])


class TTControlScroll(GstControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=groups_gst,
                                  parent=parent)
