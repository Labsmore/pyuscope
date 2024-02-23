#!/usr/bin/env bash
# Sets up a reference toolchain from a fresh Ubuntu 20.04 install

set -ex

if [ \! -d configs ] ; then
    echo "Must be run from the root dir"
    exit 1
fi
# 2024-02-23 rpi install
# Debian GNU/Linux 12 (bookworm)
linux_distribution=$(lsb_release -d |cut -d ':' -f2 |xargs)

pip_args=""
if [ "$linux_distribution" = "Debian GNU/Linux 12 (bookworm)" ] ; then
    echo "WARNING: your system doesn't like pip. Making a best effort install"
    # Got to do what we got to do right now
    # But a friendly reminder to try to sort out packaging better :)
    pip_args="--break-system-packages"
    sleep 3
fi

sudo apt-get update
sudo apt-get install -y python3-pip

install_pyuscope() {
    sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb python3-opencv python3-serial python3-numpy python3-scipy imagemagick python3-distro python3-zbar
    # 2024-01-03: dev seems to be required, not just base
    # Some people suggest gir1.2-gst-rtsp-server-1.0
    # Install all for now
    sudo apt-get install -y libgstrtspserver-1.0-0 libgstrtspserver-1.0-dev gir1.2-gst-rtsp-server-1.0
    pip3 install --user ${pip_args} json5 boto3 pygame psutil bitarray


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
    pip3 install --user ${pip_args} 'Flask>=2.2.2'

    sudo python3 setup.py develop
}

install_pyrav4l2() {
    pip3 install --user ${pip_args} git+https://github.com/antmicro/pyrav4l2.git
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

if [ "$linux_distribution" = "Debian GNU/Linux 12 (bookworm)" ] ; then
    # this actually works, there are instructions in the README
    # but their setup script doesn't work automatically
    # most rpi users right now are using open flexure which doesn't need this
    echo "WARNING: not automatically installing touptek plugin"
else
    install_gst_plugin_toupcam
fi

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

