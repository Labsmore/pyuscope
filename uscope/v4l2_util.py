'''
https://github.com/antmicro/python3-v4l2/
'''
#
try:
    import v4l2
except:
    v4l2 = None

import fcntl
import errno
import glob
from uscope.util import tostr


def get_device_controls_ex(fd, verbose=False):
    assert fd >= 0, fd
    # original enumeration method
    queryctrl = v4l2.v4l2_queryctrl(v4l2.V4L2_CID_BASE)

    verbose and print("Querying controls...")
    while queryctrl.id < v4l2.V4L2_CID_LASTP1:
        verbose and print("check main %d (%d)" %
                          (queryctrl.id, queryctrl.id - v4l2.V4L2_CID_BASE))
        try:
            fcntl.ioctl(fd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
        except IOError as e:
            verbose and print(" res: no")
            # this predefined control is not supported by this device
            assert e.errno == errno.EINVAL
            queryctrl.id += 1
            continue
        verbose and print(" res: yes")
        yield (queryctrl, "user", queryctrl.id - v4l2.V4L2_CID_BASE)
        queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)

    queryctrl.id = v4l2.V4L2_CID_CAMERA_CLASS_BASE
    while queryctrl.id <= v4l2.V4L2_CID_PRIVACY:
        verbose and print(
            "check cam %d (%d)" %
            (queryctrl.id, queryctrl.id - v4l2.V4L2_CID_CAMERA_CLASS_BASE))
        try:
            fcntl.ioctl(fd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
        except IOError as e:
            verbose and print(" res: no")
            # no more custom controls available on this device
            assert e.errno == errno.EINVAL
            queryctrl.id += 1
            continue
        verbose and print(" res: yes")
        yield (queryctrl, "cam",
               queryctrl.id - v4l2.V4L2_CID_CAMERA_CLASS_BASE)
        queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)

    queryctrl.id = v4l2.V4L2_CID_PRIVATE_BASE
    while True:
        verbose and print(
            "check private %d (%d)" %
            (queryctrl.id, queryctrl.id - v4l2.V4L2_CID_PRIVATE_BASE))
        try:
            fcntl.ioctl(fd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
        except IOError as e:
            verbose and print(" res: no")
            # no more custom controls available on this device
            assert e.errno == errno.EINVAL
            break
        verbose and print(" res: yes")
        yield (queryctrl, "private", queryctrl.id - v4l2.V4L2_CID_PRIVATE_BASE)
        queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)


def get_device_controls(fd):
    for queryctrl, group, index in get_device_controls_ex(fd):
        yield queryctrl


'''
Control: Red Balance
  Range: 0 - 1023
Control: Blue Balance
  Range: 0 - 1023
Control: Gain
  Range: 0 - 511
Control: Exposure
  Range: 0 - 800
'''


# Return name of all controls
def ctrls(fd):
    ret = []
    for queryctrl in get_device_controls(fd):
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            continue

        ret.append(queryctrl.name.decode("ascii"))

    return ret


def dump_control_names(fd):
    print("valid control names")
    for queryctrl, group, index in get_device_controls_ex(fd):
        print("  %s (%s: %d)" % (queryctrl.name.decode("ascii"), group, index))
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            print("    disabled")
        else:
            print("    range: %d to %d" %
                  (queryctrl.minimum, queryctrl.maximum))
            print("    default: %d" % (queryctrl.default, ))
            print("    step: %d" % (queryctrl.step, ))


def ctrl_get(fd, name):
    for queryctrl in get_device_controls(fd):
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            continue
        if queryctrl.name.decode("ascii") != name:
            continue

        control = v4l2.v4l2_control(queryctrl.id)
        fcntl.ioctl(fd, v4l2.VIDIOC_G_CTRL, control)
        return control.value

    dump_control_names(fd)
    raise ValueError("Failed to find control %s" % name)


def ctrl_set(fd, name, value):
    for queryctrl in get_device_controls(fd):
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            continue
        if queryctrl.name.decode("ascii") != name:
            continue
        # print("Check %s: %d <= %d <= %d" % (name, queryctrl.minimum, value, queryctrl.maximum))
        if value < queryctrl.minimum or value > queryctrl.maximum:
            raise ValueError("Require %d <= %d <= %d" %
                             (queryctrl.minimum, value, queryctrl.maximum))

        control = v4l2.v4l2_control(queryctrl.id, value)
        try:
            fcntl.ioctl(fd, v4l2.VIDIOC_S_CTRL, control)
        except:
            print("failed on", name, value)
            raise
        return

    dump_control_names(fd)
    raise ValueError("Failed to find control %s" % name)


def ctrl_minmax(fd, name):
    for queryctrl in get_device_controls(fd):
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            continue
        if queryctrl.name.decode("ascii") != name:
            continue
        return queryctrl.minimum, queryctrl.maximum

    dump_control_names(fd)
    raise ValueError("Failed to find control %s" % name)


def dump_devices():
    print("Available devices:")
    cp = v4l2.v4l2_capability()
    for dev_name in sorted(glob.glob("/dev/video*")):
        vd = open(dev_name, "r")
        assert fcntl.ioctl(vd, v4l2.VIDIOC_QUERYCAP, cp) == 0
        vd.close()
        found_name = tostr(cp.card)
        print(f"  {found_name}")


def find_device(name):
    cp = v4l2.v4l2_capability()
    for dev_name in sorted(glob.glob("/dev/video*")):
        vd = open(dev_name, "r")
        assert fcntl.ioctl(vd, v4l2.VIDIOC_QUERYCAP, cp) == 0
        vd.close()
        found_name = tostr(cp.card)
        # print("card", dev_name, found_name, name)
        if found_name == name:
            return dev_name
    else:
        dump_devices()
        raise Exception(f"Failed to find video device {name}")
