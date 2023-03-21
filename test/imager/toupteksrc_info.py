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
    print("")
    print("Host software")
    print("  SDK Version: %s" % (info["Version"], ))
    print("  SDK Brand: %s" % (info["SDK_Brand"], ))
    print("Hardware")
    if "ModelV2" in info:
        print("  Model: %s" % (info["ModelV2"]["name"], ))
    print("  SerialNumber: %s" % (info["SerialNumber"], ))
    print("  ProductionDate: %s" % (info["ProductionDate"], ))
    print("  HwVersion: %s" % (info["HwVersion"], ))
    print("  Revision: %s" % (info["Revision"], ))
    print("  FwVersion: %s" % (info["FwVersion"], ))
    print("  FpgaVersion: %s" % (info["FpgaVersion"], ))
