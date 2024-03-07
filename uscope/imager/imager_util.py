from PIL import Image
import glob
import os
import subprocess
from uscope.util import tostr


def get_scaled(image, factor, filt=Image.NEAREST):
    if factor == 1.0:
        return image
    else:
        return image.resize(
            (int(image.size[0] * factor), int(image.size[1] * factor)), filt)


def format_mm_3dec(value):
    """
    Given a value in mm return a string printing it nicely
    Currently always gives 3 decimal places
    """
    if value >= 100:
        return "%d mm" % value
    elif value >= 10:
        return "%0.1f mm" % value
    elif value >= 1:
        return "%0.2f mm" % value
    elif value >= 0.1:
        return "%d um" % (value * 1000, )
    elif value >= 0.01:
        return "%0.1f um" % (value * 1000, )
    elif value >= 0.001:
        return "%0.2f um" % (value * 1000, )
    else:
        if value < 0.0001 and value != 0.0:
            print("WARNING: formatitng very small number: %g" % (value, ))
        return "%0.3f um" % (value * 1000, )
