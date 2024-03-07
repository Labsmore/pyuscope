from uscope.imager.plugins.aplugin import ArgusGstImagerPlugin
from .widgets import TTControlScroll

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)

DEFAULT_TOUPCAMSRC_ESIZE = 0
VID_TOUPTEK = 0x0547
PID_MU800 = 0x6801

try:
    import usb
except ImportError:
    usb = None

if usb:
    import usb.core
    import usb.util


class Plugin(ArgusGstImagerPlugin):
    def source_name(self):
        return "gst-toupcamsrc"

    def get_control_scroll(self):
        return TTControlScroll

    def get_gst_source(self, name=None):
        source = Gst.ElementFactory.make('toupcamsrc', name)
        assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
        assert 0, 'fixme: esize'
        return source

    def detect_sources(self):
        print("FIXME")
        return []
        '''
        if usb:
            # find our device
            for dev in usb.core.find(find_all=True):
                if dev.idVendor != VID_TOUPTEK:
                    continue
                return True

        if usb:
            for dev in usb.core.find(find_all=True):
                if dev.idVendor != VID_TOUPTEK:
                    continue
                verbose and print("ADS: found VID 0x%04X, PID 0x%04X" %
                                  (dev.idVendor, dev.idProduct))
                verbose and print("ADS: found ToupTek generic camera")
                return "gst-toupcamsrc"
        '''
