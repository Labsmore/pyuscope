from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.gui.control_scroll import GstControlScroll

from collections import OrderedDict

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
            }
        ]),
])


class TestSrcScroll(GstControlScroll):
    def __init__(self, vidpip, ac=None, parent=None):
        GstControlScroll.__init__(self,
                                  vidpip=vidpip,
                                  groups_gst=groups_gst,
                                  ac=ac,
                                  parent=parent)
