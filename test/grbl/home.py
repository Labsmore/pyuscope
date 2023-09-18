#!/usr/bin/env python3

from uscope.motion.grbl import GRBL, GrblHal
from uscope.util import add_bool_arg
# from uscope.motion.plugins import get_motion_hal
from uscope.microscope import get_microscope_for_motion


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRBL status")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    add_bool_arg(parser,
                 "--force",
                 default=None,
                 help="Don't home if already homed")
    parser.add_argument("--microscope")
    args = parser.parse_args()

    if args.microscope:
        print("Opening as GrblHal object")
        # motion = get_motion_hal(microscope=args.microscope)
        microscope = get_microscope_for_motion(name=args.microscope)
        assert type(microscope.motion) == GrblHal, type(microscope.motion)
        # should actually do a home request at init
        microscope.motion.home(force=args.force)
    else:
        print("Opening as GRBL object")
        grbl = GRBL(verbose=args.verbose)
        print("Open ok")
        grbl.gs.h()


if __name__ == "__main__":
    main()
