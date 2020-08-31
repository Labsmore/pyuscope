from PIL import Image
import usb.core
import usb.util
import glob

VID_TOUPTEK = 0x0547
PID_MU800 = 0x6801


def get_scaled(image, factor, filt=Image.NEAREST):
    return image.resize(
        (int(image.size[0] * factor), int(image.size[1] * factor)), filt)


def auto_detect_source():
    'gst-toupcamsrc'

    # find our device
    for dev in usb.core.find(find_all=True):
        if dev.idVendor != VID_TOUPTEK:
            continue
        print("ADS: found VID 0x%04X, PID 0x%04X" %
              (dev.idVendor, dev.idProduct))
        # Way to detect if it is a modifed driver?
        if dev.idProduct == PID_MU800:
            print("ADS: found ToupTek MU800 camera")
            assert glob.glob("/dev/video*"), "Camera not found"
            return "gst-v4l2src-mu800"
        else:
            print("ADS: found ToupTek generic camera")
            return "gst-toupcamsrc"

    # Fallback to generic gst-v4l2 if there is an unknown camera
    if glob.glob("/dev/video*"):
        print("ADS: found /dev/video device")
        return "gst-v4l2src"

    print("ADS: giving up")
    return "gst-testsrc"
