"""
Objective management: this microscope's objective lineup and the objective library
Both are stored per install (see BaseConfig.user_objectives_fn)
"""

import copy
import json

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor, QFontDatabase
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from uscope.config import (objective_db_find, objective_key,
                           objectives_export_json, ordered_objective_entry,
                           parse_db_find, validate_objective_entry)
from uscope.gui.widgets import ArgusTab
from uscope.objective import objective_name

# Lineup item key remembering the applied name (to move calibration on rename)
ORIG_NAME = "_orig_name"


def num_str(v):
    if v is None or v == "":
        return ""
    try:
        return "%g" % float(v)
    except (TypeError, ValueError):
        return str(v)


class ObjectiveEditDialog(QDialog):
    """
    Add / edit an objective
    library: objective library entry (vendor and model required)
    Otherwise an objective only in this lineup (has a name, vendor / model optional)
    """

    # (key, label, placeholder)
    FIELDS = (
        ("vendor", "Vendor", "ex: Mitutoyo"),
        ("model", "Model", "ex: 378-803-3"),
        ("series", "Series", "ex: M Plan Apo"),
        ("magnification", "Magnification *", "ex: 10"),
        ("na", "Numerical aperture (NA) *", "ex: 0.28"),
        ("wd", "Working distance (mm)", ""),
        ("fl", "Focal length (mm)", ""),
        ("thread", "Thread", ""),
        ("infinity_tube_length", "Infinity tube length (mm)", "ex: 200"),
        ("notes", "Notes", ""),
    )

    def __init__(self,
                 db,
                 entry=None,
                 library=True,
                 lock_key=False,
                 title=None,
                 help_text=None,
                 parent=None):
        super().__init__(parent)
        self.db = db
        self.library = library
        self.entry = None
        self.setWindowTitle(title or "Objective")
        entry = entry or {}
        self.orig = dict(entry)

        layout = QVBoxLayout()
        if help_text:
            help_l = QLabel(help_text)
            help_l.setWordWrap(True)
            layout.addWidget(help_l)
        form = QFormLayout()
        self.widgets = {}
        if not library:
            self.name_le = QLineEdit(str(entry.get("name") or ""))
            self.name_le.setPlaceholderText("ex: Zoom 2X")
            form.addRow("Name *", self.name_le)
        for k, label, placeholder in self.FIELDS:
            if k == "thread":
                w = QComboBox()
                w.setEditable(True)
                threads = sorted({
                    str(e["thread"])
                    for e in db.db.values() if e.get("thread")
                })
                w.addItems([""] + threads)
                w.setCurrentText(str(entry.get(k) or ""))
            else:
                v = entry.get(k)
                w = QLineEdit(
                    num_str(v) if isinstance(v, (int, float)) else str(
                        v or ""))
                w.setPlaceholderText(placeholder)
            if lock_key and k in ("vendor", "model"):
                w.setEnabled(False)
            self.widgets[k] = w
            if not library and k == "vendor":
                label = "Vendor (optional)"
            if not library and k == "model":
                label = "Model (optional)"
            form.addRow(label, w)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(450, 0)

    def value(self, k):
        w = self.widgets[k]
        if isinstance(w, QComboBox):
            return w.currentText().strip()
        return w.text().strip()

    def accept(self):
        shown = {k for k, _label, _ph in self.FIELDS} | {"name", ORIG_NAME}
        # Keep fields the dialog doesn't show (ex: um_per_pixel)
        entry = {k: v for k, v in self.orig.items() if k not in shown}
        entry.update({k: self.value(k) for k, _label, _ph in self.FIELDS})
        if not self.library:
            entry["name"] = self.name_le.text().strip()
            if not entry["name"]:
                QMessageBox.warning(self, "Objective", "Name is required")
                return
        try:
            self.entry = validate_objective_entry(entry,
                                                  catalog=self.library)
        except ValueError as e:
            QMessageBox.warning(self, "Objective", str(e))
            return
        super().accept()


