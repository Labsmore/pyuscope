#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope.imager import gst
import uscope.planner
from uscope.config import get_usc
from uscope.planner.planner_util import microscope_to_planner_config, get_planner
from uscope.motion.plugins import get_motion_hal
from uscope.util import default_date_dir
import os
from collections import OrderedDict


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run a plannr job from CLI")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument('--start',
                        default="0,0",
                        help="countour.start x,y. Default: 0,0")
    parser.add_argument('--end',
                        default="0,0",
                        help="countour.end x,y. Default: 0,0")
    parser.add_argument(
        '--corners',
        default=None,
        help=
        "points-3p corners as ll,ul,lr as x0,y0:x1,y1:x2,y2 or x0,y0,z0,...")
    parser.add_argument('--postfix', default="", help="Log file postfix")
    parser.add_argument('--microscope', help="Which microscope config to use")
    parser.add_argument('--objective',
                        default=None,
                        help="Objective to use (by name), objective required")
    parser.add_argument('--objectivei',
                        default=None,
                        help="Objective to use (by index), objective required")
    add_bool_arg(parser,
                 "--dry",
                 default=False,
                 help="Must set to enable real motion")
    parser.add_argument("out", nargs="?", help="File to save to")
    args = parser.parse_args()

    contour = None
    corners = None
    if args.corners:
        corners = OrderedDict()
        big_parts = args.corners.split(":")
        assert len(big_parts) == 3, big_parts

        for cornerk, pointstr in zip(["ll", "ul", "ur"], big_parts):
            parts = [float(x) for x in pointstr.split(",")]
            if len(parts) == 2:
                corners[cornerk] = {"x": parts[0], "y": parts[1]}
            elif len(parts) == 3:
                corners[cornerk] = {
                    "x": parts[0],
                    "y": parts[1],
                    "z": parts[2]
                }
            else:
                assert 0
    else:
        x0, y0 = [float(x) for x in args.start.split(",")]
        x1, y1 = [float(x) for x in args.end.split(",")]
        contour = {
            "start": {
                "x": x0,
                "y": y0,
            },
            "end": {
                "x": x1,
                "y": y1,
            },
        }

    usc = get_usc(name=args.microscope)
    usj = usc.usj
    objectivei = args.objectivei
    if args.objectivei:
        args.objectivei = int(args.objectivei)
    pconfig = microscope_to_planner_config(usj,
                                           contour=contour,
                                           corners=corners,
                                           objectivestr=args.objective,
                                           objectivei=objectivei)
    root_dir = "out"
    if not os.path.exists(root_dir):
        os.mkdir(root_dir)
    out_dir = default_date_dir(root_dir, "", args.postfix)
    print("Writing files to %s" % out_dir)

    print("Initializing imager...")
    imager = gst.get_cli_imager_by_config(usj=usj)
    print("Initializing motion...")
    motion = get_motion_hal(usc=usc)
    print("System ready")

    planner = get_planner(pconfig=pconfig,
                          motion=motion,
                          imager=imager,
                          out_dir=out_dir,
                          dry=args.dry)

    planner.run()


if __name__ == "__main__":
    main()
