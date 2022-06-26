#!/usr/bin/env python3

import subprocess


def hdr_stack(eps, fns_in, fn_out):
    # /home/mcmaster/buffer/ic/arsenio/arsensio_mouse2_mz_mit20x_hdr
    str_fns_in = " ".join(fns_in)
    subprocess.check_output(
        "luminance-hdr-cli --tmo fattal --ev 0.15,0.30,0.45,0.60,0.75 %s -o %s --quality 90"
        % (str_fns_in, fn_out),
        shell=True)


exps = [0.15, 0.30, 0.45, 0.60, 0.75]
for col in range(4):
    for row in range(5):
        print("")
        print("")
        print("")
        fn_out = "out/c%03u_r%03u.jpg" % (col, row)
        print(fn_out)
        fns_in = ["c%03u_r%03u_%u.jpg" % (col, row, exp) for exp in range(5)]
        hdr_stack(exps, fns_in, fn_out)
