#!/usr/bin/env -S uv run

from uscope.microscope import Microscope


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Get current position")
    parser.add_argument("--microscope", help="Select configuration directory")
    args = parser.parse_args()

    # Motion only: no camera needed
    microscope = Microscope(name=args.microscope,
                            make_imager=False,
                            make_kinematics=False)
    try:
        pos = microscope.motion.pos()
        print(" ".join("%s%0.3f" % (axis.upper(), pos[axis])
                       for axis in sorted(pos)))
    finally:
        microscope.motion.close()


if __name__ == "__main__":
    main()
