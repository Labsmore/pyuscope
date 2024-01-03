from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from collections import OrderedDict

from uscope.gui.control_scroll import GstControlScroll
from uscope import config

PROP_DISP_AUTO_EXPOSURE = "auto-exposure"
PROP_DISP_EXPOSURE_TIME = "expotime"
PROP_DISP_EXPOSURE_GAIN = "expoagain"
PROP_DISP_AUTO_WB = "Auto white balance (AWB)"
PROP_DISP_WB_R = "White balance (red)"
PROP_DISP_WB_G = "White balance (green)"
PROP_DISP_WB_B = "White balance (blue)"

groups_gst = OrderedDict([
    ("Exposure", [
        {
            "prop_name": PROP_DISP_AUTO_EXPOSURE,
            "disp_name": "auto-exposure",
        },
        {
            "prop_name": PROP_DISP_EXPOSURE_TIME,
            "disp_name": "expotime",
        },
        {
            "prop_name": PROP_DISP_EXPOSURE_GAIN,
            "disp_name": "expoagain",
        },
    ]),
    ("Misc", [
        {
            "prop_name": "awb-rgb",
            "disp_name": PROP_DISP_AUTO_WB,
        },
        {
            "prop_name": "wb-r",
            "disp_name": PROP_DISP_WB_R,
        },
        {
            "prop_name": "wb-g",
            "disp_name": PROP_DISP_WB_G,
        },
        {
            "prop_name": "wb-b",
            "disp_name": PROP_DISP_WB_B,
        },
        {
            "prop_name": "hflip",
            "disp_name": "hflip",
            "visible": False,
        },
        {
            "prop_name": "vflip",
            "disp_name": "vflip",
            "visible": False,
        },
    ]),
])

groups_gst = OrderedDict(groups_gst)


class TTControlScroll(GstControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, ac=None, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=groups_gst,
                                  ac=ac,
                                  parent=parent)

    def post_imager_ready(self):
        GstControlScroll.post_imager_ready(self)

        # Normal users don't need to change these
        # but its needed to configure the camera
        # See https://github.com/Labsmore/pyuscope/issues/274
        # self.disp2element["hflip"].setVisible(config.get_bc().dev_mode())
        # self.disp2element["vflip"].setVisible(config.get_bc().dev_mode())

    def auto_exposure_enabled(self):
        return bool(self.disp_prop_read(PROP_DISP_AUTO_EXPOSURE))

    def auto_color_enabled(self):
        return bool(self.disp_prop_read(PROP_DISP_AUTO_WB))

    def set_exposure(self, n):
        self.prop_write(PROP_DISP_EXPOSURE_TIME, n)

    def get_exposure(self):
        return self.disp_prop_read(PROP_DISP_EXPOSURE_TIME)

    def get_exposure_disp_property(self):
        return PROP_DISP_EXPOSURE_TIME

    def disp_prop_was_rw(self, name, value):
        # print("disp prop rw", name, value)
        # Auto-exposure quickly fights with GUI
        # Disable the control when its activated
        if name == PROP_DISP_AUTO_EXPOSURE:
            self.set_gui_driven(
                not value,
                disp_names=[PROP_DISP_EXPOSURE_TIME, PROP_DISP_EXPOSURE_GAIN])
        if name == PROP_DISP_AUTO_WB:
            self.set_gui_driven(
                not value,
                disp_names=[PROP_DISP_WB_R, PROP_DISP_WB_G, PROP_DISP_WB_B])

    def flatten_hack(self, val):
        # NOTE: added source_properties_mod. Maybe leave this at actual max?
        # Log scale would also work well
        if val["prop_name"] == PROP_DISP_EXPOSURE_TIME:
            # Despite max 15e3 exposure max, reporting 5e6
            # actually this is us?
            # Even so limit to 1 sec
            val["max"] = min(1000000, val["max"])
            # print("override", val)
