from uscope.imager.plugins.aplugin import ArgusGstImagerPlugin
from .widgets import TTControlScroll
from PIL import Image

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

    def gst_decode_image(self, image_dict):
        buf = image_dict["buf"]
        width = image_dict["width"]
        height = image_dict["height"]
        # xxx: sometimes get too much data...is this the right fix?
        # buf = buf[0:width * height * 3]
        assert len(buf) == width * height * 3, (
            "Wanted %u, got %u, w=%u, h=%u" %
            (width * height * 3, len(buf), width, height))
        # Need 59535360 bytes, got 59535360
        # print("Need %u bytes, got %u" % (3 * width * height, len(buf)))
        return {
            "image":
            Image.frombytes('RGB', (width, height), bytes(buf), 'raw', 'RGB')
        }