class ObjectiveExportDialog(QDialog):
    """Show objective JSON to copy and paste or save to a file"""
    def __init__(self, sources, parent=None):
        """sources: list of (label, text)"""
        super().__init__(parent)
        self.setWindowTitle("Export objectives")
        self.sources = sources
        layout = QVBoxLayout()
        help_l = QLabel(
            "Library entries use the configs/objectives.j5 format. "
            "The lineup uses the microscope.j5 system format. Copy and paste "
            "or save the text to share it, for example to get an objective "
            "added to pyuscope.")
        help_l.setWordWrap(True)
        layout.addWidget(help_l)
        self.source_cb = QComboBox()
        for label, _text in sources:
            self.source_cb.addItem(label)
        self.source_cb.currentIndexChanged.connect(self.source_changed)
        layout.addWidget(self.source_cb)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.text)
        buttons = QHBoxLayout()
        copy_pb = QPushButton("Copy to clipboard")
        copy_pb.clicked.connect(self.copy)
        buttons.addWidget(copy_pb)
        save_pb = QPushButton("Save to file…")
        save_pb.clicked.connect(self.save)
        buttons.addWidget(save_pb)
        buttons.addStretch()
        close_pb = QPushButton("Close")
        close_pb.clicked.connect(self.accept)
        buttons.addWidget(close_pb)
        layout.addLayout(buttons)
        self.setLayout(layout)
        self.resize(650, 550)
        self.source_changed()

    def source_changed(self, _index=None):
        i = self.source_cb.currentIndex()
        self.text.setPlainText(self.sources[i][1] if i >= 0 else "")

    def copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def save(self):
        fn, _filter = QFileDialog.getSaveFileName(
            self, "Save objectives", "objectives.j5",
            "JSON5 (*.j5 *.json5 *.json);;All files (*)")
        if not fn:
            return
        try:
            with open(fn, "w") as f:
                f.write(self.text.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "Export objectives",
                                "Failed to save %s: %s" % (fn, e))


