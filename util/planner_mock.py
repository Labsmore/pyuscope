#!/usr/bin/env python
'''
Planner test harness
'''

from uvscada import planner
from uvscada import planner_hal

import argparse
import json

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Planner module command line')
    parser.add_argument('scan_json', nargs='?', default='scan.json', help='Scan parameters JSON')
    parser.add_argument('out', nargs='?', default='out', help='Output directory')
    args = parser.parse_args()

    hal = planner_hal.MockHal(dry=True, log=None)
    # 20x objective
    p = planner.Planner(json.load(open(args.scan_json)), hal, img_sz=(544, 400), out_dir=args.out,
                progress_cb=None,
                overwrite=True, dry=True,
                img_scalar=1,
                log=None, verbosity=2)
    p.run()
