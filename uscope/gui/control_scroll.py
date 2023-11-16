from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os

from collections import OrderedDict

from uscope import config
"""
Some properties are controlled via library
Some are driven via GUI

"""


class ICSDisplayer:
    def __init__(self, config, cs):
        """
        gui_driven
            if True the GUI can be edited to change the control
            Otherwise the property is polled to read the current value and control is read only
            Not even when GUI is
        """
        self.cs = cs
        self.config = self.defaults(config)
        """
        range_str = ""
        if "min" in prop:
            range_str = ", range %s to %s" % (prop["min"], prop["max"])
        self.verbose and print(
            "add disp %s prop %s, type %s, default %s%s" %
            (disp_name, prop_name, prop["type"], prop["default"], range_str))
        """

    def defaults(self, prop):
        self.cs.verbose and print("prop", type(prop))
        if type(prop) is dict:
            ret = dict(prop)
        else:
            ret = {"prop_name": prop}

        ret.setdefault("disp_name", ret["prop_name"])
        assert "type" in ret, ret
        # xxx: might need to change this
        assert "default" in ret

        # Read only property
        # Don't let user change it
        ret.setdefault("ro", False)
        ret.setdefault("gui_driven", not ret["ro"])

        if ret["type"] == "int":
            assert "min" in ret
            assert "max" in ret

        return ret

    def gui_driven(self):
        return self.config["gui_driven"]

    def set_gui_driven(self, val):
        self.config["gui_driven"] = val
        self.enable_user_controls(val, force=True)

    def disp_property_set_widgets(self, val, first_update=False):
        """
        Set an element to be displayed in the GUI
        Change of GUI state may trigger the property to be written
        Value comes as the low level property value
        GUI may decide to translate it to something friendlier
        """
        assert 0, "required"

    def enable_user_controls(self, enabled, force=False):
        """
        Called when the user is allowed to change properites
        Otherwise the value is displayed but read only
        """
        assert 0, "required"

    def val_raw2disp(self, val):
        """
        Convert a raw property value (ex: flags) to the value as displayed / stored in files
        """
        return val

    def val_disp2raw(self, val):
        """
        Reverse of above
        """
        return val

    def setVisible(self, val):
        assert 0, "Required"


"""
Display a 0 vs 1 int value as a checkbox
"""


class BoolDisplayer(ICSDisplayer):
    def gui_changed(self):
        # Race conditon?
        if not self.config["gui_driven"]:
            print("not gui driven")
            return
        is_checked = self.cb.isChecked()
        # print("is_checked", is_checked)
        self.cs.disp_prop_write(self.config["disp_name"], is_checked)

    def assemble(self, layoutg, row):
        # print("making cb")
        self.label = QLabel(self.config["disp_name"])
        layoutg.addWidget(self.label, row, 0)
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
        return bool(val)

    def val_disp2raw(self, val):
        return int(bool(val))

    def setVisible(self, val):
        self.label.setVisible(val)
        self.cb.setVisible(val)


class IntDisplayer(ICSDisplayer):
    def gui_changed(self):
        # Race conditon?
        if not self.config["gui_driven"]:
            return
        try:
            val = int(self.slider.value())
        except ValueError:
            pass
        else:
            self.cs.verbose and print(
                '%s (%s) GUI changed to %d, gui_driven %d' %
                (self.config["disp_name"], self.config["prop_name"], val,
                 self.config["gui_driven"]))
            self.cs.raw_prop_write(self.config["prop_name"], val)
            self.value_label.setText(str(val))

    def assemble(self, layoutg, row):
        self.label = QLabel(self.config["disp_name"])
        layoutg.addWidget(self.label, row, 0)
        self.value_label = QLabel(str(self.config["default"]))
        layoutg.addWidget(self.value_label, row, 1)
        row += 1
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(self.config["min"])
        self.slider.setMaximum(self.config["max"])
        # slider.setTickPosition(QSlider.TicksBothSides)
        if self.config["default"] is not None:
            self.slider.setValue(self.config["default"])
        self.slider.valueChanged.connect(self.gui_changed)
        # self.disp2widgets[self.confg["disp_name"]] = (self.slider, value_label)
        layoutg.addWidget(self.slider, row, 0, 1, 2)
        row += 1
        return row

    def disp_property_set_widgets(self, val, first_update=False):
        self.slider.setValue(val)
        self.value_label.setText(str(val))

    def enable_user_controls(self, enabled, force=False):
        if self.config["gui_driven"] or force:
            self.slider.setEnabled(enabled)

    def setVisible(self, val):
        self.label.setVisible(val)
        self.value_label.setVisible(val)
        self.slider.setVisible(val)


