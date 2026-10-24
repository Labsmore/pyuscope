"""
New Microscope Wizard
Create a starting configs/<name>/microscope.j5 for a custom microscope

Every field has a guess so you can click Next through the whole thing
Real values come from the calibration wizard afterwards:
./app/argus.py --microscope <name> then Advanced options => Calibration

Examples:
./app/microforge.py
./app/microforge.py --name my-scope --from ls-hvy-2
./app/microforge.py --name my-scope --out /tmp/my-scope
"""

from uscope import microforge as mf

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase, QIntValidator, QDoubleValidator
from PyQt5.QtWidgets import (
    QApplication, QWizard, QWizardPage, QLabel, QLineEdit, QPlainTextEdit,
    QComboBox, QDoubleSpinBox, QCheckBox, QPushButton, QFileDialog,
    QMessageBox, QInputDialog, QTreeWidget, QTreeWidgetItem, QTableWidget,
    QTableWidgetItem, QHeaderView, QGroupBox, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QAbstractItemView)

import argparse
import os
import sys
import traceback

CAL_HELP = ("These are guesses. After saving, start Argus and run "
            "Advanced options => Calibration to measure the real value. "
            "Calibrated values override microscope.j5.")


def label(text):
    ret = QLabel(text)
    ret.setWordWrap(True)
    return ret


def guess_label(text=CAL_HELP):
    ret = label("<i>%s</i>" % (text, ))
    return ret


def double_spin(value, minimum, maximum, decimals=3, step=None, suffix=""):
    ret = QDoubleSpinBox()
    ret.setDecimals(decimals)
    ret.setRange(minimum, maximum)
    if step:
        ret.setSingleStep(step)
    if suffix:
        ret.setSuffix(suffix)
    ret.setValue(value)
    return ret


def opt_int(s):
    s = s.strip()
    return int(s) if s else None


def opt_float(s):
    s = s.strip()
    return float(s) if s else None


def opt_str(v):
    return "" if v is None else str(v)


def text_lines(text):
    """One command per line, blank lines and // comments dropped"""
    ret = []
    for l in text.split("\n"):
        l = l.strip()
        if l and not l.startswith("//"):
            ret.append(l)
    return ret


class Page(QWizardPage):
    def p(self):
        return self.wizard().params


class IntroPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("New Microscope Wizard")
        layout = QVBoxLayout()
        layout.addWidget(
            label(
                "This wizard creates a configuration file "
                "(<tt>configs/&lt;name&gt;/microscope.j5</tt>) for a custom "
                "microscope: camera, optics, objectives and motion controller."
            ))
        layout.addWidget(
            label(
                "Every field is pre-filled with a reasonable guess. If you "
                "are unsure, keep the default: you can click Next all the way "
                "through and get a config that loads and runs."))
        layout.addWidget(
            label(
                "<b>Afterwards, calibrate.</b> Start Argus with the new "
                "microscope and run <b>Advanced options => Calibration</b>. "
                "It measures the real image scale (um / pixel), backlash, "
                "crop, settle times, etc and stores them in the calibration "
                "store, which overrides the guesses written here. Guessed "
                "values are marked with a <tt>//Guess</tt> comment in the "
                "generated file."))
        layout.addWidget(
            label("You can also edit microscope.j5 by hand at any time. See "
                  "configs/ for examples."))
        layout.addStretch()
        self.setLayout(layout)


class IdentityPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Identity")
        self.setSubTitle("Name the configuration and choose where to save it")
        self.out_dir_user_set = False

        layout = QFormLayout()
        self.name = QLineEdit()
        self.name.textEdited.connect(self.name_edited)
        layout.addRow("Config name", self.name)
        layout.addRow("",
                      label("Used with ./app/argus.py --microscope <name>. "
                            "Letters, digits, '.', '_' and '-' only"))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(
            "Optional. Ex: who it's for, stage / camera model. "
            "Goes in the header comment")
        self.description.setMaximumHeight(100)
        layout.addRow("Description", self.description)

        out_layout = QHBoxLayout()
        self.out_dir = QLineEdit()
        self.out_dir.textEdited.connect(self.out_dir_edited)
        out_layout.addWidget(self.out_dir)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse)
        out_layout.addWidget(browse)
        layout.addRow("Output directory", out_layout)
        self.out_note = label("")
        layout.addRow("", self.out_note)
        self.setLayout(layout)

    def initializePage(self):
        p = self.p()
        self.name.setText(p["name"])
        self.description.setPlainText(p.get("description") or "")
        if p.get("out_dir"):
            self.out_dir.setText(p["out_dir"])
            self.out_dir_user_set = True
        else:
            self.out_dir.setText(mf.default_out_dir(p["name"]))
        self.update_note()

    def name_edited(self, _text):
        if not self.out_dir_user_set:
            self.out_dir.setText(mf.default_out_dir(self.name.text().strip()))
        self.update_note()

    def out_dir_edited(self, _text):
        self.out_dir_user_set = True
        self.update_note()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Output directory",
                                             self.out_dir.text())
        if d:
            self.out_dir.setText(d)
            self.out_dir_user_set = True
            self.update_note()

    def update_note(self):
        name = self.name.text().strip()
        out_dir = self.out_dir.text().strip()
        if name and out_dir and not mf.is_in_configs_dir(name, out_dir):
            self.out_note.setText(
                "<i>Not configs/%s: --microscope %s won't find it unless "
                "you copy it there</i>" % (name, name))
        else:
            self.out_note.setText("")

    def validatePage(self):
        name = self.name.text().strip()
        err = mf.validate_name(name)
        if err:
            QMessageBox.warning(self, "Config name", err)
            return False
        out_dir = self.out_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "Output directory",
                                "Output directory is required")
            return False
        out_dir = os.path.abspath(os.path.expanduser(out_dir))
        if os.path.exists(os.path.join(out_dir, "microscope.j5")):
            ret = QMessageBox.question(
                self, "Config exists",
                "%s already has a microscope.j5\n"
                "It will be overwritten when you save. Continue?" %
                (out_dir, ))
            if ret != QMessageBox.Yes:
                return False
        p = self.p()
        p["name"] = name
        p["description"] = self.description.toPlainText()
        p["out_dir"] = out_dir
        return True


class CameraPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Camera")
        self.setSubTitle("Which camera and at what resolution")
        self.scalar_user_set = False
        self.loading = False

        layout = QFormLayout()
        self.source = QComboBox()
        for source, sd in mf.SOURCES.items():
            self.source.addItem("%s  (%s)" % (sd["label"], source), source)
        self.source.currentIndexChanged.connect(self.source_changed)
        layout.addRow("Camera type", self.source)

        detect_layout = QHBoxLayout()
        self.detect = QPushButton("Detect camera")
        self.detect.clicked.connect(self.detect_clicked)
        detect_layout.addWidget(self.detect)
        self.detect_status = label("")
        detect_layout.addWidget(self.detect_status, 1)
        layout.addRow("", detect_layout)

        wh_layout = QHBoxLayout()
        self.width = QLineEdit()
        self.width.setValidator(QIntValidator(1, 100000))
        self.width.textEdited.connect(self.wh_edited)
        self.height = QLineEdit()
        self.height.setValidator(QIntValidator(1, 100000))
        self.height.textEdited.connect(self.wh_edited)
        wh_layout.addWidget(self.width)
        wh_layout.addWidget(QLabel("x"))
        wh_layout.addWidget(self.height)
        layout.addRow("Width x height (pixels)", wh_layout)
        self.wh_note = label("")
        layout.addRow("", self.wh_note)

        self.pitch = QLineEdit()
        self.pitch.setValidator(QDoubleValidator(0.0, 1000.0, 4))
        self.pitch.setPlaceholderText("unknown")
        layout.addRow("Native pixel pitch (um)", self.pitch)
        layout.addRow(
            "",
            label("From the sensor datasheet (ex: 2.4). Optional: used to "
                  "guess the image scale on the next page"))

        self.model = QLineEdit()
        self.model.setPlaceholderText("optional, ex: E3ISPM20000KPA")
        layout.addRow("Camera model", self.model)
        self.v4l2_name = QLineEdit()
        self.v4l2_name.setPlaceholderText(
            "optional: first V4L2 device with this name, else default device")
        layout.addRow("V4L2 device name", self.v4l2_name)

        self.scalar = double_spin(1.0, 0.05, 1.0, decimals=2, step=0.05)
        self.scalar.valueChanged.connect(self.scalar_changed)
        layout.addRow("Output scalar", self.scalar)
        layout.addRow(
            "",
            label("Scales saved / displayed images. 0.5 is typical for large "
                  "(> 10 MP) sensors to keep things responsive"))
        self.setLayout(layout)

    def source_str(self):
        return self.source.currentData()

    def initializePage(self):
        p = self.p()
        self.loading = True
        self.source.setCurrentIndex(max(0, self.source.findData(p["source"])))
        self.width.setText(opt_str(p.get("width")))
        self.height.setText(opt_str(p.get("height")))
        self.pitch.setText(opt_str(p.get("native_pixel_pitch_um")))
        self.model.setText(opt_str(p.get("model")))
        self.v4l2_name.setText(opt_str(p.get("v4l2_name")))
        self.scalar.setValue(float(p.get("scalar") or 1.0))
        self.scalar_user_set = self.wizard().from_template
        self.loading = False
        self.update_enables()

    def source_changed(self, _index):
        if self.loading:
            return
        sd = mf.source_defaults(self.source_str())
        self.width.setText(opt_str(sd["width"]))
        self.height.setText(opt_str(sd["height"]))
        self.pitch.setText(opt_str(sd["native_pixel_pitch_um"]))
        self.v4l2_name.setText(opt_str(sd["v4l2_name"]))
        self.native_wh = (sd["native_width"], sd["native_height"])
        self.detect_status.setText("")
        if not self.scalar_user_set:
            self.set_scalar(sd["scalar"])
        self.update_enables()

    def set_scalar(self, v):
        self.scalar.blockSignals(True)
        self.scalar.setValue(v)
        self.scalar.blockSignals(False)

    def scalar_changed(self, _v):
        if not self.loading:
            self.scalar_user_set = True

    def wh_edited(self, _text):
        if not self.scalar_user_set:
            self.set_scalar(
                mf.scalar_guess(opt_int(self.width.text()),
                                opt_int(self.height.text())))

    def update_enables(self):
        source = self.source_str()
        sd = mf.SOURCES[source]
        self.v4l2_name.setEnabled(sd["v4l2"])
        self.detect.setEnabled(source == "gst-toupcamsrc" or sd["v4l2"])
        if source == "gst-toupcamsrc":
            self.width.setPlaceholderText("auto")
            self.height.setPlaceholderText("auto")
            self.wh_note.setText(
                "<i>Leave blank to use the camera's max resolution "
                "(detected at startup)</i>")
        else:
            self.width.setPlaceholderText("required")
            self.height.setPlaceholderText("required")
            self.wh_note.setText("<i>Required for this camera type</i>")

    def detect_clicked(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self.source_str() == "gst-toupcamsrc":
                msg = self.detect_toupcam()
            else:
                msg = self.detect_v4l2()
        except Exception as e:
            msg = "Detect failed: %s" % (e, )
        finally:
            QApplication.restoreOverrideCursor()
        if msg:
            self.detect_status.setText(msg)

    def detect_toupcam(self):
        from uscope.imager.plugins.gst_toupcamsrc import resolution
        info = resolution.probe()
        best = max(info["resolutions"],
                   key=lambda r: r["width"] * r["height"])
        self.width.setText(str(best["width"]))
        self.height.setText(str(best["height"]))
        self.wh_edited("")
        return "Found %ux%u (S/N %s)" % (best["width"], best["height"],
                                         info.get("serial"))

    def detect_v4l2(self):
        from uscope import v4l2_util
        devices = v4l2_util.list_devices()
        if not devices:
            return "No V4L2 devices found"
        items = ["%s  (%s)" % (card, dev) for dev, card in devices]
        item, ok = QInputDialog.getItem(self, "V4L2 device",
                                        "Select camera", items, 0, False)
        if not ok:
            return None
        card = devices[items.index(item)][1]
        self.v4l2_name.setText(card)
        return "Selected %s" % (card, )

    def validatePage(self):
        source = self.source_str()
        try:
            width = opt_int(self.width.text())
            height = opt_int(self.height.text())
            pitch = opt_float(self.pitch.text())
        except ValueError as e:
            QMessageBox.warning(self, "Camera", str(e))
            return False
        if bool(width) != bool(height):
            QMessageBox.warning(self, "Camera",
                                "Set both width and height (or neither)")
            return False
        if source != "gst-toupcamsrc" and not width:
            QMessageBox.warning(
                self, "Camera",
                "Width and height are required for this camera type")
            return False
        p = self.p()
        if p["source"] != source:
            native_w, native_h = getattr(self, "native_wh", (None, None))
            p["native_width"] = native_w
            p["native_height"] = native_h
        p["source"] = source
        p["width"] = width
        p["height"] = height
        p["native_pixel_pitch_um"] = pitch
        p["model"] = self.model.text().strip() or None
        p["v4l2_name"] = (self.v4l2_name.text().strip()
                          if mf.SOURCES[source]["v4l2"] else "") or None
        p["scalar"] = self.scalar.value()
        return True


class OpticsPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Optics")
        self.setSubTitle("Image scale and objective tube length")
        self.um_user_set = False
        self.tube_user_set = False

        layout = QFormLayout()
        self.relay = double_spin(1.0,
                                 0.01,
                                 100.0,
                                 decimals=3,
                                 step=0.1,
                                 suffix=" x")
        self.relay.valueChanged.connect(self.relay_changed)
        layout.addRow("Relay / camera lens magnification", self.relay)
        layout.addRow(
            "",
            label("Lens between the tube and the camera (ex: 0.5x, 1x C-mount "
                  "adapter). 1.0 if none / unknown"))

        um_layout = QHBoxLayout()
        self.um = double_spin(mf.UM_PER_PIXEL_DEFAULT, 0.0001, 1000.0, 7, 0.1)
        self.um.valueChanged.connect(self.um_changed)
        um_layout.addWidget(self.um)
        reguess = QPushButton("Guess")
        reguess.clicked.connect(self.reguess)
        um_layout.addWidget(reguess)
        layout.addRow("um / pixel with a 1x objective", um_layout)
        self.um_note = label("")
        layout.addRow("", self.um_note)

        self.tube = double_spin(mf.TUBE_LENGTH_DEFAULT, 0, 1000, 0, 10,
                                " mm")
        self.tube.valueChanged.connect(self.tube_changed)
        layout.addRow("Infinity tube length", self.tube)
        layout.addRow(
            "",
            label("Olympus: 180, Mitutoyo / Nikon: 200. Updated from the "
                  "objectives you choose next unless you change it here"))
        self.telecentric = QCheckBox("Telecentric optics")
        layout.addRow("", self.telecentric)
        layout.addRow("", guess_label())
        self.setLayout(layout)

    def guess(self):
        p = dict(self.p())
        p["relay_magnification"] = self.relay.value()
        return mf.um_per_pixel_guess_params(p)

    def initializePage(self):
        p = self.p()
        self.relay.blockSignals(True)
        self.relay.setValue(float(p.get("relay_magnification") or 1.0))
        self.relay.blockSignals(False)
        if self.wizard().from_template and not self.um_user_set:
            self.um_user_set = True
            self.tube_user_set = True
        if self.um_user_set:
            self.set_um(float(p["um_per_pixel_raw_1x"]))
        else:
            self.set_um(self.guess())
        self.tube.blockSignals(True)
        self.tube.setValue(float(p.get("infinity_tube_length") or 0))
        self.tube.blockSignals(False)
        self.telecentric.setChecked(bool(p.get("telecentric")))
        self.update_note()

    def set_um(self, v):
        self.um.blockSignals(True)
        self.um.setValue(v)
        self.um.blockSignals(False)

    def update_note(self):
        p = self.p()
        if p.get("native_pixel_pitch_um"):
            text = "Guess: pixel pitch %g um / %g x relay" % (
                p["native_pixel_pitch_um"], self.relay.value())
            if p.get("native_width") and p.get("width"):
                text += " * %u / %u (capture below native resolution)" % (
                    p["native_width"], p["width"])
        else:
            text = ("Guess: pixel pitch unknown, using a typical value of %g" %
                    (mf.UM_PER_PIXEL_DEFAULT, ))
        self.um_note.setText("<i>%s</i>" % (text, ))

    def relay_changed(self, _v):
        if not self.um_user_set:
            self.set_um(self.guess())
        self.update_note()

    def um_changed(self, _v):
        self.um_user_set = True

    def reguess(self):
        self.set_um(self.guess())
        self.um_user_set = False

    def tube_changed(self, _v):
        self.tube_user_set = True

    def validatePage(self):
        p = self.p()
        p["relay_magnification"] = self.relay.value()
        # Spin box rounds: keep full precision of an unedited template value
        if round(p["um_per_pixel_raw_1x"],
                 self.um.decimals()) != self.um.value():
            p["um_per_pixel_raw_1x"] = self.um.value()
        p["infinity_tube_length"] = int(self.tube.value()) or None
        p["telecentric"] = self.telecentric.isChecked()
        return True


class ObjectivesPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Objectives")
        self.setSubTitle(
            "Check the objectives you have. Add custom ones if not listed")
        self.db = None
        try:
            from uscope.config import ObjectiveDB
            self.db = ObjectiveDB()
        except Exception as e:
            print("WARNING: failed to load objective DB: %s" % (e, ))

        layout = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Objective", "Mag", "NA", "Tube (mm)"])
        self.tree.header().setSectionResizeMode(0,
                                                QHeaderView.ResizeToContents)
        self.items = {}
        if self.db:
            self.populate_tree()
        layout.addWidget(self.tree, 2)

        group = QGroupBox("Custom objectives")
        group_layout = QVBoxLayout()
        self.custom = QTableWidget(0, 3)
        self.custom.setHorizontalHeaderLabels(
            ["Name", "Magnification", "NA"])
        self.custom.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.custom.setSelectionBehavior(QAbstractItemView.SelectRows)
        group_layout.addWidget(self.custom)
        buttons = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self.add_custom("Custom 10X", 10, 0.25))
        buttons.addWidget(add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_custom)
        buttons.addWidget(remove)
        buttons.addStretch()
        group_layout.addLayout(buttons)
        group.setLayout(group_layout)
        layout.addWidget(group, 1)
        self.setLayout(layout)

    def populate_tree(self):
        vendors = {}
        for (vendor_k, model_k), entry in self.db.db.items():
            vendors.setdefault(entry["vendor"], []).append(entry)
        for vendor in sorted(vendors, key=lambda v: v.upper()):
            parent = QTreeWidgetItem([vendor])
            parent.setFlags(parent.flags() | Qt.ItemIsAutoTristate
                            | Qt.ItemIsUserCheckable)
            self.tree.addTopLevelItem(parent)
            for entry in sorted(vendors[vendor],
                                key=lambda e:
                                (e.get("magnification") or 0, e["model"])):
                name = entry["model"]
                if entry.get("series") and entry["series"] not in name:
                    name = "%s (%s)" % (name, entry["series"])
                item = QTreeWidgetItem([
                    name,
                    opt_str(entry.get("magnification")),
                    opt_str(entry.get("na")),
                    opt_str(entry.get("infinity_tube_length")),
                ])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Unchecked)
                item.setData(0, Qt.UserRole, mf.objective_db_key(entry))
                parent.addChild(item)
                self.items[(entry["vendor"].upper(),
                            entry["model"].upper())] = item

    def initializePage(self):
        p = self.p()
        # Unknown DB strings are kept as is
        self.unknown_db = []
        for item in self.items.values():
            item.setCheckState(0, Qt.Unchecked)
        for s in p.get("objectives_db") or []:
            vendor, model = mf.parse_db_key(s)
            item = self.items.get(((vendor or "").upper(),
                                   (model or "").upper()))
            if item:
                item.setCheckState(0, Qt.Checked)
                item.parent().setExpanded(True)
            else:
                self.unknown_db.append(s)
        self.custom.setRowCount(0)
        for o in p.get("objectives") or []:
            self.add_custom(o["name"], o["magnification"], o.get("na"))

    def add_custom(self, name, magnification, na):
        row = self.custom.rowCount()
        self.custom.insertRow(row)
        self.custom.setItem(row, 0, QTableWidgetItem(str(name)))
        self.custom.setItem(row, 1, QTableWidgetItem("%g" % magnification))
        self.custom.setItem(row, 2,
                            QTableWidgetItem("" if na is None else "%g" % na))

    def remove_custom(self):
        rows = sorted(set(i.row() for i in self.custom.selectedIndexes()),
                      reverse=True)
        if not rows and self.custom.rowCount():
            rows = [self.custom.rowCount() - 1]
        for row in rows:
            self.custom.removeRow(row)

    def checked_db(self):
        ret = []
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                if item.checkState(0) == Qt.Checked:
                    ret.append(item.data(0, Qt.UserRole))
        return ret

    def custom_objectives(self):
        ret = []
        for row in range(self.custom.rowCount()):

            def cell(col):
                item = self.custom.item(row, col)
                return item.text().strip() if item else ""

            name = cell(0)
            try:
                magnification = float(cell(1))
                if magnification <= 0:
                    raise ValueError()
            except ValueError:
                raise ValueError("Row %u (%s): bad magnification" %
                                 (row + 1, name))
            try:
                na = float(cell(2)) if cell(2) else None
            except ValueError:
                raise ValueError("Row %u (%s): bad NA" % (row + 1, name))
            if not name:
                raise ValueError("Row %u: name required" % (row + 1, ))
            ret.append({"name": name, "magnification": magnification, "na": na})
        return ret

    def validatePage(self):
        try:
            custom = self.custom_objectives()
        except ValueError as e:
            QMessageBox.warning(self, "Custom objectives", str(e))
            return False
        objectives_db = self.unknown_db + self.checked_db()
        if not objectives_db and not custom:
            QMessageBox.warning(self, "Objectives",
                                "Select or add at least one objective")
            return False
        p = self.p()
        p["objectives_db"] = objectives_db
        p["objectives"] = custom
        if not self.wizard().optics_page.tube_user_set:
            p["infinity_tube_length"] = mf.tube_length_guess(
                objectives_db, self.db)
        return True


class MotionPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Motion")
        self.setSubTitle("Stage motion controller")

        layout = QVBoxLayout()
        form = QFormLayout()
        self.hal = QComboBox()
        for hal, desc in mf.HALS.items():
            self.hal.addItem("%s  (%s)" % (desc, hal), hal)
        self.hal.currentIndexChanged.connect(self.update_enables)
        form.addRow("Controller", self.hal)

        port_layout = QHBoxLayout()
        self.port = QLineEdit()
        self.port.setPlaceholderText("autodetect (ex: /dev/ttyUSB0)")
        port_layout.addWidget(self.port)
        mock = QPushButton("Emulate")
        mock.setToolTip("port \"mock\": software GRBL, no hardware")
        mock.clicked.connect(lambda: self.port.setText("mock"))
        port_layout.addWidget(mock)
        form.addRow("GRBL serial port", port_layout)

        self.axes = QComboBox()
        self.axes.addItem("X, Y, Z", "xyz")
        self.axes.addItem("X, Y only (no motorized focus)", "xy")
        self.axes.currentIndexChanged.connect(self.update_enables)
        form.addRow("Axes", self.axes)

        self.backlash = double_spin(0.05, 0.0, 10.0, 3, 0.01, " mm")
        form.addRow("Backlash", self.backlash)

        self.limit_switches = QComboBox()
        self.limit_switches.addItem("Not specified (home if configured)",
                                    None)
        self.limit_switches.addItem("Yes", True)
        self.limit_switches.addItem("No (skip homing)", False)
        form.addRow("Limit switches", self.limit_switches)
        layout.addLayout(form)

        grid = QGridLayout()
        grid.addWidget(QLabel("Axis"), 0, 0)
        grid.addWidget(QLabel("Scalar"), 0, 1)
        grid.addWidget(QLabel("Soft limit min (mm)"), 0, 2)
        grid.addWidget(QLabel("Soft limit max (mm)"), 0, 3)
        self.scalars = {}
        self.limits = {}
        for row, axis in enumerate("xyz", 1):
            grid.addWidget(QLabel(axis.upper()), row, 0)
            self.scalars[axis] = double_spin(1.0, -10000.0, 10000.0, 4, 0.1)
            grid.addWidget(self.scalars[axis], row, 1)
            for col, which in enumerate(("min", "max"), 2):
                w = double_spin(0.0, -10000.0, 10000.0, 3, 1.0)
                self.limits[axis + which] = w
                grid.addWidget(w, row, col)
        self.use_limits = QCheckBox("Use soft limits")
        self.use_limits.toggled.connect(self.update_enables)
        grid.addWidget(self.use_limits, 4, 2, 1, 2)
        group = QGroupBox("Axes")
        group.setLayout(grid)
        layout.addWidget(group)
        layout.addWidget(
            guess_label(
                "Scalar: multiply GUI moves before sending to the controller "
                "(ex: gearing). Soft limits: set to your stage travel. "
                "Backlash is a guess: the calibration wizard measures it."))

        self.grbl_group = QGroupBox("GRBL (optional)")
        grbl_layout = QFormLayout()
        self.rc_pre_home = QPlainTextEdit()
        self.rc_pre_home.setPlaceholderText(
            "One command per line, sent before homing. Ex:\n$100=800.000")
        self.rc_pre_home.setMaximumHeight(70)
        grbl_layout.addRow("Pre-home commands", self.rc_pre_home)
        self.rc_post_home = QPlainTextEdit()
        self.rc_post_home.setPlaceholderText(
            "One command per line, sent after homing. Ex:\nG54")
        self.rc_post_home.setMaximumHeight(50)
        grbl_layout.addRow("Post-home commands", self.rc_post_home)
        flags = QHBoxLayout()
        self.use_wcs_offsets = QCheckBox("Use WCS offsets")
        flags.addWidget(self.use_wcs_offsets)
        self.xyz_format6 = QCheckBox("Display 6 decimal positions")
        flags.addWidget(self.xyz_format6)
        flags.addStretch()
        grbl_layout.addRow("", flags)
        self.grbl_group.setLayout(grbl_layout)
        layout.addWidget(self.grbl_group)
        self.setLayout(layout)

    def initializePage(self):
        p = self.p()
        self.hal.setCurrentIndex(max(0, self.hal.findData(p["hal"])))
        self.port.setText(opt_str(p.get("port")))
        self.axes.setCurrentIndex(max(0, self.axes.findData(p["axes"])))
        self.backlash.setValue(float(p.get("backlash") or 0.0))
        self.limit_switches.setCurrentIndex(
            max(0, self.limit_switches.findData(p.get("limit_switches"))))
        for axis in "xyz":
            self.scalars[axis].setValue(
                float((p.get("scalars") or {}).get(axis, 1.0)))
        limits = p.get("soft_limits")
        self.use_limits.setChecked(bool(limits))
        limits = limits or mf.DEFAULT_SOFT_LIMITS
        for k, w in self.limits.items():
            v = limits.get(k)
            w.setValue(float(mf.DEFAULT_SOFT_LIMITS[k] if v is None else v))
        self.rc_pre_home.setPlainText("\n".join(p.get("rc_pre_home") or []))
        self.rc_post_home.setPlainText("\n".join(
            p.get("rc_post_home") or []))
        self.use_wcs_offsets.setChecked(bool(p.get("use_wcs_offsets")))
        self.xyz_format6.setChecked(bool(p.get("xyz_format6")))
        self.update_enables()

    def update_enables(self, *_args):
        grbl = self.hal.currentData() == "grbl-ser"
        self.port.setEnabled(grbl)
        self.grbl_group.setEnabled(grbl)
        has_z = self.axes.currentData() == "xyz"
        self.scalars["z"].setEnabled(has_z)
        for k, w in self.limits.items():
            w.setEnabled(self.use_limits.isChecked() and (has_z or k[0] != "z"))

    def validatePage(self):
        p = self.p()
        axes = self.axes.currentData()
        for axis in axes:
            if self.scalars[axis].value() == 0.0:
                QMessageBox.warning(self, "Motion",
                                    "%s scalar can't be 0" % axis.upper())
                return False
            if self.use_limits.isChecked() and self.limits[
                    axis + "min"].value() >= self.limits[axis + "max"].value():
                QMessageBox.warning(
                    self, "Motion",
                    "%s soft limit: min must be less than max" % axis.upper())
                return False
        p["hal"] = self.hal.currentData()
        p["port"] = self.port.text().strip() or None
        p["axes"] = axes
        p["backlash"] = self.backlash.value()
        p["limit_switches"] = self.limit_switches.currentData()
        p["scalars"] = {k: w.value() for k, w in self.scalars.items()}
        if self.use_limits.isChecked():
            p["soft_limits"] = {k: w.value() for k, w in self.limits.items()}
        else:
            p["soft_limits"] = None
        p["rc_pre_home"] = text_lines(self.rc_pre_home.toPlainText())
        p["rc_post_home"] = text_lines(self.rc_post_home.toPlainText())
        p["use_wcs_offsets"] = self.use_wcs_offsets.isChecked()
        p["xyz_format6"] = self.xyz_format6.isChecked()
        return True


