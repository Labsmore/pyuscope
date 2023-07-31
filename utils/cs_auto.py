#!/usr/bin/env python3
"""
Pre-process files for cloud stitch
CloudStitch only operates on .jpg right now (bandwidth etc)
So pre-process files / tifs individually first

TODO: parallel
"""

from uscope.image.cs_auto import process_dir, already_uploaded
from uscope.cloud_stitch import CSInfo
from uscope.util import add_bool_arg
from uscope import config
from uscope import cloud_stitch
import os
import time


def run(directories, batch_sleep=2400, *args, **kwargs):
    if directories:
        for directory in directories:
            process_dir(directory, *args, **kwargs)
    else:
        # Something 3 like execution units right now
        burst_size = 2
        uploads = 0
        print("Scanning data dir for new scans")
        # Only take the top directory listing
        for root, directories, _files in os.walk(config.get_scan_dir()):
            break
        for basename in directories:
            directory = os.path.join(root, basename)
            if already_uploaded(directory):
                print(f"{basename}: skip, already uploaded")
                continue
            print("")
            print("")
            print("")
            print("*" * 78)
            print(f"{basename}: not uploaded")
            print("*" * 78)
            if uploads >= burst_size:
                print(
                    "WARNING: throttling upload to let stitch server catch up")
                time.sleep(batch_sleep)
            process_dir(directory, *args, **kwargs)
            uploads += 1


def main():
    import argparse

    if cloud_stitch.boto3 is None:
        raise ImportError("Requires boto3 library")

    parser = argparse.ArgumentParser(
        description="Process HDR/stacking / etc + CloudStitch")
    add_bool_arg(parser, "--verbose", default=True)
    add_bool_arg(parser, "--upload", default=True)
    add_bool_arg(parser, "--fix", default=False)
    add_bool_arg(parser,
                 "--lazy",
                 default=True,
                 help="Only process unprocessed files")
    add_bool_arg(
        parser,
        "--best-effort",
        default=True,
        help="Best effort in lieu of crashing on error (ex: stack failure)")
    parser.add_argument("--threads", default=None)
    parser.add_argument("--access-key")
    parser.add_argument("--secret-key")
    parser.add_argument("--id-key")
    parser.add_argument("--notification-email")
    # We only have a few execution units right now
    # If you upload a bunch it will throttle a bit
    # Typical 400 image scans seem to complete in about 30 min
    parser.add_argument("--batch-sleep",
                        default=2400,
                        type=int,
                        help="Hack for not overloading stitch service")
    parser.add_argument("--ewf")
    parser.add_argument("dirs_in", nargs="*")
    args = parser.parse_args()

    cs_info = CSInfo(access_key=args.access_key,
                     secret_key=args.secret_key,
                     id_key=args.id_key,
                     notification_email=args.notification_email)

    run(args.dirs_in,
        cs_info=cs_info,
        ewf=args.ewf,
        upload=args.upload,
        fix=args.fix,
        best_effort=args.best_effort,
        lazy=args.lazy,
        batch_sleep=args.batch_sleep,
        nthreads=args.threads,
        verbose=args.verbose)


if __name__ == "__main__":
    main()
