#!/usr/bin/env python3

from uscope import cloud_stitch
from uscope.util import add_bool_arg
import os
import json
import glob
import re
import subprocess
import shutil


def process_hdr_image_enfuse(fns_in, fn_out, ewf=None):
    if ewf is None:
        ewf = "gaussian"
    args = ["enfuse", "--output", fn_out, "--exposure-weight-function", ewf]
    for arg in fns_in:
        args.append(arg)
    print(" ".join(args))
    subprocess.check_call(args)


def process_stack_image_panotools(dir_in, fns_in, fn_out):
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


def get_images(dir_in):
    return sorted(glob.glob(dir_in + "/*.jpg")) + sorted(
        glob.glob(dir_in + "/*.tif"))


def hdr_run(dir_in, dir_out, ewf=None):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)
    image_suffix = get_image_suffix(dir_in)

    # Bucket [fn_base][exposures]
    fns = {}
    for fn in get_images(dir_in):
        # c000_r028_h01.jpg
        # c001_r000_z01_h02.jpg
        m = re.match(r"(c.*_r.*)_h(.*)[.].*", os.path.basename(fn))
        m = m or re.match(r"(c.*_r.*)_z.*_h(.*)[.].*", os.path.basename(fn))
        assert m, os.path.basename(fn)
        prefix = m.group(1)
        hdr = int(m.group(2))
        fns.setdefault(prefix, {})[hdr] = fn

    for prefix, hdrs in sorted(fns.items()):
        print(hdrs.items())
        fns = [fn for _i, fn in sorted(hdrs.items())]
        process_hdr_image_enfuse(fns,
                                 os.path.join(dir_out, prefix + image_suffix),
                                 ewf=ewf)


def stack_run(dir_in, dir_out):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)
    image_suffix = get_image_suffix(dir_in)

    # Remove old files
    for fn in glob.glob(os.path.join(dir_in, "aligned_*")):
        os.unlink(fn)

    # Bucket [fn_base][stacki]
    fns = {}
    for fn in get_images(dir_in):
        # c000_r028_h01.jpg
        # c001_r000_z01_h02.jpg
        m = re.match(r"(c.*_r.*)_z(.*)[.].*", os.path.basename(fn))
        assert m, os.path.basename(fn)
        prefix = m.group(1)
        stacki = int(m.group(2))
        fns.setdefault(prefix, {})[stacki] = fn

    for prefix, stacks in sorted(fns.items()):
        print(stacks.items())
        fns = [fn for _i, fn in sorted(stacks.items())]
        process_stack_image_panotools(
            dir_in, fns, os.path.join(dir_out, prefix + image_suffix))


def need_jpg_conversion(working_dir):
    fns = glob.glob(working_dir + "/*.tif")
    print("fns", fns)
    return bool(fns)


def get_image_suffix(dir_in):
    if glob.glob(dir_in + "/*.tif"):
        return ".tif"
    else:
        return ".jpg"


def jpg_convert(dir_in, dir_out):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)

    print(f"Converting tif => jpg {dir_in} => {dir_out}")
    for fn_in in get_images(dir_in):
        assert ".tif" in os.path.basename(fn_in)
        fn_out = os.path.basename(fn_in).replace(".tif", ".jpg")
        fn_out = os.path.join(dir_out, fn_out)
        args = ["convert", "-quality", "90", fn_in, fn_out]
        print(" ".join(args))
        subprocess.check_call(args)


def run(directory,
        access_key=None,
        secret_key=None,
        id_key=None,
        notification_email=None,
        ewf=None,
        upload=True,
        verbose=True):
    print("Reading metadata...")

    working_dir = directory
    while working_dir[-1] == "/":
        working_dir = working_dir[0:len(directory) - 1]

    print("")

    if not bool(glob.glob(working_dir + "/c*_r*_h*.*")):
        print("HDR: no. Straight pass through")
    else:
        print("HDR: yes. Processing")
        # dir name needs to be reasonable for CloudStitch to name it well
        hdr_dir = os.path.join(working_dir,
                               os.path.basename(working_dir) + "_hdr")
        hdr_run(working_dir, hdr_dir, ewf=ewf)
        working_dir = hdr_dir

    print("")

    if not bool(glob.glob(working_dir + "/c*_r*_z*.*")):
        print("Stacker: no. Straight pass through")
    else:
        print("Stacker: yes. Processing")
        # dir name needs to be reasonable for CloudStitch to name it well
        stacker_dir = os.path.join(working_dir,
                                   os.path.basename(working_dir) + "_stacked")
        stack_run(working_dir, stacker_dir)
        working_dir = stacker_dir

    # CloudStitch currently only supports .jpg
    if need_jpg_conversion(working_dir):
        print("")
        print("Converting to jpg")
        jpg_dir = os.path.join(working_dir,
                               os.path.basename(working_dir) + "_jpg")
        jpg_convert(working_dir, jpg_dir)
        working_dir = jpg_dir

    print("")

    if not upload:
        print("CloudStitch: skip")
    else:
        print(f"Ready to stitch {working_dir}")
        cloud_stitch.upload_dir(working_dir,
                                access_key=access_key,
                                secret_key=secret_key,
                                id_key=id_key,
                                notification_email=notification_email,
                                verbose=verbose)


def main():
    import argparse

    if cloud_stitch.boto3 is None:
        raise ImportError("Requires boto3 library")

    parser = argparse.ArgumentParser(
        description="Process HDR/stacking / etc + CloudStitch")
    add_bool_arg(parser, "--verbose", default=True)
    add_bool_arg(parser, "--upload", default=True)
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
        verbose=args.verbose)


if __name__ == "__main__":
    main()
