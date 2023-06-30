#!/usr/bin/env python3
from PIL import Image
import numpy as np
import os
from uscope.scan_util import index_scan_images, bucket_group
import cv2 as cv
import shutil


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Choose the most in focus image from each stack")
    parser.add_argument('--dir-in', help='Sample images')
    parser.add_argument('--dir-out', help='Sample images')
    args = parser.parse_args()

    dir_out = args.dir_out
    if not dir_out:
        dir_out = args.dir_in + "/autofocus"
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)

    iindex = index_scan_images(args.dir_in)
    assert iindex["stacks"], "fixme need stacking"
    buckets = bucket_group(iindex, "stack")

    for fn_prefix, stacks in sorted(buckets.items()):
        print(stacks.items())
        fns = [
            os.path.join(iindex["dir"], fn)
            for _i, fn in sorted(stacks.items())
        ]
        print(fn_prefix, fns)
        scores = {}
        for fni, fn in enumerate(fns):

            def get_score(image, blur=9):
                filtered = cv.medianBlur(image, blur)
                laplacian = cv.Laplacian(filtered, cv.CV_64F)
                return laplacian.var()

            def image_pil2cv(im):
                return np.array(im)[:, :, ::-1].copy()

            im_pil = Image.open(fn)
            im_cv = image_pil2cv(im_pil)
            score = get_score(im_cv)
            print("  %u: %0.3f" % (fni, score))
            scores[score] = fn
        _score, src_fn = sorted(scores.items())[-1]
        print("Winner: %s" % src_fn)
        # dst_fn = os.path.join(dir_out, "c%03u_r%03u.jpg" % (col, row))
        dst_fn = os.path.join(dir_out, fn_prefix + ".jpg")
        print(f"cp {src_fn} {dst_fn}")
        shutil.copyfile(src_fn, dst_fn)


if __name__ == "__main__":
    main()
