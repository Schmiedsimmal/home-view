# =============================================================================
# home-monitoring: Jetson-Nano-Wohnungsueberwachung
#
# Build context: Repo-Root (siehe docker-compose.yml).
#
# Base image per ARG waehlbar:
#   - dustynv/jetson-inference:r32.7.1 (Default, JetPack 4.6, von Docker Hub
#     erreichbar — enthält OpenCV 4.5 (CUDA+dnn), PyTorch 1.10, TensorRT,
#     GStreamer, Python 3.6)
#   - nexus.robobrain.cloud/sdk:latest (nur wenn das interne Nexus erreichbar
#     ist, z.B. per VPN; Python 3.12, torch+CUDA, opencv)
#
# Hinweise:
#   - Das Basis-Image enthaelt nur den root-User (kein ubuntu) → Service
#     laeuft als root.
#   - Kein apt-get im Build — die Ubuntu-Bionic-Paketquellen sind auf dem
#     Jetson (IPv6-only Internet) nicht zuverlaessig erreichbar. ffmpeg
#     kommt stattdessen als statisches Binary aus dem imageio-ffmpeg-Pip-
#     Paket (siehe app/recorder.py).
# =============================================================================
ARG BASE_IMAGE=dustynv/jetson-inference:r32.7.1
FROM ${BASE_IMAGE}

# UTF-8-Locale: das alte pip 9.0.1 im Basis-Image decodiert requirements.txt
# sonst mit ascii und scheitert an deutschen Kommentaren (Umlaute).
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

# Python dependencies (fastapi, uvicorn, imageio-ffmpeg, pytest).
# System-weit installieren (kein --user), da root als User laeuft.
# pip 21.3.1 ist die letzte Python-3.6-kompatible Version (Basis-Image hat
# nur pip 9.0.1, das zu alt fuer moderne Wheels ist).
#
# DNS-Fix fuer den Build: BuildKit gibt dem Build-Container 8.8.8.8 als
# Nameserver (IPv4), der Jetson hat aber nur IPv6-Internet. Mit
# network: host ist der systemd-resolved-Stub des Hosts unter 127.0.0.53
# erreichbar — daher resolv.conf im Build-Schritt ueberschreiben.
COPY requirements.txt /tmp/requirements.txt
RUN echo nameserver 127.0.0.53 > /etc/resolv.conf \
    && python3 -m pip install --no-cache-dir --upgrade "pip==21.3.1" \
    && python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

# Service code + templates.
COPY app/ /app/app/
COPY templates/ /app/templates/

# Mount points (werden per Volume ueberschrieben, aber Verzeichnisstruktur
# im Image bereitstellen).
RUN mkdir -p /app/models /recordings

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=120s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=2)" || exit 1

ENV PYTHONPATH="/app:${PYTHONPATH}"
CMD ["python3", "-m", "app.main"]
