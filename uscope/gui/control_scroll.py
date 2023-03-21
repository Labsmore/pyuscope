from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os

from collections import OrderedDict

from uscope import config


class ImagerControlScroll(QScrollArea):
    def __init__(self, groups, usc, verbose=False, parent=None):
        QScrollArea.__init__(self, parent=parent)
        if usc is None:
            usc = config.get_usc()
        self.usc = usc
        self.verbose = verbose
        self.log = lambda x: print(x)

        self.verbose and print("init", groups)

        self.disp2widgets = {}

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

        # Indexed by display name
        # self.disp2ctrl = OrderedDict()
        # Indexed by display name
        self.disp2prop = OrderedDict()
        # Indexed by low level name
        self.raw2prop = OrderedDict()

        for group_name, properties in groups.items():
            groupbox = QGroupBox(group_name)
            groupbox.setCheckable(False)
            layout.addWidget(groupbox)

            layoutg = QGridLayout()
            row = 0
            groupbox.setLayout(layoutg)

            for _disp_name, prop in properties.items():
                # assert disp_name == prop["disp_name"]
                row = self._assemble_property(prop, layoutg, row)

        widget = QWidget()
        widget.setLayout(layout)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(widget)

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_by_prop)

        # self.set_push_gui(True)
        # self.set_push_prop(True)

    def buttonLayout(self):
        layout = QHBoxLayout()

        self.cam_default_pb = QPushButton("Camera default")
        layout.addWidget(self.cam_default_pb)
        self.cam_default_pb.clicked.connect(self.update_by_cam_deafults)

        self.microscope_default_pb = QPushButton("Microscope default")
        layout.addWidget(self.microscope_default_pb)
        self.microscope_default_pb.clicked.connect(
            self.update_by_microscope_deafults)

        self.cal_save_pb = QPushButton("Cal save")
        layout.addWidget(self.cal_save_pb)
        self.cal_save_pb.clicked.connect(self.cal_save)

        self.cal_load_pb = QPushButton("Cal load")
        layout.addWidget(self.cal_load_pb)
        self.cal_load_pb.clicked.connect(self.cal_load_clicked)

        return layout

    def _assemble_int(self, prop, layoutg, row):
        def gui_changed(prop, slider, value_label):
            def f():
                try:
                    val = int(slider.value())
                except ValueError:
                    pass
                else:
                    self.verbose and print(
                        '%s (%s) req => %d, allowed %d' %
                        (prop["disp_name"], prop["prop_name"], val,
                         prop["push_gui"]))
                    assert type(prop["push_gui"]) is bool
                    if prop["push_gui"]:
                        self.prop_write(prop["prop_name"], val)
                        value_label.setText(str(val))

            return f

        layoutg.addWidget(QLabel(prop["disp_name"]), row, 0)
        value_label = QLabel(str(prop["default"]))
        layoutg.addWidget(value_label, row, 1)
        row += 1
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(prop["min"])
        slider.setMaximum(prop["max"])
        # slider.setTickPosition(QSlider.TicksBothSides)
        if prop["default"] is not None:
            slider.setValue(prop["default"])
        slider.valueChanged.connect(gui_changed(prop, slider, value_label))
        self.disp2widgets[prop["disp_name"]] = (slider, value_label)
        layoutg.addWidget(slider, row, 0, 1, 2)
        row += 1
        return row

    def _assemble_bool(self, prop, layoutg, row):
        def gui_changed(prop):
            def f(val):
                self.verbose and print('%s (%s) req => %d, allowed %d' %
                                       (prop["disp_name"], prop["prop_name"],
                                        val, prop["push_gui"]))
                if prop["push_gui"]:
                    self.prop_write(prop["prop_name"], val)

            return f

        cb = QCheckBox(prop["disp_name"])
        if prop["default"] is not None:
            cb.setChecked(prop["default"])
        cb.stateChanged.connect(gui_changed(prop))
        self.disp2widgets[prop["disp_name"]] = cb
        layoutg.addWidget(cb, row, 0, 1, 2)
        row += 1
        return row

    def _prop_defaults(self, prop):
        self.verbose and print("prop", type(prop))
        if type(prop) is dict:
            ret = dict(prop)
        else:
            ret = {"prop_name": prop}

        def default(k, default):
            ret[k] = ret.get(k, default)

        default("disp_name", ret["prop_name"])
        assert "type" in ret, ret
        # xxx: might need to change this
        assert "default" in ret

        # Read only property
        # Don't let user change it
        default("ro", False)
        # Push updates from property changing automatically
        default("push_prop", not ret["ro"])
        # Push updates from user changing GUI
        default("push_gui", not ret["ro"])
        assert type(ret["push_gui"]) is bool

        if ret["type"] == "int":
            assert "min" in ret
            assert "max" in ret
        elif ret["type"] == "bool":
            pass
        else:
            assert 0, "unknown type %s" % ret["type"]

        return ret

    def _assemble_property(self, prop, layoutg, row):
        """
        Take a user supplied property map and add it to the GUI
        """

        prop = self._prop_defaults(prop)
        # self.properties[prop["disp_name"]] = prop

        prop_name = prop["prop_name"]
        disp_name = prop.get("disp_name", prop_name)
        assert disp_name not in self.disp2prop
        self.disp2prop[disp_name] = prop
        assert prop_name not in self.raw2prop
        self.raw2prop[prop_name] = prop

        range_str = ""
        if "min" in prop:
            range_str = ", range %s to %s" % (prop["min"], prop["max"])
        self.verbose and print(
            "add disp %s prop %s, type %s, default %s%s" %
            (disp_name, prop_name, prop["type"], prop["default"], range_str))

        if prop["type"] == "int":
            row = self._assemble_int(prop, layoutg, row)
        elif prop["type"] == "bool":
            row = self._assemble_bool(prop, layoutg, row)
        else:
            assert 0, (prop["type"], prop)
        return row

    def refresh_defaults(self):
        """
        v4l2: we don't get fd until fairly late, so can't set defaults during normal init
        Instead once fd is availible force a refresh
        """
        self.get_disp_properties()

    def get_disp_properties(self):
        """
        Return dict containing property values indexed by display name
        Uses API as source of truth and may not match GUI
        """

        ret = {}
        for disp_name, prop in self.disp2prop.items():
            val = self.raw_prop_read(prop["prop_name"])
            ret[disp_name] = val
            # If we don't have a default take first value
            if prop["default"] is None:
                prop["default"] = val
        return ret

    def set_disp_properties(self, vals):
        """
        Set properties indexed by display name
        Update the GUI and underlying control
        Note: underlying control is updated either directly or indirectly through signal
        """
        for disp_name, val in vals.items():
            try:
                prop = self.disp2prop[disp_name]
            except:
                print("Widget properites:", self.disp2prop.keys())
                print("Set properites:", vals)
                raise
            # Rely on GUI signal writing API unless GUI updates are disabled
            if not prop["push_gui"]:
                self.prop_write(prop["prop_name"], val)
            widgets = self.disp2widgets[disp_name]
            if prop["type"] == "int":
                slider, value_label = widgets
                slider.setValue(val)
                value_label.setText(str(val))
            elif prop["type"] == "bool":
                widgets.setChecked(val)
            else:
                assert 0, prop

    """
    def raw_prop_default(self, name):
        raise Exception("Required")
    """

    def update_by_prop(self):
        """
        Update state based on camera API
        Query all GUI controlled properties and update GUI to reflect current state
        """
        vals = {}
        for disp_name, val in self.get_disp_properties().items():
            # print("Should update %s: %s" % (disp_name, self.disp2prop[disp_name]["push_prop"]))
            if self.disp2prop[disp_name]["push_prop"]:
                vals[disp_name] = val
        self.set_disp_properties(vals)

    def update_by_cam_deafults(self):
        """
        Update state based on default value
        """
        vals = {}
        for disp_name, prop in self.disp2prop.items():
            if prop["default"] is None:
                continue
            vals[disp_name] = prop["default"]
        self.set_disp_properties(vals)

    def update_by_microscope_deafults(self):
        self.cal_load(load_data_dir=False)

    def prop_policy(self, name, value):
        # Auto-exposure quickly fights with GUI
        # Disable the control when its activated
        if name == "auto-exposure":
            push_expotime = not value
            self.set_push_gui(push_expotime, disp_names=["expotime"])

    def prop_write(self, name, value):
        self.raw_prop_write(name, value)
        self.prop_policy(name, value)

    def prop_read(self, name):
        return self.raw_prop_read(name)

    def raw_prop_write(self, name, value):
        raise Exception("Required")

    def raw_prop_read(self, name):
        raise Exception("Required")

    def cal_load_clicked(self, checked):
        self.cal_load(load_data_dir=True)

    def cal_load(self, load_data_dir=True):
        try:
            j = config.cal_load(source=self.vidpip.source_name,
                                load_data_dir=load_data_dir)
        except ValueError as e:
            self.log("WARNING: Failed to load cal: %s" % (e, ))
            return
        if not j:
            return
        self.set_disp_properties(j)

        # Requires special care not to thrash
        self.prop_policy("auto-exposure", self.prop_read("auto-exposure"))

    def cal_save(self):
        config.cal_save_to_data(source=self.vidpip.source_name,
                                properties=self.get_disp_properties(),
                                mkdir=True)

    def run(self):
        if self.update_timer:
            self.update_timer.start(200)
        # Doesn't load reliably, add a delay
        # self.cal_load()
        # Seems to be working, good enough
        QTimer.singleShot(500, self.cal_load)

    def set_push_gui(self, val, disp_names=None):
        """
        val
            true: when the value changes in the GUI set that value onto the device
            false: do nothing when GUI value changes
        disp_names
            None: all values
            iterable: only these
        """
        val = bool(val)
        for disp_name in self.get_disp_properties().keys():
            if disp_names and disp_name not in disp_names:
                continue
            prop = self.disp2prop[disp_name]
            prop["push_gui"] = val

            widgets = self.disp2widgets[disp_name]
            if prop["type"] == "int":
                slider, _value_label = widgets
                slider.setEnabled(val)
            elif prop["type"] == "bool":
                widgets.setEnabled(val)
            else:
                assert 0, prop

    def set_push_prop(self, val, disp_names=None):
        """
        val
            true: when the value changes pon the device set that value in the GUI
            false: do nothing when device value changes
        disp_names
            None: all values
            iterable: only these
        """
        val = bool(val)
        for disp_name in self.get_disp_properties().keys():
            if disp_names and disp_name not in disp_names:
                continue
            self.disp2prop[disp_name]["push_prop"] = val


