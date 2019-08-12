#!/usr/bin/env python
'''
Planner test harness
'''

import uvscada.planner
from uvscada.cnc_hal import lcnc_ar
#from config import get_config
from uvscada.util import add_bool_arg
from uvscada.imager import Imager
import uvscada.gxs700_util
from uvscada.wps7 import WPS7

import argparse
import json
import os
import shutil
import time

SW_HV = 1
SW_FIL = 2

store_bin = False
store_png = True

class DryCheckpoint(Exception):
    pass

class XrayImager(Imager):
    def __init__(self, dry):
        Imager.__init__(self)
        self.dry = dry
        # About 3 seconds exposure required for range I'm using
        self.shot_on = 3
        # 10% duty cycle
        self.shot_off = 27
        # XXX FIXME TODO
        # I'm actually firing at 60V @ 8A => 480W
        # With 100W dissapation I can actually run at about 20% duty cycle
        # need some way to more accurately measure thermals
        # in the meantime hack this in half
        watt = 60 * 8
        print 'WARNING: set for %dW duty cycle.  Running at higher power may damage head' % watt
        self.shot_off = self.shot_on * watt / 100 - self.shot_on
        print 'Shot off time: %0.1f' % self.shot_off

        print 'Warming filament...'
        # Should dry do this?
        # Tests WPS connectivity and shouldn't fire the x-ray
        wps.on(SW_FIL)
        self.fil_on = time.time()
        self.fire_last = 0
        
        self.gxs = uvscada.gxs700_util.ez_open(verbose=False)
        
        self.gxs.wait_trig_cb = self.fire

    def fire(self):
        print 'Checking filament'
        wait = 5 - time.time() - self.fil_on
        if wait > 0:
            print 'Waiting %0.1f sec for filament to warm...' % wait
            if self.dry:
                print 'DRY: skip wait'
            else:
                time.sleep(wait)
        
        print 'Checking head temp'
        wait = self.shot_off - (time.time() - self.fire_last)
        print 'Waiting %0.1f sec for head to cool...' % wait
        if wait > 0:
            if self.dry:
                print 'DRY: skip wait'
            else:
                time.sleep(wait)
        print 'Head ready'
        
        try:
            if self.dry:
                print 'DRY: not firing'
            else:
                print 'X-RAY: BEAM ON %0.1f sec' % self.shot_on
                wps.on(SW_HV)
                time.sleep(self.shot_on)
                self.fire_last = time.time()
        finally:
            print 'X-RAY: BEAM OFF'
            wps.off(SW_HV)
        
        if self.dry:
            # Takes a while to download and want this to be quick
            #self.gxs.sw_trig()
            self.gxs.hw_trig_disarm()
            raise DryCheckpoint()

    def get(self):
        try:
            img_bin = self.gxs.cap_bin()
        except DryCheckpoint:
            if self.dry:
                print 'DRY: skipping image'
                return None
            raise
        print 'x-ray: decoding'
        img_dec = uvscada.gxs700.GXS700.decode(img_bin)
        # Cheat a little
        img_dec.raw = img_bin
        return img_dec

def take_picture(fn_base):
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
    parser.add_argument('--host', default='mk', help='Host.  Activates remote mode')
    parser.add_argument('--port', default=22617, type=int, help='Host port')
    parser.add_argument('--overwrite', action='store_true')
    add_bool_arg(parser, '--dry', default=True, help='Due to health hazard, default is True')
    add_bool_arg(parser, '--bin', default=False, help='Store raw .bin')
    add_bool_arg(parser, '--png', default=True, help='Store 16 bit .png')
    parser.add_argument('scan_json', nargs='?', default='scan.json', help='Scan parameters JSON')
    parser.add_argument('out', nargs='?', default='out/default', help='Output directory')
    args = parser.parse_args()

    store_bin = args.bin
    store_png = args.png

    if os.path.exists(args.out):
        if not args.overwrite:
            raise Exception("Refusing to overwrite")
        shutil.rmtree(args.out)
    if not args.dry:
        os.mkdir(args.out)

    wps = WPS7()
    imager = XrayImager(dry=args.dry)
    #imager = MockImager()
    hal = lcnc_ar.LcncPyHalAr(host=args.host, local_ini='config/xray/rsh.ini', dry=args.dry)
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
        img_sz = (1344, 1850)
        # Wonder if this is exact?
        # should measure broken sensor under microscope
        mm_per_pix = 1 / 55.
        planner = uvscada.planner.Planner(json.load(open(args.scan_json)), hal, imager=imager,
                    img_sz=img_sz, unit_per_pix=mm_per_pix,
                    out_dir=args.out,
                    progress_cb=None,
                    dry=args.dry,
                    log=None, verbosity=2)
        planner.take_picture = take_picture
        planner.run()
    finally:
        print 'Forcing x-ray off at exit'
        wps.off(SW_HV)
        time.sleep(0.2)
        wps.off(SW_FIL)
        hal.ar_stop()

