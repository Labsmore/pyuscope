#!/usr/bin/env -S uv run
"""Objectives page (File => Advanced options => Objectives), offscreen"""

import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="pyuscope-objectives-gui-")
os.environ["PYUSCOPE_DATA_DIR"] = _TMP
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QInputDialog, QMessageBox, QWidget

from uscope import config as uconfig
from uscope.cal.store import CalEntry
from uscope.gui import objectives as gui_objectives
from uscope.gui.objectives import ObjectivesTab
from uscope.microscope import Microscope

app = QApplication.instance() or QApplication([])


class FakeAC:
    def __init__(self, microscope):
        self.microscope = microscope
        self.logs = []

    def initUI(self):
        pass

    def log(self, msg):
        self.logs.append(msg)


class Host(QWidget):
    def add_awidget(self, name, awidget):
        pass


class TestObjectivesGui(unittest.TestCase):
    def setUp(self):
        uconfig.usc = None
        self.m = Microscope(name="mock-grbl",
                            log=lambda *a, **k: None,
                            make_imager=False,
                            hardware=False)
        self.db = self.m.usc.bc.objective_db
        if os.path.exists(self.db.user_fn):
            os.unlink(self.db.user_fn)
        self.db.load_user()
        self.m.cal_store._user = []
        self.m.cal_store._factory = []
        self.m.reload_objectives()
        self.answers = []
        QMessageBox.question = staticmethod(
            lambda *a, **k: self.answers.pop(0)
            if self.answers else QMessageBox.Yes)
        QMessageBox.warning = staticmethod(
            lambda _parent, _title, msg: self.fail("warning: " + msg))
        self.host = Host()
        self.tab = ObjectivesTab(ac=FakeAC(self.m), parent=self.host)
        self.tab.initUI()
        self.tab.post_ui_init()

    def names(self):
        return self.m.objective_state.names()

    def select_library(self, vendor, model):
        k = uconfig.objective_key(vendor, model)
        tree = self.tab.library_tree
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                if tuple(item.data(0, 0x0100)) == k:
                    tree.setCurrentItem(item)
                    return
        self.fail("not in library: %s %s" % (vendor, model))

    def test_defaults_shown(self):
        self.assertEqual(self.tab.names(), ["5X", "10X", "20X"])
        self.assertEqual(self.tab.lineup_table.rowCount(), 3)
        self.assertIn("microscope defaults", self.tab.status_l.text())
        self.assertFalse(self.tab.apply_pb.isEnabled())

    def test_add_remove_apply_reset(self):
        self.select_library("Mock", "100X Oil")
        self.tab.library_add_to_lineup()
        self.tab.lineup_table.selectRow(0)
        self.tab.lineup_remove()
        self.assertTrue(self.tab.apply_pb.isEnabled())
        self.tab.lineup_apply()
        self.assertEqual(self.names(), ["10X", "20X", "100X"])
        self.assertIn("your objective lineup", self.tab.status_l.text())
        self.tab.lineup_reset()
        self.assertEqual(self.names(), ["5X", "10X", "20X"])

    def test_duplicate_name_prompts(self):
        QInputDialog.getText = staticmethod(lambda *a, **k:
                                            ("Second 10X", True))
        self.select_library("Mock", "10X")
        self.tab.library_add_to_lineup()
        self.assertEqual(self.tab.names()[-1], "Second 10X")
        self.tab.lineup_apply()
        self.assertEqual(self.names(), ["5X", "10X", "20X", "Second 10X"])

    def test_rename_moves_calibration(self):
        store = self.m.cal_store
        store.set(CalEntry("optics.z_offset", 0.1,
                           scope={"objective": "10X"}))
        QInputDialog.getText = staticmethod(lambda *a, **k:
                                            ("Mock 10X", True))
        self.tab.lineup_table.selectRow(1)
        self.tab.lineup_rename()
        self.tab.lineup_apply()
        self.assertEqual(self.names(), ["5X", "Mock 10X", "20X"])
        self.assertIsNotNone(
            store.find_exact("optics.z_offset", {"objective": "Mock 10X"}))

    def test_custom_objective_and_export(self):
        self.tab.save_library_entry(
            uconfig.validate_objective_entry({
                "vendor": "Acme",
                "model": "AC-7",
                "magnification": 7,
                "na": 0.2
            }))
        self.assertTrue(self.db.is_user("Acme", "AC-7"))
        self.select_library("Acme", "AC-7")
        self.tab.library_add_to_lineup()
        self.tab.lineup_apply()
        self.assertEqual(self.names()[-1], "7X")
        captured = []
        gui_objectives.ObjectiveExportDialog.exec_ = lambda dialog: captured.append(dialog)
        self.select_library("Acme", "AC-7")
        self.tab.export()
        dialog = captured[0]
        labels = [label for label, _text in dialog.sources]
        self.assertTrue(labels[0].startswith("Selected: Acme AC-7"))
        selected = json.loads(dialog.text.toPlainText())
        self.assertEqual(selected["objectives"][0]["model"], "AC-7")
        lineup = json.loads(dialog.sources[-1][1])
        self.assertIn("vendor: Acme, model: AC-7", lineup["objectives_db"])
        dialog.copy()
        self.assertIn("AC-7", QApplication.clipboard().text())
        # Deleting a lineup objective falls back, never crashes
        self.tab.library_delete()
        self.assertEqual(self.names(), ["5X", "10X", "20X"])
        self.assertIn("skipped", self.tab.status_l.text())
        shot = os.getenv("OBJECTIVES_SCREENSHOT")
        if shot:
            self.tab.resize(1100, 600)
            self.tab.grab().save(shot)
            dialog.resize(650, 550)
            dialog.grab().save(shot.replace(".png", "_export.png"))


if __name__ == "__main__":
    unittest.main()
