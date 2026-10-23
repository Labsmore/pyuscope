"""Calibration settings tab and collect/analyze wizard."""

import os
import traceback

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from uscope.scripting import ScriptingPlugin
from uscope.gui.scripting import QtScriptHost, exec_prompt
from uscope.gui.widgets import ArgusTab
from uscope.cal.base import Quality
from uscope.cal.registry import all_cals, get as get_cal
from uscope.cal.session import CalSession
from uscope.cal.store import CalEntry
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


class CalibrationTab(ArgusTab):
    def _initUI(self):
        layout = QVBoxLayout()
        layout.addWidget(
            QLabel("Measured calibration overlays microscope.j5 defaults. "
                   "Every entry is optional and can be disabled."))

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Key", "Scope", "Value", "±", "Enabled", "When", "Source"
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.run_pb = QPushButton("Run calibration…")
        self.run_pb.clicked.connect(self._run_wizard)
        self.reload_pb = QPushButton("Reload")
        self.reload_pb.clicked.connect(self.reload_table)
        self.edit_pb = QPushButton("Edit value")
        self.edit_pb.clicked.connect(self._edit)
        self.revert_pb = QPushButton("Revert to nominal")
        self.revert_pb.clicked.connect(self._revert)
        self.export_pb = QPushButton("Export…")
        self.export_pb.clicked.connect(self._export)
        self.import_pb = QPushButton("Import…")
        self.import_pb.clicked.connect(self._import)
        self.effective_pb = QPushButton("Show effective values")
        self.effective_pb.clicked.connect(self._effective)
        self.parfocal_pb = QPushButton("Go to parfocal Z")
        self.parfocal_pb.clicked.connect(self._parfocal)
        self.software_gain_pb = QPushButton("Software gain…")
        self.software_gain_pb.clicked.connect(self._software_gain)
        for w in (self.run_pb, self.reload_pb, self.edit_pb, self.revert_pb,
                  self.export_pb, self.import_pb, self.effective_pb,
                  self.parfocal_pb, self.software_gain_pb):
            row.addWidget(w)
        layout.addLayout(row)

        self.effective_te = QTextEdit()
        self.effective_te.setReadOnly(True)
        self.effective_te.setMaximumHeight(140)
        layout.addWidget(self.effective_te)
        self.setLayout(layout)

    def _post_ui_init(self):
        self.reload_table()

    def _store(self):
        return self.ac.microscope.cal_store

    def reload_table(self):
        store = self._store()
        if store is None:
            return
        self.table.blockSignals(True)
        try:
            entries = store.entries(include_disabled=True)
            self.table.setRowCount(len(entries))
            self._rows = entries
            for i, e in enumerate(entries):
                self.table.setItem(i, 0, QTableWidgetItem(e.key))
                self.table.setItem(i, 1, QTableWidgetItem(e.scope_label()))
                val = e.value
                if isinstance(val, float):
                    val_s = "%0.6g" % val
                else:
                    val_s = str(val)
                self.table.setItem(i, 2, QTableWidgetItem(val_s))
                self.table.setItem(
                    i, 3,
                    QTableWidgetItem("" if e.std is None else "%0.4g" % e.std))
                enabled = QTableWidgetItem()
                enabled.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                enabled.setCheckState(
                    Qt.Checked if e.enabled else Qt.Unchecked)
                self.table.setItem(i, 4, enabled)
                self.table.setItem(i, 5, QTableWidgetItem(e.measured_at or ""))
                self.table.setItem(i, 6, QTableWidgetItem(e.source or ""))
        finally:
            self.table.blockSignals(False)

    def _item_changed(self, item):
        if item.column() != 4:
            return
        row = item.row()
        if row >= len(getattr(self, "_rows", [])):
            return
        e = self._rows[row]
        enabled = item.checkState() == Qt.Checked
        if bool(e.enabled) == enabled:
            return
        self._store().set_enabled(e.key, e.scope, enabled)

    def _selected_entry(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(getattr(self, "_rows", [])):
            return None
        return self._rows[row]

    def _run_wizard(self):
        dlg = CalibrationWizard(self.ac, parent=self)
        dlg.exec_()
        self.reload_table()

    def _edit(self):
        e = self._selected_entry()
        if e is None:
            return
        if not isinstance(e.value, (int, float, str)):
            QMessageBox.information(self, "Edit",
                                    "This value is structured; edit the file.")
            return
        text, ok = _ask_text(self, "Edit %s" % e.key, str(e.value))
        if not ok:
            return
        try:
            if isinstance(e.value, float):
                e.value = float(text)
            elif isinstance(e.value, int):
                e.value = int(text)
            else:
                e.value = text
        except ValueError:
            QMessageBox.warning(self, "Edit", "Could not parse value")
            return
        self._store().set(e)
        self.reload_table()

    def _revert(self):
        e = self._selected_entry()
        if e is None:
            return
        self._store().delete(e.key, e.scope)
        self.reload_table()

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
        self.reload_table()

    def _effective(self):
        usc = self.ac.usc
        lines = ["Effective values (store overlay + nominal):", ""]
        try:
            lines.append("um_per_pixel_raw_1x: %s" %
                         usc.optics.um_per_pixel_raw_1x())
        except Exception as e:
            lines.append("um_per_pixel_raw_1x: (%s)" % e)
        try:
            lines.append("backlash: %s" % usc.motion.backlash())
        except Exception as e:
            lines.append("backlash: (%s)" % e)
        try:
            lines.append("crop: %s" % usc.imager.crop_tblr())
        except Exception as e:
            lines.append("crop: (%s)" % e)
        try:
            lines.append("has illumination: %s" % usc.imager.has_ff_cal())
        except Exception as e:
            lines.append("has illumination: (%s)" % e)
        try:
            lines.append("software white balance: %s" %
                         usc.imager.white_balance_gains())
            lines.append("software gain: %s" % usc.imager.software_gain())
        except Exception as e:
            lines.append("software white balance / gain: (%s)" % e)
        store = self._store()
        if store:
            e = store.get("imager.response")
            lines.append("response: %s" %
                         (e.value if e else "(nominal / none)"))
        self.effective_te.setText("\n".join(lines))

    def _parfocal(self):
        try:
            self.ac.mainTab.objective_widget.go_parfocal_z()
        except Exception as e:
            QMessageBox.warning(self, "Parfocal", str(e))

    def _software_gain(self):
        from PyQt5.QtWidgets import QInputDialog
        gain, ok = QInputDialog.getDouble(
            self, "Software gain",
            "Gain applied to snapshots / scans (1.0: off)",
            self.ac.usc.imager.software_gain(), 0.01, 100.0, 2)
        if not ok:
            return
        self._store().set(CalEntry(key="imager.software_gain",
                                   value=float(gain)))
        self.reload_table()


def _ask_text(parent, title, default):
    from PyQt5.QtWidgets import QInputDialog
    return QInputDialog.getText(parent, title, "Value", QLineEdit.Normal,
                                default)
