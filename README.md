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

# Why?

Originally I needed to support specialized hardware and had a burning hated for Java
which is used by MicroManager, the flagship FOSS microchip software.
I've warmed up to Java slightly, and its possible MicroManager is a better fit for most people.
However, I've been using this workflow for years now, and will probably continue to do so

# Version history

0.0.0
 * Works with custom motor driver board ("pr0ndexer")
 * Job setup is done through our GUI
 * In practice only works with MU800 camera
 * Tested Ubuntu 16.04

1.0.0
 * Motor contril via LinuxCNC
 * Job setup is primarily done through Axis (LinuxCNC GUI)
 * Tested Ubuntu 16.04

2.0.0
 * gst-toupcamsrc supported
 * GUI simplified
 * Job setup is entirely done through Axis (LinuxCNC GUI)
 * Major configuration file format changes
 * python2 => python3
 * gstreamer-0.10 => gstreamer-1.0
 * PyQt4 => PyQT5
 * Tested Ubuntu 16.04 and 20.04

2.1.0
 * HDR support
 * First packaged release

2.2.0
 * Enforce output file naming convention
 
 2.3.0 (WIP
 * Drop obsolete "Controller" motion control API (including Axis object)
 * Drop obsolete pr0ndexer motion control support
 * Drop obsolete MC motion control support

