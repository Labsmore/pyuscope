from uscope.threads import ShutdownPhase, SimpleThread

import numpy as np
import threading
import time
import traceback


class ImagerControlThreadBase:
    # Max exposure change factor per auto exposure iteration
    AE_MAX_STEP = 4.0

    def __init__(self, microscope):
        self.microscope = microscope
        self.loop_time = 1.0

        self._auto_exposure = False
        self._ae_target = 0.4
        # Assuming exposure is linear we should be able to do a simple calculation
        #self._exposure_p = 1.0
        #self._exposure_last = None

        self.running = threading.Event()
        self.running.set()

    def shutdown_request(self, phase):
        if phase == ShutdownPhase.FINAL:
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

        if exposure_now is None or exposure_now <= 0:
            # Can't scale an unknown exposure
            return

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
        # Fully black frame (lens cap, light off): don't divide by zero
        # Treat as very dark so exposure steps up at the max rate
        average_now = max(average_now, 1e-3)
        new_exposure = None
        store = getattr(self.microscope, "cal_store", None)
        if store is not None:
            entry = store.get("imager.response")
            if entry is not None and entry.value:
                try:
                    from uscope.cal.analysis.linearity import invert_response
                    t = invert_response(self._ae_target,
                                        entry.value,
                                        exposure_now=exposure_now,
                                        average_now=average_now)
                    if t is not None:
                        new_exposure = max(1, int(round(t)))
                except Exception:
                    new_exposure = None
        if new_exposure is None:
            ratio = self._ae_target / average_now
            # Sensor response isn't perfectly linear and the camera lags
            # Limit the per iteration change to avoid large overshoot / oscillation
            ratio = min(max(ratio, 1.0 / self.AE_MAX_STEP), self.AE_MAX_STEP)
            new_exposure = max(1, int(round(exposure_now * ratio)))
        else:
            # Safety clamp even with a model
            lo = max(1, int(round(exposure_now / self.AE_MAX_STEP)))
            hi = int(round(exposure_now * self.AE_MAX_STEP))
            new_exposure = min(max(new_exposure, lo), hi)
        0 and print(
            f"EXPOSURE: {exposure_now} => {new_exposure}, w/ target {self._ae_target} currently {average_now}, error {error}"
        )
        if new_exposure == exposure_now:
            return

        self.microscope.imager_ts().set_exposure(new_exposure)
        #self._exposure_last = exposure_now
        #self._exposure_average_last = average_now

    def loop(self):
        if self._auto_exposure:
            self.run_auto_exposure()

    def run(self):
        tlast = time.time()
        while self.running.is_set():
            dt = time.time() - tlast
            time.sleep(max(self.loop_time - dt, 0.0))
            # Period is measured between loop() starts
            tlast = time.time()
            try:
                self.loop()
            except Exception as e:
                self.log('WARNING: imager thread crashed: %s' % str(e))
                traceback.print_exc()


class SimpleImagerControlThread(ImagerControlThreadBase, SimpleThread):

    def __init__(self, microscope):
        SimpleThread.__init__(self)
        ImagerControlThreadBase.__init__(self, microscope)

    def log(self, msg=""):
        self.microscope.log(msg)
