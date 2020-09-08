from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.control_scroll import ImagerControlScroll
from uscope import v4l2_util

from collections import OrderedDict
"""
acts on file descriptor directly via v4l2 API
(like on old GUI)
"""
"""
WARNING:
hack to control green directly
as such "Gain" is actualy just "Green gain"
Similarly others aren't balance but directly control invididual gains
"""

groups_gst = OrderedDict([
    ("HSV+", [
        {
            "prop_name": "Red Balance",
            "disp_name": "Red",
            "min": 0,
            "max": 1023
        },
        {
            "prop_name": "Gain",
            "disp_name": "Green",
            "min": 0,
            "max": 511
        },
        {
            "prop_name": "Blue Balance",
            "disp_name": "Blue",
            "min": 0,
            "max": 1023
        },
        {
            "prop_name": "Exposure",
            "disp_name": "Exp",
            "min": 0,
            "max": 799
        },
    ]),
])


class V4L2MU800ControlScroll(ImagerControlScroll):
    def __init__(self, vidpip, parent=None):
        self.vidpip = vidpip
        ImagerControlScroll.__init__(self,
                                     groups=self.flatten_groups(groups_gst),
                                     parent=parent)

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

    def fd(self):
        fd = self.vidpip.source.get_property("device-fd")
        # if fd < 0:
        #    print("WARNING: bad fd")
        return fd

    def raw_prop_write(self, name, val):
        v4l2_util.ctrl_set(self.fd(), name, val)

    def raw_prop_read(self, name):
        return v4l2_util.ctrl_get(self.fd(), name)

    def template_property(self, prop_entry):
        prop_name = prop_entry["prop_name"]

        ret = {}
        ret["default"] = self.raw_prop_read(prop_name)
        ret["type"] = "int"

        ret.update(prop_entry)
        return ret

    def flatten_groups(self, groups_gst):
        """
        Convert a high level gst property description to something usable by widget API
        """
        groups = OrderedDict()
        for group_name, gst_properties in groups_gst.items():
            propdict = OrderedDict()
            for propk in gst_properties:
                val = self.template_property(propk)
                propdict[val["prop_name"]] = val
            groups[group_name] = propdict
        print("groups", groups)
        # import sys; sys.exit(1)
        return groups
