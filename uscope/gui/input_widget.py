from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
"""
Sample:
{
    "Mode": {
        "widget": "QComboBox",
        "values": ["Fast", "Medium", "Slow"],
        "default": "Medium"
    },
    "Distance": {
        "widget": "QLineEdit",
        "key": "distance",
        "type": float,
        "default": "1.0"
    }
    # A row of buttons
    "Buttons1": {
        "widget": "QPushButtons",
        "buttons": {
            "West": "west",
            "East": {"more": "3"},
        }
    }
}


Button callback structure with button specific value:
{
    "group": "Buttons1",
    "label": "West",
    "value": "west,
}

This can then be embedded into a larger input structure if desired:
input["button"] = {...}
"""


class IWType:
    def __init__(self, iw, label, lconfig):
        self.iw = iw
        self.label = label
        self.lconfig = lconfig

    def configure(self):
        pass

    '''
    def update_default(self, val):
        pass
    '''

    def setValue(self, v):
        raise Exception("Required if getValue() does not return None")

    def getValue(self):
        return None


class QLineEditIW(IWType):
    def configure(self, row, default):
        self.iw.layout.addWidget(QLabel(self.label), row, 0)
        self.widget = QLineEdit(default)
        if self.iw.return_pressed:
            self.widget.returnPressed.connect(self.iw.return_pressed)
        self.iw.layout.addWidget(self.widget, row, 1)

    '''
    def update_default(self, val):
        self.widget.setText(str(val))
    '''

    def setValue(self, v):
        # some things parse to int, float etc
        self.widget.setText(str(v))

    def getValue(self):
        val = str(self.widget.text())
        if not val and self.lconfig.get("empty_as_none", False):
            return None
        if "type" in self.lconfig:
            try:
                val = self.lconfig["type"](val)
            except ValueError:
                raise ValueError(
                    f"bad input on {self.label} for type {self.lconfig['type']}: {val}"
                )
        return val


class QComboBoxIW(IWType):
    def configure(self, row, default):
        self.iw.layout.addWidget(QLabel(self.label), row, 0)
        self.widget = QComboBox()
        self.iw.layout.addWidget(self.widget, row, 1)
        for val in self.lconfig["values"]:
            self.widget.addItem(val)
        if default:
            self.widget.setCurrentText(default)

    '''
    def update_default(self, val):
        self.widget.setCurrentText(val)
    '''

    def setValue(self, v):
        self.widget.setCurrentText(v)

    def getValue(self):
        return str(self.widget.currentText())


def clicked(self, group_label, button_label, value):
    def inner():
        j = {
            "group": group_label,
            "label": button_label,
            "value": value,
        }
        self.iw.clicked(j)

    return inner


class QPushButtonIW(IWType):
    def configure(self, row, default):
        self.widget = QPushButton(self.label)
        self.iw.layout.addWidget(self.widget, row, 0, 1, 2)
        self.widget.clicked.connect(
            clicked(self, self.label, self.label, self.lconfig.get("value")))


class QPushButtonsIW(IWType):
    def configure(self, row, default):
        """
        These will fit weird with the other labels
        Solve this to make more even by adding to custom nested layout
        """
        layout = QHBoxLayout()
        for button_label, data in self.lconfig["buttons"].items():
            widget = QPushButton(button_label)
            widget.clicked.connect(
                clicked(self, self.label, button_label, data))
            layout.addWidget(widget)
        self.iw.layout.addLayout(layout, row, 0, 1, 2)


# https://stackoverflow.com/questions/37564728/pyqt-how-to-remove-a-layout-from-a-layout
def deleteItemsOfLayout(layout):
    if layout is not None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
            else:
                deleteItemsOfLayout(item.layout())


class InputWidget(QWidget):
    def __init__(self, parent=None, clicked=None, return_pressed=None):
        """
        clicked: called when a pushbutton is clicked
        return_pressed: called when return key is used on a text box
        """
        super().__init__(parent=parent)
        self.config = None
        self.widgets = None
        self.clicked = clicked
        self.return_pressed = return_pressed
        self.layout = QGridLayout()
        self.setLayout(self.layout)
        # self.clear()
        self.iws = {}

    def clear(self):
        self.config = None
        self.widgets = None
        deleteItemsOfLayout(self.layout)

    def configure(self, config):
        self.clear()
        self.config = config
        row = 0
        self.iws = {}

        for label, lconfig in self.config.items():
            if lconfig["widget"] == "QLineEdit":
                iw = QLineEditIW(self, label, lconfig)
            elif lconfig["widget"] == "QComboBox":
                iw = QComboBoxIW(self, label, lconfig)
            elif lconfig["widget"] == "QPushButton":
                iw = QPushButtonIW(self, label, lconfig)
            elif lconfig["widget"] == "QPushButtons":
                iw = QPushButtonsIW(self, label, lconfig)
            else:
                raise ValueError(
                    f"bad config: unknown widget type {lconfig['widget']}")
            default = lconfig.get("default")
            iw.configure(row, default)
            self.iws[label] = iw
            row += 1

    def getValues(self):
        """
        Get the current displayed value as a series of key:value pairs
        """
        ret = {}
        for label, iw, in self.iws.items():
            val = iw.getValue()
            if val is not None:
                ret[iw.lconfig.get("key", label)] = val

        return ret

    '''
    def update_defaults(self, vals):
        """
        Updates widgets based on label keys
        FIXME: think this is obsolete / should be removed?
        Generally want to do based on key, not label
        """
        for label, val in vals.items():
            iw = self.iws[label]
            iw.update_default(val)
    '''

    def setValues(self, j):
        """
        Updates widgets based on key:value corresponding to output keys 
        """
        for iw in self.iws.values():
            k = iw.lconfig.get("key", iw.label)
            v = j.get(k, None)
            if v is not None:
                iw.setValue(v)
