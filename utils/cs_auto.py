#!/usr/bin/env python3
"""
Pre-process files for cloud stitch
CloudStitch only operates on .jpg right now (bandwidth etc)
So pre-process files / tifs individually first

TODO: parallel
"""

from uscope import cloud_stitch
from uscope.util import add_bool_arg
import os
import json
import glob
import re
import subprocess
import shutil
from collections import OrderedDict
import traceback
from uscope import config


def process_hdr_image_enfuse(fns_in, fn_out, ewf=None, best_effort=True):
    if ewf is None:
        ewf = "gaussian"
    args = ["enfuse", "--output", fn_out, "--exposure-weight-function", ewf]
    for arg in fns_in:
        args.append(arg)
    print(" ".join(args))
    subprocess.check_call(args)


def process_stack_image_panotools(dir_in, fns_in, fn_out, best_effort=True):
    """
    align_image_stack -m -a OUT $(ls)
    -m  Optimize field of view for all images, except for first. Useful for aligning focus stacks with slightly different magnification.
        might not apply but keep for now
   -a prefix

    enfuse --exposure-weight=0 --saturation-weight=0 --contrast-weight=1 --hard-mask --output=baseOpt1.tif OUT*.tif
    """
    """
    tmp_dir = "/tmp/cs_auto"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.mkdir(tmp_dir)
    """

    # Remove old files
    for fn in glob.glob(os.path.join(dir_in, "aligned_*")):
        os.unlink(fn)

    # Always output as .tif
    args = ["align_image_stack", "-m", "-a", os.path.join(dir_in, "aligned_")]
    for fn in fns_in:
        args.append(fn)
    print(" ".join(args))
    subprocess.check_call(args)

    args = [
        "enfuse", "--exposure-weight=0", "--saturation-weight=0",
        "--contrast-weight=1", "--hard-mask", "--output=" + fn_out
    ]
    for fn in glob.glob(os.path.join(dir_in, "aligned_*")):
        args.append(fn)
    print(" ".join(args))
    subprocess.check_call(args)

    # Remove old files
    # This can also confuse globbing to find extra tifs
    for fn in glob.glob(os.path.join(dir_in, "aligned_*")):
        os.unlink(fn)


