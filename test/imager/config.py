#!/usr/bin/env python3
from uscope.imager.config import default_gstimager_config
import argparse
from uscope.util import printj

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print default config")
    args = parser.parse_args()

    imagej = {}
    default_gstimager_config(imagej)
    printj(imagej)
