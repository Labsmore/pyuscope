#!/usr/bin/env python3

from uscope import cloud_stitch
from uscope.util import add_bool_arg
import os
import json
import glob
import re
import subprocess


def process_image_enfuse(fns_in, fn_out, ewf=None):
    if ewf is None:
        ewf = "gaussian"
    args = ["enfuse", "--output", fn_out, "--exposure-weight-function", ewf]
    for arg in fns_in:
        args.append(arg)
    print(" ".join(args))
    subprocess.check_call(args)


def hdr_run(dir_in, dir_out, ewf=None):
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)

    # Bucket [fn_base][exposures]
    fns = {}
    for fn in glob.glob(dir_in + "/*.jpg"):
        # c000_r028_h01.jpg
        m = re.match(r"(c.*_r.*)_h(.*).jpg", os.path.basename(fn))
        assert m, os.path.basename(fn)
        prefix = m.group(1)
        hdr = int(m.group(2))
        fns.setdefault(prefix, {})[hdr] = fn

    for prefix, hdrs in sorted(fns.items()):
        print(hdrs.items())
        fns = [fn for _i, fn in sorted(hdrs.items())]
        process_image_enfuse(fns,
                             os.path.join(dir_out, prefix + ".jpg"),
                             ewf=ewf)


def run(directory,
        access_key=None,
        secret_key=None,
        id_key=None,
        notification_email=None,
        ewf=None,
        verbose=True):

    with open(os.path.join(directory, "uscan.json")) as f:
        uscan = json.load(f)
    hdrj = uscan.get("image-hdr")
    if not hdrj:
        print("HDR: no. Straight pass through")
        cs_directory = directory
    else:
        print("HDR: yes. Processing")
        hdr_dir = os.path.join(directory, "hdr")
        hdr_run(directory, hdr_dir, ewf=ewf)
        cs_directory = hdr_dir

    print("")
    print(f"Ready to stitch {cs_directory}")
    cloud_stitch.upload_dir(cs_directory,
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
        verbose=args.verbose)


if __name__ == "__main__":
    main()