class KinematicsPage(Page):
    labels = {
        "tsettle_motion_base": "Settle after move (base)",
        "tsettle_motion_na1": "Extra settle per 1.0 NA",
        "tsettle_hdr": "Settle after exposure change (HDR)",
        "tsettle_autofocus": "Settle during autofocus",
    }

    def __init__(self):
        super().__init__()
        self.setTitle("Kinematics")
        self.setSubTitle("How long to wait for vibration to die down before "
                         "taking an image")
        layout = QFormLayout()
        self.conservative = QCheckBox(
            "Conservative (wobbly / lightweight stage): slower but safer")
        self.conservative.toggled.connect(self.conservative_toggled)
        layout.addRow("", self.conservative)
        self.spins = {}
        for k in mf.KINEMATICS_DEFAULT:
            self.spins[k] = double_spin(0.0, 0.0, 30.0, 3, 0.05, " s")
            layout.addRow(self.labels[k], self.spins[k])
        layout.addRow(
            "",
            label("Settle time after a move = base + NA * per NA value. "
                  "High NA objectives have a shallow depth of field and "
                  "show vibration more"))
        layout.addRow("", guess_label())
        self.setLayout(layout)

    def initializePage(self):
        p = self.p()
        for k, w in self.spins.items():
            w.setValue(float(p[k]))
        self.conservative.blockSignals(True)
        self.conservative.setChecked(
            all(p[k] == v for k, v in mf.KINEMATICS_CONSERVATIVE.items()))
        self.conservative.blockSignals(False)

    def conservative_toggled(self, checked):
        values = mf.KINEMATICS_CONSERVATIVE if checked else mf.KINEMATICS_DEFAULT
        for k, w in self.spins.items():
            w.setValue(values[k])

    def validatePage(self):
        p = self.p()
        for k, w in self.spins.items():
            p[k] = w.value()
        return True


