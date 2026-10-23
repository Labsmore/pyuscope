from uscope.imager.plugins.aplugin import GstImagerPlugin
from uscope.imager import pixel_format
from .properties import TTProperties

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst

Gst.init(None)

DEFAULT_TOUPCAMSRC_ESIZE = 0
VID_TOUPTEK = 0x0547
PID_MU800 = 0x6801


class Plugin(GstImagerPlugin):
    def source_name(self):
        return "gst-toupcamsrc"

    def get_properties(self):
        return TTProperties(self.session, plugin=self)

    def get_gst_source(self, name=None):
        source = Gst.ElementFactory.make('toupcamsrc', name)
        assert source is not None, "Failed to load toupcamsrc. Is it in the path?"
        return source

    def camera_present(self, source):
        return bool(source.get_property("devicepresent"))

    def camera_not_found_detail(self, source):
        return ("toupcamsrc could not open a ToupTek camera."
                " Is it plugged in and not open in another program?")

    def probe_camera(self, source):
        ret = {}
        # model-name requires toupcamsrc built against SDK 53+
        for k, prop in (("model", "model-name"), ("serial", "serial-number")):
            if source.find_property(prop) is None:
                continue
            v = source.get_property(prop)
            if v:
                ret[k] = v
        return ret

    def configure_resolution(self, log=None):
        from .resolution import ResolutionSelector
        microscope = self.session.microscope
        self._resolution_selector = ResolutionSelector(
            microscope.usc.imager,
            cache_fn=microscope.usc.bc.argus_cache_fn(),
            model_serial_string=microscope.model_serial_string(),
            log=log)
        self._resolution_selector.configure()

    def resolution_selector(self):
        return getattr(self, "_resolution_selector", None)

    def detect_sources(self):
        print("FIXME")
        return []
        '''
        import usb.core
        if 1:
            # find our device
            for dev in usb.core.find(find_all=True):
                if dev.idVendor != VID_TOUPTEK:
                    continue
                return True

        if 1:
            for dev in usb.core.find(find_all=True):
                if dev.idVendor != VID_TOUPTEK:
                    continue
                verbose and print("ADS: found VID 0x%04X, PID 0x%04X" %
                                  (dev.idVendor, dev.idProduct))
                verbose and print("ADS: found ToupTek generic camera")
                return "gst-toupcamsrc"
        '''

    def supported_formats(self):
        """
        All formats toupcamsrc 0.7.0+ can negotiate
        16 bit formats are only usable if the camera has a > 8 bit sensor
        (see GstImager.supported_capture_formats() which checks max-bit-depth)
        """
        return list(pixel_format.FORMATS.keys())

    def _source_prop(self, name, default=None):
        source = getattr(self.session.vidpip, "source", None)
        if source is None:
            return default
        try:
            if source.find_property(name) is None:
                return default
            return source.get_property(name)
        except Exception:
            return default

    def sensor_bayer_pattern(self):
        """
        Bayer pattern of the raw frames as delivered by toupcamsrc,
        in row-major memory order (pixel [0,0], [0,1], [1,0], [1,1])
        Returns None for mono cameras

        The SDK's get_RawFormat() FourCC does not directly describe the
        row-major layout: on an E3ISPM20000KPA the SDK reports GBRG but the
        delivered frame is GRBG, ie the FourCC reads column-major (transposed).
        RGGB / BGGR are unaffected by the transpose.
        Can be overridden with imager.bayer_pattern in microscope.j5
        """
        override = self.session.microscope.usc.imager.bayer_pattern()
        if override:
            return override
        if self._source_prop("mono", False):
            return None
        sdk = self._source_prop("raw-format") or None
        if not sdk:
            return None
        return pixel_format.bayer_transform(sdk, "upper-left-diagonal")

    def capture_bayer_pattern(self):
        """
        Bayer pattern as seen by the capture sink
        Accounts for the videocrop / videoflip elements between the source
        and the capture sink
        """
        pattern = self.sensor_bayer_pattern()
        if pattern is None:
            return None
        usc = self.session.microscope.usc
        crop = usc.imager.crop_tblr()
        if crop:
            if crop["left"] % 2:
                pattern = pixel_format.bayer_transform(pattern, "shift-x")
            if crop["top"] % 2:
                pattern = pixel_format.bayer_transform(pattern, "shift-y")
        pattern = pixel_format.bayer_transform(pattern,
                                               usc.imager.videoflip_method())
        return pattern

    def gst_frame_meta(self, image_dict):
        fmt = image_dict.get("format") or pixel_format.DEFAULT_FORMAT
        if pixel_format.is_16bit(fmt):
            image_dict["bit_depth"] = self._source_prop("bit-depth") or None
        else:
            image_dict["bit_depth"] = 8
        if pixel_format.is_raw(fmt):
            image_dict["bayer_pattern"] = self.capture_bayer_pattern()

    def gst_decode_image(self, image_dict):
        buf = image_dict["bytes"]
        width = image_dict["width"]
        height = image_dict["height"]
        fmt = image_dict.get("format") or pixel_format.DEFAULT_FORMAT
        return pixel_format.decode_frame(
            buf,
            fmt,
            width,
            height,
            stride=image_dict.get("stride"),
            bayer_pattern=image_dict.get("bayer_pattern"))
