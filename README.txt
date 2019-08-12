pr0ncnc
Copyright 2011 John McMaster <JohnDMcMaster@gmail.com>


Purpose
This generates g-code for my CNC microscope:
http://uvicrec.blogspot.com/2011/01/metalurgical-microscope-cnc.html


Prerequisites
-EMC2 compatible g-code interpreter
-XY linear stages
-Camera that can be controlled from g-code interpreter
	-M7: focus
	-M8: take picture
	-M9: cancel focus / take picture
-Sample can be scanned as a rectangle
Of course, if your camera takes pictures immediatly instead of requiring both
signals, you only need to wire either M7 or M8


How to use this program

***Pay attention to z-play value!***
If you aren't careful, it could cause the objective to hit your sample
Set it to 0 if it scares you

Find where you'd like the scan to start at X=0, Y=0
	Zero X and Y
How does Z change as you move Y?  
	Zero Z at (0,0,0) after moving Z in the same direction as Y will need to move Z
	This helps correct backlash
		If you don't care about this, set "z_backlash" to 0.0
Find the end corner of the rectangle
	Using same Z technique, record this in scan config file under "end"
Find a plane reference point
	Using same Z technique, record this in scan config file under "other"
	This should be somewhere off axis as possible from the other two points
	Another corner of the rectangle is a good choice


Improvements
A mesh mode could be nice.  For some chips such as my Intel wafer, there are
things beyond my control that have lensing effects.  As such, the focal plane
is not flat.  This might be so rare as to not implement though.
