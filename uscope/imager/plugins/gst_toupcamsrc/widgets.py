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
        return bool(self.disp_prop_read("auto-exposure"))

    def set_exposure(self, n):
        self.prop_write("expotime", n)

    def get_exposure(self):
        return self.disp_prop_read("expotime")

    def get_exposure_disp_property(self):
        return "expotime"

    def disp_prop_was_rw(self, name, value):
        # print("disp prop rw", name, value)
        # Auto-exposure quickly fights with GUI
        # Disable the control when its activated
        if name == "auto-exposure":
            self.set_gui_driven(not value,
                                disp_names=["expotime", "expoagain"])
        if name == "awb-rgb":
            self.set_gui_driven(not value, disp_names=["wb-r", "wb-g", "wb-b"])

    def flatten_hack(self, val):
        # NOTE: added source_properties_mod. Maybe leave this at actual max?
        # Log scale would also work well
        if val["prop_name"] == "expotime":
            # Despite max 15e3 exposure max, reporting 5e6
            # actually this is us?
            # Even so limit to 1 sec
            val["max"] = min(1000000, val["max"])
            # print("override", val)
