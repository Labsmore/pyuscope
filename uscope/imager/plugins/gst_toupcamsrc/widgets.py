from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from collections import OrderedDict

from uscope.gui.control_scroll import GstControlScroll

groups_gst = OrderedDict([
    ("Exposure", [
        "auto-exposure",
        "expotime",
        "expoagain",
    ]),
    ("Flip", [
        "hflip",
        "vflip",
    ]),
    (
        "AWB",
        [
            "awb-rgb",
            # not sure what this is but doesn't work
            #"awb-tt",
        ]),
    # software based, leave out for now
    #("Black balance", [
    #    "bb-r",
    #    "bb-g",
    #    "bb-b",
    #]),
    ("White balance", [
        "wb-r",
        "wb-g",
        "wb-b",
    ]),
    # also software based
    #("HSV+", [
    #    "hue",
    #    "saturation",
    #    "brightness",
    #    "contrast",
    #    "gamma",
    #]),
    #(
    #    "Misc",
    #    [
    #        #"name": "esize", "ro": True,
    #    ]),
])


class TTControlScroll(GstControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, usc=None, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=groups_gst,
                                  usc=usc,
                                  parent=parent)

    def auto_exposure_enabled(self):
        return bool(self.prop_read("auto-exposure"))

    def set_exposure(self, n):
        self.prop_write("expotime", n)

    def get_exposure(self):
        return self.prop_read("expotime")

    def get_exposure_property(self):
        return "expotime"
