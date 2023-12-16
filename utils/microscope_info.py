#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope import config
from uscope.imagep.util import RC_CONST
from uscope.microscope import get_virtual_microscope, get_mconfig
import os


def run(microscope_name=None, microscope_sn=None, verbose=True):
    print("Reading config...")
    microscope = get_virtual_microscope(
        mconfig=get_mconfig(name=microscope_name, serial=microscope_sn))
    objectives = microscope.get_objectives()

    # To sample at a given resolution need two pixels per cycle
    # ex: 1 um resolution means we need a 500 nm light pixel and a 500 nm dark pixel
    niquest_scalar = 2
    """
    On color sensors pixels have packing something like:
    RGRGRGRG
    GBGBGBGB
    Maybe a little different for YUV, but similar idea
    As such we only get about half the advertised color resolution
    although spatial resolution is fine
    """
    image_scalar = microscope.usc.imager.scalar()
    if image_scalar >= 2.0:
        bayer_scalar = 1
    else:
        bayer_scalar = 2

    print("Image size:")
    raw_wh = microscope.usc.imager.raw_wh()
    print("  Step 1: raw sensor pixels: %uw x %uh" % (raw_wh[0], raw_wh[1]))
    crop_w, crop_h = microscope.usc.imager.cropped_wh()
    print("  Step 2: crop %s => %uw x %uh" %
          (microscope.usc.imager.crop_tblr(), crop_w, crop_h))
    print("  Step 3: apply scalar %0.2f" % image_scalar)
    final_wh = microscope.usc.imager.final_wh()
    print("  Step 4: final sensor pixels: %uw x %uh (%u)" %
          (final_wh[0], final_wh[1], final_wh[0] * final_wh[1]))
    print("Note constants / scalars applied:")
    print("  Rayleigh criterion: %0.3f" % RC_CONST)
    print("  Niquest sampling penalty: %u" % niquest_scalar)
    if verbose:
        print("  Bayer sampling penalty: %u" % bayer_scalar)

    print("Objectives")
    for objective in objectives.get_full_config().values():
        print(f"  {objective['name']}")
        print("    x_view (post-crop): %0.3f mm" % objective['x_view'])
        x_view = objective['x_view']
        y_view = final_wh[1] / final_wh[0] * objective['x_view']
        print("      %0.3f w x %0.3f h mm" % (x_view, y_view))
        print("    magnification: %s" % objective.get("magnification"))
        print("    um_per_pixel (post-scale): %0.3f" %
              objective["um_per_pixel"])
        na = objective.get("na", 0)
        print("    na: %0.3f" % na)
        if na:
            # Don't take bayer into account
            # ie assumes its a black / white contrast target at lowest rated nm
            def spacial_res():
                res_400 = RC_CONST * 400 / (2 * na)
                oversampling_ratio_400 = res_400 / (
                    niquest_scalar * objective["um_per_pixel"] * 1000)
                print("      max spacial resolution: %0.1f nm" % res_400)
                print("        Oversampling ratio: %0.2f" %
                      oversampling_ratio_400)
                resolvable_pixels = x_view * 1e6 / res_400 * y_view * 1e6 / res_400
                print("        Resolvable pixels: %0.1f" % resolvable_pixels)
                return oversampling_ratio_400 < 1.0

            # Pure blue sample performance
            def res_400():
                res_400 = RC_CONST * 400 / (2 * na)
                oversampling_ratio_400 = res_400 / (
                    niquest_scalar * bayer_scalar * objective["um_per_pixel"] *
                    1000)
                print("      Resolution @ 400 nm: %0.1f nm" % res_400)
                print("        Oversampling ratio: %0.2f" %
                      oversampling_ratio_400)
                resolvable_pixels = x_view * 1e6 / res_400 * y_view * 1e6 / res_400
                print("        Resolvable pixels: %0.1f" % resolvable_pixels)
                return oversampling_ratio_400 < 1.0

            # Pure red sample performance
            def res_800():
                res_800 = RC_CONST * 800 / (2 * na)
                oversampling_ratio_800 = res_800 / (
                    niquest_scalar * bayer_scalar * objective["um_per_pixel"] *
                    1000)
                print("      Resolution @ 800 nm: %0.1f nm" % res_800)
                print("        Oversampling ratio: %0.2f" %
                      oversampling_ratio_800)
                return oversampling_ratio_800 < 1.0

            undersampled = False
            if not spacial_res():
                undersampled = True
            if verbose:
                if not res_400():
                    undersampled = True
                if not res_800():
                    undersampled = True

            if undersampled:
                print("      WARNING: system is under sampled")
    """
    2023-12-15: broken and don't care about this as much right now
    this is now s/n dependent and API changed

    microscope_data_dir = microscope.usc.get_microscope_data_dir()
    print("Calibration (microscope default)")
    for propk, propv in config.cal_load(name=microscope_name,
                                        load_data_dir=False).items():
        print("  %s: %s" % (propk, propv))
    print("Calibration (including user)")
    for propk, propv in config.cal_load(name=microscope_name,
                                        load_data_dir=True).items():
        print("  %s: %s" % (propk, propv))
    # TODO: at some point this will need to be per serial number in lieu of global calibration
    ff_filename = os.path.join(microscope_data_dir,
                               "imager_calibration_ff.tif")
    has_ff = os.path.exists(ff_filename)
    print("Flat field calibration present: %s" % has_ff)
    """


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Print microscope calibration info")
    parser.add_argument("--microscope")
    parser.add_argument("--sn")
    add_bool_arg(parser, "--verbose", default=False)
    args = parser.parse_args()

    run(microscope_name=args.microscope,
        microscope_sn=args.sn,
        verbose=args.verbose)


if __name__ == "__main__":
    main()
