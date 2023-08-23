#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope import config
from uscope.imagep.util import RC_CONST
import os

def run(microscope_name=None, verbose=True):
    print("Reading config...")
    usc = config.get_usc(name=microscope_name)
    microscope_data_dir = os.path.join("data", "microscopes", config.default_microscope_name())

    print("Image size:")
    raw_wh = usc.imager.raw_wh()
    print("  Step 1: raw sensor pixels: %uw x %uh" % (raw_wh[0], raw_wh[1]))
    crop_w, crop_h = usc.imager.cropped_wh()
    print("  Step 2: crop %s => %uw x %uh" % (usc.imager.crop_tblr(), crop_w, crop_h))
    image_scalar = usc.imager.scalar()
    print("  Step 3: apply scalar %0.2f" % image_scalar)
    final_wh = usc.imager.final_wh()
    print("  Step 4: final sensor pixels: %uw x %uh (%u)" %
          (final_wh[0], final_wh[1], final_wh[0] * final_wh[1]))

    print("Objectives")
    for objective in usc.get_scaled_objectives():
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
            res_400 = RC_CONST * 400 / (2 * na)
            oversampling_ratio_400 = res_400 / (objective["um_per_pixel"] *
                                                1000)
            print("      Resolution @ 400 nm: %0.1f nm" % res_400)
            print("        Oversampling ratio: %0.2f" % oversampling_ratio_400)
            resolvable_pixels = x_view * 1e6 / res_400 * y_view * 1e6 / res_400
            print("        Resolvable pixels: %0.1f" % resolvable_pixels)
            res_800 = RC_CONST * 800 / (2 * na)
            oversampling_ratio_800 = res_800 / (objective["um_per_pixel"] *
                                                1000)
            print("      Resolution @ 800 nm: %0.1f nm" % res_800)
            print("        Oversampling ratio: %0.2f" % oversampling_ratio_800)
            if oversampling_ratio_400 < 1.0:
                print("      WARNING: system is under sampled")

    print("Calibration (microscope default)")
    for propk, propv in config.cal_load(name=microscope_name,
                                        load_data_dir=False).items():
        print("  %s: %s" % (propk, propv))
    print("Calibration (including user)")
    for propk, propv in config.cal_load(name=microscope_name,
                                        load_data_dir=True).items():
        print("  %s: %s" % (propk, propv))
    # TODO: at some point this will need to be per serial number in lieu of global calibration
    ff_filename = os.path.join(microscope_data_dir, "imager_calibration_ff.tif")
    has_ff = os.path.exists(ff_filename)
    print("Flat field calibration present: %s" % has_ff)

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Print microscope calibration info")
    parser.add_argument("--microscope")
    add_bool_arg(parser, "--verbose", default=True)
    args = parser.parse_args()

    run(microscope_name=args.microscope, verbose=args.verbose)


if __name__ == "__main__":
    main()
