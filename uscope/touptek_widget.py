from PyQt4.QtGui import *
from PyQt4.QtCore import *

from collections import OrderedDict

from . control_scroll_base import GstControlScroll

prop_layout = OrderedDict([
    ("Black balance", {
        "bb-r",
        "bb-g",
        "bb-b",
    }),
    ("White balance", {
        "wb-r",
        "wb-g",
        "wb-b",
    }),
    ("HSV+", {
        "hue",
        "saturation",
        "brightness",
        "contrast",
        "gamma",
    }),
    ("Exposure", {
        "auto-exposure",
        "expotime",
    }),
    ("Flip", {
        "hflip",
        "vflip",
    }),
    ("Misc", {
        #"name": "esize", "ro": True,
    }),
])

class TTControlScroll(GstControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, parent=None):
        GstControlScroll.__init__(self, vidpip=vidpip, prop_layout=prop_layout, parent=parent)
