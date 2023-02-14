#!/usr/bin/env python3
from uscope.motion.plugins import get_motion_hal
from uscope.config import get_usc
from uscope.util import add_bool_arg
from uscope.imager import gst
import time
import argparse
import datetime
import os
import json

from uscope.cal_util import move_str


def run(
    out_dir="backlash",
    microscope=None,
    axmin=0.0,
    axmax=1.0,
    steps=20,
    axes={"x", "y"},
):

    tstart = time.time()
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)

    usc = get_usc(name=microscope)
    print("Initializing imager...")
    imager = gst.get_cli_imager_by_config(usc=usc)
    print("Initializing motion...")
    motion = get_motion_hal(usc=usc)
    print("System ready")

    origin = motion.pos()

    def move_absolute(moves, comp=False):
        def move_relative(moves):
            motion.move_relative(moves)

        """
        Always approach from +axis
        TODO: check if need to compensate
        """
        if comp:
            backlash = axmax - axmin
            rmoves = {}
            for axis in moves.keys():
                rmoves[axis] = backlash
            move_relative(rmoves)
        motion.move_absolute(moves)

    # this can probably be skipped in practice
    # or add tstart if I want to be proper
    if imager.source_name == "toupcamsrc":
        # gain takes a while to ramp up
        print("stabalizing camera")
        time.sleep(1)

    def thread(loop):
        def run_axis(loop_axis):
            move_log = {}
            j["axes"][loop_axis] = move_log
            print("")
            print("Testing axis %s" % axis)
            # First step at origin
            for step in range(steps + 1):
                print("")
                print("Pass %u" % step)
                print(datetime.datetime.utcnow().isoformat())
                print("Moving %s to origin" % axis)
                # Snap back to origin to verify no drift
                move_absolute({axis: origin[axis]}, comp=True)

                # Now attempt to move and see if we actually do and by how much
                this_move = (axmax - axmin) * (step / steps)
                moves = {loop_axis: this_move}
                print("move %s" % (move_str(moves), ))
                move_absolute(moves, comp=False)

                # Record image to post process
                print("Getting image")
                time.sleep(1.5)
                im = imager.get()
                im = im["0"]
                basename = "%s_%003u.jpg" % (loop_axis, step)
                fn = "%s/%s" % (out_dir, basename)
                print("Saving %s" % fn)
                im.save(fn)

                move_log[step] = {
                    "magnitude": this_move,
                    "fn": basename,
                }
                print("Pass done")
                open("%s/log.json" % (out_dir, ), "w").write(
                    json.dumps(j,
                               sort_keys=True,
                               indent=4,
                               separators=(',', ': ')))
            return move_log

        j = {
            "test": "backlash_xy",
            "steps": steps,
            "min": axmin,
            "max": axmax,
            "origin": origin,
            "axes": {},
        }
        for axis in sorted(axes):
            j["axes"][axis] = run_axis(axis)

        j["seconds"] = int(time.time() - tstart)
        print("Main loop done")
        open("%s/log.json" % (out_dir, ), "w").write(
            json.dumps(j, sort_keys=True, indent=4, separators=(',', ': ')))

    gst.easy_run(imager, thread)
    print("Exiting")


def main():
    parser = argparse.ArgumentParser(
        description="Move testing axes using microscope.json configuration")
    parser.add_argument("--microscope", help="Select configuration directory")
    add_bool_arg(parser, "--y", default=True, help="Move Y axis")
    add_bool_arg(parser, "--x", default=True, help="Move X axis")
    parser.add_argument("--min",
                        type=float,
                        default=0.0,
                        help="Minimum search value")
    parser.add_argument("--max",
                        type=float,
                        default=1.0,
                        help="Maximum search value")
    parser.add_argument("--steps",
                        default=20,
                        type=int,
                        help="Number of steps")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    axes = set()
    if args.x:
        axes.add("x")
    if args.y:
        axes.add("y")

    run(microscope=args.microscope,
        axes=axes,
        axmin=args.min,
        axmax=args.max,
        steps=args.steps)


if __name__ == "__main__":
    main()
