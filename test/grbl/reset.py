#!/usr/bin/env python3

from uscope.motion.grbl import GRBL
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Soft reset")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL(reset=False, verbose=args.verbose)
    grbl.gs.reset()


if __name__ == "__main__":
    main()