"""
There are two forms properties are used:
-Raw: the underlying property name + value
    ex: auto_flgs value 1 means auto exposure is disabled
-Disp: "as displayed". A human friendly form
    ex: "Auto-exposure" value False

High level notes:
-Currently data dir saves property values in disp form
"""


class ImagerControlScroll(QScrollArea):
    def __init__(self, groups, usc, verbose=False, parent=None):
        QScrollArea.__init__(self, parent=parent)
        if usc is None:
            usc = config.get_usc()
        self.usc = usc
        self.first_update = True
        self.verbose = verbose
        # self.verbose = True
        self.log = lambda x: print(x)
        self.groups = groups

        self.layout = QVBoxLayout()
        self.layout.addLayout(self.buttonLayout())

        # NOTE: both disp2element and raw2element are expected to
        # always contain the same set of elements.
        # Indexed by display name
        # self.disp2ctrl = OrderedDict()
        # Indexed by display name
        self.disp2element = OrderedDict()
        # Indexed by low level name
        self.raw2element = OrderedDict()

    def post_imager_ready(self):
        """
        Call once gst is running
        Allows populating controls
        """

        self.verbose and print("init", self.groups)
        for group_name, properties in self.groups.items():
            groupbox = QGroupBox(group_name)
            groupbox.setCheckable(False)
            self.layout.addWidget(groupbox)

            layoutg = QGridLayout()
            row = 0
            groupbox.setLayout(layoutg)

            for _disp_name, prop in properties.items():
                if not self.validate_raw_name(prop):
                    continue
                # assert disp_name == prop["disp_name"]
                row = self._assemble_property(prop, layoutg, row)

        widget = QWidget()
        widget.setLayout(self.layout)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(widget)

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_by_reading)

    def buttonLayout(self):
        layout = QHBoxLayout()

        bc = config.get_bc()
        self.cam_default_pb = None
        if bc.dev_mode():
            self.cam_default_pb = QPushButton("Camera default")
            layout.addWidget(self.cam_default_pb)
            self.cam_default_pb.clicked.connect(self.update_by_cam_defaults)

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

    def _assemble_property(self, prop, layoutg, row):
        """
        Take a user supplied property map and add it to the GUI
        """

        # Custom type?
        if prop.get("ctor"):
            element = prop["ctor"](prop, cs=self)
        # Otherwise a few types for common cases
        elif prop["type"] == "int":
            element = IntDisplayer(prop, cs=self)
        elif prop["type"] == "bool":
            element = BoolDisplayer(prop, cs=self)
        else:
            assert 0, (prop["type"], prop)
        row = element.assemble(layoutg, row)

        # Index property and display name to element objects
        prop_name = prop["prop_name"]
        disp_name = prop.get("disp_name", prop_name)
        assert disp_name not in self.disp2element
        self.disp2element[disp_name] = element
        assert prop_name not in self.raw2element
        self.raw2element[prop_name] = element

        return row

    def refresh_defaults(self):
        """
        v4l2: we don't get fd until fairly late, so can't set defaults during normal init
        Instead once fd is availible force a refresh
        """
        self.get_disp_properties()

    def get_disp_properties(self):
        """
        Return dict containing property values indexed by display / human readable name
        Values may also be translated
        Uses API as source of truth and may not match GUI
        """

        ret = {}
        for disp_name, element in self.disp2element.items():
            val = self.disp_prop_read(disp_name)
            ret[disp_name] = val
            # If we don't have a default take first value
            if element.config["default"] is None:
                element.config["default"] = val
        return ret

    def set_disp_properties(self, vals):
        """
        Set properties indexed by display name
        Update the GUI and underlying control
        Note: underlying control is updated either directly or indirectly through signal
        """
        for disp_name, val in vals.items():
            try:
                element = self.disp2element[disp_name]
            except:
                print("Widget properites:", self.disp2element.keys())
                print("Set properites:", vals)
                raise
            # Rely on GUI signal writing API unless GUI updates are disabled
            if not element.config["gui_driven"]:
                # May be 100% excluded by policy
                # self.verbose and print(f"set_disp() {prop['prop_name']} {val}")
                # Set directly in the library,
                # but might as well also update GUI immediately?
                self.disp_prop_write(element.config["disp_name"], val)
            # Always change the GUI to reflect the set value
            # If GUI driven it will trigger the prop write
            # Otherwise update for quicker response and in case read back fails
            element.disp_property_set_widgets(val)

    """
    def raw_prop_default(self, name):
        raise Exception("Required")
    """

    def update_by_reading(self):
        """
        Update state based on camera API
        Query all GUI controlled properties and update GUI to reflect current state
        """
        for disp_name, val in self.get_disp_properties().items():
            # print("Should update %s: %s" % (disp_name, self.disp2element[disp_name]["push_prop"]))
            element = self.disp2element[disp_name]
            # Force GUI to take readback values on first update
            if not element.config["gui_driven"] or self.first_update:
                element.disp_property_set_widgets(
                    val, first_update=self.first_update)
        self.first_update = False

    def update_by_cam_defaults(self):
        """
        Update state based on default value
        """
        for element in self.disp2element.values():
            if element.config["default"] is None:
                continue
            element.disp_property_set_widgets(element.config["default"])

    def update_by_microscope_deafults(self):
        # Set defaults
        self.update_by_cam_defaults()
        # Then override microscope specific properties
        self.cal_load(load_data_dir=False)

    def raw_prop_written(self, name, value):
        """
        Called after writing given key:value pair
        Allows setting more advanced control behaviors
        Ex: when auto-exposure is enabled disable manaul exposure control
        """
        element = self.raw2element.get(name, None)
        # Not every property may be mapped
        if element:
            self.disp_prop_written(element.config["disp_name"],
                                   element.val_raw2disp(value))

    def disp_prop_written(self, name, value):
        self.disp_prop_was_rw(name, value)

    def raw_prop_was_read(self, name, value):
        element = self.raw2element.get(name, None)
        # Not every property may be mapped
        if element:
            self.disp_prop_was_read(element.config["disp_name"],
                                    element.val_raw2disp(value))

    def disp_prop_was_read(self, name, value):
        displayer = self.disp2element.get(name)
        # Ignore incoming data when read but its a GUI driven element
        # Among things this prevents race conditions when changing state
        if displayer and displayer.gui_driven():
            return
        self.disp_prop_was_rw(name, value)

    def disp_prop_was_rw(self, name, value):
        pass

    def raw_prop_write(self, name, value):
        """
        Write a property as the raw name
        """
        self.verbose and print(f"raw_prop_write() {name} = {value}")
        self._raw_prop_write(name, value)
        self.raw_prop_written(name, value)

    def raw_prop_read(self, name, default=False):
        """
        Read a property as the raw name
        """
        ret = self._raw_prop_read(name)
        self.verbose and print(f"raw_prop_read() {name} = {ret}")
        self.raw_prop_was_read(name, ret)
        return ret

    def _raw_prop_write(self, name, value):
        """
        Write to the underlying stream
        In practice this means write a gstreamer property
        """
        raise Exception("Required")

    def _raw_prop_read(self, name):
        """
        Read from the underlying stream
        In practice this means read a gstreamer property
        """
        raise Exception("Required")

    def disp_prop_read(self, disp_name):
        element = self.disp2element[disp_name]
        raw = self.raw_prop_read(element.config["prop_name"])
        return element.val_raw2disp(raw)

    def disp_prop_write(self, disp_name, disp_val):
        element = self.disp2element[disp_name]
        raw_val = element.val_disp2raw(disp_val)
        # print("translate to raw val", raw_val)
        self.raw_prop_write(element.config["prop_name"], raw_val)

    def cal_load_clicked(self, checked):
        self.cal_load(load_data_dir=True)

    def auto_exposure_enabled(self):
        raise Exception("Required")

    def auto_color_enabled(self):
        raise Exception("Required")

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

    def cal_save(self):
        config.cal_save_to_data(source=self.vidpip.source_name,
                                properties=self.get_disp_properties(),
                                mkdir=True)

    def run(self):
        self.post_imager_ready()
        # Initial update at 200 ms will read back values
        # Then read cal after a few polls at 500 ms
        if self.update_timer:
            self.update_timer.start(200)
        # Doesn't load reliably, add a delay
        # self.cal_load()
        # Seems to be working, good enough
        QTimer.singleShot(500, self.cal_load)

    def displayers(self):
        for widget in self.disp2widgets.values():
            yield widget

    def enable_user_controls(self, enabled, force=False):
        """
        Enable or disable the entire pane
        Controls disabled during imaging runs
        Only enables ones though that 
        """
        for disp_name, element in self.disp2element.items():
            element.enable_user_controls(enabled, force=force)

    def validate_disp_names(self, disp_names):
        for disp_name in disp_names:
            if disp_name not in self.disp2element:
                raise ValueError("Invalid property %s" % (disp_name, ))

    def set_gui_driven(self, val, disp_names=None):
        """
        val
            true: when the value changes in the GUI set that value onto the device
            false: do nothing when GUI value changes
        disp_names
            None: all values
            iterable: only these
        """
        # print("set_gui_driven(), disp_names", disp_names, "val", val)
        val = bool(val)
        if disp_names:
            self.validate_disp_names(disp_names)
        for disp_name, element in self.disp2element.items():
            if disp_names and disp_name not in disp_names:
                continue
            element.set_gui_driven(val)

    def validate_raw_name(self, prop_config):
        """
        Return True if should keep
        Return False if should drop (optional / not on this system)
        Throw exception if inherently bad (not optional and not found)
        """
        return True


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


