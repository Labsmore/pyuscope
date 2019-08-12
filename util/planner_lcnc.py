#!/usr/bin/env python
'''
Planner test harness
'''

from uvscada import planner
from uvscada.cnc_hal import lcnc
from uvscada.lcnc.client import LCNCRPC

import argparse        

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Planner module command line')
    # ssh -L 22617:localhost:22617 mk-xray
    parser.add_argument('--host', help='Host.  Activates remote mode')
    parser.add_argument('--port', default=22617, type=int, help='Host port')
    parser.add_argument('scan_json', nargs='?', default='scan.json', help='Scan parameters JSON')
    parser.add_argument('out', nargs='?', default='out', help='Output directory')
    args = parser.parse_args()

    if args.host:
        linuxcnc = LCNCRPC(args.host, args.port)
    else:
        import linuxcnc
    
    hal = lcnc.LcncPyHal(dry=True, log=None, linuxcnc=linuxcnc)
    # 20x objective
    p = planner.Planner(args.scan_json, hal, img_sz=(544, 400), out_dir=args.out,
                progress_cb=None,
                overwrite=True, dry=True,
                img_scalar=1,
                log=None, verbosity=2)
    p.run()
