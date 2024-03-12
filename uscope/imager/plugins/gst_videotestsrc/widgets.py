from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.gui.control_scroll import GstControlScroll, VirtualProperty, AutoExposureSoftwareVP, IntDisplayer

from collections import OrderedDict


class TestPatternDisplayer(IntDisplayer):
    def val_raw2disp(self, val):
        return val

    def val_disp2raw(self, val):
        return val


# https://gstreamer.freedesktop.org/documentation/videotestsrc/?gi-language=c
groups_gst = OrderedDict([
    (
        "Test",
        [
            # eh is of type GstVideoTestSrcPattern
            #{
            #    "prop_name": "pattern",
            #    "min": 0,
            #    "max": 25
            #},
            # chose super random property to have something to test
            {
                "prop_name": "xoffset",
                "min": 0,
                # ?
                "max": 10000,
            },
            {
                "prop_name": "pattern",
                "min": 0,
                "max": 25,
                "type": "int",
                "fill_type": False,
                "ctor": TestPatternDisplayer,
            },
            {
                "prop_name": "exposure",
                "type": "int",
                "min": 0,
                "max": 100000,
                "default": 10000,
                "gui_driven": True,
                "virtual": True,
            },
            {
                "prop_name": "auto_exposure",
                "type": "bool",
                "default": False,
                "gui_driven": True,
                "virtual": True,
            },
            {
                "prop_name": "auto_exposure_sw",
                "type": "bool",
                "default": False,
                "gui_driven": True,
                "virtual": True,
            },
            {
                "prop_name": "auto_color",
                "type": "bool",
                "default": False,
                "gui_driven": True,
                "virtual": True,
            },
        ]),
])


class TestSrcScroll(GstControlScroll):
    def __init__(self, vidpip, ac=None, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=groups_gst,
                                  ac=ac,
                                  parent=parent)
        self.add_virtual_property(VirtualProperty(name="exposure",
                                                  value=10000))
        self.add_virtual_property(
            VirtualProperty(name="auto_exposure", value=0))
        self.add_virtual_property(
            AutoExposureSoftwareVP(name="auto_exposure_sw",
                                   value=0,
                                   ac=self.ac))
        self.add_virtual_property(VirtualProperty(name="auto_color", value=0))

    def auto_exposure_enabled(self):
        return self.disp_prop_read("auto_exposure")

    def auto_color_enabled(self):
        return self.disp_prop_read("auto_color")

    def set_exposure(self, n):
        self.disp_prop_write(self.get_exposure_disp_property(), n)

    def get_exposure(self):
        return self.disp_prop_read(self.get_exposure_disp_property())

    def get_exposure_disp_property(self):
        return "exposure"
