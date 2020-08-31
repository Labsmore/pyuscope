from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.control_scroll_base import ImagerControlScroll
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
prop_layout = OrderedDict([("RGBE",
                            OrderedDict([
                                ("Red", ("Red Balance", 0, 1023)),
                                ("Green", ("Gain", 0, 511)),
                                ("Blue", ("Blue Balance", 0, 1023)),
                                ("Exp", ("Exposure", 0, 799)),
                            ]))])


class V4L2MU800ControlScroll(ImagerControlScroll):
    def __init__(self, vidpip, parent=None):
        ImagerControlScroll.__init__(self, parent=parent)
        self.vidpip = vidpip

        layout = QVBoxLayout()

        layout.addLayout(self.buttonLayout())

        # GUI elements
        # Indexed by disp_name
        self.widgets = {}
        # List of v4l2 controls actually used
        self.ctrl2disp = {}
        self.disp2ctrl = {}

        for group_name, group in prop_layout.items():
            groupbox = QGroupBox(group_name)
            groupbox.setCheckable(False)
            layout.addWidget(groupbox)

            layoutg = QGridLayout()
            row = 0
            groupbox.setLayout(layoutg)

            for groupk, groupv in group.items():
                row = self.assemble_group(groupk, groupv, layoutg, row)

        widget = QWidget()
        widget.setLayout(layout)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(widget)

    def fd(self):
        fd = self.vidpip.source.get_property("device-fd")
        # if fd < 0:
        #    print("WARNING: bad fd")
        return fd

    def assemble_group(self, disp_name, groupv, layoutg, row):
        ctrl_name, minval, maxval = groupv
        #minval, maxval = v4l2_util.ctrl_minmax(self.fd())
        self.disp2ctrl[disp_name] = ctrl_name
        self.ctrl2disp[ctrl_name] = disp_name
        # defval = v4l2_util.ctrl_get(self.fd(), ctrl_name)
        defval = 0

        def changed(slider, disp_name, ctrl_name, value_label):
            def f():
                try:
                    val = int(slider.value())
                except ValueError:
                    pass
                else:
                    v4l2_util.ctrl_set(self.fd(), ctrl_name, val)
                    value_label.setText(str(val))
                    print('%s changed => %d' % (disp_name, val))

            return f

        value_label = QLabel(str(defval))
        layoutg.addWidget(QLabel(disp_name), row, 0)
        layoutg.addWidget(value_label, row, 1)
        row += 1
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minval)
        slider.setMaximum(maxval)
        # slider.setTickPosition(QSlider.TicksBothSides)
        slider.setValue(defval)
        slider.valueChanged.connect(
            changed(slider, disp_name, ctrl_name, value_label))
        self.widgets[disp_name] = slider
        layoutg.addWidget(slider, row, 0, 1, 2)
        row += 1

        return row

    def get_properties(self):
        ret = {}
        if self.fd() < 0:
            return ret
        # controls [b'Red Balance', b'Blue Balance', b'Exposure', b'Gain']
        print("controls", v4l2_util.ctrls(self.fd()))
        for ctrl, _disp in self.ctrl2disp.items():
            ret[ctrl] = v4l2_util.ctrl_get(self.fd(), ctrl)
        print(ret)
        return ret

    def set_properties(self, vals):
        if self.fd() < 0:
            return
        """
        for ctrl, val in vals.items():
            assert ctrl in self.ctrl2disp, ctrl
            v4l2_util.ctrl_set(self.fd(), ctrl, val)
        """

        for disp_name, widget in self.widgets.items():
            ctrl_name = self.disp2ctrl[disp_name]
            try:
                val = vals[ctrl_name]
            except KeyError:
                print("WARNING: %s keeping default value" % name)
                continue
            widget.setValue(val)

    def setWidgetsToDefaults(self):
        print("WARNING: default not implemented")
