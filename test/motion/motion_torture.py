#!/usr/bin/env python3

from uscope.motion.plugins import get_motion_hal
from uscope.config import get_usj
from uscope.util import add_bool_arg
from uscope.imager import gst
import time
import argparse
import random
import datetime
import os


def run(microscope=None,
        passes=0,
        axes={"x", "y", "z"},
        scalars={
            "x": 1.0,
            "y": 1.0,
            "z": 1.0,
        }):
    out_dir = "torture"
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)

    def filter_moves(moves):
        ret = {}
        if "x" in axes:
            ret["x"] = moves["x"]
        if "y" in axes:
            ret["y"] = moves["y"]
        if "z" in axes:
            ret["z"] = moves["z"]
        return ret

    origin = filter_moves({"x": 0.0, "y": 0.0, "z": 0.0})
    usj = get_usj(name=microscope)
    print("Initializing imager...")
    imager = gst.get_cli_imager_by_config(usj)
    print("Initializing motion...")
    motion = get_motion_hal(usj)
    print("System ready")

    # Consider planner for movement? aware of things like backlash
    backlash = usj["motion"].get("backlash", 0.0)
    print("Backlash: %0.3f" % backlash)

    mpos = {}

    def move_str(moves):
        ret = ""
        for axis in "xyz":
            if not axis in axes:
                continue
            if ret:
                ret += " "
            ret += "%s%+0.3f" % (axis.upper(), moves[axis])
        return ret

    def move_relative(moves):
        nonlocal mpos

        moves = filter_moves(moves)
        # mpos = dict(moves)
        motion.move_relative(moves)

    def move_absolute(moves, comp=False):
        nonlocal mpos
        """
        Always approach from +axis
        TODO: check if need to compensate
        """
        if comp:
            move_relative(
                filter_moves({
                    "x": backlash,
                    "y": backlash,
                    "z": backlash
                }))
        for axis, pos in moves.items():
            assert abs(pos) < scalars[axis] + 0.01
        motion.move_absolute(filter_moves(moves))
        mpos = dict(moves)

    print("Moving to origin")
    move_absolute(origin, comp=True)
    print("movement done")

    # this can probably be skipped in practice
    # or add tstart if I want to be proper
    if imager.source_name == "toupcamsrc":
        # gain takes a while to ramp up
        print("stabalizing camera")
        time.sleep(1)

    def thread(loop):
        passi = 0
        while True:
            passi += 1
            if passes and passi > passes:
                break
            print("")
            print("Pass %u" % passi)
            print(datetime.datetime.utcnow().isoformat())

            # Do a series of moves
            for pokei in range(5):
                print("Poke %u" % pokei)
                # Encourage mainly small moves
                # maybe log scale?
                if random.randint(0, 4):
                    this_scalar = 0.1
                else:
                    this_scalar = 1.0

                def rand_move(axis):
                    delta = this_scalar * scalars[axis] * random.randrange(
                        -100, 100) / 100
                    pos = mpos[axis] + delta
                    if abs(pos) > scalars[axis]:
                        pos = scalars[axis] * pos / abs(pos)
                    return pos

                moves = {}
                for axis in axes:
                    moves[axis] = rand_move(axis)
                print("move %s" % (move_str(moves), ))
                move_absolute(moves, comp=False)

            print("Moving to origin")
            # Snap back to origin to verify no drift
            move_absolute(origin, comp=True)

            print("Getting image")
            time.sleep(1.5)
            im = imager.get()
            im = im["0"]
            im.save("%s/%003u.jpg" % (out_dir, passi))
            print("Pass done")

        print("Main loop done")

    gst.easy_run(imager, thread)
    print("Exiting")


def main():
    parser = argparse.ArgumentParser(
        description="Move testing axes using microscope.json configuration")
    parser.add_argument("--microscope", help="Select configuration directory")
    parser.add_argument("--passes",
                        type=int,
                        default=0,
                        help="Number of loops to run")
    add_bool_arg(parser, "--x", default=True, help="Move X axis")
    parser.add_argument("--x-mag",
                        type=float,
                        default=1.0,
                        help="Movement max range for origin")
    add_bool_arg(parser, "--y", default=True, help="Move Y axis")
    parser.add_argument("--y-mag",
                        type=float,
                        default=1.0,
                        help="Movement max range for origin")
    add_bool_arg(parser, "--z", default=True, help="Move Z axis")
    parser.add_argument("--z-mag",
                        type=float,
                        default=1.0,
                        help="Movement max range for origin")
    add_bool_arg(parser, "--image", default=True, help="Take images")
    parser.add_argument("--image-freq", help="Take images every N passes")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    axes = set()
    scalars = {}
    if args.x:
        axes.add("x")
        scalars["x"] = args.x_mag
    if args.y:
        axes.add("y")
        scalars["y"] = args.y_mag
    if args.z:
        axes.add("z")
        scalars["z"] = args.z_mag

    run(microscope=args.microscope,
        axes=axes,
        scalars=scalars,
        passes=args.passes)


if __name__ == "__main__":
    main()
