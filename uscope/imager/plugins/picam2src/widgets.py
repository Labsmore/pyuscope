from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from collections import OrderedDict

from uscope.gui.control_scroll import ImagerControlScroll

# Scaling factor for gain controls
PICAM_GAIN_SCALING = 100.0

class Picam2ControlScroll(ImagerControlScroll):
    """
    Display a number of picam2 controls and supply knobs to tweak them
    """

    def __init__(self, vidpip, ac, parent=None):
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
                    "min":      int(1.0  * PICAM_GAIN_SCALING),  # int(picam2.camera_controls['AnalogueGain'][0] * 10.0),
                    "max":      int(32.0 * PICAM_GAIN_SCALING),  # int(picam2.camera_controls['AnalogueGain'][1] * 10.0),
                    "default":  int(1.0  * PICAM_GAIN_SCALING),
                    "gui_driven": False,
                },
            ]),
            ("Colour balance", [
                {
                    "prop_name": "ColourGains_R",
                    "disp_name": "Red gain",
                    "min": 1, #int(0.0 * PICAM_GAIN_SCALING),
                    "max": int(32.0 * PICAM_GAIN_SCALING),
                    "gui_driven": False,
                },
                {
                    "prop_name": "ColourGains_B",
                    "disp_name": "Blue gain",
                    "min": 1, # int(0.0 * PICAM_GAIN_SCALING),
                    "max": int(32.0 * PICAM_GAIN_SCALING),
                    "gui_driven": False,
                },
                {
                    # This is the colour temperature (estimated by AWB), and is read-only
                    "prop_name": "ColourTemperature",
                    "disp_name": "Colour temperature (Kelvin, estimated by AWB)",
                    "min": 1000,
                    "max": 10000,

                    # FIXME: For some reason this doesn't work and a hacky fix was added below (search for ColourTempFix)
                    "ro": True,
                    "gui_driven": False,
                }
            ]),
        ])

        ImagerControlScroll.__init__(self,
                                    groups=self.flatten_groups(groups),
                                    ac=ac,
                                    parent=parent)
        self.vidpip = vidpip
        self.picam2 = ac.capture_pc2
        self.log = ac.log
        self.metadata = {
            'AnalogueGain': 1.0,
            'ColourGains': (1.0, 1.0)
        }

        # Load some reasonable default camera settings
        from libcamera import controls
        self._camera_controls = {
            "AeEnable": True,
            # "AeConstraintMode": xxx,
            # "AeMeteringMode": xxx,
            "AwbEnable": True,
            "AwbMode": controls.AwbModeEnum.Tungsten,
            "ColourGains": [1.0, 1.0],
        }
        self.picam2.set_controls(self._camera_controls)

        # Intercept the metadata from the preview widget using the title function update callback
        def title_fn(metadata):
            self.metadata = metadata
            if not True:
                self.log()
                self.log("PiCam2 New Metadata =>")
                for k in ('ExposureTime', 'AnalogueGain', 'DigitalGain', 'ColourTemperature', 'ColourGains'):
                    self.log(f"   {k:17}: {metadata[k]}")
                # self.log(f"   RawMetadata => {metadata}")
                self.log("PiCam2 New ControlState =>")
                for k in ('ExposureTime', 'AnalogueGain'):
                    self.log(f"   {k:17}: min {self.picam2.camera_controls[k][0]} max {self.picam2.camera_controls[k][1]} default {self.picam2.camera_controls[k][2]}")
                self.log()

            # FIXME: This is a bit of a hack. The update timer should update the GUI controls, but it doesn't...
            self.update_by_reading()
            return "Picamera2 preview"
        ac.vidpip.preview_widget.title_function = title_fn

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

    def _raw_prop_write(self, name, val):
        # self.log(f"PiCam2ICS._RawPropWrite {name} => {val}")

        # Disable read-only indicators
        # FIXME (ColourTempFix): Figure out why this is necessary, gui_driven (in the 'group' OrderedDict) should have done this
        self.set_gui_driven(False, disp_names=['Colour temperature (Kelvin, estimated by AWB)'])

        # Don't allow read-only parameters to be written
        if name in ('ColourCorrectionMatrix',
                    'ColourTemperature',
                    'DigitalGain',
                    'FrameDuration',
                    'HdrChannel',
                    'lux',
                    'SensorTimestamp'):
            return

        # The colour and analogue gains require special handling as they're scaled by 10x
        if name == "AnalogueGain":
            self._camera_controls["AnalogueGain"] = float(val) / PICAM_GAIN_SCALING

        # Pycamera2 also wants the colour gains to be packed into a tuple, but a list is fine
        elif name in ("ColourGains_R", "ColourGains_B"):
            # pull the colour gains in from metadata if we don't know them
            if "ColourGains" not in self._camera_controls:
                self._camera_controls["ColourGains"] = list(self.metadata["ColourGains"])

            if name == "ColourGains_R":
                self._camera_controls["ColourGains"][0] = float(val) / PICAM_GAIN_SCALING
            elif name == "ColourGains_B":
                self._camera_controls["ColourGains"][1] = float(val) / PICAM_GAIN_SCALING
        else:
            self._camera_controls[name] = val

        # If AE is on, don't try to override analogue gain or exposure time
        if self._camera_controls['AeEnable']:
            for k in ('AnalogueGain', 'ExposureTime'):
                if k in self._camera_controls:
                    del self._camera_controls[k]

        # If AWB is on, don't try to override the R/B colour gains
        if self._camera_controls['AwbEnable']:
            if 'ColourGains' in self._camera_controls:
                del self._camera_controls['ColourGains']

        # Push the new camera controls to Picamera2
        self.picam2.set_controls(self._camera_controls)

        # Auto-exposure fights with GUI: disable manual exposure controls if it's on
        if name == "AeEnable":
            self.set_gui_driven(not val,
                                disp_names=["Exposure time", "Analog gain"])

        # Auto-white-balance fights with GUI: disable manual colour controls if it's on
        if name == "AwbEnable":
            self.set_gui_driven(not val, disp_names=["Red gain", "Blue gain"])

    def _raw_prop_read(self, name):
        #self.log(f"PiCam2ICS._RawPropRead '{name}'")

        # We cache the metadata (using the preview widget's title_function)
        # because using `picam2.capture_metadata()` will block until the next
        # frame arrives. That's not a great thing to do on every property
        # read.

        # Merge the metadata and active settings
        active_settings = {**self._camera_controls, **self.metadata}

        # Scale float parameters as Pyuscope can only deal with ints
        if name in ('AnalogueGain'):
            return int(active_settings[name] * PICAM_GAIN_SCALING)

        # Colour gains for R and B are stored in a tuple and need to be handled differently
        # There is no colour gain for B, that's the exposure setting...
        if name == 'ColourGains_R':
            return int(active_settings['ColourGains'][0] * PICAM_GAIN_SCALING)
        if name == 'ColourGains_B':
            return int(active_settings['ColourGains'][1] * PICAM_GAIN_SCALING)

        # Property doesn't need special handling, just return it
        if name in active_settings:
            return active_settings[name]

        # If we land here, we're in trouble...
        self.log(f"PiCam2ICS: !!! Unknown RawPropRead '{name} !!!")
        return 0

    """
    def raw_prop_default(self, name):
        ps = self.vidpip.source.find_property(name)
        return ps.default_value
    """

    def auto_exposure_enabled(self):
        self.log("> auto_exposure_enabled")
        return bool(self.raw_prop_read("AeEnable"))

    def auto_color_enabled(self):
        self.log("> auto_color_enabled")
        return bool(self.raw_prop_read("AwbEnable"))

    def set_exposure(self, n):
        self.log("> set_exposure = {n}")
        self.raw_prop_write("ExposureTime", n)
        pass

    def get_exposure(self):
        self.log("> get_exposure")
        return self.raw_prop_read("ExposureTime")
        return 0

    def get_auto_exposure_disp_property(self):
        # Note: this matches against `disp_name`, see `control_scroll.py`
        return "Auto exposure"

    def get_exposure_disp_property(self):
        self.log("> get_exposure_disp_property")
        # Note: this matches against `disp_name`, see `control_scroll.py`
        return "Exposure time"

    def template_property(self, prop_entry):
        prop_name = prop_entry["prop_name"]

        ret = {}
        # self.raw_prop_read(prop_name)
        ret["default"] = None
        ret["type"] = "int"

        ret.update(prop_entry)
        return ret

    def flatten_groups(self, groups_gst):
        """
        Convert a high level gst property description to something usable by widget API
        """
        groups = OrderedDict()
        for group_name, gst_properties in groups_gst.items():
            propdict = OrderedDict()
            for propk in gst_properties:
                val = self.template_property(propk)
                propdict[val["prop_name"]] = val
            groups[group_name] = propdict
        # from pprint import pprint; print("Flattened group list:"); pprint(groups, indent=2)
        # import sys; sys.exit(1)
        return groups
