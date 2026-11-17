# =============================================================================
# home-monitoring: Jetson Nano apartment monitoring
#
# Build context: repo root (see docker-compose.yml).
#
# Base image selectable via ARG:
#   - dustynv/jetson-inference:r32.7.1 (default, JetPack 4.6, available on
#     Docker Hub — includes OpenCV 4.5 (CUDA+dnn), PyTorch 1.10, TensorRT,
#     GStreamer, Python 3.6)
#   - nexus.robobrain.cloud/sdk:latest (only if the internal Nexus is
#     reachable, e.g. via VPN; Python 3.12, torch+CUDA, opencv)
#
# Notes:
#   - The base image only contains the root user (no ubuntu) -> the service
#     runs as root.
#   - No apt-get in the build — the Ubuntu Bionic package sources are not
#     reliably reachable on the Jetson (IPv6-only internet). ffmpeg instead
#     comes as a static binary from the imageio-ffmpeg pip package
#     (see app/recorder.py).
# =============================================================================
ARG BASE_IMAGE=dustynv/jetson-inference:r32.7.1
FROM ${BASE_IMAGE}

# UTF-8 locale: the old pip 9.0.1 in the base image would otherwise decode
# requirements.txt as ascii and fail on German comments (umlauts).
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

# Python dependencies (fastapi, uvicorn, imageio-ffmpeg, pytest).
# Install system-wide (no --user) since root is the user.
# pip 21.3.1 is the last Python-3.6-compatible version (the base image only
# has pip 9.0.1, which is too old for modern wheels).
#
# DNS fix for the build: BuildKit gives the build container 8.8.8.8 as
# nameserver (IPv4), but the Jetson only has IPv6 internet. With
# network: host, the host's systemd-resolved stub at 127.0.0.53 is
# reachable — therefore override resolv.conf in the build step.
COPY requirements.txt /tmp/requirements.txt
RUN echo nameserver 127.0.0.53 > /etc/resolv.conf \
    && python3 -m pip install --no-cache-dir --upgrade "pip==21.3.1" \
    && python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

# Service code + templates.
COPY app/ /app/app/
COPY templates/ /app/templates/
COPY static/ /app/static/

# Mount points (overridden by volumes, but provide the directory structure
# in the image).
RUN mkdir -p /app/models /recordings

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=120s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=2)" || exit 1

ENV PYTHONPATH="/app:${PYTHONPATH}"
CMD ["python3", "-m", "app.main"]
