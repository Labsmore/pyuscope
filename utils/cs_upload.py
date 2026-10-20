#!/usr/bin/env -S uv run

from uscope import cloud_stitch
from uscope.util import add_bool_arg


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Upload CloudStitch job")
    add_bool_arg(parser, "--verbose", default=True)
    parser.add_argument("--access-key")
    parser.add_argument("--secret-key")
    parser.add_argument("--id-key")
    parser.add_argument("--notification-email")
    parser.add_argument("dir_in")
    args = parser.parse_args()

    cs_info = cloud_stitch.CSInfo(
        access_key=args.access_key,
        secret_key=args.secret_key,
        id_key=args.id_key,
        notification_email=args.notification_email)
    cloud_stitch.upload_dir(args.dir_in,
                            cs_info=cs_info,
                            verbose=args.verbose)


if __name__ == "__main__":
    main()
