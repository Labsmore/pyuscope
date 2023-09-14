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
        # Spelled differently on different systems...
        {
            "prop_name": "Auto Exposure",
            "disp_name": "Auto Exposure",
            "default": True,
            "ctor": V4L2AutoExposureDisplayer,
            "type": "ctor",
            "optional": True,
        },
        {
            "prop_name": "Exposure, Auto",
            "disp_name": "Auto Exposure",
            "default": True,
            "ctor": V4L2AutoExposureDisplayer,
            "type": "ctor",
            "optional": True,
        },
        {
            "prop_name": "Exposure (Absolute)",
            "disp_name": "Exposure",
            "min": 1,
            "max": 5000,
            "default": 100,
            "type": "int",
            "optional": True,
        },
        {
            "prop_name": "Exposure Time, Absolute",
            "disp_name": "Exposure",
            "min": 1,
            "max": 5000,
            "default": 100,
            "type": "int",
            "optional": True,
        },
        {
            "prop_name": "Gain",
            "disp_name": "Gain",
            "min": 0,
            "max": 63,
            "default": 8,
            "type": "int",
        },
        # think this is software brightness
        #{
        #    "prop_name": "Brightness",
        #    "disp_name": "Brightness",
        #    "min": 1,
        #    "max": 16,
        #    "default": 8,
        #    "type": "int",
        #},
        {
            "prop_name": "White Balance Temperature, Auto",
            "disp_name": "Auto White Balance Temperature",
            "min": 0,
            "max": 1,
            "default": 0,
            "ctor": BoolDisplayer,
            "type": "ctor",
            # Not present on all host systems for some reason
            # maybe added in Linux 5.15?
            "optional": True,
        },
        # <class 'pyrav4l2.device.WrongIntValue'>: '6509' is not valid for 'White Balance Temperature'. Allowed values: 2800 - 6500 (step: 1)
        {
            "prop_name": "White Balance Temperature",
            "disp_name": "White Balance Temperature",
            "min": 2800,
            "max": 6500,
            "default": 5000,
            "type": "int",
        },
    ],
)])


class V4L2HY800BControlScroll(V4L2ControlScroll):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, groups_gst=groups_gst)
