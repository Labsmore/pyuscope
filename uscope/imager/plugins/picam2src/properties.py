from collections import OrderedDict

from uscope.imager.properties import ImagerProperties

# Scaling factor for gain controls
PICAM_GAIN_SCALING = 100.0

groups = OrderedDict([
    ("Toggles", [
        {
            # Value is retrieved using raw_prop_read and set with raw_prop_write.
            "prop_name": "AeEnable",
            "disp_name": "Auto exposure",
            "type": "bool",
        },
        {
            "prop_name": "AwbEnable",
            "disp_name": "Auto white balance",
            "type": "bool",
        },
    ]),
    ("Exposure", [
        {
            "prop_name": "ExposureTime",
            "disp_name": "Exposure time",

            # FIXME: We should pull min/max/default from the camera controls settings, but they seem to be garbage.
            #    F.ex.: min 60, max zero, default None?!
            #"min": picam2.camera_controls['ExposureTime'][0],
            #"max": picam2.camera_controls['ExposureTime'][1],
            #"default": picam2.camera_controls['ExposureTime'][2],
            "min": 60,
            "max": 60000,
            "default": 45000,
            "gui_driven": False,
        },
        {
            "prop_name": "AnalogueGain",
            "disp_name": "Analog gain",
            # FIXME: The min/max changes depending on the mode the camera is in, TODO deal with this (somehow). For now, min 1x, max 32x.
            "min": int(1.0 * PICAM_GAIN_SCALING),
            "max": int(32.0 * PICAM_GAIN_SCALING),
            "default": int(1.0 * PICAM_GAIN_SCALING),
            "gui_driven": False,
        },
    ]),
    ("Colour balance", [
        {
            "prop_name": "ColourGains_R",
            "disp_name": "Red gain",
            "min": 1,
            "max": int(32.0 * PICAM_GAIN_SCALING),
            "gui_driven": False,
        },
        {
            "prop_name": "ColourGains_B",
            "disp_name": "Blue gain",
            "min": 1,
            "max": int(32.0 * PICAM_GAIN_SCALING),
            "gui_driven": False,
        },
        {
            # This is the colour temperature (estimated by AWB), and is read-only
            "prop_name": "ColourTemperature",
            "disp_name": "Colour temperature (Kelvin, estimated by AWB)",
            "min": 1000,
            "max": 10000,
            "ro": True,
            "gui_driven": False,
        }
    ]),
])


class Picam2Properties(ImagerProperties):
    """
    Picamera2 controls
    """
    def __init__(self, session, plugin=None):
        super().__init__(session, plugin=plugin, groups=groups)
        self.picam2 = session.capture_pc2
        self.metadata = {'AnalogueGain': 1.0, 'ColourGains': (1.0, 1.0)}

        # Load some reasonable default camera settings
        from libcamera import controls
        self._camera_controls = {
            "AeEnable": True,
            "AwbEnable": True,
            "AwbMode": controls.AwbModeEnum.Tungsten,
            "ColourGains": [1.0, 1.0],
        }
        self.picam2.set_controls(self._camera_controls)

    def setup(self):
        super().setup()
        preview_widget = getattr(self.session.vidpip, "preview_widget", None)
        if preview_widget is None:
            return

        # Intercept the metadata from the preview widget using the title function update callback
        # We cache the metadata because picam2.capture_metadata() blocks until the next frame
        def title_fn(metadata):
            self.metadata = metadata
            self.update_by_reading()
            return "Picamera2 preview"

        preview_widget.title_function = title_fn

    def template_property(self, prop_entry):
        ret = {"default": None, "type": "int"}
        ret.update(prop_entry)
        return ret

    def _raw_prop_write(self, name, val):
        # Don't allow read-only parameters to be written
        if name in ('ColourCorrectionMatrix', 'ColourTemperature',
                    'DigitalGain', 'FrameDuration', 'HdrChannel', 'lux',
                    'SensorTimestamp'):
            return

        # The colour and analogue gains require special handling as they're scaled
        if name == "AnalogueGain":
            self._camera_controls["AnalogueGain"] = float(
                val) / PICAM_GAIN_SCALING
        # Pycamera2 also wants the colour gains to be packed into a tuple, but a list is fine
        elif name in ("ColourGains_R", "ColourGains_B"):
            # pull the colour gains in from metadata if we don't know them
            if "ColourGains" not in self._camera_controls:
                self._camera_controls["ColourGains"] = list(
                    self.metadata["ColourGains"])
            if name == "ColourGains_R":
                self._camera_controls["ColourGains"][0] = float(
                    val) / PICAM_GAIN_SCALING
            else:
                self._camera_controls["ColourGains"][1] = float(
                    val) / PICAM_GAIN_SCALING
        else:
            self._camera_controls[name] = val

        # If AE is on, don't try to override analogue gain or exposure time
        if self._camera_controls['AeEnable']:
            for k in ('AnalogueGain', 'ExposureTime'):
                self._camera_controls.pop(k, None)

        # If AWB is on, don't try to override the R/B colour gains
        if self._camera_controls['AwbEnable']:
            self._camera_controls.pop('ColourGains', None)

        self.picam2.set_controls(self._camera_controls)

        # Auto-exposure fights with the user: let the camera own exposure while its on
        if name == "AeEnable":
            self.set_gui_driven(not val,
                                disp_names=["Exposure time", "Analog gain"])
        # Same for auto-white-balance
        if name == "AwbEnable":
            self.set_gui_driven(not val, disp_names=["Red gain", "Blue gain"])

    def _raw_prop_read(self, name):
        # Merge the metadata and active settings
        active_settings = {**self._camera_controls, **self.metadata}

        # Scale float parameters as Pyuscope can only deal with ints
        if name == 'AnalogueGain':
            return int(active_settings[name] * PICAM_GAIN_SCALING)
        # Colour gains for R and B are stored in a tuple
        if name == 'ColourGains_R':
            return int(active_settings['ColourGains'][0] * PICAM_GAIN_SCALING)
        if name == 'ColourGains_B':
            return int(active_settings['ColourGains'][1] * PICAM_GAIN_SCALING)
        if name in active_settings:
            return active_settings[name]
        self.log(f"Picam2Properties: unknown property '{name}'")
        return 0

    def get_auto_exposure_disp_property(self):
        return "Auto exposure"

    def auto_color_enabled(self):
        return bool(self.raw_prop_read("AwbEnable"))

    def get_exposure_disp_property(self):
        return "Exposure time"
