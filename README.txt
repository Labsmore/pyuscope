pr0ncnc
Copyright 2011-2019 John McMaster <JohnDMcMaster@gmail.com>

This is a framework and python gstreamer GUI to coordinate linear stages and sensors for panoramic scans
Its primarily used to do large XY scans of microscope samples using linuxcnc + v4l (MU800) camera
As of 2019 I'm cleaning up the code to allow non-v4l sensors

Why did you make this project?
Originally, I needed to do some custom stuff and had a burning hated for Java (used by MicroManager)
I've warmed up to Java slightly, and its possible MicroManager is a better fit for most people
However, I've been using this workflow for years now, and will probably continue to do so

How to start?
cp config/test/microscope.json .
python pgui/main.py
build and fix until you get all the requirements installed

python2 vs python3
tried to convert to python 3, but significant gstreamer changes are required
See https://pygobject.readthedocs.io/en/latest/guide/porting.html

Some old notes, not sure if these are still relevant
Originally I used this to generate g-code that ran as a full program instead of MDI
-M7: focus
-M8: take picture
-M9: cancel focus / take picture
Of course, if your camera takes pictures immediatly instead of requiring both
signals, you only need to wire either M7 or M8

