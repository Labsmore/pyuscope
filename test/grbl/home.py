#!/usr/bin/env python3

from uscope.motion.grbl import GRBL, HomingFailed
from uscope.util import add_bool_arg
import time


def home(grbl, lazy=True):
    # Can take up to two times to pop all status info
    # Third print is stable
    status = grbl.qstatus()["status"]
    print(f"Status: {status}")
    # Otherwise should be Alarm state
    if status == "Idle" and lazy:
        return
    tstart = time.time()
    # TLDR: gearbox means we need ot home several times
    # 2023-04-19: required 7 cycles in worst case...hmm add more wiggle room for now
    # related to 8/5 adjustment?
    for homing_try in range(8):
        print("Sending home command %u" % (homing_try + 1, ))
        try:
            grbl.gs.h()
            break
        except HomingFailed:
            print("Homing timed out, nudging again")
    else:
        raise HomingFailed("Failed to home despite several attempts :(")
    deltat = time.time() - tstart
    print("Homing successful after %0.1f sec. Ready to use!" % (deltat, ))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRBL status")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    add_bool_arg(parser,
                 "--lazy",
                 default=True,
                 help="Don't home if already homed")
    args = parser.parse_args()

    grbl = GRBL(verbose=args.verbose)
    print("Open ok")
    home(grbl, lazy=args.lazy)


if __name__ == "__main__":
    main()
