#!/usr/bin/env python3
'''
512 x 512
Scan speed: slightly left of center
    ie if 0 is left setting and 7 is right, "3"
About 4 seconds
'''

import uscope.planner
from uscope.hal.img.imager import Imager
from uscope.hal.cnc.hal import MotionHAL
from uscope.util import add_bool_arg

import argparse
import json
import os
import shutil
import time
import sys

class CsvImager(Imager):
    def __init__(self, csvf, verbose=False):
        Imager.__init__(self)
        self.csvf = csvf

    def take(self, fn_base):
        print("get image")
        self.csvf.write("image,%s\n" % os.path.basename(fn_base))
        return {}

class CsvHal(MotionHAL):
    def __init__(self, csvf, axes='xy', log=None, dry=False):
        MotionHAL.__init__(self, log, dry)
        self.csvf = csvf

        self._axes = list(axes)
        self._pos = {}
        # Assume starting at 0.0 until causes problems
        for axis in self._axes:
            self._pos[axis] = 0.0

    def _log(self, msg):
        if self.dry:
            self.log('Mock-dry: ' + msg)
        else:
            self.log('Mock: ' + msg)

    def axes(self):
        return self._axes

    def home(self, axes):
        raise Exception("not supported")

    def take_picture(self, file_name):
        self._log('taking picture to %s' % file_name)

    def mv_abs(self, pos):
        for axis, apos in pos.items():
            self._pos[axis] = apos
        self._log(
            'absolute move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in sorted(pos.items())]))
        self.csvf.write("move,%0.6f,%0.6f\n" % (self._pos['x'], self._pos['y']))
        self.csvf.flush()

    def mv_rel(self, delta):
        for axis, adelta in delta.items():
            self._pos[axis] += adelta
        self._log(
            'relative move to ' +
            ' '.join(['%c%0.6f' % (k.upper(), v) for k, v in delta.items()]))
        if len(delta):
            self.csvf.write("move %0.6f %0.6f\n" % (self._pos['x'], self._pos['y']))
            self.csvf.flush()

    def pos(self):
        return dict(self._pos)

    def settle(self):
        # No hardware to let settle
        pass

    #[axis], sign, self.jog_done, lambda: axis.emit_pos())
    def forever(self, axes, run, progress):
        raise Exception("not supported")

    def ar_stop(self):
        pass

def main():
    parser = argparse.ArgumentParser(description='Planner module command line')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--roi', type=str)
    parser.add_argument('--border', type=float, default=0.1)
    parser.add_argument('--img-wh', type=int, default=512)
    parser.add_argument('--mag', type=str, required=True)
    parser.add_argument('--scan-json',
                        nargs='?',
                        default='scan.json',
                        help='Scan parameters JSON')
    parser.add_argument('out',
                        nargs='?',
                        default='out',
                        help='Output directory')
    args = parser.parse_args()

    img_sz = (args.img_wh, args.img_wh)

    """
    # FIXME: fill in table
    img_width_um = {
        # Very quick estimate
        '25kx': 6.23,
        '100x': 3.2e3/2,
        # probably more accurate basis
        # default display size of 160.0 mm
        '50x': 3.2e3,
        }[args.mag]
    """
    
    
    mm_100x = 1.6
    img_width_mm = {
        '100x': mm_100x,
        '991x': mm_100x * 100 / 991,
        }[args.mag]
    img_height_mm = img_width_mm
    img_wh_mm = img_width_mm, img_width_mm * img_sz[1] / img_sz[0]

    if os.path.exists(args.out):
        if not args.overwrite:
            raise Exception("Refusing to overwrite")
        shutil.rmtree(args.out)
    os.mkdir(args.out)

    scan_json_fn = args.scan_json
    if args.roi:
        x0, y0, x1, y1 = [float(x) for x in args.roi.split(",")]
        x1full = x1 + img_width_mm
        y1full = y1 + img_height_mm
        print("Adjusted scan range: %0.6fx,%0.6fy to %0.6fx,%0.6fy" % (x0, y0, x1full, y1full))
        scan_config = {
            "border": args.border,
            "overlap": 0.7,
            "start": {
                "x": x0,
                "y": y0,
            },
            "end": {
                "x": x1full,
                "y": y1full,
            },
        }
        print(json.dumps(scan_config))
    else:
        scan_config = json.load(open(args.scan_json))

    print(args.out + "/ahk.csv")
    fout = open(args.out + "/ahk.csv", "w")

    # fout2 = open(args.out + "/ahk2.csv", "w")
    fout2 = fout

    imager = CsvImager(csvf=fout, verbose=args.verbose)
    hal = CsvHal(csvf=fout2)

    planner = uscope.planner.Planner(scan_config=scan_config,
                        hal=hal,
                        imager_take=imager,
                        img_sz=img_sz,
                        img_wh_units=img_wh_mm,
                        origin="ll",
                        out_dir=args.out,
                        progress_cb=None,
                        log=None,
                        verbosity=2)
    planner.run()

    fout.write("test2\n")

if __name__ == "__main__":
    main()
