#!/usr/bin/env bash
# =============================================================================
# Setup script for the Jetson Nano (host).
#
# Installs/checks:
#   - Docker + Docker Compose
#   - NVIDIA Container Runtime (nvidia-docker2) for GPU access in containers
#   - SSD mount (/mnt/ssd) for recordings
#   - USB webcam (/dev/video*)
#   - systemd autostart (home-monitoring.service)
#
# Idempotent: can be run multiple times safely.
# =============================================================================
set -euo pipefail

log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[err ]\033[0m %s\n' "$*" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

[[ "$(uname -m)" == "aarch64" ]] || warn "Not an aarch64 host (not a Jetson?). The script will run anyway."

# --- Docker -------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    log "Docker not found. Installing via the official convenience script."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    warn "Log out and back in for the docker group to take effect."
else
    log "Docker vorhanden: $(docker --version)"
fi

# Docker Compose: plugin ("docker compose") or standalone ("docker-compose").
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
    log "Docker Compose (Plugin): $(docker compose version --short)"
elif docker-compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
    log "Docker Compose (Standalone): $(docker-compose version --short)"
else
    warn "Docker Compose not found."
    warn "On the Jetson (GitHub often unreachable) copy the binary manually:"
    warn "  # on a machine with internet access:"
    warn "  curl -sL -o docker-compose https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-aarch64"
    warn "  scp docker-compose simon@<jetson-ip>:/tmp/"
    warn "  # on the Jetson:"
    warn "  sudo cp /tmp/docker-compose /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose"
    die "Docker Compose v2 is missing. Please install it first (see the hint above)."
fi

# --- NVIDIA Container Runtime -------------------------------------------------
if docker info 2>/dev/null | grep -q nvidia; then
    log "NVIDIA Container Runtime aktiv."
else
    warn "NVIDIA Container Runtime not detected."
    warn "On JetPack 4.x install:"
    warn "  sudo apt-get update"
    warn "  sudo apt-get install -y nvidia-docker2"
    warn "  sudo systemctl restart docker"
    warn "Then run this script again."
fi

# --- SSD-Mount (/mnt/ssd) -------------------------------------------------------
RECORDINGS_HOST_DIR="${RECORDINGS_HOST_DIR:-/mnt/ssd/recordings}"
SSD_ROOT="${RECORDINGS_HOST_DIR%/recordings}"
if [[ ! -d "$SSD_ROOT" ]]; then
    warn "SSD mountpoint $SSD_ROOT does not exist."
    warn "See docs/jetson-setup.md for formatting + mounting the SSD"
    warn "(ext4, fstab with UUID). Without an SSD the system cannot store recordings."
else
    if [[ ! -w "$SSD_ROOT" ]]; then
        warn "$SSD_ROOT is not writable (permissions?)."
    fi
    mkdir -p "$RECORDINGS_HOST_DIR" 2>/dev/null || true
    if [[ ! -w "$RECORDINGS_HOST_DIR" ]]; then
        warn "$RECORDINGS_HOST_DIR could not be created."
    else
        log "Recordings directory ready: $RECORDINGS_HOST_DIR"
    fi
fi

# --- USB-Webcam ------------------------------------------------------------------
if ls /dev/video* >/dev/null 2>&1; then
    log "Video devices found:"
    ls -l /dev/video* | sed 's/^/    /'
else
    warn "No /dev/video* devices found. Plug in the webcam and check again."
    warn "Debug: v4l2-ctl --list-devices (package: v4l-utils)"
fi

# --- Autostart: Docker + Home-Monitoring beim Booten ---------------------------
log "Enabling Docker on boot (systemctl enable docker)."
sudo systemctl enable docker

# Install the systemd service for the compose stack.
SERVICE_SRC="$REPO_DIR/scripts/home-monitoring.service"
SERVICE_DST=/etc/systemd/system/home-monitoring.service
if [[ ! -f "$SERVICE_DST" ]] || ! diff -q "$SERVICE_SRC" "$SERVICE_DST" >/dev/null 2>&1; then
    log "Installiere systemd-Service: $SERVICE_DST"
    sudo sed "s|WorkingDirectory=.*|WorkingDirectory=$REPO_DIR|" "$SERVICE_SRC" \
        | sudo tee "$SERVICE_DST" >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable home-monitoring
    log "Autostart enabled: container starts automatically on boot."
else
    log "systemd service already installed and up to date."
fi

log "Done. See README.md for the next step (docker compose up)."
