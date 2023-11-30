"""
VM1 candidate camera
Hopefully works better than the original camera choice
"""

from uscope.gui.v4l_control_scroll import V4L2AutoExposureDisplayer, V4L2ControlScroll
from uscope.gui.control_scroll import BoolDisplayer
from collections import OrderedDict
"""
this is a "menu"
auto temp is a "bool"
why?

"""

groups_gst = OrderedDict([(
    "Controls",
    [
    ],
)])


class V4L2YW500U3MControlScroll(V4L2ControlScroll):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, groups_gst=groups_gst)
