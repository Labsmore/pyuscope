"""
Advanced options => Instruments
Browse instrument plugins and add / remove instances for this microscope
Each enabled instance runs in its own ScriptingTab (see MainWindow.instrument_tab_add)
"""

import json
import os

import json5
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from uscope.gui.widgets import ArgusTab


class InstrumentDialog(QDialog):
    """
    Add / edit an instrument instance
    """

    def __init__(self,
                 db,
                 entry,
                 replacing=None,
                 reserved_tab_names=(),
                 title="Instrument",
                 parent=None):
        super().__init__(parent)
        self.db = db
        self.orig = dict(entry)
        self.replacing = replacing
        self.reserved_tab_names = reserved_tab_names
        self.entry = None
        self.setWindowTitle(title)

        layout = QVBoxLayout()
        form = QFormLayout()
        form.addRow(
            "Plugin",
            QLabel(entry.get("plugin") or entry.get("plugin_path", "")))
        self.name_le = QLineEdit(entry.get("name", ""))
        self.name_le.setToolTip("Subsystem name used by scripts / the web API")
        form.addRow("Name *", self.name_le)
        self.tab_name_le = QLineEdit(entry.get("tab_name", ""))
        form.addRow("Tab name *", self.tab_name_le)
        self.visible_cb = QCheckBox()
        self.visible_cb.setChecked(entry.get("visible", True))
        form.addRow("Show tab", self.visible_cb)
        layout.addLayout(form)

        layout.addWidget(QLabel("Parameters (JSON)"))
        self.parameters_te = QPlainTextEdit(
            json.dumps(entry.get("parameters", {}), indent=4))
        self.parameters_te.setFont(
            QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.parameters_te)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(500, 400)

    def accept(self):
        entry = dict(self.orig)
        entry["name"] = self.name_le.text().strip()
        entry["tab_name"] = self.tab_name_le.text().strip()
        entry["visible"] = self.visible_cb.isChecked()
        try:
            entry["parameters"] = json5.loads(self.parameters_te.toPlainText()
                                              or "{}")
        except ValueError as e:
            QMessageBox.warning(self, "Instrument", "Bad parameters: %s" % e)
            return
        try:
            entry = self.db.validate(
                entry,
                replacing=self.replacing,
                reserved_tab_names=self.reserved_tab_names)
        except ValueError as e:
            QMessageBox.warning(self, "Instrument", str(e))
            return
        warnings = self.db.parameter_warnings(entry)
        if warnings and QMessageBox.question(
                self, "Instrument", "\n".join(warnings) +
                "\n\nThe plugin will ignore these. Save anyway?"
        ) != QMessageBox.Yes:
            return
        self.entry = entry
        super().accept()


