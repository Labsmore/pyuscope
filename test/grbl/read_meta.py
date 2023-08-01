#!/usr/bin/env python3
from uscope.motion.grbl import GRBL, grbl_read_meta, NoGRBLMeta
from uscope.util import add_bool_arg

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Read pyuscope metadata")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL()
    try:
        info = grbl_read_meta(grbl.gs)
    except NoGRBLMeta:
        print("Config magic number not found")
        return
    print("Model: %s" % (info["model"],))
    print("S/N: %s" % (info["sn"],))

if __name__ == "__main__":
    main()
