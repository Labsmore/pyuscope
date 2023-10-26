# Version history

| pyuscope | gst-plugin-touptek | Release ticket                                          | Notes                                                                                                               |
|----------|--------------------|---------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| 0.0.0    | N/A                |                                                         | Motion: pr0ndexer, camera: MU800                                                                                    |
| 1.0.0    | N/A                |                                                         | LinuxCNC support                                                                                                    |
| 2.0.0    | yes                |                                                         | Misc software framework upgrades (ex: python 2 => 3)                                                                |
| 2.1.0    | yes                |                                                         | HDR alpha                                                                                                           |
| 2.2.0    | yes                |                                                         | Enforce output file naming convention                                                                               |
| 3.0.0    | yes                |                                                         | GRBL support                                                                                                        |
| 3.1.0    | yes                |                                                         | Soft axis limit support, Support PYUSCOPE_MICROSCOPE                                                                |
| 3.2.0    | v0.3.0             |                                                         | Advanced tab (HDR, focus stacking beta)                                                                             |
| 4.0.0    | v0.3.0             |                                                         | Plugin based objects, XY3P algorithm, Kinematics engine PoC, persist some GUI state across launches                 |
| 4.0.1    | v0.3.0             |                                                         | Fix race condition on creating data dir                                                                             |
| 4.0.2    | v0.3.0             |                                                         | Config file rename                                                                                                  |
| 4.1.0    | v0.4.2             |                                                         | Homing alpha support, new kinematics model                                                                          |
| 4.2.0    | v0.5.0             | [Link](https://github.com/Labsmore/pyuscope/issues/169) | Improved video widget sizing, joystick beta, backlash algorithm rewrite                                             |

## 0.0.0
* Works with custom motor driver board ("pr0ndexer")
* Job setup is done through our GUI
* In practice only works with MU800 camera
* Tested Ubuntu 16.04

## 1.0.0
 * Motor contril via LinuxCNC
 * Job setup is primarily done through Axis (LinuxCNC GUI)
 * Tested Ubuntu 16.04

## 2.0.0
* gst-toupcamsrc supported
* GUI simplified
* Job setup is entirely done through Axis (LinuxCNC GUI)
* Major configuration file format changes
* python2 => python3
* gstreamer-0.10 => gstreamer-1.0
* PyQt4 => PyQT5
* Tested Ubuntu 16.04 and 20.04

## 2.1.0
* HDR support
* First packaged release

## 2.2.0
* Enforce output file naming convention
 
## 3.0.0
* GRBL support
* Jog support (GRBL only)
* Coordiante system defaults to lower left instead of upper left
* Move imager controls from floating window into tab
* Major API restructure
* microscope.json changes and moved to microscope.j5
* planner.json major changes
* Drop obsolete "Controller" motion control API (including Axis object)
* Drop obsolete pr0ndexer motion control support
* Drop obsolete MC motion control support
* Motion HAL plugin architecture
* main_gui is now "argus"
* Options for argus output file naming
* --microscope command line argument
* Axis scalar support (gearbox workaround)
* Test suite
* Expanded CLI programs
* Expanded microscope calibration suite (namely fiducial tracking)

## 3.1.0
* Soft axis limit support
* lip-a1 real machine values
* Planner end_at option
* Add PYUSCOPE_MICROSCOPE
* Better GRBL automatic serial port selection
* Misc fixes

## 3.2.0
* Compatible with
  * gst-plugin-toupcam: v0.3.0
  * Motion HAL: GRBL
    * LinuxCNC was not removed but was not updated either
* Known bugs
  * Argus: first time connecting to GRBL may cuase failed start
    * Workaround: re-launch GUI or first run "python3 test/grbl/status.py"
  * Axis soft limit may be exceeded during long jog
    * Workaround: be careful and/or do shorter jogs
  * Auto-exposure may cause image to flicker
    * Workaround: toggle auto-exposure off then on to stabalize
* Microscope
  * lip-m1-beta support
  * ls-hvy-1: moved from LinuxCNC to GRBL
  * brainscope: moved from LinuxCNC to GRBL
* Argus
  * Add Advanced tab
    * Focus stack support (beta quality)
    * HDR support (beta quality)
  * Manual move w/ backlash compensation option
* Config improvements and changes
  * Config: add imager.source_properties_mod
    * ex: reduce exposure time to practical range)
  * Config: add imager.crop
    * Use an oversized sensor by cropping it down
  * Config: add planner.backlash_compensate
    * More aggressively backlash compensate to improve xy alignment
  * Config: backlash can be specified per axis
    * Intended to support XY vs Z
  * Config API cleaned up
