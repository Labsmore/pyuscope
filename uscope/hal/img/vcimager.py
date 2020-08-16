from imager import *
import VideoCapture as VC
import time


class VCImager(Imager):
    def __init__(self):
        if camera_in_use():
            print('WARNING: camera in use, not loading imager')
            raise Exception('Camera in use')
        if not VC:
            raise Exception('Failed to import VC')

        self.cam = VC.Device()
        # Some devices first image is special, throw it away
        self.cam.getImage()
        # give sometime for the device to come up
        time.sleep(1)

    def take_picture(self, file_name_out=None):
        # capture the current image
        img = self.cam.getImage()
        # on windows this causes the app to block on a MS Paint window..not desirable
        #img.show()
        img.save(file_name_out)

    def __del__(self):
        # Why did example have this?  Shouldn't this happen automatically?
        del self.cam  # no longer need the cam. uninitialize
