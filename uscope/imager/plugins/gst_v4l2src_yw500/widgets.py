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
1 => disabled
3 => enabled
"""


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
            self.slider.setEnabled(enabled)

    def disp_property_set_widgets(self, val):
        self.cb.setChecked(val)

    def val_raw2disp(self, val):
        return val == 3

    def val_disp2raw(self, val):
        if val:
            return 3
        else:
            return 1


"""
        {
            "prop_name": "White Balance Temperature, Auto",
            "disp_name": "AWB",
            "min": 1,
            "max": 16,
            "default": 8,
            "type": "int",
        },
"""
groups_gst = OrderedDict([(
    "Controls",
    [
        {
            "prop_name": "Exposure, Auto",
            "disp_name": "Auto-exposure",
            "default": True,
            "ctor": V4L2AutoExposureDisplayer,
            "type": "ctor",
        },
        {
            "prop_name": "Exposure (Absolute)",
            "disp_name": "Exposure",
            "min": 1,
            "max": 10000,
            "default": 100,
            "type": "int",
        },
    ],
)])


class V4L2YW500ControlScroll(ImagerControlScroll):
    def __init__(self, vidpip, usc=None, parent=None):
        self.vidpip = vidpip
        ImagerControlScroll.__init__(self,
                                     groups=self.flatten_groups(groups_gst),
                                     usc=usc,
                                     parent=parent)

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

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

    def auto_exposure_enabled(self):
        # 1: no, 3: yes
        return self.prop_read("Exposure, Auto") == 3

    def set_exposure(self, n):
        self.prop_write("Exposure (Absolute)", n)

    def get_exposure(self):
        return self.prop_read("Exposure (Absolute)")

    def get_exposure_property(self):
        return "Exposure (Absolute)"

    def disp_prop_was_rw(self, name, value):
        # Auto-exposure quickly fights with GUI
        # Disable the control when its activated
        # if name == "Auto-exposure":
        # print("XXX: check auto exposure thing", name, value)
        if name == "Auto-exposure":
            # print("XXX: check auto exposure thing")
            self.set_gui_driven(not value, disp_names=["Exposure"])
