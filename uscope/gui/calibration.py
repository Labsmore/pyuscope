"""Calibration / optics settings pages and collect/analyze wizard."""

import json
import os
import subprocess
import traceback

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from uscope.scripting import ScriptingPlugin
from uscope.gui.scripting import QtScriptHost, exec_prompt
from uscope.gui.widgets import ArgusTab
from uscope.cal import params as cal_params
from uscope.cal.base import Quality
from uscope.cal.registry import all_cals, get as get_cal
from uscope.cal.session import CalSession
from uscope.cal.store import CalEntry, _scope_tuple
from uscope.util import readj, writej


class CalScriptHost(QtScriptHost):
    progress_sig = pyqtSignal(float, str)

    def progress(self, frac, text):
        self.progress_sig.emit(frac, text)


class CalibrationRunPlugin(ScriptingPlugin):
    def __init__(self, session, names, quality=Quality.FULL, host=None):
        super().__init__(session, host=host)
        self._names = names
        self._quality = quality
        self.cal_session = None

    def show_run_button(self):
        return False

    def run_test(self):
        from uscope.cal.context import CalContext
        from uscope.cal.report import write_report

        ctx = CalContext(plugin=self,
                         quality=self._quality,
                         prompt_fn=self.prompt,
                         progress_fn=self.host.progress)
        self.cal_session = CalSession(self.session.microscope,
                                      self._names,
                                      quality=self._quality)
        self.cal_session.run_collect_analyze(ctx)
        write_report(self.cal_session)


