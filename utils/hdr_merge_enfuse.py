#!/usr/bin/env python3
import glob
import re
import os
import subprocess


def process_image_enfuse(fns_in, fn_out, ewf):
    args = ["enfuse", "--output", fn_out, "--exposure-weight-function", ewf]
    for arg in fns_in:
        args.append(arg)
    print(" ".join(args))
    subprocess.check_call(args)


def run(
    dir_in,
    gaussian_dir=None,
    lorentzian_dir=None,
    half_sine_dir=None,
    full_sine_dir=None,
    bi_square_dir=None,
):
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

        def run_mode(prefix_dir, ewf):
            if not prefix_dir:
                return
            if not os.path.exists(prefix_dir):
                os.mkdir(prefix_dir)
            process_image_enfuse(fns, os.path.join(prefix_dir,
                                                   prefix + ".jpg"), ewf)

        run_mode(gaussian_dir, "gaussian")
        run_mode(lorentzian_dir, "lorentzian")
        run_mode(half_sine_dir, "half-sine")
        run_mode(full_sine_dir, "full-sine")
        run_mode(bi_square_dir, "bi-square")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HDR test")
    parser.add_argument("--gaussian-dir")
    parser.add_argument("--lorentzian-dir")
    parser.add_argument("--half-sine-dir")
    parser.add_argument("--full-sine-dir")
    parser.add_argument("--bi-square-dir")
    parser.add_argument("dir_in")
    args = parser.parse_args()

    run(
        args.dir_in,
        gaussian_dir=args.gaussian_dir,
        lorentzian_dir=args.lorentzian_dir,
        half_sine_dir=args.half_sine_dir,
        full_sine_dir=args.full_sine_dir,
        bi_square_dir=args.bi_square_dir,
    )


if __name__ == "__main__":
    main()