"""
Had these in the class but really fragile pre-init
"""


def template_property(vidpip, usc, prop_entry):
    if type(prop_entry) == str:
        prop_name = prop_entry
        defaults = {}
    elif type(prop_entry) == dict:
        prop_name = prop_entry["prop_name"]
        defaults = prop_entry
    else:
        assert 0, type(prop_entry)

    ps = vidpip.source.find_property(prop_name)
    ret = {}
    ret["prop_name"] = prop_name
    ret["default"] = ps.default_value

    if ps.value_type.name == "gint":

        def override(which, default):
            if not usc:
                return default
            """
            Ex:
            prop_name: expotime
            which: max

            "source_properties_mod": {
                //In us. Can go up to 15 sec which is impractical for typical usage
                "expotime": {
                    "max": 200000
                },
            },
            """
            spm = usc.imager.source_properties_mod()
            if not spm:
                return default
            pconfig = spm.get(prop_name)
            if not pconfig:
                return default
            return pconfig.get(which, default)

        minimum = override("min", ps.minimum)
        maximum = override("max", ps.maximum)
        ret["min"] = minimum
        ret["max"] = maximum
        ret["type"] = "int"
    elif ps.value_type.name == "gboolean":
        ret["type"] = "bool"
    else:
        assert 0, ps.value_type.name

    ret.update(defaults)
    return ret