class ObjectivesTab(ArgusTab):
    """
    Pick which objectives this microscope has (replacing the microscope.j5
    defaults) and manage the objective library
    """

    LINEUP_COLUMNS = ("Name", "Magnification", "NA", "Source")
    LIBRARY_COLUMNS = ("Objective", "Magnification", "NA", "WD (mm)",
                       "Source")

    def _initUI(self):
        self.draft = []
        self.dirty = False
        layout = QVBoxLayout()
        self.status_l = QLabel("")
        self.status_l.setWordWrap(True)
        layout.addWidget(self.status_l)

        def lineup_gb():
            layout = QVBoxLayout()
            self.lineup_table = QTableWidget(0, len(self.LINEUP_COLUMNS))
            self.lineup_table.setHorizontalHeaderLabels(self.LINEUP_COLUMNS)
            self.lineup_table.setEditTriggers(
                QAbstractItemView.NoEditTriggers)
            self.lineup_table.setSelectionBehavior(
                QAbstractItemView.SelectRows)
            self.lineup_table.setSelectionMode(
                QAbstractItemView.SingleSelection)
            self.lineup_table.verticalHeader().setVisible(False)
            self.lineup_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeToContents)
            # Extra width goes to the name
            self.lineup_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.Stretch)
            self.lineup_table.setSizePolicy(QSizePolicy.Expanding,
                                            QSizePolicy.Expanding)
            self.lineup_table.itemDoubleClicked.connect(
                lambda _item: self.lineup_edit())
            layout.addWidget(self.lineup_table, 1)

            row = QHBoxLayout()
            for label, cb, tip in (
                ("Up", lambda: self.lineup_move(-1), "Move earlier. The "
                 "first objective is the default"),
                ("Down", lambda: self.lineup_move(1), "Move later"),
                ("Remove", self.lineup_remove, "Remove from this lineup"),
                ("Rename…", self.lineup_rename,
                 "Change the name shown in the objective selector"),
                ("Edit…", self.lineup_edit, "Edit the objective's values"),
            ):
                pb = QPushButton(label)
                pb.setToolTip(tip)
                pb.clicked.connect(cb)
                row.addWidget(pb)
            row.addStretch()
            layout.addLayout(row)
            row = QHBoxLayout()
            add_inline_pb = QPushButton("Add objective without vendor/model…")
            add_inline_pb.setToolTip(
                "Ex: a zoom lens setting. Lives only in this lineup")
            add_inline_pb.clicked.connect(self.lineup_add_inline)
            row.addWidget(add_inline_pb)
            row.addStretch()
            layout.addLayout(row)

            row = QHBoxLayout()
            self.apply_pb = QPushButton("Apply")
            self.apply_pb.setToolTip("Save this lineup and use it now")
            self.apply_pb.clicked.connect(self.lineup_apply)
            row.addWidget(self.apply_pb)
            self.discard_pb = QPushButton("Discard changes")
            self.discard_pb.clicked.connect(self.load_draft)
            row.addWidget(self.discard_pb)
            row.addStretch()
            self.reset_pb = QPushButton("Reset to microscope defaults")
            self.reset_pb.setToolTip(
                "Forget your lineup and use the objectives in microscope.j5")
            self.reset_pb.clicked.connect(self.lineup_reset)
            row.addWidget(self.reset_pb)
            layout.addLayout(row)

            gb = QGroupBox("This microscope's objectives")
            gb.setLayout(layout)
            return gb

        def library_gb():
            layout = QVBoxLayout()
            self.filter_le = QLineEdit()
            self.filter_le.setPlaceholderText(
                "Filter by vendor, model or series")
            self.filter_le.textChanged.connect(
                lambda _text: self.refresh_library())
            layout.addWidget(self.filter_le)
            self.library_tree = QTreeWidget()
            self.library_tree.setHeaderLabels(self.LIBRARY_COLUMNS)
            self.library_tree.header().setSectionResizeMode(
                QHeaderView.ResizeToContents)
            self.library_tree.header().setSectionResizeMode(
                0, QHeaderView.Stretch)
            self.library_tree.header().setStretchLastSection(False)
            self.library_tree.setSizePolicy(QSizePolicy.Expanding,
                                            QSizePolicy.Expanding)
            self.library_tree.itemDoubleClicked.connect(
                lambda item, _col: self.library_add_to_lineup())
            layout.addWidget(self.library_tree, 1)

            row = QHBoxLayout()
            add_pb = QPushButton("Add to lineup")
            add_pb.setToolTip("Add the selected objective to this "
                              "microscope's objectives (or double click)")
            add_pb.clicked.connect(self.library_add_to_lineup)
            row.addWidget(add_pb)
            new_pb = QPushButton("New objective…")
            new_pb.setToolTip("Add an objective that isn't in the library")
            new_pb.clicked.connect(self.library_new)
            row.addWidget(new_pb)
            edit_pb = QPushButton("Edit…")
            edit_pb.setToolTip(
                "Edit your objective, or override a built-in one's values")
            edit_pb.clicked.connect(self.library_edit)
            row.addWidget(edit_pb)
            self.delete_pb = QPushButton("Delete")
            self.delete_pb.setToolTip(
                "Delete your objective (or your override of a built-in one)")
            self.delete_pb.clicked.connect(self.library_delete)
            row.addWidget(self.delete_pb)
            row.addStretch()
            export_pb = QPushButton("Export…")
            export_pb.setToolTip("Show objective JSON to copy or save")
            export_pb.clicked.connect(self.export)
            row.addWidget(export_pb)
            layout.addLayout(row)

            gb = QGroupBox("Objective library")
            gb.setLayout(layout)
            return gb

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(lineup_gb())
        splitter.addWidget(library_gb())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([500, 550])
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Tables get all extra space, not the labels
        layout.addWidget(splitter, 1)
        note = QLabel(
            "Your objectives and lineups are saved per install and take "
            "precedence over the shipped configuration. Calibration is "
            "stored by objective name.")
        note.setWordWrap(True)
        for label in (self.status_l, note):
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(note)
        self.setLayout(layout)

    def _post_ui_init(self):
        self.load_draft()
        self.refresh_library()

    """
    Helpers
    """

    def db(self):
        return self.ac.microscope.usc.bc.objective_db

    def usc(self):
        return self.ac.microscope.usc

    def lineup_name(self):
        return self.usc().objective_lineup_name()

    def resolve(self, item):
        """(resolved objective, error string)"""
        try:
            return self.db().resolve(
                {k: v
                 for k, v in item.items() if k != ORIG_NAME}), None
        except Exception as e:
            return None, str(e)

    def item_name(self, item, i):
        resolved, _err = self.resolve(item)
        if resolved is None and "db_find" in item and "name" not in item:
            try:
                fields = parse_db_find(item["db_find"])
                return "%s %s" % (fields["vendor"], fields["model"])
            except ValueError:
                pass
        return objective_name(resolved or item, i)

    def names(self, items=None):
        items = self.draft if items is None else items
        return [self.item_name(item, i) for i, item in enumerate(items)]

    def source_label(self, item):
        if "db_find" not in item:
            return "Lineup only"
        try:
            fields = parse_db_find(item["db_find"])
        except ValueError:
            return "Invalid"
        vendor, model = fields["vendor"], fields["model"]
        db = self.db()
        if db.is_user(vendor, model):
            if db.is_shipped(vendor, model):
                return "Your library (overrides built-in)"
            return "Your library"
        if db.is_shipped(vendor, model):
            return "Built-in library"
        return "Missing from library"

    def item_keys(self, items):
        """(vendor, model) keys of library objectives used by items"""
        ret = set()
        for item in items:
            try:
                fields = parse_db_find(item["db_find"])
            except (KeyError, ValueError):
                continue
            ret.add(objective_key(fields["vendor"], fields["model"]))
        return ret

    def selected_row(self):
        rows = self.lineup_table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def set_dirty(self, dirty=True):
        self.dirty = dirty
        self.refresh_lineup()

    """
    Lineup
    """

    def load_draft(self):
        usc = self.usc()
        lineup = usc.objective_lineup()
        if lineup:
            self.draft = lineup
        else:
            self.draft = copy.deepcopy(usc.default_objectives())
        for i, name in enumerate(self.names()):
            self.draft[i][ORIG_NAME] = name
        self.set_dirty(False)

    def refresh_lineup(self, select=None):
        if select is None:
            select = self.selected_row()
        table = self.lineup_table
        table.setRowCount(0)
        for i, item in enumerate(self.draft):
            resolved, err = self.resolve(item)
            shown = resolved or item
            table.insertRow(i)
            cells = (self.item_name(item, i),
                     num_str(shown.get("magnification")),
                     num_str(shown.get("na")), self.source_label(item))
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if err:
                    cell.setToolTip(err)
                    cell.setForeground(QBrush(QColor("red")))
                table.setItem(i, col, cell)
        if select is not None and 0 <= select < len(self.draft):
            table.selectRow(select)
        self.refresh_status()

    def refresh_status(self):
        objectives = self.ac.microscope.get_objectives()
        db = self.db()
        parts = []
        if objectives.lineup_error:
            parts.append(
                "Your lineup couldn't be used (%s). Using the microscope "
                "defaults." % objectives.lineup_error)
        elif objectives.uses_lineup():
            parts.append("Using your objective lineup for %s." %
                         self.lineup_name())
            saved = self.usc().objective_lineup() or []
            skipped = len(saved) - len(objectives.names())
            if skipped > 0:
                parts.append(
                    "%u saved objective(s) can't be found in the library and "
                    "are skipped (red)." % skipped)
        else:
            parts.append(
                "Using the microscope defaults (microscope.j5). Change the "
                "list and Apply to use your own lineup.")
        if self.dirty:
            parts.append("Unapplied changes.")
        for problem in db.user_problems:
            parts.append("Warning: " + problem)
        self.status_l.setText(" ".join(parts))
        self.apply_pb.setEnabled(self.dirty)
        self.discard_pb.setEnabled(self.dirty)
        self.reset_pb.setEnabled(
            bool(self.usc().objective_lineup()) or self.dirty)

    def check_name_free(self, name, ignore_row=None):
        names = self.names()
        for i, other in enumerate(names):
            if i != ignore_row and other == name:
                QMessageBox.warning(
                    self, "Objectives",
                    "There is already an objective named '%s'" % name)
                return False
        return True

    def add_item(self, item):
        """Append item, asking for a name if its default name is taken"""
        name = self.item_name(item, len(self.draft))
        while name in self.names():
            name, ok = QInputDialog.getText(
                self, "Objective name",
                "'%s' is already in the lineup. Name for the new one:" %
                name, QLineEdit.Normal, name + " (2)")
            if not ok:
                return False
            name = name.strip()
            if name:
                item["name"] = name
        self.draft.append(item)
        self.set_dirty()
        self.lineup_table.selectRow(len(self.draft) - 1)
        return True

    def lineup_move(self, delta):
        row = self.selected_row()
        if row is None:
            return
        dst = row + delta
        if not 0 <= dst < len(self.draft):
            return
        self.draft[row], self.draft[dst] = self.draft[dst], self.draft[row]
        self.dirty = True
        self.refresh_lineup(select=dst)

    def lineup_remove(self):
        row = self.selected_row()
        if row is None:
            return
        del self.draft[row]
        self.dirty = True
        self.refresh_lineup(select=min(row, len(self.draft) - 1))

    def lineup_rename(self):
        row = self.selected_row()
        if row is None:
            return
        item = self.draft[row]
        old = self.item_name(item, row)
        name, ok = QInputDialog.getText(
            self, "Rename objective",
            "Name (blank for the default name):", QLineEdit.Normal, old)
        if not ok:
            return
        name = name.strip()
        if name:
            if not self.check_name_free(name, ignore_row=row):
                return
            item["name"] = name
        else:
            if "db_find" not in item:
                QMessageBox.warning(self, "Rename objective",
                                    "This objective needs a name")
                return
            item.pop("name", None)
            if not self.check_name_free(self.item_name(item, row),
                                        ignore_row=row):
                item["name"] = old
                return
        self.dirty = True
        self.refresh_lineup(select=row)

    def lineup_edit(self):
        row = self.selected_row()
        if row is None:
            return
        item = self.draft[row]
        if "db_find" in item:
            try:
                fields = parse_db_find(item["db_find"])
            except ValueError as e:
                QMessageBox.warning(self, "Objectives", str(e))
                return
            entry = self.db().find(fields["vendor"], fields["model"])
            if entry is None:
                entry = {"vendor": fields["vendor"], "model": fields["model"]}
            self.edit_library_entry(entry)
            return
        dialog = ObjectiveEditDialog(self.db(),
                                     entry=item,
                                     library=False,
                                     title="Edit objective",
                                     parent=self)
        if not dialog.exec_():
            return
        if not self.check_name_free(dialog.entry["name"], ignore_row=row):
            return
        new = dict(dialog.entry)
        if ORIG_NAME in item:
            new[ORIG_NAME] = item[ORIG_NAME]
        self.draft[row] = new
        self.dirty = True
        self.refresh_lineup(select=row)

    def lineup_add_inline(self):
        dialog = ObjectiveEditDialog(
            self.db(),
            library=False,
            title="Add objective",
            help_text="An objective only used by this lineup, such as a zoom "
            "lens setting. Objectives with a vendor and model are better "
            "added to the library so they can be shared.",
            parent=self)
        if not dialog.exec_():
            return
        if not self.check_name_free(dialog.entry["name"]):
            return
        self.add_item(dict(dialog.entry))

    def lineup_problems(self):
        if not self.draft:
            return "Add at least one objective"
        for i, item in enumerate(self.draft):
            _resolved, err = self.resolve(item)
            if err:
                return "%s: %s" % (self.item_name(item, i), err)
        names = self.names()
        for name in names:
            if names.count(name) > 1:
                return "Objective name '%s' is used more than once" % name
        return None

    def move_calibration(self, items, names):
        """Offer to move calibration of renamed objectives. Returns False to cancel"""
        store = self.ac.microscope.cal_store
        renames = []
        for item, name in zip(items, names):
            old = item.get(ORIG_NAME)
            if old and old != name and old not in names and store.objective_entries(
                    old):
                renames.append((old, name))
        if not renames:
            return True
        ret = QMessageBox.question(
            self, "Renamed objectives",
            "Calibration is stored by objective name. Move it to the new "
            "names?\n\n" + "\n".join("%s => %s" % r for r in renames),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if ret == QMessageBox.Cancel:
            return False
        if ret == QMessageBox.Yes:
            for old, new in renames:
                store.rename_objective(old, new)
        return True

    def lineup_apply(self):
        problem = self.lineup_problems()
        if problem:
            QMessageBox.warning(self, "Objectives", problem)
            return
        names = self.names()
        if not self.move_calibration(self.draft, names):
            return
        items = [{k: v
                  for k, v in item.items() if k != ORIG_NAME}
                 for item in self.draft]
        try:
            self.db().set_lineup(self.lineup_name(), items)
        except Exception as e:
            QMessageBox.warning(self, "Objectives",
                                "Failed to save lineup: %s" % (e, ))
            return
        self.reload_microscope()
        self.ac.log("Objectives: applied lineup %s" % ", ".join(names))
        self.load_draft()

    def lineup_reset(self):
        if self.usc().objective_lineup():
            ret = QMessageBox.question(
                self, "Reset objectives",
                "Forget your lineup and use the microscope.j5 objectives? "
                "Your objective library is kept.")
            if ret != QMessageBox.Yes:
                return
            self.db().clear_lineup(self.lineup_name())
            self.reload_microscope()
            self.ac.log("Objectives: using microscope defaults")
        self.load_draft()

    def reload_microscope(self):
        try:
            self.ac.microscope.reload_objectives()
        except Exception as e:
            QMessageBox.warning(self, "Objectives",
                                "Failed to reload objectives: %s" % (e, ))
        error = self.ac.microscope.get_objectives().lineup_error
        if error:
            QMessageBox.warning(
                self, "Objectives",
                "Your lineup couldn't be used, using the microscope "
                "defaults: %s" % error)

    """
    Library
    """

    def refresh_library(self):
        db = self.db()
        selected = self.selected_library_key()
        filt = self.filter_le.text().strip().upper()
        tree = self.library_tree
        tree.clear()
        vendors = {}
        for k, entry in db.db.items():
            text = " ".join(
                str(entry.get(f) or "")
                for f in ("vendor", "model", "series")).upper()
            if filt and filt not in text:
                continue
            vendors.setdefault(entry["vendor"], []).append((k, entry))
        for vendor in sorted(vendors, key=lambda v: v.upper()):
            parent = QTreeWidgetItem([vendor])
            tree.addTopLevelItem(parent)
            for k, entry in sorted(vendors[vendor],
                                   key=lambda ke:
                                   (float(ke[1].get("magnification") or 0),
                                    ke[1]["model"])):
                label = entry["model"]
                if entry.get("series") and entry["series"] not in label:
                    label = "%s (%s)" % (label, entry["series"])
                if db.is_user(*k):
                    source = "Yours (overrides built-in)" if db.is_shipped(
                        *k) else "Yours"
                else:
                    source = "Built-in"
                item = QTreeWidgetItem([
                    label,
                    num_str(entry.get("magnification")),
                    num_str(entry.get("na")),
                    num_str(entry.get("wd")), source
                ])
                item.setData(0, Qt.UserRole, k)
                parent.addChild(item)
                if k == selected:
                    tree.setCurrentItem(item)
            if filt or any(db.is_user(*k) for k, _e in vendors[vendor]):
                parent.setExpanded(True)

    def selected_library_key(self):
        item = self.library_tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    def selected_library_entry(self):
        k = self.selected_library_key()
        return None if k is None else self.db().db.get(tuple(k))

    def library_changed(self):
        self.reload_microscope()
        self.refresh_library()
        self.refresh_lineup()

    def library_add_to_lineup(self):
        entry = self.selected_library_entry()
        if entry is None:
            return
        self.add_item({"db_find": objective_db_find(entry)})

    def library_new(self):
        dialog = ObjectiveEditDialog(
            self.db(),
            title="New objective",
            help_text="Saved to your objective library. Use Export to share "
            "it.",
            parent=self)
        if not dialog.exec_():
            return
        entry = dialog.entry
        db = self.db()
        k = objective_key(entry["vendor"], entry["model"])
        if k in db.user:
            msg = "Replace your existing %s %s?"
        elif k in db.shipped:
            msg = "%s %s is a built-in objective. Override its values with yours?"
        else:
            msg = None
        if msg and QMessageBox.question(
                self, "New objective", msg %
            (entry["vendor"], entry["model"])) != QMessageBox.Yes:
            return
        self.save_library_entry(entry)
        if QMessageBox.question(
                self, "New objective",
                "Add %s %s to this microscope's lineup?" %
            (entry["vendor"], entry["model"])) == QMessageBox.Yes:
            self.add_item({"db_find": objective_db_find(entry)})

    def library_edit(self):
        entry = self.selected_library_entry()
        if entry is not None:
            self.edit_library_entry(entry)

    def edit_library_entry(self, entry):
        db = self.db()
        if db.is_user(entry["vendor"], entry["model"]):
            title = "Edit objective"
            help_text = None
        else:
            title = "Override built-in objective"
            help_text = ("Your values are saved to your objective library "
                         "and replace the built-in ones. Delete restores "
                         "the built-in values.")
        dialog = ObjectiveEditDialog(db,
                                     entry=entry,
                                     lock_key=True,
                                     title=title,
                                     help_text=help_text,
                                     parent=self)
        if dialog.exec_():
            self.save_library_entry(dialog.entry)

    def save_library_entry(self, entry):
        try:
            self.db().set_user_entry(entry)
        except Exception as e:
            QMessageBox.warning(self, "Objectives",
                                "Failed to save objective: %s" % (e, ))
            return
        self.ac.log("Objectives: saved %s %s to your library" %
                    (entry["vendor"], entry["model"]))
        self.library_changed()

    def library_delete(self):
        entry = self.selected_library_entry()
        db = self.db()
        if entry is None:
            return
        vendor, model = entry["vendor"], entry["model"]
        if not db.is_user(vendor, model):
            QMessageBox.information(self, "Delete objective",
                                    "Built-in objectives can't be deleted")
            return
        if db.is_shipped(vendor, model):
            msg = "Delete your values for %s %s and use the built-in ones?"
        else:
            msg = "Delete %s %s from your library?"
            items = self.draft + (self.usc().objective_lineup() or [])
            if objective_key(vendor, model) in self.item_keys(items):
                msg += " It is in the lineup and will be skipped."
        if QMessageBox.question(self, "Delete objective", msg %
                                (vendor, model)) != QMessageBox.Yes:
            return
        db.remove_user_entry(vendor, model)
        self.library_changed()

    """
    Export
    """

    def lineup_export_text(self):
        """Lineup as a microscope.j5 system snippet"""
        objectives_db = []
        objectives = []
        for item in self.draft:
            item = {k: v for k, v in item.items() if k != ORIG_NAME}
            if "db_find" in item and set(item) == {"db_find"}:
                objectives_db.append(item["db_find"])
            else:
                objectives.append(ordered_objective_entry(item))
        j = {}
        if objectives_db:
            j["objectives_db"] = objectives_db
        if objectives:
            j["objectives"] = objectives
        return json.dumps(j, indent=4, separators=(",", ": ")) + "\n"

    def lineup_library_entries(self):
        ret = []
        for item in self.draft:
            resolved, _err = self.resolve(item)
            if "db_find" in item and resolved:
                fields = parse_db_find(item["db_find"])
                entry = self.db().find(fields["vendor"], fields["model"])
                if entry and entry not in ret:
                    ret.append(entry)
        return ret

    def export(self):
        db = self.db()
        sources = []
        entry = self.selected_library_entry()
        if entry is not None:
            sources.append(("Selected: %s %s" %
                            (entry["vendor"], entry["model"]),
                            objectives_export_json([entry])))
        sources.append(("Your library (%u objectives)" % len(db.user),
                        objectives_export_json(db.user.values())))
        sources.append(("Library entries used by this lineup",
                        objectives_export_json(
                            self.lineup_library_entries())))
        sources.append(("This lineup (microscope.j5 format)",
                        self.lineup_export_text()))
        ObjectiveExportDialog(sources, parent=self).exec_()
