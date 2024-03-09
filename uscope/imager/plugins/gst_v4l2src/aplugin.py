from uscope.imager.plugins.aplugin import ArgusGstImagerPlugin
from .widgets import V4L2GstControlScroll
from uscope.v4l2_util import find_device

import glob
import gi
import cv2
import numpy as np
from PIL import Image

DEFAULT_V4L2_DEVICE = "/dev/video0"

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)


def have_v4l2_camera():
    # FIXME: more proper check
    return os.path.exists("/dev/video0")


class Plugin(ArgusGstImagerPlugin):
    def name(self):
        return "gst-v4l2src"

    def source_name(self):
        return "v4l2src"

    def get_control_scroll(self):
        return V4L2GstControlScroll

    def get_gst_source(self, name=None):
        source = Gst.ElementFactory.make('v4l2src', name)
        assert source is not None

        assert 0, 'fixme: device name'
        '''
        # Set default v4l2 device, if not given
        if not properties.get("device"):
            properties["device"] = DEFAULT_V4L2_DEVICE
            # TODO: scrape info one way or the other to identify preferred device
            name = self.usc.imager.j.get("v4l2_name")
            if name:
                device = find_device(name)
                print(f"Camera '{name}': selected {device}")
                properties["device"] = device

        return source
        '''

    def detect_sources(self):
        print("FIXME")
        return []
        '''
        # Fallback to generic gst-v4l2 if there is an unknown camera
        if glob.glob("/dev/video*"):
            verbose and print("ADS: found /dev/video device")
            return "gst-v4l2src"
        '''

    def gst_decode_image(self, image_dict):
        buf = image_dict["bytes"]
        width = image_dict["width"]
        height = image_dict["height"]

        w = width
        h = height
        shape = (h, w, 2)
        yuv = np.frombuffer(buf, dtype=np.uint8)
        yuv = yuv.reshape(shape)
        rgba = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGBA_YUYV)
        rgb = cv2.cvtColor(rgba, cv2.COLOR_RGBA2RGB)
        return Image.fromarray(rgb)
