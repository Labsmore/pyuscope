#!/usr/bin/env python3
"""
How to tell if power is applied?
"""

from uscope.motion.grbl import GRBL
from uscope.util import add_bool_arg
from uscope.imager import gst
import uscope.planner
import shutil
import os
import json
import threading
import time


def reformat_config(s):
    """
    "$0=10 (step pulse,usec)",
    to
    ("$0=10", "step pulse,usec")
    """
    s = s.strip()
    if "(" in s:
        config, comment = s.split("(")
        comment = comment.replace(")", "").strip()
        config = config.strip()
        return config, comment
    else:
        return s, None


def print_config(s, prefix=""):
    config, comment = reformat_config(s)
    if comment:
        print(f'{prefix}"{config}", //{comment}')
    else:
        print(f'{prefix}"{config}",')


def print_configs(l):
    for s in l:
        print_config(s, prefix="")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRBL status")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL()
    print("Open ok")
    if args.verbose:
        # Can take up to two times to pop all status info
        # Third print is stable
        for i in range(3):
            print("")
            print("? (%u / %u)" % (i + 1, 3))
            print(grbl.gs.question())
        print("")
        print("i")
        print_configs(grbl.gs.i())
        # FIXME
        print("")
        print("g")
        print_config(grbl.gs.g())
        print("")
        print("$")
        print_configs(grbl.gs.dollar())
        print("")
        print("#")
        print_configs(grbl.gs.hash())
    else:
        print(grbl.gs.question())


if __name__ == "__main__":
    main()
