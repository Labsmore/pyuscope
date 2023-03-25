#!/usr/bin/env python3

from uscope import cloud_stitch
from uscope.util import add_bool_arg


def main():
    import argparse

    if cloud_stitch.boto3 is None:
        raise ImportError("Requires boto3 library")

    parser = argparse.ArgumentParser(description="Upload CloudStitch job")
    add_bool_arg(parser, "--verbose", default=True)
    parser.add_argument("--access-key")
    parser.add_argument("--secret-key")
    parser.add_argument("--id-key")
    parser.add_argument("--notification-email")
    parser.add_argument("dir_in")
    args = parser.parse_args()

    cloud_stitch.upload_dir(args.dir_in,
                            access_key=args.access_key,
                            secret_key=args.secret_key,
                            id_key=args.id_key,
                            notification_email=args.notification_email,
                            verbose=args.verbose)


if __name__ == "__main__":
    main()
