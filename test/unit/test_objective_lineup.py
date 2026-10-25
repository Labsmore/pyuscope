#!/usr/bin/env -S uv run
"""User objective library (per install) and objective lineups"""

import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="pyuscope-objectives-")
os.environ["PYUSCOPE_DATA_DIR"] = _TMP
os.environ["PYUSCOPE_MICROSCOPE"] = "mock-grbl"

from uscope import config as uconfig
from uscope.cal.params import rows
from uscope.cal.store import CalEntry
from uscope.microscope import Microscope

CUSTOM = {
    "vendor": "Acme",
    "model": "AC-7",
    "series": "Plan",
    "magnification": 7,
    "na": 0.2,
    "wd": 15.5,
}


class TestObjectiveLineup(unittest.TestCase):
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
        store = self.m.cal_store
        store._user = []
        store._factory = []
        self.m.reload_objectives()

    def names(self):
        return self.m.objective_state.names()

    def test_user_file_per_install(self):
        self.assertEqual(os.path.dirname(self.db.user_fn),
                         self.m.usc.bc.get_data_dir())

    def test_add_user_entry(self):
        self.db.set_user_entry(CUSTOM)
        self.assertTrue(self.db.is_user("acme", "ac-7"))
        self.assertEqual(self.db.get("ACME", "ac-7")["wd"], 15.5)
        db2 = uconfig.ObjectiveDB(user_fn=self.db.user_fn)
        self.assertEqual(db2.get("Acme", "AC-7")["series"], "Plan")
        self.assertTrue(self.db.remove_user_entry("Acme", "AC-7"))
        self.assertIsNone(self.db.find("Acme", "AC-7"))

    def test_user_overrides_shipped(self):
        shipped = dict(self.db.get("Mock", "10X"))
        self.db.set_user_entry(dict(shipped, na=0.3))
        self.assertEqual(self.db.get("Mock", "10X")["na"], 0.3)
        self.assertEqual(self.db.shipped_entry("Mock", "10X")["na"], 0.2)
        # Shipped config db_find consistency check yields to the user value
        resolved = self.db.resolve(
            {"db_find": "vendor: Mock, model: 10X, na: 0.2"})
        self.assertEqual(resolved["na"], 0.3)
        self.m.reload_objectives()
        self.assertEqual(self.m.objective_state.full_config()["10X"]["na"],
                         0.3)
        self.db.remove_user_entry("Mock", "10X")
        self.assertEqual(self.db.get("Mock", "10X")["na"], 0.2)

    def test_validate(self):
        bad = [
            dict(CUSTOM, magnification=0),
            dict(CUSTOM, na=2.0),
            dict(CUSTOM, na=None),
            dict(CUSTOM, model=""),
            dict(CUSTOM, vendor="A, B"),
        ]
        for entry in bad:
            with self.assertRaises(ValueError, msg=entry):
                uconfig.validate_objective_entry(entry)
        with self.assertRaises(ValueError):
            uconfig.validate_objective_entry(
                {
                    "vendor": "X",
                    "magnification": 5,
                    "na": 0.1
                },
                catalog=False)
        inline = uconfig.validate_objective_entry(
            {
                "name": "Zoom 2X",
                "magnification": "2",
                "na": "0.05"
            },
            catalog=False)
        self.assertEqual(inline["magnification"], 2)

    def test_tolerant_user_file(self):
        with open(self.db.user_fn, "w") as f:
            json.dump(
                {
                    "objectives": [CUSTOM, {
                        "vendor": "Bad",
                        "model": "B",
                        "magnification": -1,
                        "na": 0.1
                    }]
                }, f)
        self.db.load_user()
        self.assertTrue(self.db.is_user("Acme", "AC-7"))
        self.assertFalse(self.db.is_user("Bad", "B"))
        self.assertEqual(len(self.db.user_problems), 1)
        with open(self.db.user_fn, "w") as f:
            f.write("{not json")
        self.db.load_user()
        self.assertEqual(len(self.db.user), 0)
        self.assertTrue(self.db.user_problems)
        # Shipped entries still work
        self.assertIsNotNone(self.db.find("Mock", "10X"))

    def test_lineup_replaces_defaults(self):
        self.assertEqual(self.names(), ["5X", "10X", "20X"])
        self.db.set_user_entry(CUSTOM)
        objectives = self.m.get_objectives()
        self.db.set_lineup("mock-grbl", [
            {
                "db_find": "vendor: Mock, model: 20X"
            },
            {
                "db_find": uconfig.objective_db_find(CUSTOM)
            },
            {
                "name": "Zoom 2X",
                "magnification": 2,
                "na": 0.05
            },
        ])
        self.m.reload_objectives()
        self.assertEqual(self.names(), ["20X", "Plan 7X", "Zoom 2X"])
        # Rebuilt in place: webserver etc keep their reference
        self.assertIs(self.m.get_objectives(), objectives)
        self.assertTrue(objectives.uses_lineup())
        self.assertIn("um_per_pixel",
                      self.m.objective_state.full_config()["Plan 7X"])
        # Per install file, keyed by microscope
        db2 = uconfig.ObjectiveDB(user_fn=self.db.user_fn)
        self.assertEqual(len(db2.get_lineup("mock-grbl")), 3)
        self.assertIsNone(db2.get_lineup("other-scope"))
        self.db.clear_lineup("mock-grbl")
        self.m.reload_objectives()
        self.assertEqual(self.names(), ["5X", "10X", "20X"])
        self.assertFalse(objectives.uses_lineup())

    def test_lineup_selection_kept(self):
        self.m.objective_state.select("20X", parfocal=False)
        self.db.set_lineup("mock-grbl",
                           [{
                               "db_find": "vendor: Mock, model: 5X"
                           }, {
                               "db_find": "vendor: Mock, model: 20X"
                           }])
        self.m.reload_objectives()
        self.assertEqual(self.m.objective_state.name(), "20X")
        self.db.set_lineup("mock-grbl",
                           [{
                               "db_find": "vendor: Mock, model: 5X"
                           }])
        self.m.reload_objectives()
        self.assertEqual(self.m.objective_state.name(), "5X")

    def test_lineup_missing_entry_skipped(self):
        self.db.set_lineup("mock-grbl", [
            {
                "db_find": "vendor: Gone, model: X1"
            },
            {
                "db_find": "vendor: Mock, model: 10X"
            },
        ])
        self.m.reload_objectives()
        self.assertEqual(self.names(), ["10X"])
        self.db.set_lineup("mock-grbl",
                           [{
                               "db_find": "vendor: Gone, model: X1"
                           }])
        self.m.reload_objectives()
        self.assertEqual(self.names(), ["5X", "10X", "20X"])

    def test_lineup_duplicate_names_fall_back(self):
        self.db.set_lineup("mock-grbl", [
            {
                "db_find": "vendor: Mock, model: 10X"
            },
            {
                "name": "10X",
                "magnification": 10,
                "na": 0.25
            },
        ])
        self.m.reload_objectives()
        objectives = self.m.get_objectives()
        self.assertEqual(self.names(), ["5X", "10X", "20X"])
        self.assertFalse(objectives.uses_lineup())
        self.assertIn("duplicated", objectives.lineup_error)

    def test_export_round_trip(self):
        self.db.set_user_entry(CUSTOM)
        text = uconfig.objectives_export_json(self.db.user.values())
        j = json.loads(text)
        self.assertEqual(list(j["objectives"][0].keys())[:4],
                         ["vendor", "model", "series", "magnification"])
        fn = os.path.join(_TMP, "export.j5")
        with open(fn, "w") as f:
            f.write(text)
        db2 = uconfig.ObjectiveDB(fn=fn)
        self.assertEqual(db2.get("Acme", "AC-7")["wd"], 15.5)

    def test_rename_moves_calibration(self):
        store = self.m.cal_store
        store.set(CalEntry("optics.z_offset", 0.1, scope={"objective":
                                                           "10X"}),
                  save=False)
        self.assertEqual(store.rename_objective("10X", "Mock 10X"), 1)
        self.assertIsNone(store.find_exact("optics.z_offset",
                                           {"objective": "10X"}))
        self.assertIsNotNone(
            store.find_exact("optics.z_offset", {"objective": "Mock 10X"}))
        # Not in the lineup => flagged
        row = [
            r for r in rows(self.m) if r.key == "optics.z_offset"
            and r.scope.get("objective") == "Mock 10X"
        ][0]
        self.assertTrue(row.orphaned())
        self.assertIn("not in lineup", row.status())


if __name__ == "__main__":
    unittest.main()
