#!/usr/bin/env python3

import cv2
from uscope.util import add_bool_arg
import argparse
import os
import numpy as np
import glob


def main():

    parser = argparse.ArgumentParser(description="test")
    add_bool_arg(parser, "--show", default=False, help="Verbose output")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument("fn", nargs="?", help="")
    args = parser.parse_args()

    fn = args.fn
    if not fn:
        # default to last snapshot
        # (ie a test image)
        fn = sorted(glob.glob("snapshot/*.jpg"))[-1]
    show = args.show
    out_dir = "test/cal/centroid"

    if not os.path.exists(out_dir):
        os.mkdir(out_dir)

    # sample_img = cv2.imread('circle.jpg',0)
    sample_img = cv2.imread(fn, cv2.IMREAD_GRAYSCALE)
    assert sample_img is not None

    cv2.imwrite("%s/01_in.jpg" % out_dir, sample_img)
    if show:
        cv2.imshow('image', sample_img)
        cv2.waitKey(0)

    # ret, thresh = cv2.threshold(sample_img, 225, 255, 0)
    # ret, thresh = cv2.threshold(sample_img, 225, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU )
    ret, thresh = cv2.threshold(sample_img, 225, 255, cv2.THRESH_BINARY)
    cv2.imwrite("%s/02_thresh.jpg" % out_dir, thresh)
    if show:
        cv2.imshow('image', thresh)
        cv2.waitKey(0)

    # remove noise
    # actually this hurt accuracy
    # only eroded top for some reason
    if 1:
        for i in range(1):
            kernel = np.ones((5, 5), np.uint8)
            thresh = cv2.dilate(thresh, kernel)
            for i in range(2):
                thresh = cv2.erode(thresh, kernel)
            cv2.imwrite("%s/03_erode.jpg" % out_dir, thresh)
        if 0:
            cv2.imshow('image', thresh)
            cv2.waitKey(0)

    Moments = cv2.moments(thresh)
    print(Moments)

    x = int(Moments["m10"] / Moments["m00"])
    y = int(Moments["m01"] / Moments["m00"])
    print("centroid", x, y)

    sample_img = cv2.circle(sample_img, (x, y), 25, 0, -1)

    # sample_img = cv2.putText(sample_img, "Centroid", (x - 25, y - 25),  cv2.FONT_HERSHEY_SIMPLEX, 0.5, 128, 2)

    if show:
        cv2.imshow('image', sample_img)
        cv2.waitKey(0)
    cv2.imwrite("%s/04_centroid.jpg" % out_dir, sample_img)


if __name__ == "__main__":
    main()
