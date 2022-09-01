#!/usr/bin/env bash
# WARNING: this is untested / rough idea
# Sets up a reference toolchain from a fresh Ubuntu 20.04 install
# Configured for LIP-A1

set -ex

sudo apt-get install python3-serial python3-pip
sudo pip3 install json5

install_toupcam_sdk() {
    if [ /opt/toupcamsdk ] ; then
        echo "toupcamsdk: already installed"
    else
        echo "toupcamsdk: installing"
        pushd download
        wget -c http://www.touptek.com/upload/download/toupcamsdk.zip
        mkdir toupcamsdk
        pushd toupcamsdk
        unzip ../toupcamsdk.zip
        popd
        sudo mv toupcamsdk /opt/
        popd

        pushd /opt/toupcamsdk/
        sudo cp linux/udev/99-toupcam.rules  /etc/udev/rules.d/
        sudo udevadm control --reload-rules
        sudo cp linux/x64/libtoupcam.so /lib/x86_64-linux-gnu/
        sudo ldconfig
        popd
    fi
}

install_gst_plugin_toupcam() {
    if [ -f "/usr/local/lib/gstreamer-1.0/libgsttoupcamsrc.so" ] ; then
        echo "gst-plugin-toupcam: already installed"
    else
        echo "gst-plugin-toupcam: installing"
        git submodule update --remote
        cd gst-plugin-toupcam
        sudo apt-get install -y autoconf libtool dpkg-dev devscripts gstreamer1.0-tools libgstreamer-plugins-base1.0-dev

        install_toupcam_sdk

        ./autogen.sh
        make

        sudo make install
        echo "export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0" >> ~/.profile
    fi
}

install_stitching() {
    sudo apt install -y hugin hugin-tools enblend imagemagick python3-psutil
    sudo pip3 install Pillow

    if [ -f "$(which xy-ts)" ] ; then
        echo "xy-stitch: already installed"
    else
        echo "xy-stitch: installing"
        pushd xystitch
        sudo python3 setup.py develop
        popd
    fi
}

install_pyuscope() {
    sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb
    sudo python3 setup.py develop
    # Set default configuration
    if [ config ] ; then
        echo "Default config already set, skipping"
    else
        ln -s configs/lip-a1 config
    fi
}

# Misc
sudo usermod -a -G dialout $USER

install_gst_plugin_toupcam
install_stitching
install_pyuscope

# usermod is finicky requires login / logout
# GST_PLUGIN_PATH can be similar
echo "Please restart system to have changes take effect"

