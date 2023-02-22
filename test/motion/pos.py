#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope.motion import motion_util
from uscope.motion.plugins import get_motion_hal
from uscope.config import get_usc
from uscope.motion.grbl import GrblHal


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Get current position")
    parser.add_argument("--microscope", help="Select configuration directory")
    args = parser.parse_args()

    # grbl = GRBL(verbose=args.verbose)
    usc = get_usc(name=args.microscope)
    motion = get_motion_hal(usc=usc)
    pos = motion.pos()
    print("X%0.3f Y%0.3f Z%0.3f" % (pos["x"], pos["y"], pos["z"]))


if __name__ == "__main__":
    main()
