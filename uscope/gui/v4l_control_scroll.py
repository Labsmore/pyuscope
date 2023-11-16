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

    def disp_property_set_widgets(self, val, first_update=False):
        self.cb.setChecked(val)

    def val_raw2disp(self, val):
        return val == AUTO_EXPOSURE_VAL

    def val_disp2raw(self, val):
        if val:
            return AUTO_EXPOSURE_VAL
        else:
            return 1


class V4L2ControlScroll(ImagerControlScroll):
    def __init__(self, vidpip, usc=None, parent=None, groups_gst=None):
        assert groups_gst is not None
        self.vidpip = vidpip
        self.all_controls = None
        ImagerControlScroll.__init__(self,
                                     groups=self.flatten_groups(groups_gst),
                                     usc=usc,
                                     parent=parent)

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())
        self.control_rw = None

    def run(self):
        self.control_rw = v4l2_util.get_control_rw(self.fd())
        self.all_controls = set(self.control_rw.ctrls())
        super().run()

    def fd(self):
        fd = self.vidpip.source.get_property("device-fd")
        # if fd < 0:
        #    print("WARNING: bad fd")
        return fd

    def _raw_prop_write(self, name, val):
        if self.control_rw is not None:
            self.verbose and print(f"v4l2 raw_prop_write() {name} = {val}")
            self.control_rw.ctrl_set(name, val)

    def _raw_prop_read(self, name):
        if self.control_rw is None:
            return None
        else:
            ret = self.control_rw.ctrl_get(name)
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
        # import sys; sys.exit(1)
        return groups

    def get_auto_exposure_raw_name(self):
        auto_exposure_options = ["Exposure, Auto", "Auto Exposure"]
        for option in auto_exposure_options:
            if option in self.all_controls:
                return option
        assert 0, "Failed to find auto exposure name"

    def auto_exposure_enabled(self):
        # 1: no, 3: yes
        return self.disp_prop_read(
            self.get_auto_exposure_disp_property()) == AUTO_EXPOSURE_VAL

    def auto_color_enabled(self):
        prop = "Auto White Balance Temperature"
        if prop not in self.all_controls:
            # maybe?
            return False
        return bool(self.disp_prop_read(prop))

    def set_exposure(self, n):
        self.disp_prop_write(self.get_exposure_disp_property(), n)

    def get_exposure(self):
        return self.disp_prop_read(self.get_exposure_disp_property())

    def get_auto_exposure_disp_property(self):
        return "Auto Exposure"

    def get_exposure_disp_property(self):
        return "Exposure"

    def disp_prop_was_rw(self, name, value):
        # FIXME: these aren't translating to disp,
        # looks like its raw prop
        # https://github.com/Labsmore/pyuscope/issues/280

        # Auto Exposure quickly fights with GUI
        # Disable the control when its activated
        # if name == "Auto Exposure":
        # print("XXX: check auto exposure thing", name, value)
        if name == "Auto Exposure":
            # print("XXX: check auto exposure thing")
            self.set_gui_driven(not value, disp_names=["Exposure"])
        if name == "Auto White Balance Temperature":
            self.set_gui_driven(not value,
                                disp_names=["White Balance Temperature"])

    def validate_raw_name(self, prop_config):
        if prop_config["prop_name"] in self.all_controls:
            return True
        if prop_config.get("optional", False):
            return False
        self.control_rw.dump_control_names()
        print("prop_config", prop_config)
        raise ValueError(f"Bad control name: {prop_config['prop_name']}")
