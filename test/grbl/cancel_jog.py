#!/usr/bin/env python3

from uscope.motion.grbl import GRBL
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Execute a G1 movement command cancel")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    add_bool_arg(parser,
                 "--status",
                 default=False,
                 help="Output position after command")
    args = parser.parse_args()

    grbl = GRBL(reset=False, verbose=args.verbose)
    grbl.gs.cancel_jog()


if __name__ == "__main__":
    main()
