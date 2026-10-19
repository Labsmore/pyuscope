#!/usr/bin/env bash
# Sets up a reference toolchain from a fresh Ubuntu 20.04 install

set -ex

if [ \! -d configs ] ; then
    echo "Must be run from the root dir"
    exit 1
fi

install_uv_system=false
while [ "$#" -gt 0 ] ; do
    case "$1" in
        --uv-system)
            install_uv_system=true
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 [--uv-system]" >&2
            exit 2
            ;;
    esac
    shift
done

sudo apt-get update
sudo apt-get install -y curl

if $install_uv_system ; then
    curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
    UV=/usr/local/bin/uv
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV="${HOME}/.local/bin/uv"
fi

if [ ! -x "$UV" ] ; then
    UV=$(command -v uv)
fi

# 2024-02-23 rpi install
# Debian GNU/Linux 12 (bookworm)
linux_distribution=$(lsb_release -d |cut -d ':' -f2 |xargs)

install_pyuscope() {
    sudo apt-get install -y python3-gst-1.0 python3-gi python3-pyqt5 python3-usb python3-opencv python3-serial python3-numpy python3-scipy imagemagick python3-distro python3-zbar luminance-hdr
    # 2024-01-03: dev seems to be required, not just base
    # Some people suggest gir1.2-gst-rtsp-server-1.0
    # Install all for now
    sudo apt-get install -y libgstrtspserver-1.0-0 libgstrtspserver-1.0-dev gir1.2-gst-rtsp-server-1.0


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

    # Keep the uv environment isolated from unrelated distribution packages.
    # GObject/GStreamer, Qt, and optional hardware bindings are selectively
    # linked below because they are supplied by apt rather than PyPI.
    "$UV" venv
    "$UV" sync
    /usr/bin/python3 utils/uv_system_bindings.py .venv
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

# Intended for rpi camera
if [ "$linux_distribution" = "Debian GNU/Linux 12 (bookworm)" ] ; then
    sudo apt-get install -y libcamera-apps libcamera-tools gstreamer1.0-libcamera
fi


install_pyuscope

apply_migrations

# usermod is finicky requires login / logout
# GST_PLUGIN_PATH can be similar
echo ""
echo "Installation complete"
echo "Please restart system to have changes take effect"