* Bug fixes / usability improvements
  * Argus: update position during long moves
  * Fix image flickering during move
  * Fix image flickering caused by GUI exposure fighting auto-exposure
  * Reduce console verbosity
  * Unit test suite significantly expanded

## 4.0.0
 * Compatible with
   * gst-plugin-toupcam: v0.4.0
     * Rev for toupcamsrc automatic resolution detection
   * Motion HAL: GRBL
 * Significant code restructure (Argus, planner, motion)
 * Config API clean up
 * Argus split into smaller widgets
 * Planner V2 (now plugin based)
 * Motion HAL V2 (now plugin based)
 * XY3P algorithm (beta)
 * Batch imaging (beta)
 * Fixed GRBL Argus launch bug

## 4.0.1
  * Fix race condition on creating data dir

## 4.0.2
  * Config file rename

## 4.1.0
 * Compatible with
   * gst-plugin-toupcam: v0.4.2
   * Motion HAL: GRBL
 * Argus
   * Persist some GUI state across launches
   * Jogging fine mode, more keyboard shortcuts
   * XY2P/XY3P "Go" to coordinate buttons
   * HDR usability improvements. cs_auto.py can also ingest now
 * Homing beta support
   * Configuration: add motion.use_wcs_offsets (as opposed to MPos)
   * utils/home_run.sh wrapper script
 * Core
   * New kinematics model to significantly improve image throughput
   * Coordinates are now relative to image center (was image corner)
 * Add microscope_info.py
 * Misc bug fixes
   * Argus: crash on data dir missing
   * Argus: log crash on scan abort
   * Argus: auto exposure issues
   * Argus: planner thread stops more reliably
   * Calibration fixes

## 4.2.0
 * Compatible with
   * gst-plugin-toupcam: v0.5.0
   * Motion HAL: GRBL
 * Argus
   * View widget resized
   * ROI zoom support
   * Add dropdown menus
 * Joystick beta support
 * Backlash engine rewrite
 * cs_auto parallel support
 * User plugin alpha support
 * Some v4l support revived (launch only, no scan)

## 4.3.0
 * Compatible with
   * gst-plugin-toupcam: v0.5.0
   * Motion HAL: GRBL
 * Argus
   * User scripting beta support
   * Camera widget rewrite (misc fixes)
   * Autofocus support
   * Focus stacking based on NA, not hard coded offsets
   * View widget rewrite
     * Eliminate ROI view in favor of zooming
 * Planner focus drift correction added
 * Microscope support
   * LIP-VM1: beta support
   * LIP-A2: beta support
 * Imaging
   * Image processing pipeline overhaul
     * Add flat field correction
     * Argus CloudStitch checkbox will run before uploading to cloud
   * V4L2: automatically find camera
   * V4L2: misc control improvements
 * GRBL
   * Microscope metadata support (ex: unit S/N)
   * Soft homing support
 * Misc fixes / improvements
   * Joystick tweaks
   * LIP-X1: homing should now work in 1 pass

## 4.3.1
  * Add second scripting path for pyuscope-rhodium

## 4.4.0
 * Compatible with
   * gst-plugin-toupcam: v0.5.0
   * Motion HAL: GRBL
 * Argus
   * Jog slider tweaks
   * Measurement tab
   * Snapshot basic image processing (ex: VM1 correction)
   * Jogging confirmation message box
   * Focus stacking: calculate based on NA
   * Save .tif or .jpg easily
 * Image stablization support
 * Autofocus much faster
 * Jogging engine rewrite
 * Joystick tweaks / fixes
 * Microscope support
   * LIP-VA1: beta support
   * LIP-VM1: rotate image 180
   * Re-enable soft limits after jogging fixes
 * Misc fixes / improvements
   * Planner: focus stacking Z return fixes
   * Soft limits should be more reliable
