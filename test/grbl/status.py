#!/usr/bin/env python3
"""
How to tell if power is applied?
"""

from uscope.motion.grbl import GRBL, print_config, print_configs
from uscope.util import add_bool_arg
from uscope.imager import gst
import uscope.planner
import shutil
import os
import json
import threading
import time


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
