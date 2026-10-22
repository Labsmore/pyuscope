"""
VM1 candidate camera
Hopefully works better than the original camera choice
"""

from uscope.imager.properties import V4L2ImagerProperties, AutoExposureSoftwareVP, AutoExposureSoftwareTargetVP
from collections import OrderedDict
"""
this is a "menu"
auto temp is a "bool"
why?

"""
"""
Re: hidden properties
hardware auto-exposure:
-Can't read back exposure => doesn't synchronize well
White balance:
-Can't read back AWB parameter
-Only AWB can correct fully
-Favor software correcting color curves at known value
"""
groups_gst = OrderedDict([(
    "Controls",
    [
        # Spelled differently on different systems...
        {
            "prop_name": "Auto Exposure",
            "disp_name": "Auto Exposure",
            "default": True,
            "type": "bool",
            "conv": "v4l2_auto_exposure",
            "optional": True,
            "visible": False,
        },
        {
            "prop_name": "Exposure, Auto",
            "disp_name": "Auto Exposure (HW)",
            "default": True,
            "type": "bool",
            "conv": "v4l2_auto_exposure",
            "optional": True,
            "visible": False,
        },
        {
            "prop_name": "auto_exposure_sw",
            "disp_name": "Auto Exposure (SW)",
            "type": "bool",
            "default": False,
            "virtual": True,
        },
        {
            "prop_name": "auto_exposure_sw_target",
            "disp_name": "Auto Exposure (SW) Target",
            "min": 1,
            "max": 100,
            "default": 40,
            "type": "int",
            "virtual": True,
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
            "default": 0,
            "type": "bool",
            # Not present on all host systems for some reason
            # maybe added in Linux 5.15?
            "optional": True,
            "visible": False,
        },
        # <class 'pyrav4l2.device.WrongIntValue'>: '6509' is not valid for 'White Balance Temperature'. Allowed values: 2800 - 6500 (step: 1)
        {
            "prop_name": "White Balance Temperature",
            "disp_name": "White Balance Temperature",
            "min": 2800,
            "max": 6500,
            "default": 5000,
            "type": "int",
            "visible": False,
        },
    ],
)])


class V4L2HY800BProperties(V4L2ImagerProperties):
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups_gst)
        self.add_virtual_property(
            AutoExposureSoftwareVP(name="auto_exposure_sw",
                                   value=0,
                                   session=session))
        self.add_virtual_property(
            AutoExposureSoftwareTargetVP(name="auto_exposure_sw_target",
                                         value=0,
                                         session=session))

    def get_auto_exposure_disp_property(self):
        return "Auto Exposure (SW)"
