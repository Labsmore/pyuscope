#!/usr/bin/env -S uv run
"""
Imager controls (exposure, white balance, etc)
A Qt view over the imager property model (see uscope/imager/properties.py)
"""

import math

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
        self.value_label.setText(self.value_text(val))
        self.gui_changed(val)

    def value_text(self, val):
        return str(val)

    def make_slider(self):
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(self.config["min"])
        slider.setMaximum(self.config["max"])
        return slider

    def assemble(self, layoutg, row):
        self.label = QLabel(self.config["disp_name"])
        layoutg.addWidget(self.label, row, 0)
        self.value_label = QLabel()
        if self.config["default"] is not None:
            self.value_label.setText(self.value_text(self.config["default"]))
        layoutg.addWidget(self.value_label, row, 1)
        row += 1
        self.slider = self.make_slider()
        if self.config["default"] is not None:
            self.slider.setValue(self.config["default"])
        self.slider.valueChanged.connect(self.slider_changed)
        layoutg.addWidget(self.slider, row, 0, 1, 2)
        row += 1
        return row

    def disp_property_set_widgets(self, val):
        self.slider.setValue(int(val))
        self.value_label.setText(self.value_text(val))

    def setEnabled(self, enabled):
        self.slider.setEnabled(enabled)

    def setVisible(self, val):
        self.label.setVisible(val)
        self.value_label.setVisible(val)
        self.slider.setVisible(val)


def format_us(us):
    if us >= 1000000:
        return f"{us / 1000000:g} s"
    if us >= 1000:
        return f"{us / 1000:g} ms"
    return f"{us:g} us"


def format_gain_percent(val):
    return f"{val / 100:g}x"


UNIT_FORMATTERS = {
    "us": format_us,
    "gain_percent": format_gain_percent,
}


def log_ticks(lo, hi):
    """
    Round values to label across [lo, hi]
    Decades only if the range is wide, otherwise 1-2-5
    """
    mults = (1, ) if math.log10(hi / lo) >= 3 else (1, 2, 5)
    ret = []
    for exp in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
        for mult in mults:
            val = mult * 10**exp
            if lo <= val <= hi:
                ret.append(val)
    if len(ret) < 2:
        ret = [lo, hi]
    return ret


class LogScaleLabels(QWidget):
    """
    Tick marks + labels drawn under a LogSlider, aligned to the handle center
    """
    TICK_LEN = 4

    def __init__(self, log_slider, formatter, parent=None):
        super().__init__(parent=parent)
        self.log_slider = log_slider
        self.formatter = formatter
        self.ticks = log_ticks(log_slider.lo, log_slider.hi)
        self.setMinimumHeight(self.fontMetrics().height() + self.TICK_LEN + 2)

    def tick_x(self, val):
        slider = self.log_slider.slider
        opt = QStyleOptionSlider()
        slider.initStyleOption(opt)
        style = slider.style()
        groove = style.subControlRect(QStyle.CC_Slider, opt,
                                      QStyle.SC_SliderGroove, slider)
        handle = style.subControlRect(QStyle.CC_Slider, opt,
                                      QStyle.SC_SliderHandle, slider)
        span = groove.width() - handle.width()
        x = groove.x() + handle.width() // 2 + QStyle.sliderPositionFromValue(
            slider.minimum(), slider.maximum(),
            self.log_slider.val2pos(val), span)
        return self.mapFromGlobal(slider.mapToGlobal(QPoint(x, 0))).x()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.WindowText))
        fm = self.fontMetrics()
        last_right = None
        for val in self.ticks:
            x = self.tick_x(val)
            text = self.formatter(val)
            w = fm.horizontalAdvance(text)
            left = max(0, min(self.width() - w, x - w // 2))
            # Drop labels that would collide when the pane is narrow
            if last_right is not None and left < last_right + fm.averageCharWidth():
                continue
            last_right = left + w
            painter.drawLine(x, 0, x, self.TICK_LEN)
            painter.drawText(left, self.TICK_LEN + 1 + fm.ascent(), text)


class LogSlider(QWidget):
    """
    Slider over [vmin, vmax] with log spacing
    API mirrors the subset of QSlider used by IntDisplayer
    """
    valueChanged = pyqtSignal(int)
    STEPS = 1000

    def __init__(self, vmin, vmax, formatter, log_min=1, parent=None):
        super().__init__(parent=parent)
        self.lo = max(vmin, log_min, 1)
        self.hi = max(vmax, self.lo + 1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(self.STEPS)
        self.slider.setPageStep(self.STEPS // 20)
        self.slider.valueChanged.connect(
            lambda pos: self.valueChanged.emit(self.pos2val(pos)))

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.slider)
        layout.addWidget(LogScaleLabels(self, formatter))
        self.setLayout(layout)

    def pos2val(self, pos):
        return int(
            round(self.lo * (self.hi / self.lo)**(pos / self.STEPS)))

    def val2pos(self, val):
        if val <= self.lo:
            return 0
        if val >= self.hi:
            return self.STEPS
        return int(
            round(self.STEPS * math.log(val / self.lo) /
                  math.log(self.hi / self.lo)))

    def value(self):
        return self.pos2val(self.slider.value())

    def setValue(self, val):
        self.slider.setValue(self.val2pos(val))


class LogIntDisplayer(IntDisplayer):
    def formatter(self):
        return UNIT_FORMATTERS.get(self.config.get("units"), str)

    def value_text(self, val):
        units = self.config.get("units")
        if units not in UNIT_FORMATTERS:
            return str(val)
        return f"{val} ({UNIT_FORMATTERS[units](val)})"

    def make_slider(self):
        return LogSlider(self.config["min"],
                         self.config["max"],
                         self.formatter(),
                         log_min=self.config.get("log_min", 1))


DISPLAYERS = {
    "int": IntDisplayer,
    "bool": BoolDisplayer,
}


def displayer_class(prop):
    if prop["type"] == "int" and prop.get("scale") == "log":
        return LogIntDisplayer
    return DISPLAYERS[prop["type"]]


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
                element = displayer_class(prop)(prop, cs=self)
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