def flatten_groups(vidpip, groups_gst, usc, flatten_hack):
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
            flatten_hack(val)
            propdict[val["prop_name"]] = val
        groups[group_name] = propdict
    # print("groups", groups)
    # import sys; sys.exit(1)
    return groups


class MockControlScroll(ImagerControlScroll):
    def __init__(self, vidpip, usc, parent=None):
        self.vidpip = vidpip
        groups = {}
        ImagerControlScroll.__init__(self,
                                     groups=groups,
                                     usc=usc,
                                     parent=parent)

    def _raw_prop_write(self, name, value):
        pass

    def _raw_prop_read(self, name):
        # sure why not
        return 0

    def auto_exposure_enabled(self):
        return False

    def auto_color_enabled(self):
        return False


class GstControlScroll(ImagerControlScroll):
    """
    Display a number of gst-toupcamsrc based controls and supply knobs to tweak them
    """
    def __init__(self, vidpip, groups_gst, usc, parent=None):
        groups = flatten_groups(vidpip=vidpip,
                                groups_gst=groups_gst,
                                usc=usc,
                                flatten_hack=self.flatten_hack)
        ImagerControlScroll.__init__(self,
                                     groups=groups,
                                     usc=usc,
                                     parent=parent)
        self.vidpip = vidpip
        # FIXME: hack
        self.log = self.vidpip.log

        layout = QVBoxLayout()
        layout.addLayout(self.buttonLayout())

    def flatten_hack(self, val):
        pass

    def _raw_prop_write(self, name, val):
        source = self.vidpip.source
        source.set_property(name, val)

    def _raw_prop_read(self, name):
        source = self.vidpip.source
        return source.get_property(name)

    """
    def raw_prop_default(self, name):
        ps = self.vidpip.source.find_property(name)
        return ps.default_value
    """
