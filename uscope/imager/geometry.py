"""
Runtime camera resolution / crop changes for sessions (ArgusCommon, HeadlessSession)
Qt free

Session interface used:
    microscope, usc, vidpip (may be None), imager, log()
"""



class GeometryBusy(RuntimeError):
    """
    Image geometry can't be changed right now (ex: scan running)
    """
    pass


class SessionGeometry:
    def resolution_selector(self):
        """
        ResolutionSelector if the camera source can select resolution, else None
        """
        if self.vidpip is None:
            return None
        return self.vidpip.imager_aplugin.resolution_selector()

    def reconfigure_geometry(self, resolution=None):
        """
        See GstImagerPipeline.reconfigure_geometry()
        Returns True if the geometry changed
        """
        # picam2 pipeline doesn't support it
        if self.imager is None or not hasattr(self.vidpip,
                                              "reconfigure_geometry"):
            return False
        if not self.vidpip.reconfigure_geometry(resolution=resolution):
            return False
        self.geometry_changed()
        return True

    def set_camera_resolution(self, wh):
        """
        wh: (w, h) or None for the default (microscope.j5 or camera max)
        Applied immediately
        """
        selector = self.resolution_selector()
        if selector is None:
            raise ValueError("Camera source %s can't select resolution" %
                             (self.usc.imager.source(), ))
        if not selector.can_select():
            raise ValueError(selector.unavailable_reason())
        selector.set_override(
            wh, apply=lambda r: self.reconfigure_geometry(resolution=r))
        self.camera_resolution_saved()

    def camera_resolution_saved(self):
        """
        Hook: persist the resolution selection (see ResolutionSelector.cache_sn_save())
        """
        pass

    def apply_crop(self):
        """
        Follow crop changes (objective switch, calibration, capture format)
        No-op if the crop in use is still correct
        """
        # Called from listeners / Qt slots: report instead of raising
        try:
            return self.reconfigure_geometry()
        except GeometryBusy as e:
            self.log(f"WARNING: crop not updated: {e}")
        except Exception as e:
            self.log(f"ERROR: failed to apply crop: {type(e).__name__}: {e}")
        return False

    def geometry_changed(self):
        """
        Refresh everything that depends on the image size
        """
        imager = self.usc.imager
        self.imager.geometry_changed()
        self.microscope.objective_state.reload()
        raw_w, raw_h = imager.raw_wh()
        crop_w, crop_h = imager.cropped_wh()
        final_w, final_h = imager.final_wh()
        self.log("Image geometry: camera %ux%u, cropped %ux%u, final %ux%u" %
                 (raw_w, raw_h, crop_w, crop_h, final_w, final_h))
        self.check_illumination()

    def check_illumination(self):
        """
        Flat field / illumination calibrations are per resolution
        """
        store = getattr(self.microscope, "cal_store", None)
        if store is None:
            return
        if not any(e.key == "imager.illumination"
                   for e in store.entries(include_disabled=False)):
            return
        if store.get("imager.illumination",
                     self.usc.imager._illumination_scope()) is None:
            raw_w, raw_h = self.usc.imager.raw_wh()
            self.log(
                "WARNING: no flat field calibration for %ux%u: correction disabled at this resolution"
                % (raw_w, raw_h))