class ReviewPage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Review")
        self.setSubTitle("This will be written to microscope.j5")
        self.setButtonText(QWizard.NextButton, "Save")
        layout = QVBoxLayout()
        self.path = label("")
        layout.addWidget(self.path)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.preview.setFont(font)
        layout.addWidget(self.preview)
        self.setLayout(layout)

    def initializePage(self):
        p = self.p()
        self.path.setText("<tt>%s</tt>" %
                          os.path.join(p["out_dir"], "microscope.j5"))
        self.preview.setPlainText(mf.render_j5(p))

    def validatePage(self):
        p = self.p()
        errors = mf.validate_params(p)
        if errors:
            QMessageBox.warning(self, "Config", "\n".join(errors))
            return False
        try:
            self.wizard().saved_fn = mf.write_config(p)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        print("Wrote %s" % (self.wizard().saved_fn, ))
        return True


class DonePage(Page):
    def __init__(self):
        super().__init__()
        self.setTitle("Saved")
        self.setFinalPage(True)
        layout = QVBoxLayout()
        self.text = label("")
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.text)
        layout.addStretch()
        self.setLayout(layout)

    def initializePage(self):
        p = self.p()
        name = p["name"]
        html = "<p>Wrote <tt>%s</tt></p>" % (self.wizard().saved_fn, )
        html += ("<p>Launch Argus with it:</p>"
                 "<pre>./app/argus.py --microscope %s</pre>"
                 "<p>or set <tt>PYUSCOPE_MICROSCOPE=%s</tt></p>" %
                 (name, name))
        if not mf.is_in_configs_dir(name, p["out_dir"]):
            html += ("<p><b>Note:</b> the output directory is not "
                     "<tt>configs/%s</tt> so <tt>--microscope %s</tt> won't "
                     "find it. Copy the directory into <tt>configs/</tt> "
                     "first.</p>" % (name, name))
        html += (
            "<p><b>Next: calibrate.</b> Values written are guesses. In Argus "
            "open <b>Advanced options => Calibration</b> and run the "
            "calibration wizard to measure image scale (um / pixel), "
            "backlash, crop and settle times. Results go in the calibration "
            "store and override microscope.j5.</p>"
            "<p>Also check soft limits against your real stage travel "
            "before moving far.</p>")
        self.text.setText(html)


