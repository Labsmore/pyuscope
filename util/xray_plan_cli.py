#!/usr/bin/env python3
'''
Planner test harness
'''

import uscope.planner
from uscope.hal.cnc import lcnc_ar
#from config import get_config
from uscope.util import add_bool_arg
from uscope.hal.img.imager import Imager
from gxs700 import usbint
from gxs700 import xray

import argparse
import json
import os
import shutil
import time

store_bin = False
store_png = True


class DryCheckpoint(Exception):
    pass


class XrayImager(Imager):
    def __init__(self, dry):
        Imager.__init__(self)
        self.dry = dry
        self.xr = None
        self.gxs = None

        self.xr = xray.WPS7XRay(verbose=args.verbose, dry=self.dry)

        print('Warming filament...')
        self.xr.warm()
        self.gxs = usbint.GXS700()

    def __del__(self):
        if self.xr:
            self.xr.off()

    def get(self):
        try:
            # XXX: there used to be a hack here to prevent image download
            # for quicker dry check
            img_bin = self.gxs.cap_bin(xr=self.xr)
        except DryCheckpoint:
            if self.dry:
                print('DRY: skipping image')
                return None
            raise
        print('x-ray: decoding')
        img_dec = self.gxs.decode(img_bin)
        # Cheat a little
        img_dec.raw = img_bin
        return img_dec

# TODO: planner needs to support more image types
# something like this needs to be rolled into the core more
class MyPlanner(uscope.planner.Planner):
    def take_picture(self, fn_base):
        planner.hal.settle()
        img_dec = planner.imager.get()
        if not planner.dry:
            if store_bin:
                open(fn_base + '.bin', 'w').write(img_dec.raw)
            if store_png:
                img_dec.save(fn_base + '.png')
        planner.all_imgs += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Planner module command line')
    parser.add_argument('--host',
                        default='mk',
                        help='Host.  Activates remote mode')
    parser.add_argument('--port', default=22617, type=int, help='Host port')
    parser.add_argument('--overwrite', action='store_true')
    add_bool_arg(parser,
                 '--dry',
                 default=True,
                 help='Due to health hazard, default is True')
    add_bool_arg(parser, '--bin', default=False, help='Store raw .bin')
    add_bool_arg(parser, '--png', default=True, help='Store 16 bit .png')
    parser.add_argument('scan_json',
                        nargs='?',
                        default='scan.json',
                        help='Scan parameters JSON')
    parser.add_argument('out',
                        nargs='?',
                        default='out/default',
                        help='Output directory')
    args = parser.parse_args()

    store_bin = args.bin
    store_png = args.png

    if os.path.exists(args.out):
        if not args.overwrite:
            raise Exception("Refusing to overwrite")
        shutil.rmtree(args.out)
    if not args.dry:
        os.mkdir(args.out)

    imager = XrayImager(dry=args.dry)
    #imager = MockImager()
    hal = lcnc_ar.LcncPyHalAr(host=args.host,
                              local_ini='config/xray/rsh.ini',
                              dry=args.dry)
    try:
        #config = get_config()
        '''
        2015-10-03 improved calibration
        p4/x-ray/04_l_65kvp_75map/png/c000_r000.png
        slot length meas: 5.4 mm
        398 - 101 pix = 297 pix
        297 / 5.4 mm = 55 pix / mm
        mm / pix = 1 / 55. = 0.018181818
        Was using: 0.019221622 (1.400 x 1.017")
        This makes the sensor (including unusable areas):
            1344 / 55 = 24.4 mm => 0.962"
            1850 / 55 = 33.6 mm => 1.324"
            1.324 x 0.962"
        '''
        # Sensor *roughly* 1 x 1.5"
        # 10 TPI stage
        # Run in inch mode long run but for now stage is set for mm
        # about 25 / 1850
        #img_sz = (1850, 1344)
        # mechanically this is better
        # Post process data
        img_sz = imager.gxs.WH
        # Wonder if this is exact?
        # should measure broken sensor under microscope
        mm_per_pix = 1 / 55.
        planner = MyPlanner(json.load(open(args.scan_json)),
                                         hal,
                                         imager=imager,
                                         img_sz=img_sz,
                                         unit_per_pix=mm_per_pix,
                                         out_dir=args.out,
                                         progress_cb=None,
                                         dry=args.dry,
                                         log=None,
                                         verbosity=2)
        planner.run()
    finally:
        print('Forcing x-ray off at exit')
        imager.off()
        hal.ar_stop()
