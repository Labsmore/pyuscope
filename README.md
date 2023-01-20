pyuscope is a collection of python based microscope utilities. In particular:
* Complete microscope applications for a few "official" configurations
* Framework for advanced users

The flagship application creates panoramic scans using:
* LinuxCNC for motion control
* ToupTek cameras (via gstreamer) to take pictures

These panoramic scans are typically chip images (ex: see http://siliconpr0n.org/)

Notable applications:
  * main_gui/main.py: primary GUI
  * touptek/tvl.py: for testing touptek plugin
  * demo/*.py: small tech demos

# Supported hardware

## Supported configurations

"pr0nscope"
* Laptop: ThinkPad T430
* OS: Ubuntu 20.04
* Camera: ToupTek E3ISPM20000KPA
* Motion control: LinuxCNC via machinekit (BBB)
* Microscope: Olympus BH2 based

WIP: 3018 GRBL
* Laptop: ThinkPad T430
* OS: Ubuntu 20.04
* Camera: ToupTek E3ISPM20000KPA
* Motion control: Grbl 1.1f
* Microscope: Olympus BH2 based

Why does the laptop matter?
Mostly for the screen resolution to make the GUI nice

## Supported hardware

See some high level usage notes here: https://microwiki.org/wiki/index.php/McScope

Supported gstreamer image sources:
  * toupcamsrc (primary)
  * v4l2src
  * Other sources may work but without calibration

Supported movement sources:
  * linuxcnc
  * Others, but they aren't well maintained

# Quick start touptek

FIXME: clean up instructions

You might be able to run this to get a turnkey setup: ./configs/lip-a1/setup_ubuntu_20.04.sh

Otherwise start by installing https://github.com/JohnDMcMaster/gst-plugin-toupcam


# Quick start V4L2

```
sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb

# install for v4l2
# https://github.com/antmicro/python3-v4l2
```

First you might be able to try this GUI which will try to auto-detect v4l2:

```
python3 util/prop_gui.py
```

If that doesn't work or you want the full GUI, try this:

```
cp -r configs/v4l2_example/ config
```

Edit config/microscope.json and set desired width/height.
You may need to use a program like "cheese" to see what the options are

```
python3 main_gui/main.py
```

# Installation

To install:

```
sudo python3 setup.py install
```

Or for development:

```
sudo python3 setup.py develop
```

Setup environment before running:

```
export PYTHONPATH=$PYTHONPATH:$PWD
export GST_PLUGIN_PATH=~/gst-plugin-toupcam/src/.libs/:$PWD
python3 main_gui/main.py
```

# Why?

Originally I needed to support specialized hardware and had a burning hated for Java
which is used by MicroManager, the flagship FOSS microchip software.
I've warmed up to Java slightly, and its possible MicroManager is a better fit for most people.
However, I've been using this workflow for years now, and will probably continue to do so

# Version history

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

## 3.3.0 (WIP)
 * Compatible with
  * gst-plugin-toupcam: v0.3.1 (WIP)
   * Rev for toupcamsrc automatic resolution detection
  * Motion HAL: GRBL
 * Config API clean up
 * WIP: fix axis soft limit bug
 * WIP: add ability to temporarily override soft limits
 * WIP: reliable Argus launch
