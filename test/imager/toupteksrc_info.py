#!/usr/bin/env python3

import argparse
from uscope.util import printj
from uscope.imager.touptek import toupcamsrc_info

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print resolution options")
    args = parser.parse_args()

    info = toupcamsrc_info()
    printj(info)
    print("")
    print("Resolutions:")
    for esize, v in info["eSizes"].items():
        print("  esize %u: %uw x %u h" %
              (esize, v["StillResolution"]["w"], v["StillResolution"]["h"]))
