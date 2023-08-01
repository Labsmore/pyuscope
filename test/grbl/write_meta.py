#!/usr/bin/env python3
from uscope.motion.grbl import GRBL, grbl_write_meta, grbl_read_meta, NoGRBLMeta
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Write pyuscope metadata to GRBL controller")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument("--model", help="Model number")
    parser.add_argument("--sn", help="Serial number")
    args = parser.parse_args()

    grbl = GRBL()
    model = args.model
    sn = args.sn
    if model is None or sn is None:
        try:
            info = grbl_read_meta(grbl.gs)
            print("Queried existing info")
            print("  Model: %s" % (info["model"], ))
            print("  S/N: %s" % (info["sn"], ))
        except NoGRBLMeta:
            print("WCS magic number not found")
            info = {}
    if model is None:
        model = info["model"]
    if sn is None:
        sn = info["sn"]
    print("Writing")
    grbl_write_meta(grbl.gs, model=model, sn=sn)

    print("Verifying")
    try:
        info = grbl_read_meta(grbl.gs)
    except NoGRBLMeta:
        print("Config magic number not found")
        return
    print("  Model: %s" % (info["model"], ))
    print("  S/N: %s" % (info["sn"], ))
    assert info["model"] == model
    assert info["sn"] == sn

    print("Write ok")


if __name__ == "__main__":
    main()
