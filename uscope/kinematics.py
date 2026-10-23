import time
from uscope.util import LogTimer


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
        self.last_frame_sync = None

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
        self.should_frame_sync = self.microscope.usc.kinematics.frame_sync()
        self.frame_sync_frames_hdr = self.microscope.usc.kinematics.frame_sync_frames_hdr(
        )
        self.tsettle_video_pipeline = 3.0

        # self.diagnostic_info()

    # May be updated as objective is changed
    def set_tsettle_motion(self, tsettle_motion):
        self.tsettle_motion = tsettle_motion

    def set_tsettle_hdr(self, tsettle_hdr):
        self.tsettle_hdr = tsettle_hdr

    def set_tsettle_autofocus(self, tsettle_autofocus):
        self.tsettle_autofocus = tsettle_autofocus

    def sleep(self, t):
        self.verbose and self.log("kinematics sleep %0.3f" % t)
        time.sleep(t)

    def wait_video_pipeline(self):
        if self.microscope.imager is None or self.tsettle_video_pipeline <= 0:
            return
        since_restart = self.microscope.imager.since_last_restart()
        # Pipeline never restarted (ex: CLI / mock imager)
        if since_restart is None:
            return
        tsettle = self.tsettle_video_pipeline - since_restart
        if tsettle > 0.0:
            self.log(
                "Kinematics sleeping due to video pipeline restart: %0.3f" %
                tsettle)
            self.sleep(tsettle)

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
        if not self.should_frame_sync:
            return

        imager = self.microscope.imager
        motion = self.microscope.motion
        properties_changed = True
        if self.last_frame_sync is not None:
            since_last_sync = time.time() - self.last_frame_sync
            properties_changed = imager.since_properties_change(
            ) <= since_last_sync
            motion_changed = motion is not None and motion.since_last_motion(
            ) <= since_last_sync
            if not properties_changed and not motion_changed:
                return

        # Frames already exposing / in flight when a property was written
        # still have the old value. Pull fresh frames one after another
        # rather than relying on wall time
        nframes = 1
        if properties_changed:
            nframes = max(1, self.frame_sync_frames_hdr)
        # Stamp when the flush was requested, not when it completed
        # A property change racing with the flush must trigger another sync
        tstart = time.time()
        imager_ts = self.microscope.imager_ts()
        for _framei in range(nframes):
            imager_ts.get()
        tend = time.time()
        self.verbose and self.log("FIXME TMP: flush %u image(s) took %0.3f" %
                                  (nframes, tend - tstart))
        self.last_frame_sync = tstart

    def wait_imaging_ok(self, flush_image=True):
        """
        Return once its safe to image
        Could be due to vibration, exposure settings, frame sync, etc
        """

        with LogTimer("wait video_pipeline",
                      variable="PYUSCOPE_PROFILE_TIMAGE"):
            self.wait_video_pipeline()
        with LogTimer("wait motion", variable="PYUSCOPE_PROFILE_TIMAGE"):
            self.wait_motion()
        with LogTimer("wait hdr", variable="PYUSCOPE_PROFILE_TIMAGE"):
            self.wait_hdr()

        # In an ideal world we'd compare elapsed time vs exposure
        # Otherwise if its close snap an image to sync up
        if flush_image:
            with LogTimer("wait frame_sync",
                          variable="PYUSCOPE_PROFILE_TIMAGE"):
                self.frame_sync()

    def diagnostic_info(self, indent=None, verbose=False, log=None):
        if log is None:
            log = self.log
        if indent is None:
            indent = "Kinematics(): "
        log(indent + "tsettle_motion: %0.3f" % self.tsettle_motion)
        log(indent + "tsettle_hdr: %0.3f" % self.tsettle_hdr)
        log(indent + "tsettle_autofocus: %0.3f" % self.tsettle_autofocus)
        log(indent + "frame_sync: %s" % self.should_frame_sync)
        log(indent + "frame_sync_frames_hdr: %u" % self.frame_sync_frames_hdr)
        log(indent +
            "tsettle_video_pipeline: %0.3f" % self.tsettle_video_pipeline)
