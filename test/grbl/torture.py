#!/usr/bin/env python3

from uscope.motion.grbl import GRBL
from uscope.util import add_bool_arg
import random


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="GRBL communications torture test")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL(verbose=args.verbose)
    f = 1000
    scalar = 10
    i = 0
    while True:
        i += 1
        print("")
        print("Iter %u" % i)
        grbl.move_absolute({"x": 0.0, "y": 0.0}, f=f)
        grbl.wait_idle()

        def randaxis():
            return scalar * random.randrange(-100, 100) / 100

        # Do two random moves so that can do non-origin moves
        grbl.move_relative({"x": randaxis(), "y": randaxis()}, f=f)
        grbl.wait_idle()
        grbl.move_relative({"x": randaxis(), "y": randaxis()}, f=f)
        grbl.wait_idle()
        print(grbl.gs.question())


if __name__ == "__main__":
    main()
