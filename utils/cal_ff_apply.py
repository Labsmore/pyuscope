#!/usr/bin/env python3
from PIL import Image
import numpy as np
import glob
import os
import math


def npf2im(statef):
    #return statef, None
    rounded = np.round(statef)
    #print("row1: %s" % rounded[1])
    statei = np.array(rounded, dtype=np.uint16)
    #print(len(statei), len(statei[0]), len(statei[0]))
    height = len(statef)
    width = len(statef[0])

    # for some reason I isn't working correctly
    # only L
    # workaround by plotting manually
    im = Image.new("RGB", (width, height), "Black")
    for y, row in enumerate(statei):
        for x, val in enumerate(row):
            # this causes really weird issues if not done
            val = tuple(int(x) for x in val)
            im.putpixel((x, y), val)

    return im


def average_imgs(imgs, scalar=None):
    width, height = imgs[0].size
    if not scalar:
        scalar = 1.0
    scalar = scalar / len(imgs)

    statef = np.zeros((height, width, 3), np.float)
    for im in imgs:
        assert (width, height) == im.size
        statef = statef + scalar * np.array(im, dtype=np.float)

    return statef, npf2im(statef)


def average_dir(din, images=0, verbose=1, scalar=None):
    imgs = []

    files = list(glob.glob(os.path.join(din, "*.jpg")))
    verbose and print('Reading %s w/ %u images' % (din, len(files)))

    for fni, fn in enumerate(files):
        imgs.append(Image.open(fn))
        if images and fni + 1 >= images:
            verbose and print("WARNING: only using first %u images" % images)
            break
    return average_imgs(imgs, scalar=scalar)


"""
def histeq_np_create(npim, nbr_bins=256, verbose=0):
    '''
    Given a numpy nD array (ie image), return a histogram equalized numpy nD array of pixels
    That is, return 2D if given 2D, or 1D if 1D
    '''

    # get image histogram
    flat = npim.flatten()
    verbose and print('flat', flat)
    imhist, bins = np.histogram(flat, nbr_bins)
    verbose and print('imhist', imhist)
    verbose and print('imhist', bins)
    cdf = imhist.cumsum()  #cumulative distribution function
    verbose and print('cdfraw', cdf)
    cdf = 0xFFFF * cdf / cdf[-1]  #normalize
    verbose and print('cdfnorm', cdf)
    return cdf, bins


def histeq_np_apply(npim, create):
    cdf, bins = create

    # use linear interpolation of cdf to find new pixel values
    ret1d = np.interp(npim.flatten(), bins[:-1], cdf)
    return ret1d.reshape(npim.shape)

def histeq_np(npim, nbr_bins=256):
    '''
    Given a numpy nD array (ie image), return a histogram equalized numpy nD array of pixels
    That is, return 2D if given 2D, or 1D if 1D
    '''
    return histeq_np_apply(npim, histeq_np_create(npim, nbr_bins=nbr_bins))

def histeq_im(im, nbr_bins=256):
    imnp2 = np.array(im)
    imnp2_eq = histeq_np(imnp2, nbr_bins=nbr_bins)
    imf = Image.fromarray(imnp2_eq)
    return imf.convert("RGB")
"""


def bounds_close_band(ffi, band):
    hist = band.histogram()
    width, height = ffi.size
    npixels = width * height
    thresh = 0.01

    low = None
    high = None
    pixels = 0
    for i, vals in enumerate(hist):
        pixels += vals
        if low is None and pixels / npixels >= thresh:
            low = i
        if high is None and pixels / npixels >= (1.0 - thresh):
            high = i
            break
    return low, high


def bounds_close(ffi):
    rband, gband, bband = ffi.split()
    return bounds_close_band(ffi, rband), bounds_close_band(
        ffi, gband), bounds_close_band(ffi, bband)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Apply flat field calibration')
    parser.add_argument('--images',
                        type=int,
                        default=0,
                        help='Only take first n images, for debugging')
    parser.add_argument('--ffi-in', help="Flat field inverted image in")
    parser.add_argument('--dir-in', help='Sample images')
    parser.add_argument('--dir-out', help='Sample images')
    args = parser.parse_args()

    dir_in = args.dir_in
    dir_out = args.dir_out

    if not dir_out:
        dir_out = args.dir_in + "/autoflat3"
    if not os.path.exists(dir_out):
        os.mkdir(dir_out)

    ffi_im = Image.open(args.ffi_in)

    # It's easy to have an outlier that boosts everything
    # hmm
    if 0:
        ((ffi_rmin, ffi_rmax), (ffi_gmin, ffi_gmax),
         (ffi_bmin, ffi_bmax)) = ffi_im.getextrema()
    else:
        ((ffi_rmin, ffi_rmax), (ffi_gmin, ffi_gmax),
         (ffi_bmin, ffi_bmax)) = bounds_close(ffi_im)
    print(f"ffi r: {ffi_rmin} : {ffi_rmax}")
    print(f"ffi g: {ffi_gmin} : {ffi_gmax}")
    print(f"ffi b: {ffi_bmin} : {ffi_bmax}")

    print("")

    for im_in in sorted(glob.glob(dir_in + "/*.jpg")):
        # im_in = "cal/cal06_ff_1.5x/2023-06-20_01-22-25_blue_20x_cal6_1.5x_pic/c000_r001.jpg"
        print("im", im_in)
        im = Image.open(im_in)
        for x in range(im.width):
            for y in range(im.height):
                pixr, pixg, pixb = list(im.getpixel((x, y)))
                ffr, ffg, ffb = list(ffi_im.getpixel((x, y)))
                # nop
                if 0:
                    pixr2 = pixr
                    pixg2 = pixg
                    pixb2 = pixb
                # expected version
                if 1:
                    pixr2 = int(math.ceil(min(255, pixr * ffi_rmax / ffr)))
                    pixg2 = int(math.ceil(min(255, pixg * ffi_gmax / ffg)))
                    pixb2 = int(math.ceil(min(255, pixb * ffi_bmax / ffb)))
                # old code sort of hack
                if 0:
                    pixr2 = int(min(255, pixr * ffr / ffi_rmin))
                    pixg2 = int(min(255, pixg * ffg / ffi_gmin))
                    pixb2 = int(min(255, pixb * ffb / ffi_bmin))
                if x == 0 and y == 0 or x == 400 and y == 300:
                    print(
                        f"x={x}, y={y}: ({pixr}, {pixg}, {pixg}) => ({pixr2}, {pixg2}, {pixg2})"
                    )
                    print(pixr, ffi_rmax, ffr, ffi_rmax / ffr)
                im.putpixel((x, y), (pixr2, pixg2, pixb2))
        im_out = os.path.join(dir_out, os.path.basename(im_in))
        print("Saving to", im_out)
        im.save(im_out)
        # break


if __name__ == "__main__":
    main()
