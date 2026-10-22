from uscope.imager.properties import ImagerProperties
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


class V4L2MU800Properties(ImagerProperties):
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups_gst)

    def fd(self):
        return self.session.vidpip.source.get_property("device-fd")

    def template_property(self, prop_entry):
        ret = {"default": None, "type": "int"}
        ret.update(prop_entry)
        return ret

    def _raw_prop_write(self, name, val):
        fd = self.fd()
        if fd >= 0:
            v4l2_util.ctrl_set(fd, name, val)

    def _raw_prop_read(self, name):
        fd = self.fd()
        if fd < 0:
            return None
        return v4l2_util.ctrl_get(fd, name)