def index_scan_images(dir_in):
    """
    Return dict of image_name to
    {
        # Max number of elements
        "hdrs": 2,
        "stacks": 3,

        "images": {
            "c000_r028_h01.jpg": {
                "hdr": 1,
                "col": 0,
                "row": 28,
                "extension": ".jpg",
            },
            "c001_r000_z01_h02.tif": {
                "hdr": 2,
                "stack": 1,
                "col": 0,
                "row": 28,
                "extension": ".tif",
            },
        },
    }
    """
    ret = OrderedDict()
    images = OrderedDict()
    cols = 0
    rows = 0
    hdrs = 0
    stacks = 0
    for fn_full in sorted(
            list(glob.glob(dir_in + "/*.jpg")) +
            list(glob.glob(dir_in + "/*.tif"))):
        basename = os.path.basename(fn_full)

        def parse_fn():
            """
            Nobody is going to be impressed with my regular expression skills
            but this should work
            """
            m = re.match(r"c([0-9]+)_r([0-9]+)_z([0-9]+)_h([0-9]+)(\.[a-z]+)",
                         basename)
            if m:
                return {
                    "col": int(m.group(1)),
                    "col_str": "c" + m.group(1),
                    "row": int(m.group(2)),
                    "row_str": "r" + m.group(2),
                    "stack": int(m.group(3)),
                    "stack_str": "z" + m.group(3),
                    "hdr": int(m.group(4)),
                    "hdr_str": "h" + m.group(4),
                    "extension": m.group(5),
                }
            m = re.match(r"c([0-9]+)_r([0-9]+)_z([0-9]+)(\.[a-z]+)", basename)
            if m:
                return {
                    "col": int(m.group(1)),
                    "col_str": "c" + m.group(1),
                    "row": int(m.group(2)),
                    "row_str": "r" + m.group(2),
                    "stack": int(m.group(3)),
                    "stack_str": "z" + m.group(3),
                    "extension": m.group(4),
                }
            m = re.match(r"c([0-9]+)_r([0-9]+)_h([0-9]+)(\.[a-z]+)", basename)
            if m:
                return {
                    "col": int(m.group(1)),
                    "col_str": "c" + m.group(1),
                    "row": int(m.group(2)),
                    "row_str": "r" + m.group(2),
                    "hdr": int(m.group(3)),
                    "hdr_str": "h" + m.group(3),
                    "extension": m.group(4),
                }
            m = re.match(r"c([0-9]+)_r([0-9]+)(\.[a-z]+)", basename)
            if m:
                return {
                    "col": int(m.group(1)),
                    "col_str": "c" + m.group(1),
                    "row": int(m.group(2)),
                    "row_str": "r" + m.group(2),
                    "extension": m.group(3),
                }
            return None

        v = parse_fn()
        if not v:
            continue
        images[basename] = v
        hdrs = max(hdrs, v.get("hdr", -1) + 1)
        stacks = max(stacks, v.get("stack", -1) + 1)
        rows = max(rows, v.get("row") + 1)
        cols = max(cols, v.get("col") + 1)

    # xxx: maybe this removes /
    # yes
    working_dir = os.path.realpath(dir_in)
    # while working_dir[-1] == "/":
    #    working_dir = working_dir[0:len(working_dir) - 1]

    ret["dir"] = working_dir
    ret["images"] = images
    ret["hdrs"] = hdrs
    ret["stacks"] = stacks
    ret["cols"] = cols
    ret["rows"] = rows
    return ret


def unkey_fn_prefix(filev, remove_key):
    ret = f"{filev['col_str']}_{filev['row_str']}"
    if "stack" in filev and remove_key != "stack":
        ret += f"_{filev['stack_str']}"
    if "hdr" in filev and remove_key != "hdr":
        ret += f"_{filev['hdr_str']}"
    return ret


def bucket_group(iindex_in, bucket_key):
    # Bucket [fn_base][exposures]
    fns = OrderedDict()
    for fn, filev in iindex_in["images"].items():
        # assert image_suffix == filev['extension']
        fn_prefix = unkey_fn_prefix(filev, bucket_key)
        fns.setdefault(fn_prefix, {})[filev[bucket_key]] = fn
    return fns


def hdr_run(iindex_in, dir_out, ewf=None, lazy=True, best_effort=True):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)
    image_suffix = get_image_suffix(iindex_in["dir"])
    buckets = bucket_group(iindex_in, "hdr")

    # Must be in exposure order?
    for fn_prefix, hdrs in sorted(buckets.items()):
        fns = [
            os.path.join(iindex_in["dir"], fn)
            for _i, fn in sorted(hdrs.items())
        ]
        fn_out = os.path.join(dir_out, fn_prefix + image_suffix)
        if lazy and os.path.exists(fn_out):
            print(f"lazy: skip {fn_out}")
        else:
            print(fn_prefix, fn_out)
            print("  ", hdrs.items())
            process_hdr_image_enfuse(fns,
                                     fn_out,
                                     ewf=ewf,
                                     best_effort=best_effort)


