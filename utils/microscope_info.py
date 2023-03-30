#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope import config


def run(microscope_name=None, verbose=True):
    print("Reading config...")
    usc = config.get_usc(name=microscope_name)

    print("Image size:")
    raw_wh = usc.imager.raw_wh()
    print("  Step 1: raw sensor pixels: %uw x %uh" % (raw_wh[0], raw_wh[1]))
    print("  Step 2: crop %s" % (usc.imager.crop_tblr(), ))
    image_scalar = usc.imager.scalar()
    print("  Step 3: apply scalar %0.2f" % image_scalar)
    final_wh = usc.imager.final_wh()
    print("  Step 4: final sensor pixels: %uw x %uh" %
          (final_wh[0], final_wh[1]))

    print("Objectives")
    for objective in usc.get_scaled_objectives():
        print(f"  {objective['name']}")
        print("    x_view (post-crop): %0.3f mm" % objective['x_view'])
        print("    magnification: %s" % objective.get("magnification"))
        print("    um_per_pixel (post-scale): %0.3f" %
              objective["um_per_pixel"])
        na = objective.get("na", 0)
        print("    na: %0.3f" % na)
        if na:
            res_400 = 400 / (2 * na)
            oversampling_ratio_400 = res_400 / (objective["um_per_pixel"] *
                                                1000)
            print("      Resolution @ 400 nm: %0.1f nm" % res_400)
            print("        Oversampling ratio: %0.2f" % oversampling_ratio_400)
            res_800 = 800 / (2 * na)
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
