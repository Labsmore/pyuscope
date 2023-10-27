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
        "type": float,
        "default": "1.0"
    }
}
"""


class InputWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.config = None
        self.layout = QGridLayout()
        self.setLayout(self.layout)
        self.widgets = None

    def clear(self):
        self.config = None
        self.widgets = None
        # Remove all widgets
        for i in reversed(range(self.layout.count())):
            self.layout.itemAt(i).widget().setParent(None)

    def configure(self, config):
        self.clear()
        self.config = config
        row = 0
        self.widgets = {}

        for label, lconfig in self.config.items():
            default = lconfig.get("default")
            widget = None
            if lconfig["widget"] == "QLineEdit":
                self.layout.addWidget(QLabel(label), row, 0)
                widget = QLineEdit(default)
                self.layout.addWidget(widget, row, 1)
            elif lconfig["widget"] == "QComboBox":
                self.layout.addWidget(QLabel(label), row, 0)
                widget = QComboBox()
                self.layout.addWidget(widget, row, 1)
                for val in lconfig["values"]:
                    widget.addItem(val)
                if default:
                    widget.setCurrentText(default)
            else:
                raise ValueError(
                    f"bad config: unknown widget type {lconfig['widget']}")
            self.widgets[label] = widget
            row += 1

    def getValue(self):
        ret = {}
        for label, lconfig in self.config.items():
            widget = self.widgets[label]
            if lconfig["widget"] == "QLineEdit":
                val = str(widget.text())
                if "type" in lconfig:
                    try:
                        val = lconfig["type"](val)
                    except ValueError:
                        raise ValueError(
                            f"bad input on {label} for type {lconfig['type']}: {val}"
                        )
                ret[label] = val
            elif lconfig["widget"] == "QComboBox":
                ret[label] = str(widget.currentText())
            else:
                raise ValueError(
                    f"bad config: unknown widget type {lconfig['widget']}")
        return ret
