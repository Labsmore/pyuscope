import cv2
import os
import numpy as np
import time


def move_str(moves):
    ret = ""
    for axis in sorted(moves.keys()):
        if ret:
            ret += " "
        ret += "%s%+0.3f" % (axis.upper(), moves[axis])
    return ret


def find_centroid(im, show=False):
    im.save("/tmp/centroid.jpg", quality=90)
    try:
        find_centroid_fn("/tmp/centroid.jpg", show=show)
    finally:
        os.unlink("/tmp/centroid.jpg")


def find_centroid_fn(fn, show=False):
    im_orig = cv2.imread(fn, cv2.IMREAD_GRAYSCALE)

    # Only consider pixels near saturation
    _ret, im_wip = cv2.threshold(im_orig, 225, 255, cv2.THRESH_BINARY)
    # remove noise
    # actually this hurt accuracy
    # only eroded top for some reason
    kernel = np.ones((5, 5), np.uint8)
    for i in range(1):
        im_wip = cv2.dilate(im_wip, kernel)
    for i in range(2):
        im_wip = cv2.erode(im_wip, kernel)

    moments = cv2.moments(im_wip)
    x = int(moments["m10"] / moments["m00"])
    y = int(moments["m01"] / moments["m00"])

    return x, y


def stabalize_camera_start(imager, usj):
    if imager.source_name == "toupcamsrc":
        # gain takes a while to ramp up
        print("stabalizing camera")
        time.sleep(1)


def stabalize_camera_snap(imager, usj):
    time.sleep(1.5)
