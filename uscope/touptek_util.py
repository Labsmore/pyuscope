from PyQt4.QtGui import *
from PyQt4.QtCore import *

from collections import OrderedDict

prop_layout = OrderedDict([
    ("Black balance", {
        "bb-r",
        "bb-g",
        "bb-b",
    }),
    ("White balance", {
        "wb-r",
        "wb-g",
        "wb-b",
    }),
    ("HSV+", {
        "hue",
        "saturation",
        "brightness",
        "contrast",
        "gamma",
    }),
    ("Exposure", {
        "auto-exposure",
        "expotime",
    }),
    ("Flip", {
        "hflip",
        "vflip",
    }),
    ("Misc", {
        #"name": "esize", "ro": True,
    }),
])

def unpack_groupv(groupv):
    if type(groupv) is dict:
        return groupv.get("name"), groupv.get("ro", True)
    else:
        return groupv, False

class TTControlScroll(QScrollArea):
    def __init__(self, vidpip, parent=None):
        QScrollArea.__init__(self, parent=parent)

        self.vidpip = vidpip

        layout = QVBoxLayout()
        row = 0
        self.ctrls = {}

        self.properties = []

        for group_name, group in prop_layout.items():
            groupbox = QGroupBox(group_name)
            groupbox.setCheckable(False)
            layout.addWidget(groupbox)

            layoutg = QGridLayout()
            groupbox.setLayout(layoutg)

            for groupv in group:
                row = self.assemble_group(groupv, layoutg, row)

        widget = QWidget()
        widget.setLayout(layout)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(widget)

    def defaultControls(self):
        print("default controls")
        for name, widget in self.ctrls.items():
            ps = self.vidpip.source.find_property(name)
            if type(widget) == QSlider:
                widget.setValue(ps.default_value)
            elif type(widget) == QCheckBox:
                widget.setChecked(ps.default_value)
            else:
                assert 0, type(widget)

    def assemble_gint(self, name, layoutg, row, ps):
        def changed(name, value_label):
            def f():
                slider = self.ctrls[name]
                try:
                    val = int(slider.value())
                except ValueError:
                    pass
                else:
                    self.vidpip.source.set_property(name, val)
                    value_label.setText(str(val))
                    print('%s changed => %d' % (name, val))

            return f

        value_label = QLabel(str(ps.default_value))
        layoutg.addWidget(QLabel(name), row, 0)
        layoutg.addWidget(value_label, row, 1)
        row += 1
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(ps.minimum)
        slider.setMaximum(ps.maximum)
        slider.setValue(ps.default_value)
        slider.valueChanged.connect(changed(name, value_label))
        self.ctrls[name] = slider
        layoutg.addWidget(slider, row, 0, 1, 2)
        row += 1
        return row

    def assemble_gboolean(self, name, layoutg, row, ps):
        def changed(name, cb):
            def f(val):
                self.vidpip.source.set_property(name, val)
                print('%s changed => %d' % (name, val))

            return f

        cb = QCheckBox(name)
        cb.setChecked(ps.default_value)
        cb.stateChanged.connect(changed(name, cb))
        self.ctrls[name] = cb
        layoutg.addWidget(cb, row, 0, 1, 2)
        row += 1
        return row

    def assemble_group(self, groupv, layoutg, row):
        name, is_const = unpack_groupv(groupv)
        self.properties.append(name)
        ps = self.vidpip.source.find_property(name)
        # default = self.vidpip.source.get_property(name)
        if ps.value_type.name == "gint":
            print("%s, %s, default %s, range %s to %s" %
                  (name, ps.value_type.name, ps.default_value, ps.minimum,
                   ps.maximum))
        else:
            print("%s, %s, default %s" %
                  (name, ps.value_type.name, ps.default_value))

        if ps.value_type.name == "gint":
            row = self.assemble_gint(name, layoutg, row, ps)
        elif ps.value_type.name == "gboolean":
            row = self.assemble_gboolean(name, layoutg, row, ps)
        else:
            assert 0, (name, ps.value_type.name)
        return row
