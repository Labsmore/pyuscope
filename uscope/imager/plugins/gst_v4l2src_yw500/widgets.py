"""
Exposure settings are pretty unreliable. Really wants you to use Auto Exposure
If you really try to use manual exposure:
-Auto Exposure level is not reflected in fetching exposure level
-Auto Exposure has more control over exposure than manually setting
-Setting step sizes is in weird steps
-Gain and brighness controls don't do anything
-XXX: contrast is actually brightness?

guvcview
same issues
only displays manual or aperature priority mode
is there a flag that would have indicated which is supported?
"""

from uscope.gui.v4l_control_scroll import V4L2AutoExposureDisplayer, V4L2ControlScroll
from collections import OrderedDict
"""
these controls seem not to work
        {
            "prop_name": "Gain",
            "disp_name": "Gain",
            "min": 0,
            "max": 63,
            "default": 8,
            "type": "int",
        },
        {
            "prop_name": "Brightness",
            "disp_name": "Brightness",
            "min": 1,
            "max": 16,
            "default": 8,
            "type": "int",
        },
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
            "max": 10000,
            "default": 100,
            "type": "int",
            "optional": True,
        },
        {
            "prop_name": "Exposure Time, Absolute",
            "disp_name": "Exposure",
            "min": 1,
            "max": 10000,
            "default": 100,
            "type": "int",
            "optional": True,
        },
        {
            "prop_name": "White Balance Temperature, Auto",
            "disp_name": "Auto White Balance Temperature",
            "min": 0,
            "max": 1,
            "default": 0,
            "type": "int",
            # Not present on all host systems for some reason
            # maybe added in Linux 5.15?
            "optional": True,
        },
        {
            "prop_name": "White Balance Temperature",
            "disp_name": "White Balance Temperature",
            "min": 1800,
            "max": 10000,
            "default": 5000,
            "type": "int",
        },
    ],
)])


class V4L2YW500ControlScroll(V4L2ControlScroll):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, groups_gst=groups_gst)