def stack_run(iindex_in, dir_out, lazy=True, best_effort=True):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)
    image_suffix = get_image_suffix(iindex_in["dir"])
    buckets = bucket_group(iindex_in, "stack")

    def clean_tmp_files():
        # Remove old files
        for fn in glob.glob(os.path.join(iindex_in["dir"], "aligned_*")):
            os.unlink(fn)

    clean_tmp_files()
    try:
        # Must be in stack order?
        for fn_prefix, stacks in sorted(buckets.items()):
            print(stacks.items())
            fns = [
                os.path.join(iindex_in["dir"], fn)
                for _i, fn in sorted(stacks.items())
            ]
            fn_out = os.path.join(dir_out, fn_prefix + image_suffix)
            if lazy and os.path.exists(fn_out):
                print(f"lazy: skip {fn_out}")
            else:
                # Stacking can fail to align features
                # Consider what to do such as filling in a patch image
                # from the middle of the stack
                try:
                    process_stack_image_panotools(iindex_in["dir"],
                                                  fns,
                                                  fn_out,
                                                  best_effort=best_effort)
                except subprocess.CalledProcessError:
                    if not best_effort:
                        raise
                    else:
                        print("WARNING: ignoring exception")
                        traceback.print_exc()
    finally:
        clean_tmp_files()


def need_jpg_conversion(working_dir):
    fns = glob.glob(working_dir + "/*.tif")
    print("fns", fns)
    return bool(fns)


def get_image_suffix(dir_in):
    if glob.glob(dir_in + "/*.tif"):
        return ".tif"
    else:
        return ".jpg"


def tif2jpg_dir(iindex_in, dir_out, lazy=True):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)

    print(f"Converting tif => jpg {iindex_in['dir']} => {dir_out}")
    for fn_base in iindex_in["images"].keys():
        assert ".tif" in fn_base
        fn_in = os.path.join(iindex_in["dir"], fn_base)
        fn_out = fn_base.replace(".tif", ".jpg")
        assert fn_out != fn_base, (fn_out, fn_base)
        fn_out = os.path.join(dir_out, fn_out)
        if lazy and os.path.exists(fn_out):
            print(f"lazy: skip {fn_out}")
        else:
            args = ["convert", "-quality", "90", fn_in, fn_out]
            print(" ".join(args))
            subprocess.check_call(args)


def fix_dir(this_iindex, dir_out):
    """
    Make a best estimate by filling in images from directory above
    For now assumes dir above is focus stack and the output dir is the final upload dir
    Should cover most use cases we care about for now
    """

    if not os.path.exists(dir_out):
        os.mkdir(dir_out)

    open_set = get_open_set(this_iindex)
    stack_iindex = index_scan_images(os.path.dirname(this_iindex["dir"]))
    assert stack_iindex["stacks"], "fixme assumes dir above is stack"
    stacks = stack_iindex["stacks"]
    # Assume middle is mostly in focus
    selected_stack = stacks // 2
    for (col, row) in sorted(open_set):
        src_fn = os.path.join(
            stack_iindex["dir"],
            "c%03u_r%03u_z%02u.jpg" % (col, row, selected_stack))
        dst_fn = os.path.join(dir_out, "c%03u_r%03u.jpg" % (col, row))
        print(f"cp {src_fn} {dst_fn}")
        shutil.copyfile(src_fn, dst_fn)

    # Now copy in the "real" files
    for basename in this_iindex["images"].keys():
        src_fn = os.path.join(this_iindex["dir"], basename)
        dst_fn = os.path.join(dir_out, basename)
        print(f"cp {src_fn} {dst_fn}")
        shutil.copyfile(src_fn, dst_fn)


def get_open_set(working_iindex):
    open_set = set()
    for col in range(working_iindex["cols"]):
        for row in range(working_iindex["rows"]):
            open_set.add((col, row))
    for filev in working_iindex["images"].values():
        open_set.remove((filev["col"], filev["row"]))
    return open_set


def inspect_final_dir(working_iindex):
    healthy = True
    n_healthy = working_iindex["cols"] * working_iindex["rows"]
    n_actual = len(working_iindex["images"])
    print("Have %u / %u images" % (n_actual, n_healthy))
    open_set = get_open_set(working_iindex)
    print("Failed to find: %u files" % (len(open_set)))
    for (col, row) in sorted(open_set):
        print("  c%03u_r%03u.jpg" % (col, row))
        healthy = False
    return healthy