class InstrumentsTab(ArgusTab):
    COLUMNS = ("Name", "Plugin", "Tab", "Status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Instrument names waiting for their plugin to stop
        self.stopping = set()

    @property
    def mw(self):
        return self.ac.mw

    @property
    def db(self):
        return self.mw.instrument_db

    def _initUI(self):
        layout = QVBoxLayout()
        help_l = QLabel(
            "Instruments are plugins that run for as long as Argus is open "
            "(ex: a laser or PWM output). They are found in the instrument/ "
            "folder of each script directory and saved per microscope in %s" %
            self.db.fn)
        help_l.setWordWrap(True)
        layout.addWidget(help_l)

        def available_widget():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Available"))
            self.tree = QTreeWidget()
            self.tree.setHeaderLabels(["Title", "File"])
            self.tree.currentItemChanged.connect(
                lambda _cur, _prev: self.update_description())
            self.tree.itemDoubleClicked.connect(
                lambda _item, _col: self.add_clicked())
            layout.addWidget(self.tree)
            self.description_te = QPlainTextEdit()
            self.description_te.setReadOnly(True)
            layout.addWidget(self.description_te)
            row = QHBoxLayout()
            self.add_pb = QPushButton("Add...")
            self.add_pb.clicked.connect(self.add_clicked)
            row.addWidget(self.add_pb)
            rescan_pb = QPushButton("Rescan")
            rescan_pb.setToolTip("Look for new plugin files")
            rescan_pb.clicked.connect(self.rescan_clicked)
            row.addWidget(rescan_pb)
            row.addStretch()
            layout.addLayout(row)
            w = QWidget()
            w.setLayout(layout)
            return w

        def installed_widget():
            layout = QVBoxLayout()
            layout.addWidget(
                QLabel("Installed on %s" %
                       self.ac.microscope.usc.get_microscope_dataname()))
            self.table = QTableWidget(0, len(self.COLUMNS))
            self.table.setHorizontalHeaderLabels(self.COLUMNS)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeToContents)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self.update_buttons)
            self.table.itemDoubleClicked.connect(
                lambda _item: self.edit_clicked())
            layout.addWidget(self.table)
            row = QHBoxLayout()
            self.edit_pb = QPushButton("Edit...")
            self.edit_pb.clicked.connect(self.edit_clicked)
            self.remove_pb = QPushButton("Remove")
            self.remove_pb.clicked.connect(self.remove_clicked)
            self.enable_pb = QPushButton("Disable")
            self.enable_pb.clicked.connect(self.enable_clicked)
            self.kill_pb = QPushButton("Kill")
            self.kill_pb.setToolTip(
                "Force stop a plugin that didn't stop gracefully")
            self.kill_pb.clicked.connect(self.kill_clicked)
            self.goto_pb = QPushButton("Go to tab")
            self.goto_pb.clicked.connect(self.goto_clicked)
            for pb in (self.edit_pb, self.remove_pb, self.enable_pb,
                       self.kill_pb, self.goto_pb):
                row.addWidget(pb)
            row.addStretch()
            layout.addLayout(row)
            self.problems_l = QLabel("")
            self.problems_l.setWordWrap(True)
            self.problems_l.setStyleSheet("color: red")
            layout.addWidget(self.problems_l)
            w = QWidget()
            w.setLayout(layout)
            return w

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(available_widget())
        splitter.addWidget(installed_widget())
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status)

        self.populate_tree()
        self.populate_table()

    def showEvent(self, event):
        self.status_timer.start(500)
        self.update_status()
        super().showEvent(event)

    def hideEvent(self, event):
        self.status_timer.stop()
        super().hideEvent(event)

    """
    Available
    """

    def populate_tree(self):
        self.tree.clear()
        sources = {}
        for entry in self.db.catalog.entries.values():
            parent = sources.get(entry["source"])
            if parent is None:
                parent = QTreeWidgetItem([entry["source"]])
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                sources[entry["source"]] = parent
            title = entry["title"]
            if entry["problem"]:
                title += " (error)"
            item = QTreeWidgetItem([title, os.path.basename(entry["path"])])
            item.setData(0, Qt.UserRole, entry["id"])
            item.setToolTip(1, entry["path"])
            parent.addChild(item)
        self.tree.resizeColumnToContents(0)
        self.update_description()

    def selected_catalog_entry(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return self.db.catalog.get(item.data(0, Qt.UserRole))

    def update_description(self):
        entry = self.selected_catalog_entry()
        self.add_pb.setEnabled(bool(entry and not entry["problem"]))
        if entry is None:
            self.description_te.setPlainText("")
        elif entry["problem"]:
            self.description_te.setPlainText("%s\n\n%s" %
                                             (entry["path"], entry["problem"]))
        else:
            self.description_te.setPlainText(
                "%s\n\n%s" % (entry["path"], entry["description"]))

    def rescan_clicked(self):
        self.db.catalog.scan()
        self.populate_tree()
        self.populate_table()

    def add_clicked(self):
        catalog_entry = self.selected_catalog_entry()
        if not catalog_entry or catalog_entry["problem"]:
            return
        stem = os.path.splitext(os.path.basename(catalog_entry["path"]))[0]
        name = self.db.unique_name(stem)
        entry = {
            "name": name,
            "plugin": catalog_entry["id"],
            "plugin_path": catalog_entry["path"],
            "tab_name": catalog_entry["title"] if name == stem else name,
            "visible": True,
            "enabled": True,
            "parameters": dict(catalog_entry["parameters"]),
        }
        dialog = InstrumentDialog(
            self.db,
            entry,
            reserved_tab_names=self.mw.builtin_tab_names(),
            title="Add instrument",
            parent=self)
        if not dialog.exec_():
            return
        if not self.save(lambda: self.db.add(
                dialog.entry, reserved_tab_names=self.mw.builtin_tab_names())):
            return
        self.ac.log("Instruments: added %s" % dialog.entry["name"])
        self.mw.instrument_tab_add(dialog.entry, live=True)
        self.populate_table(select=dialog.entry["name"])

    """
    Installed
    """

    def save(self, f):
        """
        Run a DB mutation, reporting errors
        """
        try:
            f()
        except Exception as e:
            QMessageBox.warning(self, "Instruments", str(e))
            return False
        return True

    def populate_table(self, select=None):
        if select is None:
            select = self.selected_name()
        self.table.setRowCount(0)
        for name, entry in self.db.instances.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(name)
            item.setData(Qt.UserRole, name)
            self.table.setItem(row, 0, item)
            self.table.setItem(
                row, 1,
                QTableWidgetItem(
                    entry.get("plugin") or entry.get("plugin_path", "")))
            self.table.setItem(row, 2, QTableWidgetItem(entry["tab_name"]))
            self.table.setItem(row, 3, QTableWidgetItem(""))
            if name == select:
                self.table.selectRow(row)
        problems = list(self.db.problems)
        problems += [
            "%s: %s" % (e["path"], e["problem"])
            for e in self.db.catalog.entries.values() if e["problem"]
        ]
        self.problems_l.setText("\n".join(problems))
        self.update_status()

    def selected_name(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole)

    def status(self, name):
        tab = self.mw.instrumentTabs.get(name)
        if tab is None:
            return "Disabled"
        if name in self.stopping:
            return "Stopping..."
        if tab.plugin is None:
            return "Load failed"
        if tab.runner.is_running():
            return "Running"
        return "Idle"

    def update_status(self):
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).data(Qt.UserRole)
            self.table.item(row, 3).setText(self.status(name))
        self.update_buttons()

    def update_buttons(self):
        name = self.selected_name()
        selected = name is not None and name not in self.stopping
        for pb in (self.edit_pb, self.remove_pb, self.enable_pb):
            pb.setEnabled(selected)
        self.goto_pb.setEnabled(selected and name in self.mw.instrumentTabs
                                and self.db.instances[name]["visible"])
        self.kill_pb.setEnabled(name in self.stopping)
        if name in self.db.instances and not self.db.instances[name]["enabled"]:
            self.enable_pb.setText("Enable")
        else:
            self.enable_pb.setText("Disable")

    def after_stopped(self, name, f):
        """
        Stop the instrument's plugin (if running) and then call f()
        """
        tab = self.mw.instrumentTabs.get(name)
        if tab is None or not tab.runner.is_running():
            f()
            return

        def done():
            self.stopping.discard(name)
            f()

        self.stopping.add(name)
        tab._after_done = done
        tab.runner.stop()
        self.update_status()

    def confirm_stop(self, name, action):
        if not self.mw.instrumentTabs.get(name):
            return True
        return QMessageBox.question(
            self, "Instruments",
            "%s %s?\n\nThis stops the plugin, which may change hardware state "
            "(ex: turn an output on or off)." %
            (action, name)) == QMessageBox.Yes

    def edit_clicked(self):
        name = self.selected_name()
        if name is None or name in self.stopping:
            return
        reserved = self.mw.builtin_tab_names()
        dialog = InstrumentDialog(self.db,
                                  self.db.instances[name],
                                  replacing=name,
                                  reserved_tab_names=reserved,
                                  title="Edit instrument",
                                  parent=self)
        if not dialog.exec_():
            return
        entry = dialog.entry
        if not self.confirm_stop(name, "Restart"):
            return

        def apply():
            if name in self.mw.instrumentTabs:
                self.mw.instrument_tab_remove(name)
            if self.save(lambda: self.db.update(
                    name, entry, reserved_tab_names=reserved)):
                self.ac.log("Instruments: updated %s" % entry["name"])
                if entry["enabled"]:
                    self.mw.instrument_tab_add(entry, live=True)
                self.populate_table(select=entry["name"])
            elif self.db.instances[name]["enabled"]:
                # Restore the old instance
                self.mw.instrument_tab_add(self.db.instances[name], live=True)

        self.after_stopped(name, apply)

    def remove_clicked(self):
        name = self.selected_name()
        if name is None or name in self.stopping:
            return
        if QMessageBox.question(
                self, "Instruments",
                "Remove instrument %s?\n\nThis stops the plugin, which may "
                "change hardware state (ex: turn an output on or off)." %
                name) != QMessageBox.Yes:
            return

        def apply():
            if name in self.mw.instrumentTabs:
                self.mw.instrument_tab_remove(name)
            if self.save(lambda: self.db.remove(name)):
                self.ac.log("Instruments: removed %s" % name)
            self.populate_table()

        self.after_stopped(name, apply)

    def enable_clicked(self):
        name = self.selected_name()
        if name is None or name in self.stopping:
            return
        entry = dict(self.db.instances[name])
        entry["enabled"] = not entry["enabled"]
        if entry["enabled"]:
            if not self.save(lambda: self.db.update(name, entry)):
                return
            self.mw.instrument_tab_add(entry, live=True)
            self.populate_table()
            return
        if not self.confirm_stop(name, "Disable"):
            return

        def apply():
            if name in self.mw.instrumentTabs:
                self.mw.instrument_tab_remove(name)
            self.save(lambda: self.db.update(name, entry))
            self.populate_table()

        self.after_stopped(name, apply)

    def kill_clicked(self):
        name = self.selected_name()
        tab = self.mw.instrumentTabs.get(name)
        if tab is not None:
            tab.kill_pb_clicked()

    def goto_clicked(self):
        tab = self.mw.instrumentTabs.get(self.selected_name())
        if tab is None:
            return
        index = self.mw.tab_widget.indexOf(tab)
        if index >= 0:
            self.mw.tab_widget.setCurrentIndex(index)
            self.mw.raise_()
            self.mw.activateWindow()