class MicroforgeWizard(QWizard):
    def __init__(self, params=None, from_template=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("pyuscope: New Microscope Wizard")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnLastPage, True)
        self.setOption(QWizard.NoCancelButtonOnLastPage, True)
        self.params = params if params else mf.default_params()
        # Values came from an existing config: don't replace them with guesses
        self.from_template = from_template
        self.saved_fn = None
        self.intro_page = IntroPage()
        self.identity_page = IdentityPage()
        self.camera_page = CameraPage()
        self.optics_page = OpticsPage()
        self.objectives_page = ObjectivesPage()
        self.motion_page = MotionPage()
        self.kinematics_page = KinematicsPage()
        self.review_page = ReviewPage()
        self.done_page = DonePage()
        for page in (self.intro_page, self.identity_page, self.camera_page,
                     self.optics_page, self.objectives_page, self.motion_page,
                     self.kinematics_page, self.review_page, self.done_page):
            self.addPage(page)
        self.resize(820, 720)


def make_params(name=None, out=None, from_=None):
    params = mf.default_params()
    if from_:
        params = mf.params_from_usj(mf.load_usj(from_), params)
        params["description"] = "Based on %s" % (from_, )
    if name:
        params["name"] = name
    elif from_:
        params["name"] = os.path.basename(os.path.normpath(from_)) + "-new"
    if out:
        params["out_dir"] = os.path.abspath(os.path.expanduser(out))
    return params


def parse_args():
    parser = argparse.ArgumentParser(
        description="New Microscope Wizard: create configs/<name>/microscope.j5"
    )
    parser.add_argument("--name", help="Config name")
    parser.add_argument("--out",
                        help="Output directory (default: configs/<name>)")
    parser.add_argument(
        "--from",
        dest="from_",
        metavar="CONFIG",
        help="Start from an existing config (name in configs/ or directory)")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        params = make_params(name=args.name, out=args.out, from_=args.from_)
    except Exception as e:
        print("Failed to load --from %s: %s" % (args.from_, e))
        sys.exit(1)
    app = QApplication(sys.argv[:1])
    wizard = MicroforgeWizard(params=params, from_template=bool(args.from_))
    wizard.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
