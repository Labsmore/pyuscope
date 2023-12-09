#!/usr/bin/env python3
from uscope.motion.grbl import GRBL, grbl_write_meta, grbl_read_meta, NoGRBLMeta, microscope_name_hash, grbl_delete_meta
from uscope.util import add_bool_arg
import os


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description=
        "Write updated pyuscope metadata to GRBL controller (if required)")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL()

    try:
        info = grbl_read_meta(grbl.gs)
    except NoGRBLMeta:
        print("WCS magic number not found")
        return

    print("Queried existing info")
    print("  Meta version: %s" % (info["meta_ver"], ))
    print("  Comment: %s" % (info["comment"], ))
    print("  S/N: %s" % (info["sn"], ))
    print("  Config: %s" % (info["config"].hex(), ))

    if info["meta_ver"] == 1:
        print("Found old metadata version. Updating")
    elif info["meta_ver"] == 2:
        print("Metadata is current version. Leaving alone")
        return
    else:
        ver = info["meta_ver"]
        assert 0, f"Unexpected metadata version {ver}"

    # Writes are always in the new version
    grbl_write_meta(grbl.gs,
                    comment=info["comment"],
                    sn=info["sn"],
                    config=info["config"])

    print("Verifying")
    try:
        info2 = grbl_read_meta(grbl.gs)
    except NoGRBLMeta:
        print("Config magic number not found")
        return

    assert info["comment"] == info2["comment"]
    assert info["sn"] == info2["sn"]
    assert info["config"] == info2["config"]

    print("Update ok")


if __name__ == "__main__":
    main()
