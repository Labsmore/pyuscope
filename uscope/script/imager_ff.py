#!/usr/bin/env python3

from uscope.gui.scripting import ArgusScriptingPlugin
from uscope.util import datetime_file_str, LogToFile

import numpy as np
import time
import os
import json
import glob
from PIL import Image, ImageStat
import subprocess


def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5


def savej(j, out_dir):
    open("%s/log.json" % (out_dir, ), "w").write(
        json.dumps(j, sort_keys=True, indent=4, separators=(',', ': ')))


def npf2im(statef):
    #return statef, None
    rounded = np.round(statef)
    #print("row1: %s" % rounded[1])
    statei = np.array(rounded, dtype=np.uint16)
    #print(len(statei), len(statei[0]), len(statei[0]))
    height = len(statef)
    width = len(statef[0])

    # for some reason I isn't working correctly
    # only L
    # workaround by plotting manually
    im = Image.new("RGB", (width, height), "Black")
    for y, row in enumerate(statei):
        for x, val in enumerate(row):
            # this causes really weird issues if not done
            val = tuple(int(x) for x in val)
            im.putpixel((x, y), val)

    return im


def average_imgs(imgs, scalar=None):
    width, height = imgs[0].size
    if not scalar:
        scalar = 1.0
    scalar = scalar / len(imgs)

    statef = np.zeros((height, width, 3), float)
    for im in imgs:
        assert (width, height) == im.size
        statef = statef + scalar * np.array(im, dtype=float)

    return statef, npf2im(statef)


def average_files(files, images=0, verbose=1, scalar=None):
    imgs = []

    for fni, fn in enumerate(files):
        imgs.append(Image.open(fn))
        if images and fni + 1 >= images:
            verbose and print("WARNING: only using first %u images" % images)
            break
    return average_imgs(imgs, scalar=scalar)


def histeq_im(fn_in, fn_out):
    subprocess.check_call([
        "convert", fn_in, '(', '+clone', '-equalize', ')', '-average', fn_out
    ])


def analyze(in_dir=None, log=None, verbose=False):
    if log is None:
        log = print

    if not in_dir:
        in_dir = sorted(glob.glob("data/script/*imager-ff_*"))[-1]

    log("Loading from %s" % in_dir)
    with LogToFile(os.path.join(in_dir, "analysis.txt"), log=log) as log:
        dir_out = in_dir

        log("")
        files = list(glob.glob(os.path.join(in_dir, "images", "*.tif")))
        log("Found %u files" % len(files))
        _fff, ffi = average_files(files)
        ffi.save(dir_out + '/ff.tif')
        histeq_im(dir_out + '/ff.tif', dir_out + '/ffe.tif')


