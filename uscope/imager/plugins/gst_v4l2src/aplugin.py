from uscope.imager.plugins.aplugin import GstImagerPlugin
from .properties import V4L2GstProperties
from uscope.v4l2_util import find_device, list_devices
from uscope.imager.camera_check import CameraNotFound, not_found_message

import os
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


def describe_v4l2_devices():
    devices = list_devices()
    if not devices:
        return "No v4l2 video devices (/dev/video*) found"
    return "v4l2 video devices found:\n" + "\n".join(
        f"  {device}: {card}" for device, card in devices)


class Plugin(GstImagerPlugin):
    def name(self):
        return "gst-v4l2src"

    def source_name(self):
        return "v4l2src"

    def get_properties(self):
        return V4L2GstProperties(self.session, plugin=self)

    def get_gst_source(self, name=None):
        source = Gst.ElementFactory.make('v4l2src', name)
        assert source is not None

        # Set default v4l2 device, if not given
        # TODO: scrape info one way or the other to identify preferred device
        device = DEFAULT_V4L2_DEVICE
        name = self.session.usc.imager.v4l2_name()
        if name:
            try:
                device = find_device(name)
            except Exception:
                microscope = self.session.microscope
                raise CameraNotFound(
                    not_found_message(
                        microscope.name,
                        getattr(microscope, "config_reason", "unknown"),
                        self.name(),
                        f"Config expects v4l2 camera '{name}'.\n" +
                        describe_v4l2_devices())) from None
            print(f"Camera '{name}': selected {device}")
        source.set_property("device", device)

        return source

    def camera_not_found_detail(self, source):
        device = source.get_property("device")
        return f"Could not open {device}.\n" + describe_v4l2_devices()

    def probe_camera(self, source):
        return {
            "device": source.get_property("device"),
            "card": source.get_property("device-name"),
        }

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