def run(directory,
        access_key=None,
        secret_key=None,
        id_key=None,
        notification_email=None,
        ewf=None,
        upload=True,
        lazy=True,
        best_effort=True,
        verbose=True):
    print("Reading metadata...")

    working_iindex = index_scan_images(directory)
    dst_basename = os.path.basename(os.path.abspath(directory))

    print("")

    if not working_iindex["hdrs"]:
        print("HDR: no. Straight pass through")
    else:
        print("HDR: yes. Processing")
        # dir name needs to be reasonable for CloudStitch to name it well
        next_dir = os.path.join(working_iindex["dir"], "hdr")
        hdr_run(working_iindex,
                next_dir,
                ewf=ewf,
                lazy=lazy,
                best_effort=best_effort)
        working_iindex = index_scan_images(next_dir)

    print("")

    if not working_iindex["stacks"]:
        print("Stacker: no. Straight pass through")
    else:
        print("Stacker: yes. Processing")
        # dir name needs to be reasonable for CloudStitch to name it well
        next_dir = os.path.join(working_iindex["dir"], "stack")
        # maybe? helps some use cases
        if lazy and os.path.exists(next_dir):
            print("lazy: skip stack")
        else:
            stack_run(working_iindex,
                      next_dir,
                      lazy=lazy,
                      best_effort=best_effort)
        working_iindex = index_scan_images(next_dir)

    # CloudStitch currently only supports .jpg
    if need_jpg_conversion(working_iindex["dir"]):
        print("")
        print("Converting to jpg")
        next_dir = os.path.join(working_iindex["dir"], "jpg")
        tif2jpg_dir(working_iindex, next_dir, lazy=lazy)
        working_iindex = index_scan_images(next_dir)

    print("")
    healthy = inspect_final_dir(working_iindex)
    print("")

    if not healthy and best_effort:
        print("WARNING: data is incomplete but trying to patch")
        next_dir = os.path.join(working_iindex["dir"], "fix")
        fix_dir(working_iindex, next_dir)
        working_iindex = index_scan_images(next_dir)
        print("")
        print("re-inspecting new dir")
        healthy = inspect_final_dir(working_iindex)
        assert healthy
        print("")

    if not upload:
        print("CloudStitch: skip (requested)")
    elif not healthy:
        print("CloudStitch: skip (incomplete data)")
    elif not access_key and not config.get_bc().labsmore_stitch_aws_access_key(
    ):
        print("CloudStitch: skip (missing credidentials)")
    else:
        print("Ready to stitch " + working_iindex["dir"])
        cloud_stitch.upload_dir(working_iindex["dir"],
                                access_key=access_key,
                                secret_key=secret_key,
                                id_key=id_key,
                                notification_email=notification_email,
                                dst_basename=dst_basename,
                                verbose=verbose)


def main():
    import argparse

    if cloud_stitch.boto3 is None:
        raise ImportError("Requires boto3 library")

    parser = argparse.ArgumentParser(
        description="Process HDR/stacking / etc + CloudStitch")
    add_bool_arg(parser, "--verbose", default=True)
    add_bool_arg(parser, "--upload", default=True)
    add_bool_arg(parser,
                 "--lazy",
                 default=True,
                 help="Only process unprocessed files")
    add_bool_arg(
        parser,
        "--best-effort",
        default=True,
        help="Best effort in lieu of crashing on error (ex: stack failure)")
    parser.add_argument("--access-key")
    parser.add_argument("--secret-key")
    parser.add_argument("--id-key")
    parser.add_argument("--notification-email")
    parser.add_argument("--ewf")
    parser.add_argument("dir_in")
    args = parser.parse_args()

    run(args.dir_in,
        access_key=args.access_key,
        secret_key=args.secret_key,
        id_key=args.id_key,
        notification_email=args.notification_email,
        ewf=args.ewf,
        upload=args.upload,
        best_effort=args.best_effort,
        lazy=args.lazy,
        verbose=args.verbose)


if __name__ == "__main__":
    main()
