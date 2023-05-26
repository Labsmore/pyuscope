#!/usr/bin/env python3

from uscope.motion.grbl import GRBL, grbl_home
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRBL status")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    add_bool_arg(parser,
                 "--lazy",
                 default=True,
                 help="Don't home if already homed")
    args = parser.parse_args()

    grbl = GRBL(verbose=args.verbose)
    print("Open ok")
    grbl_home(grbl, lazy=args.lazy)


if __name__ == "__main__":
    main()
