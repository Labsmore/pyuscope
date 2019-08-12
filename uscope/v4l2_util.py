'''
pythonic v4l2 wrapper
TOOD: replace this entirely with ctypes to eliminate dependency

sudo pip install v4l2

http://nullege.com/codes/show/src%40v%404%40v4l2-0.2%40tests.py/276/v4l2.VIDIOC_QUERYCAP/python
http://linuxtv.org/downloads/v4l-dvb-apis/control.html
linux/videodev2.h
'''

import v4l2
import fcntl
import errno

'''
vd = open('/dev/video0', 'rw')
cp = v4l2.v4l2_capability()
print fcntl.ioctl(vd, v4l2.VIDIOC_QUERYCAP, cp)
# touptek
print cp.driver
# USB Camera (0547:6801)
print cp.card
'''

def get_device_controls(fd):
    # original enumeration method
    queryctrl = v4l2.v4l2_queryctrl(v4l2.V4L2_CID_BASE)
  
    while queryctrl.id < v4l2.V4L2_CID_LASTP1:
        try:
            fcntl.ioctl(fd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
        except IOError, e:
            # this predefined control is not supported by this device
            assert e.errno == errno.EINVAL
            queryctrl.id += 1
            continue
        yield queryctrl
        queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)
  
    queryctrl.id = v4l2.V4L2_CID_PRIVATE_BASE
    while True:
        try:
            fcntl.ioctl(fd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
        except IOError, e:
            # no more custom controls available on this device
            assert e.errno == errno.EINVAL
            break
        yield queryctrl
        queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)

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
        
        ret.append(queryctrl.name)
    
    return ret

def ctrl_get(fd, name):
    for queryctrl in get_device_controls(fd):
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            continue
        if queryctrl.name != name:
            continue
        
        control = v4l2.v4l2_control(queryctrl.id)
        fcntl.ioctl(fd, v4l2.VIDIOC_G_CTRL, control)
        return control.value
    
    raise ValueError("Failed to find control %s" % name)

def ctrl_set(fd, name, value):
    for queryctrl in get_device_controls(fd):
        if queryctrl.flags & v4l2.V4L2_CTRL_FLAG_DISABLED:
            continue
        if queryctrl.name != name:
            continue
        if value < queryctrl.minimum or value > queryctrl.maximum:
            raise ValueError("Require %d <= %d <= %d" % (queryctrl.minimum, value, queryctrl.maximum))

        control = v4l2.v4l2_control(queryctrl.id, value)
        fcntl.ioctl(fd, v4l2.VIDIOC_S_CTRL, control)
        return
    
    raise ValueError("Failed to find control %s" % name)

