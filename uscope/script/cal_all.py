#!/usr/bin/env python3
"""Thin scripting shim around the uscope.cal framework.

The Calibration tab is the preferred UI. This plugin remains so existing
muscle memory (Scripting → cal_all.py) still works.
"""

from uscope.scripting import ScriptingPlugin
from uscope.cal.analysis.centroid import find_centroid
from uscope.cal.base import Quality
from uscope.cal.session import CalSession
from uscope.cal.context import CalContext
from uscope.cal.report import write_report
from PIL import ImageStat

DEFAULT_SET = ("scale", "backlash_xy", "repeat_xy", "dark_frame", "flat_field")


class Plugin(ScriptingPlugin):
    def input_config(self):
        return {
            "Sample centroid": {
                "widget": "QPushButton",
                "value": "sample_centroid",
            },
            "Sample brightness": {
                "widget": "QPushButton",
                "value": "sample_brightness",
            },
            "Collect (quick)": {
                "widget": "QPushButton",
                "value": "collect_quick",
            },
            "Collect (full)": {
                "widget": "QPushButton",
                "value": "collect_full",
            },
            "Analyze last session": {
                "widget": "QPushButton",
                "value": "analyze",
            },
        }

    def show_run_button(self):
        return False

    def sample_brightness(self):
        im = self.image()
        im = im.convert("L")
        stat = ImageStat.Stat(im)
        self.log("Average pixel value: %0.1f" % stat.mean[0])

    def sample_centroid(self):
        im = self.image()
        try:
            x, y = find_centroid(im, show=self.show_image)
            self.log("Centroid at x=%0.2f, y=%0.2f" % (x, y))
        except ValueError:
            self.log("Failed to find centroid :(")

    def _run_set(self, quality):
        ctx = CalContext(plugin=self, quality=quality)
        session = CalSession(self.session.microscope, DEFAULT_SET, quality=quality)
        self.log("Session %s" % session.path)
        session.run_collect_analyze(ctx)
        write_report(session)
        for name, result in session.results.items():
            self.log("%s: %s %s" %
                     (name, "PASS" if result.ok else "FAIL", result.message))

    def run_test(self):
        vals = self.get_input()
        button = vals.get("button")
        task = button["value"]
        if task == "sample_centroid":
            self.sample_centroid()
        elif task == "sample_brightness":
            self.sample_brightness()
        elif task == "collect_quick":
            self._run_set(Quality.QUICK)
        elif task == "collect_full":
            self._run_set(Quality.FULL)
        elif task == "analyze":
            from uscope.cal.base import session_root
            import os
            root = session_root(self.session.microscope)
            sessions = sorted(
                [os.path.join(root, n) for n in os.listdir(root)
                 if os.path.isdir(os.path.join(root, n))])
            if not sessions:
                self.log("No sessions in %s" % root)
                return
            path = sessions[-1]
            self.log("Analyzing %s" % path)
            from uscope.cal.registry import names as cal_names
            from uscope.cal.dataset import Dataset
            session = CalSession(self.session.microscope, [], path=path)
            for name in cal_names():
                d = os.path.join(path, name)
                if os.path.exists(os.path.join(d, "manifest.json")):
                    session.names.append(name)
                    session.datasets[name] = Dataset.load(d)
                    result = session.analyze_one(name, log=self.log)
                    self.log("%s: %s" % (name, result.message))
            write_report(session)
        else:
            assert 0, task
