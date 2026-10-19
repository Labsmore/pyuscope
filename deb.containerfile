ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        dpkg-dev \
        git \
        gir1.2-gst-plugins-base-1.0 \
        gir1.2-gst-rtsp-server-1.0 \
        gir1.2-gstreamer-1.0 \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-tools \
        imagemagick \
        libgstrtspserver-1.0-0 \
        linux-libc-dev \
        libzbar0 \
        python3 \
        python3-dev \
        python3-gi \
        python3-gst-1.0 \
        python3-pygame \
        python3-pyqt5 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /src
