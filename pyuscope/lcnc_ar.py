#!/usr/bin/env python

from uvscada.cnc_hal import lcnc_ar

import argparse
import time

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='LinuxCNC automatic remote client test')
    parser.add_argument('host', help='Host')
    args = parser.parse_args()

    hal = None
    try:
        hal = lcnc_ar.LcncPyHalAr(host=args.host, dry=False, log=None)
        hal.home()
        print hal.limit()
        #time.sleep(1)
        print 'getting ready to hal'
        hal._cmd('G90 G0 X100')
        hal._cmd('G90 G0 X0')
        print 'Movement done'
        #hal.mv_rel({'x': )
    finally:
        print 'Shutting down hal'
        if hal:
            hal.ar_stop()
