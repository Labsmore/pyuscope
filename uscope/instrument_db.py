"""
Instruments: InstrumentScriptPlugin scripts that run for the life of the GUI

InstrumentCatalog: instrument plugins available in the script dirs
InstrumentDB: instances configured for this microscope
(data/microscopes/<dataname>/instruments.j5)
"""

from uscope.util import readj

from collections import OrderedDict
import ast
import copy
import json
import os
import re

INSTRUMENTS_SCHEMA_VERSION = 1
# Subsystems registered by Argus / Session
RESERVED_NAMES = ("argus", "session")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_script_dirs(bc=None):
    """
    Same set of dirs offered by the scripting tab
    """
    ret = OrderedDict()
    ret["uscope"] = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "script")
    if bc is not None:
        ret.update(bc.script_dirs())
    return ret


def parse_instrument_file(fn):
    """
    Statically inspect a plugin file (no import => no side effects)
    Returns a dict or None if this isn't an instrument plugin
    Raises on syntax error
    """
    with open(fn) as f:
        tree = ast.parse(f.read(), filename=fn)
    is_instrument = False
    meta = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Plugin":
            for base in node.bases:
                name = getattr(base, "id", None) or getattr(base, "attr", None)
                if name == "InstrumentScriptPlugin":
                    is_instrument = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == "INSTRUMENT":
                    meta = ast.literal_eval(node.value)
    if not is_instrument:
        return None
    if not isinstance(meta, dict):
        raise ValueError("INSTRUMENT must be a dict")
    stem = os.path.splitext(os.path.basename(fn))[0]
    parameters = meta.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("INSTRUMENT parameters must be a dict")
    return {
        "title": meta.get("title", stem),
        "description": meta.get("description",
                                ast.get_docstring(tree) or ""),
        "parameters": parameters,
    }


class InstrumentCatalog:
    """
    Instrument plugins found in <script dir>/instrument/*.py
    """

    def __init__(self, script_dirs):
        self.script_dirs = OrderedDict(script_dirs)
        self.entries = OrderedDict()
        self.scan()

    def scan(self):
        self.entries = OrderedDict()
        for source, directory in self.script_dirs.items():
            idir = os.path.join(directory, "instrument")
            if not os.path.isdir(idir):
                continue
            for basename in sorted(os.listdir(idir)):
                if not basename.endswith(".py") or basename.startswith("_"):
                    continue
                fn = os.path.join(idir, basename)
                entry = {
                    "id": "%s:instrument/%s" % (source, basename),
                    "source": source,
                    "path": os.path.abspath(fn),
                    "title": os.path.splitext(basename)[0],
                    "description": "",
                    "parameters": {},
                    "problem": None,
                }
                try:
                    meta = parse_instrument_file(fn)
                except Exception as e:
                    entry["problem"] = "failed to parse: %s" % (e, )
                    self.entries[entry["id"]] = entry
                    continue
                if meta is None:
                    continue
                entry.update(meta)
                self.entries[entry["id"]] = entry

    def get(self, plugin_id):
        return self.entries.get(plugin_id)

    def find_path(self, path):
        path = os.path.abspath(path)
        for entry in self.entries.values():
            if entry["path"] == path:
                return entry
        return None


