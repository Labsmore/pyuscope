#!/usr/bin/env python3

import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate objective template")
    parser.add_argument("--scalar",
                        type=float,
                        required=True,
                        help="Image width @ 4.5x w/ no barlow lens")
    args = parser.parse_args()

    ref_scalar = args.scalar
    ref_mag = 4.5

    for barlow_str in ("0.5", "1.0", "2.0"):
        barlow_scalar = float(barlow_str)

        for knob_str in ("0.7", "1.5", "3.0", "4.5"):
            knob_scalar = float(knob_str)
            mag = barlow_scalar * knob_scalar
            entry_name = "B%sx @ %sx" % (barlow_str, knob_str)
            this_mag = barlow_scalar * knob_scalar
            x_view = ref_scalar * ref_mag / this_mag
            print("""\
                {
                    "name":"%s",
                    "magnification": %0.3f,
                    "x_view": %0.3f
                },""" % (entry_name, mag, x_view))