class CalibrationWizard(QDialog):
    def __init__(self, ac, parent=None):
        super().__init__(parent)
        self.ac = ac
        self.plugin = None
        self.host = CalScriptHost(parent=self)
        self.host.log_sig.connect(self._log)
        self.host.progress_sig.connect(self._on_progress)
        self.host.prompt_sig.connect(self._on_prompt)
        self.host.done_sig.connect(self._done)
        self.setWindowTitle("Run calibration")
        self.resize(640, 520)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Select calibrations (all optional):"))
        self.checks = {}
        for name, cal in all_cals().items():
            cb = QCheckBox("%s — %s" % (cal.title, cal.description))
            cb.setChecked(False)
            self.checks[name] = cb
            layout.addWidget(cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Quality"))
        self.quality_cb = QComboBox()
        self.quality_cb.addItems([Quality.QUICK, Quality.FULL])
        self.quality_cb.setCurrentText(Quality.FULL)
        row.addWidget(self.quality_cb)
        row.addStretch()
        layout.addLayout(row)

        self.preflight_te = QTextEdit()
        self.preflight_te.setReadOnly(True)
        self.preflight_te.setMaximumHeight(120)
        layout.addWidget(self.preflight_te)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel("Idle")
        layout.addWidget(self.status)

        self.log_te = QTextEdit()
        self.log_te.setReadOnly(True)
        layout.addWidget(self.log_te)

        btns = QHBoxLayout()
        self.check_pb = QPushButton("Check preflight")
        self.check_pb.clicked.connect(self._preflight)
        self.run_pb = QPushButton("Collect + analyze")
        self.run_pb.clicked.connect(self._run)
        self.stop_pb = QPushButton("Stop")
        self.stop_pb.setEnabled(False)
        self.stop_pb.clicked.connect(self._stop)
        self.apply_pb = QPushButton("Apply results")
        self.apply_pb.setEnabled(False)
        self.apply_pb.clicked.connect(self._apply)
        self.report_pb = QPushButton("Open report")
        self.report_pb.setEnabled(False)
        self.report_pb.clicked.connect(self._open_report)
        for w in (self.check_pb, self.run_pb, self.stop_pb, self.apply_pb,
                  self.report_pb):
            btns.addWidget(w)
        layout.addLayout(btns)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.setLayout(layout)

    def _selected(self):
        names = [n for n, cb in self.checks.items() if cb.isChecked()]
        # Auto-include hard requires
        added = True
        while added:
            added = False
            for n in list(names):
                for req in get_cal(n).requires:
                    if req not in names:
                        names.append(req)
                        self.checks[req].setChecked(True)
                        added = True
        return names

    def _preflight(self):
        names = self._selected()
        if not names:
            self.preflight_te.setText("No calibrations selected.")
            return
        lines = []
        from uscope.cal.context import CalContext
        ctx = CalContext(plugin=None, microscope=self.ac.microscope)
        for name in names:
            lines.append(get_cal(name).title)
            try:
                for c in get_cal(name).preflight(ctx):
                    mark = "OK" if c.ok else "WAIT"
                    lines.append("  [%s] %s %s" % (mark, c.name, c.hint or ""))
            except Exception as e:
                lines.append("  preflight error: %s" % e)
        self.preflight_te.setText("\n".join(lines))

    def _run(self):
        names = self._selected()
        if not names:
            QMessageBox.information(self, "Calibration",
                                    "Select at least one calibration.")
            return
        if self.plugin is not None:
            return
        quality = self.quality_cb.currentText()
        self.plugin = CalibrationRunPlugin(self.ac,
                                           names,
                                           quality=quality,
                                           host=self.host)
        self.run_pb.setEnabled(False)
        self.stop_pb.setEnabled(True)
        self.apply_pb.setEnabled(False)
        self.status.setText("Running…")
        self.plugin.reset()
        self.plugin.start()

    def _stop(self):
        if self.plugin:
            self.plugin.shutdown()

    def _log(self, s):
        self.log_te.append(s)

    def _on_progress(self, frac, text):
        self.progress.setValue(int(frac * 100))
        self.status.setText(text)

    def _on_prompt(self, title, text, buttons, box_ev):
        exec_prompt(self, title, text, buttons, box_ev)

    def _done(self, _plugin=None):
        self.stop_pb.setEnabled(False)
        self.run_pb.setEnabled(True)
        ok = self.plugin and self.plugin.succeeded()
        self.status.setText("Finished" if ok else "Stopped / failed")
        if self.plugin and self.plugin.cal_session:
            self.apply_pb.setEnabled(True)
            self.report_pb.setEnabled(True)
            for name, result in self.plugin.cal_session.results.items():
                self._log("%s: %s" %
                          (name, "PASS" if result.ok else "FAIL") +
                          (("  " + result.message) if result.message else ""))

    def _apply(self):
        if not self.plugin or not self.plugin.cal_session:
            return
        store = self.ac.microscope.cal_store
        applied = []
        for name, result in self.plugin.cal_session.results.items():
            if not result.entries:
                continue
            ans = QMessageBox.question(
                self, "Apply %s" % get_cal(name).title,
                "%s\n\nApply %u value(s) to the calibration store?" %
                (result.message or name, len(result.entries)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                continue
            try:
                self.plugin.cal_session.apply_one(name, store=store)
                applied.append(name)
            except Exception as e:
                traceback.print_exc()
                QMessageBox.warning(self, "Apply failed", str(e))
        self._log("Applied: %s" % (", ".join(applied) or "(none)"))
        if hasattr(self.parent(), "reload_table"):
            self.parent().reload_table()

    def _open_report(self):
        if not self.plugin or not self.plugin.cal_session:
            return
        fn = os.path.join(self.plugin.cal_session.path, "report.pdf")
        if os.path.exists(fn):
            os.system("xdg-open %s &" % fn)
        else:
            QMessageBox.information(self, "Report", "No PDF yet.")


def _status_brush(status):
    if status.startswith("calibrated"):
        return QBrush(QColor(0, 120, 0))
    if status.startswith("disabled"):
        return QBrush(QColor(150, 150, 150))
    return None


def _row_ident(row):
    return (row.key, _scope_tuple(row.scope))


def _format_number(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return "%0.6g" % v
    return str(v)


class CalValueEditor(QWidget):
    """Typed editor for one calibration value"""
    edited = pyqtSignal()

    def __init__(self, param, parent=None):
        super().__init__(parent)
        self.param = param
        self.les = {}
        self.text = None
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        if not param.editable:
            self.text = QTextEdit()
            self.text.setReadOnly(True)
            self.text.setMaximumHeight(120)
            layout.addWidget(self.text)
        elif isinstance(param, cal_params.FieldsParam):
            for name, label, _type in param.fields:
                layout.addWidget(QLabel(label))
                le = QLineEdit()
                le.setMaximumWidth(90)
                le.textEdited.connect(lambda _s: self.edited.emit())
                self.les[name] = le
                layout.addWidget(le)
        else:
            le = QLineEdit()
            le.setMaximumWidth(160)
            le.textEdited.connect(lambda _s: self.edited.emit())
            self.les[None] = le
            layout.addWidget(le)
        if param.unit and param.editable:
            layout.addWidget(QLabel(param.unit))
        if self.text is None:
            layout.addStretch()
        self.setLayout(layout)

    def set_value(self, value):
        if self.text is not None:
            self.text.setPlainText("" if value is None else json.dumps(
                value, indent=2, sort_keys=True))
        elif isinstance(self.param, cal_params.FieldsParam):
            fields = self.param.get_fields(value)
            for name, le in self.les.items():
                le.setText(_format_number(fields.get(name)))
        else:
            self.les[None].setText(_format_number(value))

    def value(self, old_value):
        """Parse the edited value. Raises ValueError"""
        if isinstance(self.param, cal_params.FieldsParam):
            fields = {}
            for name, label, type_ in self.param.fields:
                s = self.les[name].text().strip()
                try:
                    fields[name] = type_(float(s)) if type_ is int else type_(s)
                except ValueError:
                    raise ValueError("%s: can't parse '%s'" % (label, s))
            return self.param.set_fields(old_value, fields)
        s = self.les[None].text().strip()
        try:
            return self.param.parse(s)
        except ValueError as e:
            if not s:
                raise ValueError("%s: value required" % self.param.title)
            raise ValueError("%s: %s" % (self.param.title, e))


class CalibrationTab(ArgusTab):
    """
    Every calibration parameter with its nominal (microscope.j5), calibrated
    (calibration.j5) and effective (in use) value. Select one to edit it
    """
    storeChanged = pyqtSignal()

    COL_PARAM, COL_SCOPE, COL_EFFECTIVE, COL_STATUS, COL_NOMINAL = range(5)

    def _initUI(self):
        self.rows = []
        self.row = None
        self.value_dirty = False
        self.extras_dirty = False
        self.notes_dirty = False
        self.editor = None
        self.extra_cbs = {}

        layout = QVBoxLayout()

        buttons = QHBoxLayout()
        self.run_pb = QPushButton("Run calibration…")
        self.run_pb.clicked.connect(self._run_wizard)
        self.reload_pb = QPushButton("Reload")
        self.reload_pb.setToolTip("Reload calibration files from disk")
        self.reload_pb.clicked.connect(self._reload_files)
        self.import_pb = QPushButton("Import…")
        self.import_pb.clicked.connect(self._import)
        self.export_pb = QPushButton("Export…")
        self.export_pb.clicked.connect(self._export)
        self.folder_pb = QPushButton("Open folder")
        self.folder_pb.clicked.connect(self._open_folder)
        for w in (self.run_pb, self.reload_pb, self.import_pb, self.export_pb,
                  self.folder_pb):
            buttons.addWidget(w)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.paths_l = QLabel("")
        self.paths_l.setWordWrap(True)
        self.paths_l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.paths_l)

        splitter = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(
            ["Parameter", "Applies to", "Effective", "Status", "Nominal"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self.tree)

        splitter.addWidget(self._detail_widget())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 380])
        layout.addWidget(splitter, 1)

        self.setLayout(layout)

    def _detail_widget(self):
        detail = QWidget()
        layout = QVBoxLayout()

        self.title_l = QLabel("Select a parameter")
        font = self.title_l.font()
        font.setBold(True)
        self.title_l.setFont(font)
        layout.addWidget(self.title_l)
        self.help_l = QLabel("")
        self.help_l.setWordWrap(True)
        layout.addWidget(self.help_l)

        form = QFormLayout()
        self.scope_l = QLabel("")
        form.addRow("Applies to", self.scope_l)
        self.status_l = QLabel("")
        form.addRow("Status", self.status_l)
        self.nominal_l = QLabel("")
        self.nominal_l.setToolTip("microscope.j5 / built in value, used "
                                  "when there is no enabled calibration")
        form.addRow("Nominal", self.nominal_l)
        self.effective_l = QLabel("")
        self.effective_l.setToolTip("Value the system is using now")
        form.addRow("Effective", self.effective_l)

        self.editor_holder = QVBoxLayout()
        self.editor_holder.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self.editor_holder)
        form.addRow("Calibrated value", holder)

        self.extras_holder = QVBoxLayout()
        self.extras_holder.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self.extras_holder)
        form.addRow("", holder)

        self.enabled_cb = QCheckBox("Use calibrated value")
        self.enabled_cb.setToolTip(
            "Unchecked: keep the value but use nominal instead")
        self.enabled_cb.clicked.connect(self._enabled_clicked)
        form.addRow("", self.enabled_cb)

        self.notes_le = QLineEdit()
        self.notes_le.textEdited.connect(self._notes_edited)
        form.addRow("Notes", self.notes_le)

        self.stats_l = QLabel("")
        form.addRow("Uncertainty", self.stats_l)
        self.measured_l = QLabel("")
        form.addRow("Measured", self.measured_l)
        self.source_l = QLabel("")
        self.source_l.setWordWrap(True)
        form.addRow("Source", self.source_l)
        self.file_l = QLabel("")
        self.file_l.setWordWrap(True)
        self.file_l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("File", self.file_l)
        layout.addLayout(form)

        self.restart_l = QLabel("")
        self.restart_l.setWordWrap(True)
        self.restart_l.setStyleSheet("color: #b05000")
        layout.addWidget(self.restart_l)

        buttons = QHBoxLayout()
        self.save_pb = QPushButton("Save")
        self.save_pb.clicked.connect(self._save)
        self.discard_pb = QPushButton("Discard edits")
        self.discard_pb.clicked.connect(self._discard)
        self.remove_pb = QPushButton("Remove calibration")
        self.remove_pb.setToolTip(
            "Delete the user value: falls back to factory, then nominal")
        self.remove_pb.clicked.connect(self._remove)
        for w in (self.save_pb, self.discard_pb, self.remove_pb):
            buttons.addWidget(w)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()

        detail.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(detail)
        return scroll

    def _post_ui_init(self):
        # Store can be changed from any thread (ex: scripts)
        self.storeChanged.connect(self.reload_table)
        self._store().subscribe(lambda _store, _keys: self.storeChanged.emit())
        # Effective values depend on objectives (names, scalars, settle)
        self.ac.objectiveChanged.connect(lambda _config: self.reload_table())
        self.reload_table()

    def _store(self):
        return self.ac.microscope.cal_store

    """
    Tree
    """

    def reload_table(self):
        store = self._store()
        self.paths_l.setText(
            "Calibrated values override nominal values from microscope.j5. "
            "Saved to %s" % store.path)
        self.paths_l.setToolTip("Factory calibration: %s" %
                                (store.factory_path or "none"))
        keep = _row_ident(self.row) if self.row else None
        self.rows = cal_params.rows(self.ac.microscope)

        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            select = None
            for group in cal_params.GROUPS:
                group_rows = [r for r in self.rows if r.param.group == group]
                if not group_rows:
                    continue
                group_item = QTreeWidgetItem([group])
                font = group_item.font(0)
                font.setBold(True)
                group_item.setFont(0, font)
                self.tree.addTopLevelItem(group_item)
                params = []
                for row in group_rows:
                    if row.param not in params:
                        params.append(row.param)
                for param in params:
                    param_rows = [r for r in group_rows if r.param is param]
                    if len(param_rows) == 1:
                        item = self._row_item(param_rows[0], param.title,
                                              param_rows[0].scope_label())
                        group_item.addChild(item)
                    else:
                        per = param.scope_field or "scope"
                        item = QTreeWidgetItem([param.title, "per " + per])
                        group_item.addChild(item)
                        for row in param_rows:
                            item.addChild(
                                self._row_item(row, row.scope_label(), ""))
            self.tree.expandAll()
            for i in range(self.tree.columnCount()):
                self.tree.resizeColumnToContents(i)

            if keep:
                it = self._find_item(keep)
                if it is not None:
                    select = it
            if select is not None:
                self.tree.setCurrentItem(select)
        finally:
            self.tree.blockSignals(False)
        self._selection_changed()

    def _row_item(self, row, label, scope):
        param = row.param
        effective = param.format_unit(row.effective())
        for name, label_, _choices in param.extras:
            v = row.extra(name)
            if v is not None:
                effective += ", %s %s" % (label_.lower(),
                                          "%+d" % v if v else "off")
        status = row.status()
        item = QTreeWidgetItem([
            label, scope, effective, status,
            param.format_unit(row.nominal())
        ])
        item.setData(0, Qt.UserRole, self.rows.index(row))
        brush = _status_brush(status)
        if brush is not None:
            item.setForeground(self.COL_STATUS, brush)
        if row.user_entry is not None and row.user_entry.needs_restart:
            item.setToolTip(self.COL_STATUS, "Takes effect after restart")
        return item

    def _find_item(self, ident):
        stack = [
            self.tree.topLevelItem(i)
            for i in range(self.tree.topLevelItemCount())
        ]
        while stack:
            item = stack.pop()
            index = item.data(0, Qt.UserRole)
            if index is not None and _row_ident(self.rows[index]) == ident:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def _selected_row(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        index = items[0].data(0, Qt.UserRole)
        if index is None:
            return None
        return self.rows[index]

    def _selection_changed(self):
        self._show_row(self._selected_row())

    """
    Detail panel
    """

    def _dirty(self):
        return self.value_dirty or self.extras_dirty or self.notes_dirty

    def _clear_dirty(self):
        self.value_dirty = False
        self.extras_dirty = False
        self.notes_dirty = False

    def _show_row(self, row):
        """Fill the detail panel. Keeps in progress edits to the same row"""
        same = (row is not None and self.row is not None
                and _row_ident(row) == _row_ident(self.row))
        keep_edits = same and self._dirty()
        self.row = row

        widgets = (self.save_pb, self.discard_pb, self.remove_pb,
                   self.enabled_cb, self.notes_le)
        if row is None:
            self.title_l.setText("Select a parameter")
            for label in (self.help_l, self.scope_l, self.status_l,
                          self.nominal_l, self.effective_l, self.stats_l,
                          self.measured_l, self.source_l, self.file_l,
                          self.restart_l):
                label.setText("")
            self._set_editor(None)
            for w in widgets:
                w.setEnabled(False)
            return

        param = row.param
        entry = row.entry
        self.title_l.setText(param.title)
        self.help_l.setText(param.help or param.key)
        self.help_l.setToolTip(param.key)
        self.scope_l.setText(row.scope_label())
        self.status_l.setText(row.status())
        self.nominal_l.setText(param.format_unit(row.nominal()) or "none")
        self.effective_l.setText(param.format_unit(row.effective()) or "none")

        if entry is None:
            self.stats_l.setText("")
            self.measured_l.setText("")
            self.source_l.setText("")
            self.file_l.setText("")
        else:
            stats = []
            if entry.std is not None:
                stats.append("± %0.4g%s" % (entry.std, (" " + entry.unit)
                                            if entry.unit else ""))
            if entry.n is not None:
                stats.append("n = %u" % entry.n)
            self.stats_l.setText(", ".join(stats))
            measured = (entry.measured_at or "?")[:19].replace("T", " ")
            self.measured_l.setText(measured)
            self.measured_l.setToolTip("pyuscope %s" %
                                       (entry.pyuscope_version or "?"))
            source = entry.source or ""
            if row.user_entry is None:
                source = "factory calibration.j5" + (
                    ": " + source if source else "")
            self.source_l.setText(source)
            self.file_l.setText(entry.file or entry.extra.get(
                "dataset_file", ""))
        restart = []
        if param.needs_restart:
            restart.append("Changes take effect after restarting Argus.")
        if param.key == "optics.z_offset":
            restart.append("Enabled: Z moves by the offset difference "
                           "whenever the objective changes.")
        self.restart_l.setText(" ".join(restart))

        self.enabled_cb.setEnabled(entry is not None)
        self.enabled_cb.setChecked(entry is not None and entry.enabled)
        self.remove_pb.setEnabled(row.user_entry is not None)

        if not keep_edits:
            self._set_editor(row)
            self.notes_le.setText(entry.notes if entry else "")
            self._clear_dirty()
        editable = param.editable
        self.notes_le.setEnabled(entry is not None or editable)
        self._update_buttons()

    def _set_editor(self, row):
        if self.editor is not None:
            self.editor_holder.removeWidget(self.editor)
            # deleteLater() alone leaves it painted until the event loop runs
            self.editor.setParent(None)
            self.editor.deleteLater()
            self.editor = None
        while self.extras_holder.count():
            w = self.extras_holder.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.extra_cbs = {}
        if row is None:
            return

        param = row.param
        self.editor = CalValueEditor(param)
        value = row.calibrated()
        # Nothing stored yet: start from what is in use
        if value is None and param.editable:
            value = row.nominal()
        self.editor.set_value(value)
        self.editor.edited.connect(self._value_edited)
        self.editor_holder.addWidget(self.editor)

        for name, label, choices in param.extras:
            w = QWidget()
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(QLabel(label))
            cb = QComboBox()
            for value, text in choices:
                cb.addItem(text, value)
            current = row.extra(name)
            cb.setCurrentIndex(cb.findData(current) if current is not None
                               else -1)
            cb.activated.connect(self._extras_edited)
            layout.addWidget(cb)
            layout.addStretch()
            w.setLayout(layout)
            self.extras_holder.addWidget(w)
            self.extra_cbs[name] = cb

    def _value_edited(self):
        self.value_dirty = True
        self._update_buttons()

    def _extras_edited(self, _index=None):
        self.extras_dirty = True
        self._update_buttons()

    def _notes_edited(self, _text=None):
        self.notes_dirty = True
        self._update_buttons()

    def _update_buttons(self):
        dirty = self._dirty()
        self.save_pb.setEnabled(dirty)
        self.discard_pb.setEnabled(dirty)

    def _save(self):
        row = self.row
        if row is None:
            return
        try:
            value = None
            if self.value_dirty and row.param.editable:
                value = self.editor.value(row.calibrated())
            extras = {}
            if self.extras_dirty:
                for name, cb in self.extra_cbs.items():
                    if cb.currentIndex() >= 0:
                        extras[name] = cb.currentData()
            notes = self.notes_le.text() if self.notes_dirty else None
        except ValueError as e:
            QMessageBox.warning(self, "Calibration", str(e))
            return
        if row.entry is None and not row.param.editable:
            return
        try:
            cal_params.set_value(self._store(),
                                 row,
                                 value=value,
                                 extras=extras,
                                 notes=notes)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(self, "Calibration", "Save failed: %s" % e)
            return
        self.ac.log("Calibration: set %s (%s)" %
                    (row.param.title, row.scope_label()))
        if row.param.needs_restart:
            self.ac.log("Calibration: restart Argus to apply %s" %
                        row.param.title)
        self._clear_dirty()
        # Store notification reloads the tree
        self.reload_table()

    def _discard(self):
        self._clear_dirty()
        self._show_row(self.row)

    def _enabled_clicked(self, checked):
        row = self.row
        if row is None or row.entry is None:
            return
        cal_params.set_enabled(self._store(), row, checked)
        self.ac.log("Calibration: %s %s (%s)" %
                    ("enabled" if checked else "disabled", row.param.title,
                     row.scope_label()))

    def _remove(self):
        row = self.row
        if row is None or row.user_entry is None:
            return
        fallback = "factory value" if row.factory_entry else "nominal value"
        ans = QMessageBox.question(
            self, "Remove calibration",
            "Remove the calibrated %s (%s)?\n\nThe %s will be used instead." %
            (row.param.title, row.scope_label(), fallback),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self._clear_dirty()
        cal_params.remove_user(self._store(), row)
        self.ac.log("Calibration: removed %s (%s)" %
                    (row.param.title, row.scope_label()))

    """
    Files
    """

    def _run_wizard(self):
        dlg = CalibrationWizard(self.ac, parent=self)
        dlg.exec_()
        self.reload_table()

    def _reload_files(self):
        store = self._store()
        store.load()
        store._notify([e.key for e in store.entries()])

    def _open_folder(self):
        d = self._store().dir
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        subprocess.Popen(["xdg-open", d])

    def _export(self):
        fn, _f = QFileDialog.getSaveFileName(self, "Export calibration",
                                             "calibration.j5",
                                             "JSON (*.j5 *.json)")
        if not fn:
            return
        writej(fn, readj(self._store().path) if os.path.exists(self._store().path)
               else {"schema_version": 1, "entries": []})

    def _import(self):
        fn, _f = QFileDialog.getOpenFileName(self, "Import calibration", "",
                                             "JSON (*.j5 *.json)")
        if not fn:
            return
        j = readj(fn)
        store = self._store()
        for ej in j.get("entries") or []:
            store.set(CalEntry.from_j(ej), notify=False)
        store.save()
        store._notify([e.get("key") for e in j.get("entries") or []])


class OpticsTab(ArgusTab):
    """
    Magnification scalar and the resulting per objective image scale
    """
    storeChanged = pyqtSignal()

    COLUMNS = ("Objective", "Magnification", "NA", "um / pixel",
               "Field of view (mm)", "Settle (s)", "Parfocal Z (mm)")

    def _initUI(self):
        layout = QVBoxLayout()

        def magnification_gb():
            layout = QGridLayout()
            layout.addWidget(QLabel("Global magnification scalar"), 0, 0)
            self.global_scalar_le = QLineEdit()
            self.global_scalar_le.setPlaceholderText("1.0")
            self.global_scalar_le.setMaximumWidth(120)
            self.global_scalar_le.returnPressed.connect(
                self.global_scalar_apply)
            layout.addWidget(self.global_scalar_le, 0, 1)
            apply_pb = QPushButton("Apply")
            apply_pb.clicked.connect(self.global_scalar_apply)
            layout.addWidget(apply_pb, 0, 2)
            reset_pb = QPushButton("Reset")
            reset_pb.setToolTip("No correction (1.0)")
            reset_pb.clicked.connect(self.global_scalar_reset)
            layout.addWidget(reset_pb, 0, 3)
            layout.setColumnStretch(4, 1)
            help_l = QLabel(
                "Multiplies every objective's magnification. Use for a "
                "barlow / relay lens change or to correct a systematic scale "
                "error. Saved per microscope serial number. Blank: 1.0")
            help_l.setWordWrap(True)
            layout.addWidget(help_l, 1, 0, 1, 5)
            gb = QGroupBox("Magnification")
            gb.setLayout(layout)
            return gb

        def scale_gb():
            layout = QFormLayout()
            self.um_raw_l = QLabel("")
            layout.addRow("Raw sensor pixel at 1x", self.um_raw_l)
            self.image_scalar_l = QLabel("")
            layout.addRow("Image scalar (microscope.j5)", self.image_scalar_l)
            self.final_wh_l = QLabel("")
            layout.addRow("Final image size", self.final_wh_l)
            gb = QGroupBox("Image scale")
            gb.setLayout(layout)
            return gb

        def objectives_gb():
            layout = QVBoxLayout()
            self.objectives_table = QTableWidget(0, len(self.COLUMNS))
            self.objectives_table.setHorizontalHeaderLabels(self.COLUMNS)
            self.objectives_table.setEditTriggers(
                QAbstractItemView.NoEditTriggers)
            self.objectives_table.verticalHeader().setVisible(False)
            self.objectives_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeToContents)
            layout.addWidget(self.objectives_table)
            note = QLabel("um / pixel = raw sensor pixel at 1x / (objective "
                          "magnification × global scalar × image scalar). "
                          "Edit calibrated values on the Calibration page.")
            note.setWordWrap(True)
            layout.addWidget(note)
            gb = QGroupBox("Objectives (effective, active in bold)")
            gb.setLayout(layout)
            return gb

        layout.addWidget(magnification_gb())
        layout.addWidget(scale_gb())
        layout.addWidget(objectives_gb())
        self.setLayout(layout)

    def _post_ui_init(self):
        self.storeChanged.connect(self.refresh)
        self.ac.microscope.cal_store.subscribe(
            lambda _store, _keys: self.storeChanged.emit())
        self.ac.objectiveChanged.connect(lambda _config: self.refresh())
        self.refresh()

    def _state(self):
        return self.ac.microscope.objective_state

    def global_scalar_apply(self):
        s = str(self.global_scalar_le.text()).strip()
        if not s:
            self._state().set_global_scalar(None)
            return
        try:
            val = float(s)
        except ValueError:
            self.ac.log("Failed to parse global magnification scalar")
            return
        if val <= 0:
            self.ac.log("Global magnification scalar must be > 0")
            return
        self._state().set_global_scalar(val)

    def global_scalar_reset(self):
        self.global_scalar_le.setText("")
        self._state().set_global_scalar(None)

    def refresh(self):
        state = self._state()
        usc = self.ac.usc
        store = self.ac.microscope.cal_store

        if not self.global_scalar_le.hasFocus():
            v = state.global_scalar
            self.global_scalar_le.setText("" if v is None else "%g" % v)

        um_raw = usc.optics.um_per_pixel_raw_1x()
        if store.get("optics.um_per_pixel_raw_1x") is not None:
            where = "calibrated"
        else:
            where = "nominal, microscope.j5"
        self.um_raw_l.setText(
            "%s um/pix (%s)" % (_format_number(um_raw), where) if um_raw else
            "unknown: objectives must specify x_view")
        self.image_scalar_l.setText("%g" % usc.imager.scalar())
        try:
            w, h = usc.imager.final_wh()
            self.final_wh_l.setText("%u x %u pixels (after crop and scalar)" %
                                    (w, h))
        except Exception as e:
            self.final_wh_l.setText(str(e))

        global_scalar = state.global_scalar or 1.0
        objectives = state.full_config()
        active = state.name()
        table = self.objectives_table
        table.setRowCount(len(objectives))
        for i, (name, obj) in enumerate(objectives.items()):
            mag = obj.get("magnification")
            if mag is None:
                mag_s = ""
            elif global_scalar != 1.0:
                mag_s = "%gX × %g = %gX" % (mag, global_scalar,
                                           mag * global_scalar)
            else:
                mag_s = "%gX" % mag
            z = store.get("optics.z_offset", {"objective": name},
                          include_disabled=True)
            if z is None:
                z_s = ""
            else:
                z_s = "%0.4f%s" % (z.value, "" if z.enabled else " (off)")
            cells = (
                name,
                mag_s,
                _format_number(obj.get("na")),
                "%0.4f" % obj["um_per_pixel"],
                "%0.3f x %0.3f" % (obj["x_view"], obj.get("y_view") or 0.0),
                "%0.3f" % obj["tsettle_motion"],
                z_s,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if name == active:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(i, col, item)
