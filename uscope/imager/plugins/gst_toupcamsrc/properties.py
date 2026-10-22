from uscope.imager.properties import GstImagerProperties
from uscope.imager import pixel_format

from collections import OrderedDict

PROP_DISP_AUTO_EXPOSURE = "auto-exposure"
PROP_DISP_EXPOSURE_TIME = "expotime"
PROP_DISP_EXPOSURE_GAIN = "expoagain"
PROP_DISP_AUTO_WB = "Auto white balance (AWB)"
PROP_DISP_WB_R = "White balance (red)"
PROP_DISP_WB_G = "White balance (green)"
PROP_DISP_WB_B = "White balance (blue)"

groups_gst = OrderedDict([
    ("Exposure", [
        {
            "prop_name": PROP_DISP_AUTO_EXPOSURE,
            "disp_name": "auto-exposure",
        },
        {
            "prop_name": PROP_DISP_EXPOSURE_TIME,
            "disp_name": "expotime",
        },
        {
            "prop_name": PROP_DISP_EXPOSURE_GAIN,
            "disp_name": "expoagain",
        },
    ]),
    ("Misc", [
        {
            "prop_name": "awb-rgb",
            "disp_name": PROP_DISP_AUTO_WB,
        },
        {
            "prop_name": "wb-r",
            "disp_name": PROP_DISP_WB_R,
        },
        {
            "prop_name": "wb-g",
            "disp_name": PROP_DISP_WB_G,
        },
        {
            "prop_name": "wb-b",
            "disp_name": PROP_DISP_WB_B,
        },
        # Normal users don't need to change these
        # but its needed to configure the camera
        # See https://github.com/Labsmore/pyuscope/issues/274
        {
            "prop_name": "hflip",
            "disp_name": "hflip",
            "visible": False,
        },
        {
            "prop_name": "vflip",
            "disp_name": "vflip",
            "visible": False,
        },
    ]),
])


class TTProperties(GstImagerProperties):
    """
    gst-toupcamsrc properties
    """
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups_gst)
        # White balance is applied by the camera ISP
        # Doesn't do anything in sensor raw modes
        self.raw_mode = False

    def setup(self):
        super().setup()
        fmt = self.session.vidpip.get_capture_format()
        if fmt:
            self.capture_format_changed(fmt)

    def capture_format_changed(self, fmt):
        self.raw_mode = pixel_format.is_raw(fmt)
        self.update_wb_enabled()

    def update_wb_enabled(self):
        wb = [PROP_DISP_WB_R, PROP_DISP_WB_G, PROP_DISP_WB_B]
        if self.raw_mode:
            self.set_gui_driven(False, disp_names=[PROP_DISP_AUTO_WB] + wb)
        else:
            self.set_gui_driven(True, disp_names=[PROP_DISP_AUTO_WB])
            self.set_gui_driven(not self.auto_color_enabled(), disp_names=wb)

    def get_auto_exposure_disp_property(self):
        return PROP_DISP_AUTO_EXPOSURE

    def auto_color_enabled(self):
        return bool(self.disp_prop_read(PROP_DISP_AUTO_WB))

    def get_exposure_disp_property(self):
        return PROP_DISP_EXPOSURE_TIME

    def disp_prop_was_rw(self, name, value):
        # Auto-exposure quickly fights with the user
        # Let the camera own exposure while its active
        if name == PROP_DISP_AUTO_EXPOSURE:
            self.set_gui_driven(
                not value,
                disp_names=[PROP_DISP_EXPOSURE_TIME, PROP_DISP_EXPOSURE_GAIN])
        if name == PROP_DISP_AUTO_WB:
            self.set_gui_driven(
                not value and not self.raw_mode,
                disp_names=[PROP_DISP_WB_R, PROP_DISP_WB_G, PROP_DISP_WB_B])

    def flatten_hack(self, prop):
        # NOTE: added source_properties_mod. Maybe leave this at actual max?
        # Log scale would also work well
        if prop["prop_name"] == PROP_DISP_EXPOSURE_TIME:
            # Despite max 15e3 exposure max, reporting 5e6
            # actually this is us?
            # Even so limit to 1 sec
            prop["max"] = min(1000000, prop["max"])
