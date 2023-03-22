#!/usr/bin/env bash
# WARNING: this is untested / rough idea
# Sets up a reference toolchain from a fresh Ubuntu 20.04 install
# Configured for LIP-A1

set -ex

if [ -z "$PYUSCOPE_MICROSCOPE" ] ; then
    echo "Must specify PYUSCOPE_MICROSCOPE to install. Ex: PYUSCOPE_MICROSCOPE=mock ./setup_ubuntu_20.04.sh"
    echo "Ex: PYUSCOPE_MICROSCOPE=ls-hvy-2 ./setup_ubuntu_20.04.sh"
    echo "Ex: PYUSCOPE_MICROSCOPE=none ./setup_ubuntu_20.04.sh"
    find configs -maxdepth 1 -mindepth 1 -type d |sort
    exit 1
fi
if [ "$PYUSCOPE_MICROSCOPE" = "none" ] ; then
    true
elif [ '!' -d "configs/$PYUSCOPE_MICROSCOPE" ] ; then
    echo "Invalid PYUSCOPE_MICROSCOPE given"
    exit 1
fi

if [ \! -d configs ] ; then
    echo "Must be run from the root dir"
    exit 1
fi

sudo apt-get update
sudo apt-get install -y python3-pip

install_toupcam_sdk() {
    if [  -d /opt/toupcamsdk ] ; then
        echo "toupcamsdk: already installed"
    else
        echo "toupcamsdk: installing"
        mkdir -p download
        pushd download
        # 2023-03-21: latest version on site is old (50.x)
        # Get the newer one
        # wget -c http://www.touptek.com/upload/download/toupcamsdk.zip
        wget -c https://microwiki.org/media/touptek/toupcamsdk_53.21907.20221217.zip
        mv toupcamsdk_53.21907.20221217.zip toupcamsdk.zip
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
        pushd gst-plugin-toupcam
        sudo apt-get install -y autoconf libtool dpkg-dev devscripts gstreamer1.0-tools libgstreamer-plugins-base1.0-dev

        install_toupcam_sdk

        ./autogen.sh
        make

        sudo make install
        echo "export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0" >> ~/.profile
        popd
    fi
}

install_pyuscope() {
    sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb python3-opencv python3-serial
    sudo pip3 install json5
    sudo python3 setup.py develop
}

# Misc
sudo usermod -a -G dialout $USER

install_gst_plugin_toupcam
install_pyuscope

if [ "$PYUSCOPE_MICROSCOPE" != "none" ] ; then
    # Found some systems don't read .profile....shrug
    echo "Adding default microscope to .profile and .bashrc"
    echo "export PYUSCOPE_MICROSCOPE=$PYUSCOPE_MICROSCOPE" >> ~/.profile
    echo "export PYUSCOPE_MICROSCOPE=$PYUSCOPE_MICROSCOPE" >> ~/.bashrc
fi

# usermod is finicky requires login / logout
# GST_PLUGIN_PATH can be similar
echo ""
echo "Installation complete"
echo "Please restart system to have changes take effect"

