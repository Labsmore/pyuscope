from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os

from collections import OrderedDict

from uscope import config


class ImagerControlScroll(QScrollArea):
    def __init__(self, groups, parent=None):
        QScrollArea.__init__(self, parent=parent)

        print("init", groups)

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

        self.default_pb = QPushButton("Default")
        layout.addWidget(self.default_pb)
        self.default_pb.clicked.connect(self.update_by_deafults)

        self.cal_save_pb = QPushButton("Cal save")
        layout.addWidget(self.cal_save_pb)
        self.cal_save_pb.clicked.connect(self.cal_save)

        self.cal_load_pb = QPushButton("Cal load")
        layout.addWidget(self.cal_load_pb)
        self.cal_load_pb.clicked.connect(self.cal_load)

        return layout

    def _assemble_int(self, prop, layoutg, row):
        def gui_changed(prop, slider, value_label):
            def f():
                try:
                    val = int(slider.value())
                except ValueError:
                    pass
                else:
                    print('%s (%s) req => %d, allowed %d' %
                          (prop["disp_name"], prop["prop_name"], val, prop["push_gui"]))
                    assert type(prop["push_gui"]) is bool
                    if prop["push_gui"]:
                        self.raw_prop_write(prop["prop_name"], val)
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
        slider.setValue(prop["default"])
        slider.valueChanged.connect(gui_changed(prop, slider, value_label))
        self.disp2widgets[prop["disp_name"]] = (slider, value_label)
        layoutg.addWidget(slider, row, 0, 1, 2)
        row += 1
        return row

    def _assemble_bool(self, prop, layoutg, row):
        def gui_changed(prop):
            def f(val):
                print('%s (%s) req => %d, allowed %d' %
                      (prop["disp_name"], prop["prop_name"], val, prop["push_gui"]))
                if prop["push_gui"]:
                    self.raw_prop_write(prop["prop_name"], val)

            return f

        cb = QCheckBox(prop["disp_name"])
        cb.setChecked(prop["default"])
        cb.stateChanged.connect(gui_changed(prop))
        self.disp2widgets[prop["disp_name"]] = cb
        layoutg.addWidget(cb, row, 0, 1, 2)
        row += 1
        return row

    def _prop_defaults(self, prop):
        print("prop", type(prop))
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
        print("add disp %s prop %s, type %s, default %s%s" %
              (disp_name, prop_name, prop["type"], prop["default"], range_str))

        if prop["type"] == "int":
            row = self._assemble_int(prop, layoutg, row)
        elif prop["type"] == "bool":
            row = self._assemble_bool(prop, layoutg, row)
        else:
            assert 0, (prop["type"], prop)
        return row

    def get_disp_properties(self):
        """
        Return dict containing property values indexed by display name
        Uses API as source of truth and may not match GUI
        """

        ret = {}
        for disp_name, prop in self.disp2prop.items():
            ret[disp_name] = self.raw_prop_read(prop["prop_name"])
        return ret

    def set_disp_properties(self, vals):
        """
        Set properties indexed by display name
        Update the GUI and underlying control
        Note: underlying control is updated either directly or indirectly through signal
        """
        for disp_name, val in vals.items():
            prop = self.disp2prop[disp_name]
            # Rely on GUI signal writing API unless GUI updates are disabled
            if not prop["push_gui"]:
                self.raw_prop_write(prop["prop_name"], val)
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

    def update_by_deafults(self):
        """
        Update state based on default value
        """
        vals = {}
        for disp_name, prop in self.disp2prop.items():
            vals[disp_name] = prop["default"]
        self.set_disp_properties(vals)

    def raw_prop_write(self, name, value):
        raise Exception("Required")

    def raw_prop_read(self, name):
        raise Exception("Required")

    def cal_load(self):
        j = config.cal_load(source=self.vidpip.source_name)
        if not j:
            return
        self.set_disp_properties(j)

    def cal_save(self):
        config.cal_save(source=self.vidpip.source_name,
                        j=self.get_properties())

    def run(self):
        if self.update_timer:
            self.update_timer.start(200)
        self.cal_load()

    def set_push_gui(self, val):
        val = bool(val)
        for disp_name in self.get_disp_properties().keys():
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


    def set_push_prop(self, val):
        val = bool(val)
        for disp_name in self.get_disp_properties().keys():
            self.disp2prop[disp_name]["push_prop"] = val


class GstControlScroll(ImagerControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, groups_gst, parent=None):
        self.vidpip = vidpip
        ImagerControlScroll.__init__(self,
                                     groups=self.flatten_groups(groups_gst),
                                     parent=parent)

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

    def template_property(self, prop_entry):
        if type(prop_entry) == str:
            prop_name = prop_entry
            defaults = {}
        elif type(prop_entry) == dict:
            prop_name = prop_entry["prop_name"]
            defaults = prop_entry
        else:
            assert 0, type(prop_entry)

        ps = self.vidpip.source.find_property(prop_name)
        ret = {}
        ret["prop_name"] = prop_name
        ret["default"] = ps.default_value

        if ps.value_type.name == "gint":
            ret["min"] = ps.minimum
            ret["max"] = ps.maximum
            ret["type"] = "int"
        elif ps.value_type.name == "gboolean":
            ret["type"] = "bool"
        else:
            assert 0, ps.value_type.name

        ret.update(defaults)
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
