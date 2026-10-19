#!/usr/bin/env python3
from uscope.motion.plugins import get_motion_hal
from uscope.config import get_usc
from uscope.util import add_bool_arg, default_date_dir, mkdir_p
from uscope.imager import gst
from uscope.cal_util import stabalize_camera_start, stabalize_camera_snap
import time
import argparse
import datetime
import os
import json


def run(
    out_dir=None,
    postfix=None,
    microscope=None,
    backlash=1.0,
    ref_scalar=None,
    steps=20,
    axes={"x", "y"},
):

    tstart = time.time()

    if postfix is None:
        postfix = ("axes-%s_steps-%u_backlash-%0.3f" %
                   (''.join(sorted(axes)), steps, backlash))
    if not out_dir:
        out_dir = default_date_dir("out", "repeat_xy", postfix)
    print("writing to %s" % out_dir)
    mkdir_p(out_dir)

    usc = get_usc(name=microscope)
    print("Initializing imager...")
    imager = gst.get_cli_imager_by_config(usc=usc)
    print("Initializing motion...")
    motion = get_motion_hal(usc=usc)
    print("System ready")

    origin = motion.pos()

    if ref_scalar is None:
        ref_scalar = backlash * 2

    # this can probably be skipped in practice
    # or add tstart if I want to be proper
    stabalize_camera_start(imager, usc=usc)

    def save_image(basename):
        stabalize_camera_snap(imager, usc=usc)
        im = imager.get()
        im = im["0"]
        fn = "%s/%s" % (out_dir, basename)
        # print("Saving %s" % fn)
        im.save(fn)

    def savej(j):
        open("%s/log.json" % (out_dir, ), "w").write(
            json.dumps(j, sort_keys=True, indent=4, separators=(',', ': ')))

    def thread(loop):
        def run_axis(loop_axis):
            axj = {}
            j["axes"][loop_axis] = axj
            axj["steps"] = {}
            print("")
            print("Testing axis %s" % axis)

            axis_out_dir = out_dir + "/" + loop_axis
            if not os.path.exists(axis_out_dir):
                os.mkdir(axis_out_dir)

            def run_scalar():
                # Do a series of moves with known magnitude
                j_ref_scalar = {"mag": ref_scalar, "steps": {}}
                j["axes"][loop_axis]["ref_move"] = j_ref_scalar
                # Take median of 1 3 or 5
                # maybe more to scale with other sample size
                scalar_moves = 3
                for step in range(scalar_moves):
                    # Move far away
                    motion.move_relative({axis: ref_scalar + backlash})
                    # Complete backlash compensation
                    motion.move_relative({axis: -backlash})
                    basename = "%s/scalar_%02u.jpg" % (loop_axis, step)
                    j_ref_scalar["steps"][step] = {"fn": basename}
                    save_image(basename)
                    # Complete move back
                    motion.move_relative({axis: -ref_scalar})
                    savej(j)

            def run_main():
                for step in range(steps):
                    print("Pass %u" % step)
                    print(datetime.datetime.utcnow().isoformat())
                    motion.move_relative({axis: +backlash})
                    motion.move_relative({axis: -backlash})

                    # Record image to post process
                    print("Getting image")
                    basename = "%s/%003u.jpg" % (loop_axis, step)
                    save_image(basename)

                    axj["steps"][step] = {
                        "fn": basename,
                    }
                    print("Pass done")
                    savej(j)

            run_scalar()
            run_main()

        j = {
            "test": "repeat_xy",
            "steps": steps,
            "backlash": backlash,
            "origin": origin,
            "axes": {},
        }
        for axis in sorted(axes):
            run_axis(axis)

        j["seconds"] = int(time.time() - tstart)
        print("Main loop done")
        savej(j)

    gst.easy_run(imager, thread)
    print("Exiting")


def main():
    parser = argparse.ArgumentParser(
        description="Move testing axes using microscope.json configuration")
    parser.add_argument("--microscope", help="Select configuration directory")
    add_bool_arg(parser, "--y", default=True, help="Move Y axis")
    add_bool_arg(parser, "--x", default=True, help="Move X axis")
    parser.add_argument(
        "--backlash",
        type=float,
        default=1.0,
        help="At least known backlash, suggest wide margin over (say 2 to 5x)")
    parser.add_argument("--steps",
                        default=20,
                        type=int,
                        help="Number of steps")
    parser.add_argument("--postfix", default=None, help="Log file postfix")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    axes = set()
    if args.x:
        axes.add("x")
    if args.y:
        axes.add("y")

    run(microscope=args.microscope,
        axes=axes,
        backlash=args.backlash,
        steps=args.steps,
        postfix=args.postfix)


if __name__ == "__main__":
    main()
