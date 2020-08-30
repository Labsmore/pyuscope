#!/usr/bin/env python

import uscope.planner
from uscope.hal.cnc import lcnc_ar
#from config import get_config
from uscope.util import add_bool_arg
from uscope.hal.img.imager import Imager
import toupcam

import argparse
import json
import os
import shutil
import time

class DryCheckpoint(Exception):
    pass

class XrayImager(Imager):
    def __init__(self, dry):
        Imager.__init__(self)
        self.dry = dry
        self.hcam = None
        self.buf = None
        self.total = 0

        tcs = toupcam.Toupcam.EnumV2()
        if len(tcs) == 0:
            raise Exception('failed to open camera')
        self.tc = tcs[0]
        print('{}: flag = {:#x}, preview = {}, still = {}'.format(s[0].displayname, self.tc[0].model.flag, self.tc[0].model.preview, self.tc[0].model.still))
        for r in self.tc[0].model.res:
            print('\t = [{} x {}]'.format(r.width, r.height))
        self.hcam = toupcam.Toupcam.Open(self.tc.id)
        assert self.hcam

        width, height = self.hcam.get_Size()
        bufsize = ((width * 24 + 31) // 32 * 4) * height
        print('image size: {} x {}, bufsize = {}'.format(width, height, bufsize))
        self.buf = bytes(bufsize)
        assert self.buf:
        try:
            self.hcam.StartPullModeWithCallback(self.cameraCallback, self)
        except toupcam.HRESULTException:
            raise Exception('failed to start camera')

    # the vast majority of callbacks come from toupcam.dll/so/dylib internal threads
    @staticmethod
    def cameraCallback(nEvent, ctx):
        if nEvent == toupcam.TOUPCAM_EVENT_IMAGE:
            ctx.CameraCallback(nEvent)

    def CameraCallback(self, nEvent):
        if nEvent == toupcam.TOUPCAM_EVENT_IMAGE:
            try:
                self.hcam.PullImageV2(self.buf, 24, None)
                self.total += 1
                print('pull image ok, total = {}'.format(self.total))
            except toupcam.HRESULTException:
                print('pull image failed')
        else:
            print('event callback: {}'.format(nEvent))


    def __del__(self):
        if self.hcam:
            self.hcam.Close()
        self.hcam = None
        self.buf = None
    

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
        planner = uscope.planner.Planner(json.load(open(args.scan_json)), hal, imager=imager,
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

