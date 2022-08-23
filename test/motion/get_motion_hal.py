#!/usr/bin/env python3
'''
Planner test harness
'''

# from uscope.cnc_hal import lcnc
# from uscope.lcnc.client import LCNCRPC
from uscope.gui.plugin import get_cnc_hal
from uscope.config import get_usj
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Planner module command line')
    # ssh -L 22617:localhost:22617 mk-xray
    parser.add_argument('--host', help='Host.  Activates remote mode')
    parser.add_argument('--port', default=22617, type=int, help='Host port')
    args = parser.parse_args()

    usj = get_usj()
    # linuxcnc = LCNCRPC(args.host, args.port)
    hal = get_cnc_hal(usj)

