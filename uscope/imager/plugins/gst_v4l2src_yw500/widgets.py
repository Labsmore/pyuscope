"""
Exposure settings are pretty unreliable. Really wants you to use Auto Exposure
If you really try to use manual exposure:
-Auto Exposure level is not reflected in fetching exposure level
-Auto Exposure has more control over exposure than manually setting
-Setting step sizes is in weird steps
-Gain and brighness controls don't do anything
-XXX: contrast is actually brightness?

guvcview
same issues
only displays manual or aperature priority mode
is there a flag that would have indicated which is supported?
"""

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.gui.control_scroll import ImagerControlScroll, ICSDisplayer
from uscope import v4l2_util

from collections import OrderedDict
"""
acts on file descriptor directly via v4l2 API
(like on old GUI)
"""
"""
WARNING:
hack to control green directly
as such "Gain" is actualy just "Green gain"
Similarly others aren't balance but directly control invididual gains
"""
"""
groups_gst = OrderedDict([
    ("HSV+", [
        {
            "prop_name": "Brightness",
            "disp_name": "Brightness",
            "min": 1,
            "max": 16,
            "default": 8,
            "type": "int",
        },
        {
            "prop_name": "Exposure, Auto",
            "disp_name": "Exposure, Auto",
            "min": 1,
            "max": 3,
            "default": 3,
            "type": "int",
        },
        {
            "prop_name": "Exposure (Absolute)",
            "disp_name": "Exposure (Absolute)",
            "min": 1,
            "max": 10000,
            "default": 100,
            "type": "int",
        },
        {
            "prop_name": "Focus (absolute)",
            "disp_name": "Focus (absolute)",
            "min": 0,
            "max": 65535,
            "default": 0,
            "type": "int",
        },
    ],)
])
"""
"""
Map between the raw int enum value and a simple true false state

enum  v4l2_exposure_auto_type {
    V4L2_EXPOSURE_AUTO = 0,
    V4L2_EXPOSURE_MANUAL = 1,
    V4L2_EXPOSURE_SHUTTER_PRIORITY = 2,
    V4L2_EXPOSURE_APERTURE_PRIORITY = 3
};

  Exposure, Auto (cam: 1)
    range: 0 to 3
    default: 3
    step: 1

it should be 0 or 1 then
Ignore the other settings
"""
AUTO_EXPOSURE_VAL = 3


class V4L2AutoExposureDisplayer(ICSDisplayer):
    def gui_changed(self):
        # print("cb toggled")
        # Race conditon?
        if not self.config["gui_driven"]:
            print("not gui driven")
            return
        self.cs.disp_prop_write(self.config["disp_name"], self.cb.isChecked())

    def assemble(self, layoutg, row):
        # print("making cb")
        layoutg.addWidget(QLabel(self.config["disp_name"]), row, 0)
        self.cb = QCheckBox()
        layoutg.addWidget(self.cb, row, 1)
        row += 1
        self.cb.stateChanged.connect(self.gui_changed)

        return row

    def enable_user_controls(self, enabled, force=False):
        if self.config["gui_driven"] or force:
            self.cb.setEnabled(enabled)

    def disp_property_set_widgets(self, val):
        self.cb.setChecked(val)

    def val_raw2disp(self, val):
        return val == AUTO_EXPOSURE_VAL

    def val_disp2raw(self, val):
        if val:
            return AUTO_EXPOSURE_VAL
        else:
            return 1


