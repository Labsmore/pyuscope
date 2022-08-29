#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope.motion import motion_util
from uscope.motion.plugins import get_motion_hal
from uscope.config import get_usj


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Execute a G1 movement command")
    add_bool_arg(parser, "--relative", default=False, help="relative movement")
    add_bool_arg(parser, "--idle", default=True, help="Wait for idle")
    parser.add_argument("--microscope", help="Select configuration directory")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument("cmd", nargs="+", help="Ex: X1.0")
    args = parser.parse_args()

    # grbl = GRBL(verbose=args.verbose)
    usj = get_usj(name=args.microscope)
    motion = get_motion_hal(usj)

    moves = motion_util.parse_move(" ".join(list(args.cmd)))
    if args.relative:
        motion.move_relative(moves)
    else:
        motion.move_absolute(moves)
    """
    if args.idle:
        args.verbose and print("Waiting for idle...")
        grbl.wait_idle()
    """


if __name__ == "__main__":
    main()
