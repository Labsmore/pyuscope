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


class InputWidget(QWidget):
    def __init__(self, parent=None, clicked=None):
        super().__init__(parent=parent)
        self.config = None
        self.layout = QGridLayout()
        self.setLayout(self.layout)
        self.widgets = None
        self.clicked = clicked

    def clear(self):
        self.config = None
        self.widgets = None
        # Remove all widgets
        for i in reversed(range(self.layout.count())):
            w = self.layout.itemAt(i).widget()
            if w is not None:
                w.setParent(None)

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
                self.widgets[label] = widget
            elif lconfig["widget"] == "QComboBox":
                self.layout.addWidget(QLabel(label), row, 0)
                widget = QComboBox()
                self.layout.addWidget(widget, row, 1)
                for val in lconfig["values"]:
                    widget.addItem(val)
                if default:
                    widget.setCurrentText(default)
                    self.widgets[label] = widget
            elif lconfig["widget"] == "QPushButtons":
                """
                These will fit weird with the other labels
                Solve this to make more even by adding to custom nested layout
                """
                layout = QHBoxLayout()
                for button_label, data in lconfig["buttons"].items():
                    widget = QPushButton(button_label)

                    def clicked(group_label, button_label, data):
                        def inner():
                            j = {
                                "group": group_label,
                                "label": button_label,
                                "value": data,
                            }
                            self.clicked(j)

                        return inner

                    widget.clicked.connect(clicked(label, button_label, data))
                    layout.addWidget(widget)
                self.layout.addLayout(layout, row, 0, 1, 2)
            else:
                raise ValueError(
                    f"bad config: unknown widget type {lconfig['widget']}")
            row += 1

    def update_defaults(self, vals):
        for label, val in vals.items():
            widget = self.widgets[label]
            lconfig = self.config[label]
            if lconfig["widget"] == "QLineEdit":
                widget.setText(str(val))
            elif lconfig["widget"] == "QComboBox":
                widget.setCurrentText(val)
            else:
                raise ValueError(
                    f"bad config: unknown widget type {lconfig['widget']}")

    def getValue(self):
        ret = {}
        for label, lconfig in self.config.items():
            widget = self.widgets.get(label)
            val = None
            if lconfig["widget"] == "QLineEdit":
                val = str(widget.text())
                if "type" in lconfig:
                    try:
                        val = lconfig["type"](val)
                    except ValueError:
                        raise ValueError(
                            f"bad input on {label} for type {lconfig['type']}: {val}"
                        )
            elif lconfig["widget"] == "QComboBox":
                val = str(widget.currentText())
            elif lconfig["widget"] == "QPushButtons":
                pass
            else:
                raise ValueError(
                    f"bad config: unknown widget type {lconfig['widget']}")
            if val is not None:
                ret[lconfig.get("key", label)] = val
        return ret
