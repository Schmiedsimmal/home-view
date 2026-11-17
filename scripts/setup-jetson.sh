#!/usr/bin/env bash
# =============================================================================
# Einrichtungsskript fuer den Jetson Nano (Host).
#
# Installiert/prüft:
#   - Docker + Docker Compose
#   - NVIDIA Container Runtime (nvidia-docker2) fuer GPU-Zugriff in Containern
#   - SSD-Mount (/mnt/ssd) fuer Aufnahmen
#   - USB-Webcam (/dev/video*)
#   - systemd-Autostart (home-monitoring.service)
#
# Idempotent: kann gefahrlos mehrfach ausgefuehrt werden.
# =============================================================================
set -euo pipefail

log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[err ]\033[0m %s\n' "$*" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

[[ "$(uname -m)" == "aarch64" ]] || warn "Kein aarch64-Host (kein Jetson?). Skript läuft trotzdem."

# --- Docker -------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    log "Docker nicht gefunden. Installiere via offiziellem Convenience-Skript."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    warn "Ab- und wieder anmelden, damit die docker-Gruppe greift."
else
    log "Docker vorhanden: $(docker --version)"
fi

# Docker Compose: Plugin ("docker compose") oder Standalone ("docker-compose").
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
    log "Docker Compose (Plugin): $(docker compose version --short)"
elif docker-compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
    log "Docker Compose (Standalone): $(docker-compose version --short)"
else
    warn "Docker Compose nicht gefunden."
    warn "Auf dem Jetson (GitHub oft nicht erreichbar) das Binary manuell kopieren:"
    warn "  # auf einem Rechner mit Internet:"
    warn "  curl -sL -o docker-compose https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-aarch64"
    warn "  scp docker-compose simon@<jetson-ip>:/tmp/"
    warn "  # auf dem Jetson:"
    warn "  sudo cp /tmp/docker-compose /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose"
    die "Docker Compose v2 fehlt. Bitte zuerst installieren (siehe Hinweis oben)."
fi

# --- NVIDIA Container Runtime -------------------------------------------------
if docker info 2>/dev/null | grep -q nvidia; then
    log "NVIDIA Container Runtime aktiv."
else
    warn "NVIDIA Container Runtime nicht erkannt."
    warn "Auf JetPack 4.x installieren:"
    warn "  sudo apt-get update"
    warn "  sudo apt-get install -y nvidia-docker2"
    warn "  sudo systemctl restart docker"
    warn "Danach dieses Skript erneut ausführen."
fi

# --- SSD-Mount (/mnt/ssd) -------------------------------------------------------
RECORDINGS_HOST_DIR="${RECORDINGS_HOST_DIR:-/mnt/ssd/recordings}"
SSD_ROOT="${RECORDINGS_HOST_DIR%/recordings}"
if [[ ! -d "$SSD_ROOT" ]]; then
    warn "SSD-Mountpoint $SSD_ROOT existiert nicht."
    warn "Siehe docs/jetson-setup.md fuer das Formatieren + Mounten der SSD"
    warn "(ext4, fstab mit UUID). Ohne SSD kann das System Aufnahmen nicht speichern."
else
    if [[ ! -w "$SSD_ROOT" ]]; then
        warn "$SSD_ROOT ist nicht beschreibbar (Berechtigungen?)."
    fi
    mkdir -p "$RECORDINGS_HOST_DIR" 2>/dev/null || true
    if [[ ! -w "$RECORDINGS_HOST_DIR" ]]; then
        warn "$RECORDINGS_HOST_DIR konnte nicht angelegt werden."
    else
        log "Aufnahmeverzeichnis bereit: $RECORDINGS_HOST_DIR"
    fi
fi

# --- USB-Webcam ------------------------------------------------------------------
if ls /dev/video* >/dev/null 2>&1; then
    log "Video-Geraete gefunden:"
    ls -l /dev/video* | sed 's/^/    /'
else
    warn "Keine /dev/video*-Geraete gefunden. Webcam anschliessen und erneut pruefen."
    warn "Debug: v4l2-ctl --list-devices (Paket: v4l-utils)"
fi

# --- Autostart: Docker + Home-Monitoring beim Booten ---------------------------
log "Aktiviere Docker beim Booten (systemctl enable docker)."
sudo systemctl enable docker

# systemd-Service fuer den Compose-Stack installieren.
SERVICE_SRC="$REPO_DIR/scripts/home-monitoring.service"
SERVICE_DST=/etc/systemd/system/home-monitoring.service
if [[ ! -f "$SERVICE_DST" ]] || ! diff -q "$SERVICE_SRC" "$SERVICE_DST" >/dev/null 2>&1; then
    log "Installiere systemd-Service: $SERVICE_DST"
    sudo sed "s|WorkingDirectory=.*|WorkingDirectory=$REPO_DIR|" "$SERVICE_SRC" \
        | sudo tee "$SERVICE_DST" >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable home-monitoring
    log "Autostart aktiviert: Container starten beim Booten automatisch."
else
    log "systemd-Service bereits installiert und aktuell."
fi

log "Fertig. Siehe README.md fuer den naechsten Schritt (docker compose up)."
