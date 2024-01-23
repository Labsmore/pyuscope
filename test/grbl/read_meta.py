#!/usr/bin/env python3
from uscope.motion.grbl import GRBL, grbl_read_meta, NoGRBLMeta, microscope_hash2name
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
    print("Comment: %s" % (info["comment"], ))
    print("S/N: %s" % (info["sn"], ))
    print("Config: %s" % (info["config"].hex(), ))
    hash2name = microscope_hash2name()
    print("  Microscope:", hash2name.get(info["config"]))
    if args.verbose:
        print("All hashes:")
        for this_h, name in hash2name.items():
            print("  %s: %s" % (name, this_h.hex()))


if __name__ == "__main__":
    main()