class Plugin(ArgusScriptingPlugin):
    def input_config(self):
        return {
            "Output directory": {
                "widget": "QLineEdit",
                "type": str,
                "default": ""
            },
            "Sample": {
                "widget": "QPushButton",
                "value": "sample",
            },
            # Collect data
            "Collect": {
                "widget": "QPushButton",
                "value": "collect",
            },
            "Collect (quick)": {
                "widget": "QPushButton",
                "value": "collect_quick",
            },
            # Plot collected data
            "Analyze": {
                "widget": "QPushButton",
                "value": "analyze",
            },
            "Go to FF target": {
                "widget": "QPushButton",
                "value": "ff-target",
            },
            "Raw": {
                "widget": "QComboBox",
                "values": ["Yes", "No"],
                "default": "Yes"
            },
            "Fast": {
                "widget": "QComboBox",
                "values": ["Yes", "No"],
                "default": "No"
            },
            "Autofocus": {
                "widget": "QComboBox",
                "values": ["Yes", "No"],
                "default": "No"
            },
        }

    def show_run_button(self):
        return False

    def sample(self):
        im = self.image()

        assert im
        im = im.convert('L')
        assert im
        stat = ImageStat.Stat(im)
        self.log("Average pixel value: %0.1f" % stat.mean[0])


    def collect(
        self,
        output_directory,
        overwrite=False,
        origin=None,
        raw=True,
        autofocus=True,
        quick=False,
        fast=False,
    ):
        objective_config = self.get_objective_config()
        self.log(f"imager_ff, quick={quick}")
        self.log("Using objective %s" % objective_config["name"])
        magnification = objective_config["magnification"]

        save_extension = ".tif"
        # 25 / 100
        # note: fast will move fast
        # seems like it might be ok, but leaving off for now
        # quick will take fewer measurements
        if quick:
            x_sites = 5
            y_sites = 5
        else:
            x_sites = 10
            y_sites = 10

        output_directory = str(output_directory)

        # these could probably be 0 since most systems are already backlash compensated
        #x_max_backlash = 0.05
        #y_max_backlash = 0.05
        # motion = self.motion()
        # epsilons = motion.epsilon()

        objective_config = self.get_objective_config()
        x_pitch = objective_config["x_view"] * 0.7
        y_pitch = objective_config["y_view"] * 0.7

        if not output_directory:
            dt = datetime_file_str()
            model = self.microscope_model()
            sn = self.microscope_serial()
            sn_str = ""
            if sn:
                sn_str = f"_sn-{sn}"
            output_directory = f"data/script/{dt}_imager-ff_model-{model}{sn_str}_mag-{magnification}"

        self.log(f"Saving to {output_directory}")
        if os.path.exists(output_directory):
            # if not self.message_box_yes_cancel("Start?", "Output directory already exists. Are you sure you want to continue"):
            #     return
            if not overwrite:
                self.log("Aborted: refusing to overwrite existing directory")
                return
            self.log("WARNING: output directory already exists")
        else:
            os.mkdir(output_directory)

        image_dir = os.path.join(output_directory, "images")
        self.log(f"Saving to {image_dir}")
        os.mkdir(image_dir)

        tstart = time.time()
        if origin is None:
            origin = self.pos()
        self.origin = origin

        if autofocus:
            self.autofocus()

        j = {
            "test": "imager_ff",
            "microscope_model": self.microscope_model(),
            "microscope_serial": self.microscope_serial(),
            "autofocus": autofocus,
            "quick": quick,
            "x_sites": x_sites,
            "y_sites": y_sites,
            "x_pitch": x_pitch,
            "y_pitch": y_pitch,
            "origin": self.origin,
            "objective": objective_config,
            "fast": fast,
            "raw": raw,
            "save_extension": save_extension,
            "moves": [],
        }

        def save_current():
            savej(j, output_directory)

        save_current()

        # Make behavior mirror planner:
        # row / col upper left, but motion XY origin lower right
        for row in range(y_sites):
            for col in range(x_sites):
                pos = {
                    "x": origin["x"] + col * x_pitch,
                    "y": origin["y"] + (y_sites - row - 1) * y_pitch,
                }
                self.move_absolute(pos)
                basename = "c%03u_r%03u%s" % (col, row, save_extension)
                filename = os.path.join(image_dir, basename)
                mode = "raw" if raw else "processed"
                self.image(mode=mode, wait_imaging_ok=not fast).save(filename)
                self.log(f"Saved {filename}")
                j["moves"].append({
                    "fn": basename,
                    "col": col,
                    "row": row,
                    "pos": pos,
                })

        self.move_absolute(origin)

        j["seconds"] = int(time.time() - tstart)
        self.log("Main loop done")
        save_current()

    def cleanup(self):
        if self.origin is not None:
            self.move_absolute(self.origin)

    def run_test(self):
        self.origin = None
        vals = self.get_input()
        button = vals.get("button")

        task = button["value"]
        if task == "ff-target":
            # cal = get_cal(self._ac.microscope)
            # cal.fid70_go()
            self.log("FIXME")
        elif task == "sample":
            self.sample()
        elif task == "collect":
            self.log("Sleeping. Clear the area!")
            self.sleep(5)
            self.collect(
                output_directory=vals.get("Output directory", ""),
                autofocus=vals.get("Autofocus") == "Yes",
                raw=vals.get("Raw") == "Yes",
                fast=vals.get("Fast") == "Yes",
            )
        elif task == "collect_quick":
            self.collect(
                output_directory=vals.get("Output directory", ""),
                autofocus=vals.get("Autofocus") == "Yes",
                raw=vals.get("Raw") == "Yes",
                fast=vals.get("Fast") == "Yes",
                quick=True,
            )
        elif task == "analyze":
            analyze(in_dir=vals.get("Output directory", ""), log=self.log)
        else:
            assert 0, task


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run test")
    parser.add_argument("in_dir", nargs="?")
    args = parser.parse_args()

    analyze(in_dir=args.in_dir)


if __name__ == "__main__":
    main()
