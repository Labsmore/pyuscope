#!/usr/bin/env python3
from uscope.util import add_bool_arg
import argparse
import json
import matplotlib.pyplot as plt
import glob

from uscope import cal_util

# FIXME: make distance vs origin
# no garauntee camera is positioned well


def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5


def get_positions(in_dir, adict):
    ret = {}
    for stepk, stepv in adict.items():
        fn = in_dir + "/" + stepv["fn"]
        # print("Processing %s..." % fn)
        x, y = cal_util.find_centroid_fn(fn)
        ret[stepv["fn"]] = (x, y)
    return ret


def median_pos(poss):
    xs = []
    ys = []
    for (x, y) in poss:
        xs.append(x)
        ys.append(y)
    return sorted(xs)[len(xs) // 2], sorted(ys)[len(ys) // 2]


def run(in_dir=None):
    if not in_dir:
        in_dir = sorted(glob.glob("out/repeat_xy_*"))[-1]
    print("Loading from %s" % in_dir)
    j = json.load(open(in_dir + "/log.json"))
    pj = {}
    for axis, axisv in sorted(j["axes"].items()):
        print("")
        print(axis)
        results = {}
        origin = None
        imw, imh = 5440, 3648
        scalar_poss = get_positions(in_dir, axisv["ref_move"]["steps"])
        scalar_pos = median_pos(scalar_poss.values())
        steps_poss = get_positions(in_dir, axisv["steps"])
        steps_pos = median_pos(steps_poss.values())
        ref_pix = distance(scalar_pos, steps_pos)
        print("Reference move")
        ref_mag = axisv["ref_move"]["mag"]
        print("  mm: %0.1f" % ref_mag)
        print("  pixels: %0.1f" % ref_pix)
        imw_mm = ref_mag / ref_pix * imw
        print("  imw: %0.1f mm" % imw_mm)
        continue

        for stepk, stepv in axisv.items():
            fn = in_dir + "/" + stepv["fn"]
            # print("Processing %s..." % fn)
            x, y = cal_util.find_centroid_fn(fn)
            # print("Found x %0.1f y %0.1f" % (x, y))
            if origin is None:
                origin = x, y

            d = distance(origin, (x, y))
            results[stepk] = d
            print("%s step %s: %0.3f" % (axis, stepk, d))
        pj[axis] = results

        plt.clf()
        plt.plot(list(results.keys()), list(results.values()))
        # plt.show()
        plt.savefig("%s/processed_%s.png" % (in_dir, axis))
    open("%s/processed.json" % (in_dir, ), "w").write(
        json.dumps(pj, sort_keys=True, indent=4, separators=(',', ': ')))


def main():
    parser = argparse.ArgumentParser(
        description="Move testing axes using microscope.json configuration")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument("dir", nargs="?", help="Data directory")
    args = parser.parse_args()

    run(in_dir=args.dir)


if __name__ == "__main__":
    main()