def flatten_groups(vidpip, groups_gst, usc):
    """
    Convert a high level gst property description to something usable by widget API
    """
    groups = OrderedDict()
    for group_name, gst_properties in groups_gst.items():
        propdict = OrderedDict()
        for prop_entry in gst_properties:
            val = template_property(vidpip=vidpip,
                                    prop_entry=prop_entry,
                                    usc=usc)
            # NOTE: added source_properties_mod. Maybe leave this at actual max?
            # Log scale would also work well
            if val["prop_name"] == "expotime":
                # Despite max 15e3 exposure max, reporting 5e6
                # actually this is us?
                # Even so limit to 1 sec
                val["max"] = min(1000000, val["max"])
                # print("override", val)
            propdict[val["prop_name"]] = val
        groups[group_name] = propdict
    # print("groups", groups)
    # import sys; sys.exit(1)
    return groups


class GstControlScroll(ImagerControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, groups_gst, usc, parent=None):
        groups = flatten_groups(vidpip=vidpip, groups_gst=groups_gst, usc=usc)
        ImagerControlScroll.__init__(self,
                                     groups=groups,
                                     usc=usc,
                                     parent=parent)
        self.vidpip = vidpip
        # FIXME: hack
        self.log = self.vidpip.log

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

    def raw_prop_write(self, name, val):
        source = self.vidpip.source
        source.set_property(name, val)

    def raw_prop_read(self, name):
        source = self.vidpip.source
        return source.get_property(name)

    """
    def raw_prop_default(self, name):
        ps = self.vidpip.source.find_property(name)
        return ps.default_value
    """
