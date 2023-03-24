#!/usr/bin/env python3
import cv2 as cv
import numpy as np
import glob
import re
import os


# https://docs.opencv.org/3.4/d2/df0/tutorial_py_hdr.html
def process_image(fns_in,
                  exposure_times,
                  fn_out_debevec=None,
                  fn_out_robertson=None,
                  fn_out_mertens=None):
    print("Processing", fns_in, exposure_times)
    # Loading exposure images into a list
    img_list = [cv.imread(fn) for fn in fns_in]
    exposure_times = np.array(exposure_times, dtype=np.float32)

    # Tonemap HDR image
    tonemap1 = cv.createTonemap(gamma=2.2)

    if fn_out_debevec:
        # Merge exposures to HDR image
        merge_debevec = cv.createMergeDebevec()
        hdr_debevec = merge_debevec.process(img_list,
                                            times=exposure_times.copy())
        res_debevec = tonemap1.process(hdr_debevec.copy())
        res_debevec_8bit = np.clip(res_debevec * 255, 0, 255).astype('uint8')
        cv.imwrite(fn_out_debevec, res_debevec_8bit)
        print("  Saving", fn_out_debevec)

    if fn_out_robertson:
        merge_robertson = cv.createMergeRobertson()
        hdr_robertson = merge_robertson.process(img_list,
                                                times=exposure_times.copy())
        res_robertson = tonemap1.process(hdr_robertson.copy())
        res_robertson_8bit = np.clip(res_robertson * 255, 0,
                                     255).astype('uint8')
        cv.imwrite(fn_out_robertson, res_robertson_8bit)
        print("  Saving", fn_out_robertson)

    if 1:
        # Exposure fusion using Mertens
        merge_mertens = cv.createMergeMertens()
        res_mertens = merge_mertens.process(img_list)
        # Convert datatype to 8-bit and save
        res_mertens_8bit = np.clip(res_mertens * 255, 0, 255).astype('uint8')
        cv.imwrite(fn_out_mertens, res_mertens_8bit)
        print("  Saving", fn_out_mertens)


def run(dir_in,
        exposures,
        debevec_dir=None,
        robertson_dir=None,
        mertens_dir=None):
    # Bucket [fn_base][exposures]
    fns = {}
    for fn in glob.glob(dir_in + "/*.jpg"):
        # c000_r028_h01.jpg
        m = re.match(r"(c.*_r.*)_h(.*).jpg", os.path.basename(fn))
        assert m, os.path.basename(fn)
        prefix = m.group(1)
        hdr = int(m.group(2))
        fns.setdefault(prefix, {})[hdr] = fn

    if debevec_dir and not os.path.exists(debevec_dir):
        os.mkdir(debevec_dir)
    if robertson_dir and not os.path.exists(robertson_dir):
        os.mkdir(robertson_dir)
    if mertens_dir and not os.path.exists(mertens_dir):
        os.mkdir(mertens_dir)

    for prefix, hdrs in sorted(fns.items()):
        if debevec_dir:
            debevec_fn = os.path.join(debevec_dir, prefix + ".jpg")
        if robertson_dir:
            robertson_fn = os.path.join(robertson_dir, prefix + ".jpg")
        if mertens_dir:
            mertens_fn = os.path.join(mertens_dir, prefix + ".jpg")
        print(hdrs.items())
        fns = [fn for i, fn in sorted(hdrs.items())]
        process_image(fns,
                      exposures,
                      fn_out_debevec=debevec_fn,
                      fn_out_robertson=robertson_fn,
                      fn_out_mertens=mertens_fn)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HDR test")
    parser.add_argument("--exposures")
    parser.add_argument("--debevec-dir")
    parser.add_argument("--robertson-dir")
    parser.add_argument("--mertens-dir")
    parser.add_argument("dir_in")
    args = parser.parse_args()

    run(args.dir_in, [int(x) for x in args.exposures.split(',')],
        debevec_dir=args.debevec_dir,
        robertson_dir=args.robertson_dir,
        mertens_dir=args.mertens_dir)


if __name__ == "__main__":
    main()