"""
these controls seem not to work
        {
            "prop_name": "Gain",
            "disp_name": "Gain",
            "min": 0,
            "max": 63,
            "default": 8,
            "type": "int",
        },
        {
            "prop_name": "Brightness",
            "disp_name": "Brightness",
            "min": 1,
            "max": 16,
            "default": 8,
            "type": "int",
        },
"""
groups_gst = OrderedDict([(
    "Controls",
    [
        # Spelled differently on different systems...
        {
            "prop_name": "Auto Exposure",
            "disp_name": "Auto Exposure",
            "default": True,
            "ctor": V4L2AutoExposureDisplayer,
            "type": "ctor",
            "optional": True,
        },
        {
            "prop_name": "Exposure, Auto",
            "disp_name": "Auto Exposure",
            "default": True,
            "ctor": V4L2AutoExposureDisplayer,
            "type": "ctor",
            "optional": True,
        },
        {
            "prop_name": "Exposure (Absolute)",
            "disp_name": "Exposure",
            "min": 1,
            "max": 10000,
            "default": 100,
            "type": "int",
            "optional": True,
        },
        {
            "prop_name": "Exposure Time, Absolute",
            "disp_name": "Exposure",
            "min": 1,
            "max": 10000,
            "default": 100,
            "type": "int",
            "optional": True,
        },
        {
            "prop_name": "White Balance Temperature, Auto",
            "disp_name": "Auto White Balance Temperature",
            "min": 0,
            "max": 1,
            "default": 0,
            "type": "int",
            # Not present on all host systems for some reason
            # maybe added in Linux 5.15?
            "optional": True,
        },
        {
            "prop_name": "White Balance Temperature",
            "disp_name": "White Balance Temperature",
            "min": 1800,
            "max": 10000,
            "default": 5000,
            "type": "int",
        },
    ],
)])


class V4L2YW500ControlScroll(ImagerControlScroll):
    def __init__(self, vidpip, usc=None, parent=None):
        self.vidpip = vidpip
        self.all_controls = None
        ImagerControlScroll.__init__(self,
                                     groups=self.flatten_groups(groups_gst),
                                     usc=usc,
                                     parent=parent)

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

    def run(self):
        self.all_controls = set(v4l2_util.ctrls(self.fd()))
        super().run()

    def fd(self):
        fd = self.vidpip.source.get_property("device-fd")
        # if fd < 0:
        #    print("WARNING: bad fd")
        return fd

    def raw_prop_write(self, name, val):
        self.verbose and print(f"v4l2 raw_prop_write() {name} = {val}")
        fd = self.fd()
        if fd >= 0:
            v4l2_util.ctrl_set(fd, name, val)

    def raw_prop_read(self, name):
        fd = self.fd()
        if fd < 0:
            return None
        ret = v4l2_util.ctrl_get(fd, name)
        self.verbose and print(f"v4l2 raw_prop_read() {name} = {ret}")
        return ret

    def template_property(self, prop_entry):
        ret = {}
        # self.raw_prop_read(prop_name)
        ret["default"] = None

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
        print("groups", groups)
        # import sys; sys.exit(1)
        return groups

    def get_auto_exposure_name(self):
        auto_exposure_options = ["Exposure, Auto", "Auto Exposure"]
        for option in auto_exposure_options:
            if option in self.all_controls:
                return option

    def auto_exposure_enabled(self):
        # 1: no, 3: yes
        return self.prop_read(self.get_auto_exposure_name()) == 3

    def set_exposure(self, n):
        self.prop_write("Exposure (Absolute)", n)

    def get_exposure(self):
        return self.prop_read("Exposure (Absolute)")

    def get_exposure_property(self):
        return "Exposure (Absolute)"

    def disp_prop_was_rw(self, name, value):
        # Auto Exposure quickly fights with GUI
        # Disable the control when its activated
        # if name == "Auto Exposure":
        # print("XXX: check auto exposure thing", name, value)
        if name == "Auto Exposure":
            # print("XXX: check auto exposure thing")
            self.set_gui_driven(not value,
                                disp_names=["Exposure"])
        if name == "Auto White Balance Temperature":
            self.set_gui_driven(not value,
                                disp_names=["White Balance Temperature"])

    def validate_raw_name(self, prop_config):
        if prop_config["prop_name"] in self.all_controls:
            return True
        if prop_config.get("optional", False):
            return False
        v4l2_util.dump_control_names(self.fd())
        print("prop_config", prop_config)
        raise ValueError("Bad control name: {prop_config['prop_name']}")
