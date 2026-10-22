from uscope.imager.properties import GstImagerProperties, VirtualProperty, AutoExposureSoftwareVP

from collections import OrderedDict

# https://gstreamer.freedesktop.org/documentation/videotestsrc/?gi-language=c
groups_gst = OrderedDict([
    (
        "Test",
        [
            # chose super random property to have something to test
            {
                "prop_name": "xoffset",
                "min": 0,
                # ?
                "max": 10000,
            },
            # eh is of type GstVideoTestSrcPattern
            {
                "prop_name": "pattern",
                "min": 0,
                "max": 25,
                "type": "int",
                "fill_type": False,
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


class TestSrcProperties(GstImagerProperties):
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups_gst)
        self.add_virtual_property(VirtualProperty(name="exposure",
                                                  value=10000))
        self.add_virtual_property(
            VirtualProperty(name="auto_exposure", value=0))
        self.add_virtual_property(
            AutoExposureSoftwareVP(name="auto_exposure_sw",
                                   value=0,
                                   session=session))
        self.add_virtual_property(VirtualProperty(name="auto_color", value=0))

    def get_auto_exposure_disp_property(self):
        return "auto_exposure"

    def auto_color_enabled(self):
        return bool(self.disp_prop_read("auto_color"))

    def get_exposure_disp_property(self):
        return "exposure"
