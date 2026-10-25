Fiducial target
===============

Used by Imager scale, XY backlash and XY repeatability

Black background with white dot
I used a piece of anodized aluminum w/ paint marker dot
Should fill about 1/3 to 1/6 of image width

Turn off auto brightness before starting test
Make sure the white dot is saturated clearly vs background

Fiducial check
--------------

Run Calibrate => Fiducial check before the fiducial calibrations.
It captures a few frames the same way they do and reports:
  * Dot and background levels vs the centroid threshold (127 of 255).
    Want the dot saturated (>= ~245) and the background dark (<= ~70)
  * Contrast, stray bright objects / specks, background glare
  * Dot size (as a fraction of the image width)
  * Centering: the dot must stay fully in frame during the scale
    calibration's +/- 15% field of view moves
  * Edge sharpness (focus) and centroid jitter between frames
Each problem comes with a suggested fix. The preview shows the detected dot
(green = ready, red = not ready) and the region it must stay inside (blue)

Evaluate checks the current setup without changing anything
Evaluate + tune first runs the selected tuning steps:
  * Exposure: turns off camera auto exposure (it targets average
    brightness, which blows out a small bright dot) and adjusts exposure
    until the dot just saturates while the background stays dark
  * Focus: autofocuses on the dot (needs Z). The new focus is kept

Re-run it after changing objective, illumination or target
Scripting: fiducial_check(auto_exposure=True, autofocus=True)
