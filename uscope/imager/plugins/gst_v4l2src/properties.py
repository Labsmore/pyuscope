from uscope.imager.properties import GstImagerProperties

from collections import OrderedDict

groups_gst = OrderedDict([
    ("HSV+", [
        {
            "prop_name": "brightness",
            "min": 0,
            "max": 255
        },
        {
            "prop_name": "contrast",
            "min": 0,
            "max": 255
        },
        {
            "prop_name": "saturation",
            "min": 0,
            "max": 100
        },
        {
            "prop_name": "hue",
            "min": -180,
            "max": 180
        },
    ]),
])


class V4L2GstProperties(GstImagerProperties):
    """
    Generic v4l2src: controls exposed as gstreamer element properties
    """
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups_gst)
