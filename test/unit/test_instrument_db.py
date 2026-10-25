#!/usr/bin/env -S uv run
"""
InstrumentCatalog / InstrumentDB (no microscope, no Qt)
"""

import json
import os
import shutil
import tempfile
import textwrap
import unittest

from uscope.instrument_db import (
    INSTRUMENTS_SCHEMA_VERSION,
    InstrumentCatalog,
    InstrumentDB,
    default_script_dirs,
)

INSTRUMENT_PY = """
from uscope.scripting import InstrumentScriptPlugin

INSTRUMENT = {
    "title": "My laser",
    "description": "Pew pew",
    "parameters": {"power": 1.0},
}


class Plugin(InstrumentScriptPlugin):
    pass
"""

SCRIPT_PY = """
from uscope.scripting import ScriptingPlugin


class Plugin(ScriptingPlugin):
    pass
"""


class TestInstrumentDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pyuscope-test-instruments-")
        self.kitchen = os.path.join(self.tmp, "kitchen")
        os.makedirs(os.path.join(self.kitchen, "instrument"))
        self.write("laser.py", INSTRUMENT_PY)
        self.write("not_instrument.py", SCRIPT_PY)
        self.write("broken.py", "class Plugin(:\n")
        self.write("_private.py", INSTRUMENT_PY)
        dirs = default_script_dirs()
        dirs["kitchen"] = self.kitchen
        self.catalog = InstrumentCatalog(dirs)
        self.fn = os.path.join(self.tmp, "microscope", "instruments.j5")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, basename, text):
        with open(os.path.join(self.kitchen, "instrument", basename),
                  "w") as f:
            f.write(textwrap.dedent(text))

    def db(self):
        return InstrumentDB(self.fn, catalog=self.catalog)

    def entry(self, **kwargs):
        ret = {"name": "laser", "plugin": "kitchen:instrument/laser.py"}
        ret.update(kwargs)
        return ret

    def test_catalog(self):
        pwm = self.catalog.get("uscope:instrument/grbl_pwm.py")
        self.assertIsNotNone(pwm)
        self.assertIsNone(pwm["problem"])
        self.assertEqual(pwm["title"], "GRBL PWM")
        self.assertIn("startup_onoff", pwm["parameters"])

        laser = self.catalog.get("kitchen:instrument/laser.py")
        self.assertEqual(laser["title"], "My laser")
        self.assertEqual(laser["parameters"], {"power": 1.0})

        broken = self.catalog.get("kitchen:instrument/broken.py")
        self.assertIn("failed to parse", broken["problem"])
        self.assertIsNone(
            self.catalog.get("kitchen:instrument/not_instrument.py"))
        self.assertIsNone(self.catalog.get("kitchen:instrument/_private.py"))

    def test_round_trip(self):
        db = self.db()
        db.add(self.entry(parameters={"power": 2.0}))
        db.add(self.entry(name="laser_2", tab_name="Laser 2"))
        db2 = self.db()
        self.assertEqual(db2.problems, [])
        self.assertEqual(list(db2.instances), ["laser", "laser_2"])
        laser = db2.instances["laser"]
        self.assertEqual(laser["parameters"], {"power": 2.0})
        self.assertTrue(laser["enabled"])
        self.assertTrue(laser["visible"])
        self.assertEqual(laser["tab_name"], "laser")
        self.assertEqual(db2.resolve_path(laser),
                         os.path.join(self.kitchen, "instrument", "laser.py"))

    def test_resolve_fallback(self):
        db = self.db()
        entry = db.validate(
            self.entry(plugin="gone:instrument/x.py",
                       plugin_path="/nonexistent/x.py"))
        self.assertEqual(db.resolve_path(entry), "/nonexistent/x.py")

    def test_validate(self):
        db = self.db()
        db.add(self.entry(tab_name="Laser"))
        bad = (
            self.entry(name="argus"),
            self.entry(name="session"),
            self.entry(name="has space"),
            self.entry(name=""),
            # Duplicate name
            self.entry(tab_name="Other"),
            # Duplicate tab name
            self.entry(name="laser_2", tab_name="Laser"),
            self.entry(name="nope", plugin=None),
            self.entry(name="badparams", parameters=[]),
        )
        for entry in bad:
            with self.assertRaises(ValueError, msg=str(entry)):
                db.validate(entry)
        with self.assertRaises(ValueError):
            db.validate(self.entry(name="laser_3", tab_name="Main"),
                        reserved_tab_names=["Main"])
        # Editing an instance doesn't conflict with itself
        db.update("laser", self.entry(tab_name="Laser", visible=False))
        self.assertFalse(db.instances["laser"]["visible"])
        # Rename keeps position
        db.add(self.entry(name="z", tab_name="Z"))
        db.update("laser", self.entry(name="a", tab_name="Laser"))
        self.assertEqual(list(db.instances), ["a", "z"])

    def test_parameter_warnings(self):
        db = self.db()
        entry = db.validate(self.entry(parameters={"powr": 1}))
        self.assertEqual(db.parameter_warnings(entry),
                         ["Unknown parameter powr"])
        self.assertEqual(
            db.parameter_warnings(
                db.validate(self.entry(parameters={"power": 1}))), [])

    def test_unique_name(self):
        db = self.db()
        self.assertEqual(db.unique_name("laser"), "laser")
        db.add(self.entry())
        self.assertEqual(db.unique_name("laser"), "laser_2")
        db.add(self.entry(name="laser_2", tab_name="2"))
        self.assertEqual(db.unique_name("laser"), "laser_3")
        self.assertEqual(db.unique_name("argus"), "argus_1")

    def test_remove(self):
        db = self.db()
        db.add(self.entry())
        self.assertTrue(db.remove("laser"))
        self.assertFalse(db.remove("laser"))
        self.assertEqual(self.db().instances, {})

    def test_newer_schema(self):
        os.makedirs(os.path.dirname(self.fn))
        with open(self.fn, "w") as f:
            json.dump(
                {
                    "schema_version": INSTRUMENTS_SCHEMA_VERSION + 1,
                    "instances": []
                }, f)
        db = self.db()
        self.assertEqual(len(db.problems), 1)
        self.assertTrue(db.read_only)
        with self.assertRaises(ValueError):
            db.add(self.entry())
        # Untouched
        with open(self.fn) as f:
            self.assertEqual(
                json.load(f)["schema_version"], INSTRUMENTS_SCHEMA_VERSION + 1)

    def test_bad_entry_skipped(self):
        os.makedirs(os.path.dirname(self.fn))
        with open(self.fn, "w") as f:
            json.dump(
                {
                    "schema_version":
                    1,
                    "instances": [
                        self.entry(),
                        self.entry(name="argus", tab_name="x"),
                    ]
                }, f)
        db = self.db()
        self.assertEqual(list(db.instances), ["laser"])
        self.assertEqual(len(db.problems), 1)
        self.assertFalse(db.read_only)


if __name__ == "__main__":
    unittest.main()
