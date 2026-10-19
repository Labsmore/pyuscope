#!/usr/bin/env python3

import matplotlib.pyplot as plt  # noqa: F401

from uscope.gui.scripting import ArgusScriptingPlugin
from uscope.script import motion_backlash_xy
from uscope.script import motion_repeat_xy
from uscope.script import imager_scale
from uscope.script import imager_ff
from uscope.calibration import find_centroid
from PIL import ImageStat


def analyze(log=None, microscope=None):
    if log is None:
        log = print
        pass
    motion_backlash_xy.analyze(log=log, microscope=microscope)
    motion_repeat_xy.analyze(log=log, microscope=microscope)
    imager_scale.analyze(log=log, microscope=microscope)
    imager_ff.analyze(log=log, microscope=microscope)


class Plugin(ArgusScriptingPlugin):
    def input_config(self):
        return {
            # Collect a single image and throw thresholding
            "Sample centroid": {
                "widget": "QPushButton",
                "value": "sample_centroid",
            },
            "Sample brightness": {
                "widget": "QPushButton",
                "value": "sample_brightness",
            },
            "Collect fiducial": {
                "widget": "QPushButton",
                "value": "collect_fiducial",
            },
            "Collect fiducial (quick)": {
                "widget": "QPushButton",
                "value": "collect_fiducial_quick",
            },
            "Collect ff": {
                "widget": "QPushButton",
                "value": "collect_ff",
            },
            "Collect ff (quick)": {
                "widget": "QPushButton",
                "value": "collect_ff_quick",
            },
            "Analyze": {
                "widget": "QPushButton",
                "value": "analyze",
            },
        }

    def show_run_button(self):
        return False

    def collect_fiducial(self, quick=False):
        objective_config = self.get_objective_config()
        self.log("Using objective %s" % objective_config["name"])

        if not quick:
            self.log("Sleeping. Clear the area!")
            self.sleep(5)
            test = "collect"
        else:
            test = "collect_quick"

        self.log("")
        self.log("")
        self.log("")
        self.run_plugin(motion_backlash_xy, button_value=test)
        self.run_plugin(motion_backlash_xy, button_value="analyze")
        self.log("")
        self.log("")
        self.log("")
        self.run_plugin(motion_repeat_xy, button_value=test)
        self.run_plugin(motion_repeat_xy, button_value="analyze")
        self.log("")
        self.log("")
        self.log("")
        self.run_plugin(imager_scale, button_value=test)
        self.run_plugin(imager_scale, button_value="analyze")

    def collect_ff(self, quick=False):
        objective_config = self.get_objective_config()
        self.log("Using objective %s" % objective_config["name"])

        if not quick:
            self.log("Sleeping. Clear the area!")
            self.sleep(5)
            test = "collect"
        else:
            test = "collect_quick"

        self.log("")
        self.log("")
        self.log("")
        self.run_plugin(imager_ff, button_value=test)
        self.run_plugin(imager_ff, button_value="analyze")

    def sample_brightness(self):
        im = self.image()

        assert im
        im = im.convert('L')
        assert im
        stat = ImageStat.Stat(im)
        self.log("Average pixel value: %0.1f" % stat.mean[0])

    def sample_centroid(self):
        im = self.image()

        try:
            x, y = find_centroid(im, show=True)
            self.log(f"Centroid at x={x}, y={y}")
        except ValueError:
            self.log("Failed to find centroid :(")

    def cleanup(self):
        pass

    def run_test(self):
        self.origin = None
        vals = self.get_input()
        button = vals.get("button")

        task = button["value"]
        if task == "collect_fiducial":
            self.collect_fiducial()
        elif task == "collect_fiducial_quick":
            self.collect_fiducial(quick=True)
        elif task == "collect_ff":
            self.collect_ff()
        elif task == "collect_ff_quick":
            self.collect_ff(quick=True)
        elif task == "sample_centroid":
            self.sample_centroid()
        elif task == "sample_brightness":
            self.sample_brightness()
        elif task == "analyze":
            analyze(log=self.log)
        else:
            assert 0, task


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run test")
    _args = parser.parse_args()

    analyze()


if __name__ == "__main__":
    main()
