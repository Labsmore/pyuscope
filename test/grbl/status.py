#!/usr/bin/env -S uv run
"""
How to tell if power is applied?
"""

from uscope.motion.grbl import GRBL
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRBL status")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL()
    print("Open ok")
    if args.verbose:
        grbl.log_info()
    else:
        print(grbl.gs.question())


if __name__ == "__main__":
    main()
