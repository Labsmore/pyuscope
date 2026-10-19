#!/usr/bin/env -S uv run

from uscope.util import add_bool_arg
from uscope.motion import motion_util
from uscope.microscope import Microscope


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Execute a G1 movement command")
    add_bool_arg(parser, "--relative", default=False, help="relative movement")
    parser.add_argument("--microscope", help="Select configuration directory")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument("cmd", nargs="+", help="Ex: X1.0")
    args = parser.parse_args()

    # Motion only: no camera needed
    microscope = Microscope(name=args.microscope,
                            make_imager=False,
                            make_kinematics=False)
    try:
        motion = microscope.motion
        moves = motion_util.parse_move(" ".join(list(args.cmd)))
        args.verbose and print("Moving", moves)
        if args.relative:
            motion.move_relative(moves)
        else:
            motion.move_absolute(moves)
    finally:
        microscope.motion.close()


if __name__ == "__main__":
    main()
