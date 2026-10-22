"""
VM1 candidate camera
Hopefully works better than the original camera choice
"""

from uscope.imager.properties import V4L2ImagerProperties
from collections import OrderedDict
"""
this is a "menu"
auto temp is a "bool"
why?

"""

groups_gst = OrderedDict([(
    "Controls",
    [],
)])


class V4L2YW500U3MProperties(V4L2ImagerProperties):
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups_gst)
