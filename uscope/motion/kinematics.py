import time


class Kinematics:
    def __init__(self,
                 imager=None,
                 motion=None,
                 tsettle_motion=0.0,
                 tsettle_hdr=0.0,
                 log=None):
        self.imager = imager
        assert self.imager
        self.motion = motion
        assert self.motion
        self.tsettle_motion = tsettle_motion
        self.tsettle_hdr = tsettle_hdr
        if log is None:

            def log(s=''):
                print(s)

        self.log = log
        self.log("Kinematics(): tsettle_motion: %0.3f" % self.tsettle_motion)
        self.log("Kinematics(): tsettle_hdr: %0.3f" % self.tsettle_hdr)

    # May be updated as objective is changed
    def set_tsettle_motion(self, tsettle_motion):
        self.tsettle_motion = tsettle_motion

    def set_tsettle_hdr(self, tsettle_hdr):
        self.tsettle_hdr = tsettle_hdr

    def wait_motion(self):
        if self.tsettle_motion <= 0:
            return
        tsettle = self.tsettle_motion - self.motion.since_last_motion()
        self.log("FIXME TMP: this tsettle_motion: %0.3f" % tsettle)
        if tsettle > 0.0:
            time.sleep(tsettle)

    def wait_hdr(self):
        if self.tsettle_hdr <= 0:
            return
        tsettle = self.tsettle_hdr - self.imager.since_properties_change()
        self.log("FIXME TMP: this tsettle_hdr: %0.3f" % tsettle)
        if tsettle > 0.0:
            time.sleep(tsettle)

    def frame_sync(self):
        tstart = time.time()
        images = self.imager.get()
        tend = time.time()
        self.log("FIXME TMP: flush image took %0.3f" % (tend - tstart, ))
        assert len(images) == 1, "Expecting single image"

    def wait_imaging_ok(self, flush_image=True):
        """
        Return once its safe to image
        Could be due to vibration, exposure settings, frame sync, etc
        """
        self.wait_motion()
        self.wait_hdr()

        # In an ideal world we'd compare elapsed time vs exposure
        # Otherwise if its close snap an image to sync up
        if flush_image:
            self.frame_sync()
