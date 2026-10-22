#!/usr/bin/env -S uv run
"""
Imager controls (exposure, white balance, etc)
A Qt view over the imager property model (see uscope/imager/properties.py)
"""

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *


class ICSDisplayer:
    """
    Widget(s) for a single property
    Values are in disp (as displayed) form
    """
    def __init__(self, config, cs):
        # Shared with the model: reflects live gui_driven changes
        self.config = config
        self.cs = cs
        # Set while updating widgets from the model so we don't echo writes back
        self.updating = False

    def disp_name(self):
        return self.config["disp_name"]

    def gui_changed(self, val):
        if self.updating:
            return
        if not self.cs.model.is_gui_driven(self.disp_name()):
            return
        self.cs.model.disp_prop_write(self.disp_name(), val)

    def set_value(self, val):
        if val is None:
            return
        self.updating = True
        try:
            self.disp_property_set_widgets(val)
        finally:
            self.updating = False

    def assemble(self, layoutg, row):
        assert 0, "required"

    def disp_property_set_widgets(self, val):
        assert 0, "required"

    def setEnabled(self, enabled):
        assert 0, "required"

    def setVisible(self, val):
        assert 0, "Required"


class BoolDisplayer(ICSDisplayer):
    def assemble(self, layoutg, row):
        self.label = QLabel(self.config["disp_name"])
        layoutg.addWidget(self.label, row, 0)
        self.cb = QCheckBox()
        layoutg.addWidget(self.cb, row, 1)
        row += 1
        self.cb.stateChanged.connect(
            lambda _state: self.gui_changed(self.cb.isChecked()))
        return row

    def setEnabled(self, enabled):
        self.cb.setEnabled(enabled)

    def disp_property_set_widgets(self, val):
        self.cb.setChecked(bool(val))

    def setVisible(self, val):
        self.label.setVisible(val)
        self.cb.setVisible(val)


class IntDisplayer(ICSDisplayer):
    def slider_changed(self):
        val = int(self.slider.value())
        self.value_label.setText(str(val))
        self.gui_changed(val)

    def assemble(self, layoutg, row):
        self.label = QLabel(self.config["disp_name"])
        layoutg.addWidget(self.label, row, 0)
        self.value_label = QLabel(str(self.config["default"]))
        layoutg.addWidget(self.value_label, row, 1)
        row += 1
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(self.config["min"])
        self.slider.setMaximum(self.config["max"])
        if self.config["default"] is not None:
            self.slider.setValue(self.config["default"])
        self.slider.valueChanged.connect(self.slider_changed)
        layoutg.addWidget(self.slider, row, 0, 1, 2)
        row += 1
        return row

    def disp_property_set_widgets(self, val):
        self.slider.setValue(int(val))
        self.value_label.setText(str(val))

    def setEnabled(self, enabled):
        self.slider.setEnabled(enabled)

    def setVisible(self, val):
        self.label.setVisible(val)
        self.value_label.setVisible(val)
        self.slider.setVisible(val)


DISPLAYERS = {
    "int": IntDisplayer,
    "bool": BoolDisplayer,
}


class ImagerControlScroll(QScrollArea):
    # Model listener events, possibly from a non-GUI thread
    propertiesSet = pyqtSignal(dict)
    propertiesRead = pyqtSignal(dict)
    guiDrivenChanged = pyqtSignal(str, bool)

    def __init__(self, model, ac, parent=None):
        QScrollArea.__init__(self, parent=parent)
        self.model = model
        self.ac = ac
        # Force GUI to take readback values on first update
        self.first_update = True
        self.disp2element = {}
        self.update_timer = None

        self.layout = QVBoxLayout()
        self.layout.addLayout(self.buttonLayout())

        self.propertiesSet.connect(self.properties_set_slot)
        self.propertiesRead.connect(self.properties_read_slot)
        self.guiDrivenChanged.connect(self.gui_driven_changed_slot)
        self.model.add_listener(self)

    """
    Model listener (any thread)
    """

    def properties_set(self, vals):
        self.propertiesSet.emit(vals)

    def properties_read(self, vals):
        self.propertiesRead.emit(vals)

    def gui_driven_changed(self, disp_name, val):
        self.guiDrivenChanged.emit(disp_name, val)

    """
    GUI thread
    """

    def properties_set_slot(self, vals):
        for disp_name, val in vals.items():
            element = self.disp2element.get(disp_name)
            if element:
                element.set_value(val)

    def properties_read_slot(self, vals):
        for disp_name, val in vals.items():
            element = self.disp2element.get(disp_name)
            if not element:
                continue
            if not self.model.is_gui_driven(disp_name) or self.first_update:
                element.set_value(val)
        self.first_update = False

    def gui_driven_changed_slot(self, disp_name, val):
        element = self.disp2element.get(disp_name)
        if element:
            element.setEnabled(val)

    def buttonLayout(self):
        layout = QHBoxLayout()

        bc = self.ac.microscope.bc
        self.cam_default_pb = None
        if bc.dev_mode():
            self.cam_default_pb = QPushButton("Camera default")
            layout.addWidget(self.cam_default_pb)
            self.cam_default_pb.clicked.connect(
                lambda _checked: self.model.apply_cam_defaults())

        self.microscope_default_pb = QPushButton("Microscope default")
        layout.addWidget(self.microscope_default_pb)
        self.microscope_default_pb.clicked.connect(
            lambda _checked: self.model.apply_microscope_defaults())

        self.cal_save_pb = QPushButton("Cal save")
        layout.addWidget(self.cal_save_pb)
        self.cal_save_pb.clicked.connect(
            lambda _checked: self.model.cal_save())

        self.cal_load_pb = QPushButton("Cal load")
        layout.addWidget(self.cal_load_pb)
        self.cal_load_pb.clicked.connect(
            lambda _checked: self.model.cal_load(load_data_dir=True))

        return layout

    def post_imager_ready(self):
        """
        Call once the model is setup
        Allows populating controls
        """
        dev_mode = self.ac.microscope.bc.dev_mode()
        for group_name, props in self.model.groups.items():
            groupbox = QGroupBox(group_name)
            groupbox.setCheckable(False)
            self.layout.addWidget(groupbox)

            layoutg = QGridLayout()
            row = 0
            groupbox.setLayout(layoutg)

            for prop in props.values():
                element = DISPLAYERS[prop["type"]](prop, cs=self)
                row = element.assemble(layoutg, row)
                element.setEnabled(prop["gui_driven"])
                # Normal users don't need to change these
                # but its needed to configure the camera
                # See https://github.com/Labsmore/pyuscope/issues/274
                # Ex: hflip/vflip
                if not prop.get("visible", True):
                    element.setVisible(dev_mode)
                self.disp2element[prop["disp_name"]] = element

        widget = QWidget()
        widget.setLayout(self.layout)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(widget)

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.model.update_by_reading)

    def run(self):
        self.post_imager_ready()
        # Initial update at 200 ms will read back values
        # Then read cal after a few polls at 500 ms
        self.update_timer.start(200)
        # Doesn't load reliably, add a delay
        QTimer.singleShot(500, self.model.cal_load)

    def enable_user_controls(self, enabled):
        """
        Enable or disable the entire pane
        Controls disabled during imaging runs
        Only re-enables those the user owns
        """
        for disp_name, element in self.disp2element.items():
            element.setEnabled(enabled and self.model.is_gui_driven(disp_name))
