pr0ncnc
Copyright 2011-2019 John McMaster <JohnDMcMaster@gmail.com>

This is a framework and python gstreamer GUI to coordinate linear stages and sensors for panoramic scans
Its primarily used to do large XY scans of microscope samples using linuxcnc + v4l (MU800) camera
As of 2019 I'm cleaning up the code to also integrate with the touptek SDK

Why did you make this project?
Originally, I needed to do some custom stuff and had a burning hated for Java (used by MicroManager)
I've warmed up to Java slightly, and its possible MicroManager is a better fit for most people
However, I've been using this workflow for years now, and will probably continue to do so

How to start?
cp config/test/microscope.json .
python pgui/main.py
build and fix until you get all the requirements installed

See some high level usage notes here: https://microwiki.org/wiki/index.php/McScope

```
sudo apt-get install -y python3-gst-1.0
sudo apt-get install -y python3-gi
sudo pip3 install v4l2

git clone https://github.com/GStreamer/gst-python.git
pushd gst-python
sudo apt-get install -y python-gi-dev
PYTHON=python3 ./configure
sudo make install
popd
```


```
mcmaster@necropolis:~/doc/ext/pyuscope$ ls -lah /usr/local/lib/gstreamer-1.0
total 72K
drwxr-xr-x 2 root root 4.0K Aug 14 00:30 .
drwxr-xr-x 6 root root 4.0K Jul  5 23:40 ..
-rwxr-xr-x 1 root root  57K Aug 14 00:30 libgstpythonplugin.cpython-35m-x86_64-linux-gnu.so
-rwxr-xr-x 1 root root 1.3K Aug 14 00:30 libgstpythonplugin.la
```


```
python/sinkelement.py
GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0:$PWD gst-inspect-1.0 mysink
```

