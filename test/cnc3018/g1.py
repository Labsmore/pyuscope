#!/usr/bin/env python3

from uscope.motion.grbl import GRBL
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Execute a G1 movement command")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    add_bool_arg(parser,
                 "--status",
                 default=False,
                 help="Output position after command")
    parser.add_argument(
        "cmd",
        # nargs="?",
        help="Ex: G90 X1.0 F1000")
    args = parser.parse_args()

    grbl = GRBL(verbose=args.verbose)
    # cmd = " ".join(list(args.cmds))
    grbl.gs.j(args.cmd)
    if args.status:
        print("Waiting for idle...")
        grbl.wait_idle()
        mpos = grbl.qstatus()["MPos"]
        print("X%0.3f Y%0.3f Z%0.3F" % (mpos["x"], mpos["y"], mpos["z"]))


if __name__ == "__main__":
    main()
