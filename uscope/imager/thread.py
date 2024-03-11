from uscope.threads import CommandThreadBase

import cv2 as cv
import numpy as np
import threading
import time
import traceback


class ImagerControlThreadBase(CommandThreadBase):
    def __init__(self, microscope):
        super().__init__(microscope)

        self.loop_time = 1.0

        self._auto_exposure = False
        self._ae_target = 0.4
        # Assuming exposure is linear we should be able to do a simple calculation
        #self._exposure_p = 1.0
        #self._exposure_last = None

        self.running = threading.Event()
        self.running.set()

    def shutdown(self):
        self.running.clear()

    def log(self, msg=""):
        print(msg)

    def set_auto_exposure(self, value):
        self._auto_exposure = bool(value)

    def auto_exposure(self):
        return self._auto_exposure

    def set_auto_exposure_target100(self, value):
        assert 1 <= value <= 100
        self._ae_target = value / 100

    def auto_exposure_target100(self):
        return int(self._ae_target * 100)

    def run_auto_exposure(self):
        take_center = True
        # XXX: I think exposure is actually on here
        capim = self.microscope.imager_ts().get()
        im_pil = capim.image
        # exposure_now = self.microscope.imager.get_exposure_cache()
        exposure_now = capim.exposure()
        #if self._exposure_last is None:
        #    self._exposure_last = exposure_now

        if take_center:
            width, height = im_pil.size

            left = (width - width / 3) / 2
            top = (height - height / 3) / 2
            right = (width + width / 3) / 2
            bottom = (height + height / 3) / 2

            # Crop the center of the image
            im_pil = capim.image.crop((left, top, right, bottom))

        im_np = np.array(im_pil)
        """
        If image is half as bright as it should be,
        double the exposure
        """
        # normalize
        average_now = np.average(im_np) / 255.0
        # exposure 1 to N
        # average 0 to 1
        error = self._ae_target - average_now
        new_exposure = int(self._ae_target * exposure_now / average_now)
        0 and print(
            f"EXPOSURE: {exposure_now} => {new_exposure}, w/ target {self._ae_target} currently {average_now}, error {error}"
        )

        self.microscope.imager_ts().set_exposure(new_exposure)
        #self._exposure_last = exposure_now
        #self._exposure_average_last = average_now

    def loop(self):
        if self._auto_exposure:
            self.run_auto_exposure()

    def run(self):
        tlast = time.time()
        while self.running:
            tnow = time.time()
            dt = tnow - tlast
            time.sleep(max(self.loop_time - dt, 0.0))
            try:
                self.loop()
            except Exception as e:
                self.log('WARNING: imager thread crashed: %s' % str(e))
                traceback.print_exc()
            tlast = tnow
