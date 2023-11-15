import time


def move_str(moves):
    ret = ""
    for axis in sorted(moves.keys()):
        if ret:
            ret += " "
        ret += "%s%+0.3f" % (axis.upper(), moves[axis])
    return ret


def stabalize_camera_start(imager, usj):
    if imager.source_name == "toupcamsrc":
        # gain takes a while to ramp up
        print("stabalizing camera")
        time.sleep(1)


def stabalize_camera_snap(imager, usj):
    time.sleep(1.5)
