#!/bin/bash -ex

# For GRBL etc serial port
sudo usermod -a -G dialout $USER

sudo apt-get install --assume-yes \
    python3-gst-1.0 python3-gi python3-pyqt5 python3-usb python3-serial python3-numpy \
    python3-scipy imagemagick python3-distro python3-zbar luminance-hdr \
    libgstrtspserver-1.0-0 libgstrtspserver-1.0-dev gir1.2-gst-rtsp-server-1.0 \
    hugin-tools enfuse \
    python3-werkzeug \
    python3-venv python3-pip \
    python3-opencv

sudo apt install --assume-yes cmake

sudo apt install --assume-yes python3-gi python3-gi-cairo gir1.2-gtk-4.0

sudo apt install --assume-yes libcairo2-dev libgirepository1.0-dev pkg-config

sudo apt install --assume-yes libgirepository-2.0-dev gobject-introspection

sudo apt install --assume-yes python3-numpy
sudo apt install --assume-yes python3-dev

mkdir venv
python3 -m venv --system-site-packages venv/labsmore
. venv/labsmore/bin/activate

pip install pycairo
pip install numpy
pip install opencv-python-headless
pip install distro
pip install paramiko
pip install pyzbar
pip install json5 boto3 pygame psutil bitarray piexif
pip install Flask # >=2.2.2
pip install Pillow
pip install pyserial
pip install PyQt5
pip install setuptools

git clone https://github.com/Labsmore/pyuscope.git
cd pyuscope
python3 setup.py develop
