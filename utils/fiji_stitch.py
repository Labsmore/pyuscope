#!/usr/bin/env -S uv run
"""
Stitch completed scan(s) with a local Fiji (ImageJ) install
Run cs_auto.py first if the scan needs HDR / stacking / etc processing

Output: <scan>/fiji/stitched.tif
Fiji: --fiji, PYUSCOPE_FIJI, bc config fiji_path or auto-detected
"""

from uscope import fiji_stitch
from uscope.util import add_bool_arg
import sys


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Stitch scan directories with local Fiji")
    parser.add_argument(
        "--fiji", help="Fiji launcher or Fiji.app directory (default: auto)")
    add_bool_arg(
        parser,
        "--compute-overlap",
        default=True,
        help="Refine stage positions with phase correlation (recommended)")
    parser.add_argument("--fusion-method",
                        default="Linear Blending",
                        help="ex: Linear Blending, Average, Max. Intensity")
    parser.add_argument("--mem", help="Fiji JVM memory, ex: 16g")
    parser.add_argument("--timeout",
                        type=float,
                        default=None,
                        help="Seconds before giving up on Fiji")
    add_bool_arg(parser,
                 "--lazy",
                 default=False,
                 help="Skip scans that already have fiji/stitched.tif")
    add_bool_arg(parser,
                 "--dry",
                 default=False,
                 help="Only write TileConfiguration.txt / macro")
    parser.add_argument("dirs_in", nargs="+")
    args = parser.parse_args()

    if not args.dry:
        fiji = fiji_stitch.find_fiji(args.fiji)
        if not fiji:
            print(
                f"Fiji not found. Install from https://fiji.sc/ and/or use --fiji or {fiji_stitch.ENV_FIJI}"
            )
            sys.exit(1)
        print(f"Using Fiji {fiji}")
    else:
        fiji = args.fiji

    failed = []
    for directory in args.dirs_in:
        try:
            fiji_stitch.stitch_scan_dir(directory,
                                        fiji=fiji,
                                        compute_overlap=args.compute_overlap,
                                        fusion_method=args.fusion_method,
                                        mem=args.mem,
                                        lazy=args.lazy,
                                        dry=args.dry,
                                        timeout=args.timeout)
        except Exception as e:
            if len(args.dirs_in) == 1:
                raise
            print(f"ERROR: {directory}: {e}")
            failed.append(directory)
    if failed:
        print(f"Failed {len(failed)} / {len(args.dirs_in)}")
        for directory in failed:
            print(f"  {directory}")
        sys.exit(1)


if __name__ == "__main__":
    main()
