#!/usr/bin/env python3
from uscope.util import add_bool_arg
import argparse
import json
import matplotlib.pyplot as plt

from uscope import cal_util

# FIXME: make distance vs origin
# no garauntee camera is positioned well


def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5


def run(in_dir="backlash"):
    j = json.load(open(in_dir + "/log.json"))
    pj = {}
    for axis, axisv in sorted(j["axes"].items()):
        print("")
        print(axis)
        results = {}
        origin = None
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

        plt.plot(results.keys(), results.values())
        # plt.show()
        plt.savefig("%s/processed_%s.png" % (in_dir, axis))
    open("%s/processed.json" % (in_dir, ), "w").write(
        json.dumps(pj, sort_keys=True, indent=4, separators=(',', ': ')))


def main():
    parser = argparse.ArgumentParser(
        description="Move testing axes using microscope.json configuration")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    parser.add_argument("dir", help="Data directory")
    args = parser.parse_args()

    run(in_dir=args.dir)


if __name__ == "__main__":
    main()
