#!/bin/bash -ex

sudo apt install --assume-yes \
    gstreamer1.0-tools \
    libgstreamer-plugins-base1.0-dev \
    dpkg-dev \
    devscripts \
    libtool \
    autoconf \
    automake \
    unzip

mkdir toupcamsdk

(cd toupcamsdk/
  wget https://www.touptekphotonics.com/downloads/software/download.php?soft=toupcamsdk -O toupcamsdk.zip
  unzip toupcamsdk.zip
)
sudo mv toupcamsdk /opt/

(cd /opt/toupcamsdk/
sudo cp linux/x64/libtoupcam.so /lib/x86_64-linux-gnu/
sudo cp linux/udev/99-toupcam.rules  /etc/udev/rules.d/
sudo udevadm control --reload-rules
)

sudo ldconfig

git clone https://github.com/JohnDMcMaster/gst-plugin-toupcam.git
cd gst-plugin-toupcam
./autogen.sh
make

sudo make install
echo "export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0" >> ~/.profile

# verify:
GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0 gst-inspect-1.0 toupcamsrc
