from uscope.imager.plugins.gst_v4l2src.aplugin import Plugin as V4l2Plugin
from .widgets import V4L2MU800ControlScroll
from uscope.v4l2_util import find_device

import gi

DEFAULT_V4L2_DEVICE = "/dev/video0"

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)

VID_TOUPTEK = 0x0547
PID_MU800 = 0x6801


class Plugin(V4l2Plugin):
    def name(self):
        return "gst-v4l2src-mu800"

    def get_control_scroll(self):
        return V4L2MU800ControlScroll

    def detect_sources(self):
        print("FIXME")
        return []
        # FIXME: this is probably obsolete now
        # Way to detect if it is a modifed driver w/ direct gain control?
        # Takes about 20 ms, leave in
        if b"touptek" in subprocess.check_output("lsmod"):
            for dev in usb.core.find(find_all=True):
                if dev.idVendor != VID_TOUPTEK:
                    continue
                verbose and print(
                    "ADS: found ToupTek kernel module + camera (MU800?)")
                assert glob.glob("/dev/video*"), "Camera not found???"
                return "gst-v4l2src-mu800"
