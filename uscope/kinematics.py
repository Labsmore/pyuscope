import time


class Kinematics:
    def __init__(
        self,
        microscope=None,
        log=None,
    ):
        self.microscope = microscope
        self.verbose = False
        if log is None:

            def log(s=''):
                print(s)

        self.log = log

    def configure(self,
                  tsettle_motion=None,
                  tsettle_hdr=None,
                  tsettle_autofocus=None):
        # some CLI apps don't require these
        # assert self.microscope.imager
        # assert self.microscope.motion

        if tsettle_motion is None:
            tsettle_motion = self.microscope.usc.kinematics.tsettle_motion_max(
            )
        self.tsettle_motion = tsettle_motion

        if tsettle_hdr is None:
            tsettle_hdr = self.microscope.usc.kinematics.tsettle_hdr()
        self.tsettle_hdr = tsettle_hdr

        if tsettle_autofocus is None:
            tsettle_autofocus = self.microscope.usc.kinematics.tsettle_autofocus(
            )
        self.tsettle_autofocus = tsettle_autofocus

        self.verbose and self.log(
            "Kinematics(): tsettle_motion: %0.3f" % self.tsettle_motion)
        self.verbose and self.log(
            "Kinematics(): tsettle_hdr: %0.3f" % self.tsettle_hdr)
        self.verbose and self.log(
            "Kinematics(): tsettle_autofocus: %0.3f" % self.tsettle_autofocus)

    # May be updated as objective is changed
    def set_tsettle_motion(self, tsettle_motion):
        self.tsettle_motion = tsettle_motion

    def set_tsettle_hdr(self, tsettle_hdr):
        self.tsettle_hdr = tsettle_hdr

    def set_tsettle_autofocus(self, tsettle_autofocus):
        self.tsettle_autofocus = tsettle_autofocus

    def sleep(self, t):
        self.verbose and self.log("kinematics sleep", t)
        time.sleep(t)

    def wait_motion(self):
        if self.microscope.motion is None or self.tsettle_motion <= 0:
            return
        tsettle = self.tsettle_motion - self.microscope.motion.since_last_motion(
        )
        self.verbose and self.log(
            "FIXME TMP: this tsettle_motion: %0.3f" % tsettle)
        if tsettle > 0.0:
            self.sleep(tsettle)

    def wait_hdr(self):
        if self.microscope.imager is None or self.tsettle_hdr <= 0:
            return
        tsettle = self.tsettle_hdr - self.microscope.imager.since_properties_change(
        )
        self.verbose and self.log(
            "FIXME TMP: this tsettle_hdr: %0.3f" % tsettle)
        if tsettle > 0.0:
            self.sleep(tsettle)

    def wait_autofocus(self):
        """
        Much more aggressive than other methods
        Let's try to keep this responsive
        """
        if self.microscope.imager is None or self.tsettle_autofocus <= 0:
            return
        tsettle = self.tsettle_autofocus - self.microscope.motion.since_last_motion(
        )
        if tsettle > 0.0:
            self.sleep(tsettle)

    def frame_sync(self):
        tstart = time.time()
        images = self.microscope.imager.get()
        tend = time.time()
        self.verbose and self.log("FIXME TMP: flush image took %0.3f" %
                                  (tend - tstart, ))
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