class InstrumentDB:
    """
    Instrument instances for one microscope
    """

    def __init__(self, fn, catalog=None):
        self.fn = fn
        self.catalog = catalog
        self.instances = OrderedDict()
        # Problems found loading the file
        self.problems = []
        # Don't overwrite a file we couldn't understand
        self.read_only = False
        self.load()

    @staticmethod
    def for_microscope(microscope, catalog=None):
        if catalog is None:
            catalog = InstrumentCatalog(default_script_dirs(microscope.bc))
        fn = os.path.join(microscope.usc.get_microscope_data_dir(),
                          "instruments.j5")
        return InstrumentDB(fn, catalog=catalog)

    def _problem(self, msg):
        msg = "%s: %s" % (self.fn, msg)
        print("WARNING: instruments: " + msg)
        self.problems.append(msg)

    def load(self):
        self.instances = OrderedDict()
        self.problems = []
        self.read_only = False
        if not os.path.exists(self.fn):
            return
        try:
            j = readj(self.fn)
            if j.get("schema_version", 1) > INSTRUMENTS_SCHEMA_VERSION:
                raise ValueError("written by a newer pyuscope")
            for entry in j.get("instances") or []:
                try:
                    entry = self.validate(entry)
                except ValueError as e:
                    self._problem("skipped instrument %s: %s" %
                                  (entry.get("name"), e))
                    continue
                self.instances[entry["name"]] = entry
        except Exception as e:
            self.instances = OrderedDict()
            self.read_only = True
            self._problem("failed to load: %s" % (e, ))

    def save(self):
        if self.read_only:
            raise ValueError("Refusing to overwrite %s: fix or remove it" %
                             (self.fn, ))
        j = OrderedDict()
        j["schema_version"] = INSTRUMENTS_SCHEMA_VERSION
        j["instances"] = list(self.instances.values())
        parent = os.path.dirname(self.fn)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        tmp = self.fn + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(j, indent=4, separators=(",", ": ")) + "\n")
        os.replace(tmp, self.fn)

    def validate(self, entry, replacing=None, reserved_tab_names=()):
        """
        Return a normalized copy of entry or raise ValueError
        replacing: name of the instance being edited (excluded from uniqueness)
        """
        entry = copy.deepcopy(dict(entry))
        name = entry.get("name")
        if not name or not NAME_RE.match(str(name)):
            raise ValueError("Name must be letters, digits, _ or -")
        if name in RESERVED_NAMES:
            raise ValueError("Name %s is reserved" % (name, ))
        if not entry.get("plugin") and not entry.get("plugin_path"):
            raise ValueError("No plugin")
        entry.setdefault("tab_name", name)
        if not entry["tab_name"]:
            raise ValueError("Tab name required")
        if entry["tab_name"] in reserved_tab_names:
            raise ValueError("Tab name %s is already used" %
                             (entry["tab_name"], ))
        entry.setdefault("visible", True)
        entry.setdefault("enabled", True)
        entry.setdefault("parameters", {})
        if not isinstance(entry["parameters"], dict):
            raise ValueError("Parameters must be a dict")
        for other in self.instances.values():
            if other["name"] in (name, replacing):
                continue
            if other["tab_name"] == entry["tab_name"]:
                raise ValueError("Tab name %s is already used by %s" %
                                 (entry["tab_name"], other["name"]))
        if name != replacing and name in self.instances:
            raise ValueError("Name %s is already used" % (name, ))
        return entry

    def parameter_warnings(self, entry):
        """
        Parameters the plugin doesn't declare (likely typos)
        """
        catalog_entry = self.catalog_entry(entry)
        if not catalog_entry or not catalog_entry["parameters"]:
            return []
        return [
            "Unknown parameter %s" % (k, ) for k in entry["parameters"]
            if k not in catalog_entry["parameters"]
        ]

    def catalog_entry(self, entry):
        if self.catalog is None:
            return None
        ret = None
        if entry.get("plugin"):
            ret = self.catalog.get(entry["plugin"])
        if ret is None and entry.get("plugin_path"):
            ret = self.catalog.find_path(entry["plugin_path"])
        return ret

    def resolve_path(self, entry):
        """
        Plugin file to load: catalog id first, then the saved path
        """
        catalog_entry = self.catalog_entry(entry)
        if catalog_entry:
            return catalog_entry["path"]
        return entry.get("plugin_path")

    def unique_name(self, base):
        base = re.sub(r"[^A-Za-z0-9_-]", "_", base) or "instrument"
        if base in RESERVED_NAMES:
            base += "_1"
        name = base
        i = 2
        while name in self.instances:
            name = "%s_%u" % (base, i)
            i += 1
        return name

    def add(self, entry, reserved_tab_names=(), save=True):
        entry = self.validate(entry, reserved_tab_names=reserved_tab_names)
        self.instances[entry["name"]] = entry
        if save:
            self.save()
        return entry

    def update(self, name, entry, reserved_tab_names=(), save=True):
        """
        Replace instance name (entry may rename it), keeping its position
        """
        if name not in self.instances:
            raise ValueError("No instrument %s" % (name, ))
        entry = self.validate(entry,
                              replacing=name,
                              reserved_tab_names=reserved_tab_names)
        self.instances = OrderedDict(
            (entry["name"], entry) if k == name else (k, v)
            for k, v in self.instances.items())
        if save:
            self.save()
        return entry

    def remove(self, name, save=True):
        if self.instances.pop(name, None) is None:
            return False
        if save:
            self.save()
        return True
