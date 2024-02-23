#!/usr/bin/env bash
# Sets up a reference toolchain from a fresh Ubuntu 20.04 install

set -ex

if [ \! -d configs ] ; then
    echo "Must be run from the root dir"
    exit 1
fi
linux_distribution=$(lsb_release -d |cut -d ':' -f2 |xargs)

sudo apt-get update
sudo apt-get install -y python3-pip

install_pyuscope() {
    sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb python3-opencv python3-serial python3-numpy python3-scipy imagemagick python3-distro python3-zbar
    # 2024-01-03: dev seems to be required, not just base
    # Some people suggest gir1.2-gst-rtsp-server-1.0
    # Install all for now
    sudo apt-get install -y libgstrtspserver-1.0-0 libgstrtspserver-1.0-dev gir1.2-gst-rtsp-server-1.0
    pip3 install --user json5 boto3 pygame psutil bitarray


    # Package removed
    # use flat pack
    # Ubuntu 22.04
    # FIXME: make this suck less...
    if [ "$linux_distribution" = "Ubuntu 22.04.3 LTS" ] ; then
        sudo apt-get install -y flatpak
        sudo flatpak install -y --noninteractive https://dl.flathub.org/repo/appstream/net.sourceforge.Hugin.flatpakref
    # Ubuntu 20.04.6 LTS
    # Linux Mint 20.3
    else
        sudo apt-get install -y hugin-tools enfuse
    fi

    # For webserver
    sudo apt-get install -y python3-werkzeug
    pip3 install --user 'Flask>=2.2.2'

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
    pip3 install --user git+https://github.com/antmicro/pyrav4l2.git
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

apply_migrations() {
    # User might not have microscope attached, not homed, et
    # Make token effort though
    ./test/grbl/migrate_meta.py 2>/dev/null || true
}

install_gst_plugin_toupcam
install_pyuscope
# Deprecated, use pyrav4l2
# install_antmicro_v4l2
install_pyrav4l2

apply_migrations

# usermod is finicky requires login / logout
# GST_PLUGIN_PATH can be similar
echo ""
echo "Installation complete"
echo "Please restart system to have changes take effect"

