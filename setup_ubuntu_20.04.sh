#!/usr/bin/env bash
# Sets up a reference toolchain from a fresh Ubuntu 20.04 install

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

install_pyuscope() {
    sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb python3-opencv python3-serial python3-numpy python3-scipy imagemagick
    sudo pip3 install json5 boto3 pygame psutil


    # Ubuntu 20.04
    sudo apt-get install -y hugin-tools
    # Ubuntu 22.04
    # sudo apt update && sudo apt install flatpak
    # flatpak install https://dl.flathub.org/repo/appstream/net.sourceforge.Hugin.flatpakref

    # For webserver
    sudo apt-get install -y python3-werkzeug
    sudo pip3 install Flask>=2.2.2

    sudo python3 setup.py develop
}

install_antmicro_v4l2() {
    # Install from https://github.com/antmicro/python3-v4l2/
    mkdir tmp-installv4l2
    pushd tmp-installv4l2
    git clone https://github.com/antmicro/python3-v4l2.git
    pushd python3-v4l2
    sudo python3 ./setup.py install
    popd
    popd
    rm -rf tmp-installv4l2
}

install_pyrav4l2() {
    sudo pip3 install git+https://github.com/antmicro/pyrav4l2.git
}

# For GRBL etc serial port
sudo usermod -a -G dialout $USER

install_gst_plugin_toupcam() {
    git submodule init
    git submodule update --remote
    pushd gst-plugin-toupcam
    ./setup_ubuntu_20.04.sh
    popd
}

install_gst_plugin_toupcam
install_pyuscope
# Deprecated, use pyrav4l2
# install_antmicro_v4l2
install_pyrav4l2

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

