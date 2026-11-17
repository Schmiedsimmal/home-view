#!/usr/bin/env bash
# =============================================================================
# Optional: WireGuard direkt auf dem Jetson Nano als Server einrichten.
#
# Damit ist der Jetson unter einer festen VPN-IP (z.B. 10.0.0.1) erreichbar,
# das Handy verbindet sich als Peer. Am Router muss UDP 51820 auf die
# Jetson-IP weitergeleitet werden (oder DDNS + Port-Forward).
#
# Siehe docs/wireguard-setup.md fuer Details und Alternativen
# (WireGuard auf dem Router oder einem VPS).
#
# Idempotent: kann gefahrlos mehrfach ausgefuehrt werden.
# =============================================================================
set -euo pipefail

log()  { printf '\033[1;34m[wg     ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn   ]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[err    ]\033[0m %s\n' "$*" >&2; exit 1; }

WG_CONF_DIR=/etc/wireguard
WG_CONF="$WG_CONF_DIR/wg0.conf"
JETSON_IP="10.0.0.1/24"
LISTEN_PORT="${WG_LISTEN_PORT:-51820}"

[[ "$(uname -m)" == "aarch64" ]] || warn "Kein aarch64-Host (kein Jetson?). Skript läuft trotzdem."

# --- WireGuard installieren ---------------------------------------------------
if ! command -v wg >/dev/null 2>&1; then
    log "Installiere WireGuard..."
    sudo apt-get update
    sudo apt-get install -y wireguard wireguard-tools
else
    log "WireGuard vorhanden: $(wg --version | head -1)"
fi

# --- Keys generieren (falls noch nicht vorhanden) ------------------------------
if [[ ! -f "$WG_CONF_DIR/jetson_private.key" ]]; then
    log "Generiere WireGuard-Keys..."
    sudo mkdir -p "$WG_CONF_DIR"
    sudo sh -c 'umask 077; wg genkey | tee jetson_private.key | wg pubkey > jetson_public.key' \
        --chdir="$WG_CONF_DIR" 2>/dev/null || {
        # Fallback ohne --chdir (aeltere wg-Versionen)
        sudo bash -c "umask 077; wg genkey > $WG_CONF_DIR/jetson_private.key && wg pubkey < $WG_CONF_DIR/jetson_private.key > $WG_CONF_DIR/jetson_public.key"
    }
fi

JETSON_PRIV=$(sudo cat "$WG_CONF_DIR/jetson_private.key")
JETSON_PUB=$(sudo cat "$WG_CONF_DIR/jetson_public.key")

# --- wg0.conf anlegen (falls nicht vorhanden) -----------------------------------
if [[ ! -f "$WG_CONF" ]]; then
    log "Lege $WG_CONF an (ohne Phone-Peer — Peer spaeter ergaenzen)."
    sudo tee "$WG_CONF" >/dev/null <<EOF
# WireGuard-Server auf dem Jetson Nano (home-monitoring)
# Siehe docs/wireguard-setup.md
[Interface]
Address = $JETSON_IP
ListenPort = $LISTEN_PORT
PrivateKey = $JETSON_PRIV
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Phone-Peer hier ergaenzen (siehe docs/wireguard-setup.md):
# [Peer]
# PublicKey = <public-key-des-handys>
# AllowedIPs = 10.0.0.2/32
EOF
    sudo chmod 600 "$WG_CONF"
else
    log "$WG_CONF existiert bereits — ueberschreibe nicht."
fi

# --- Service aktivieren ----------------------------------------------------------
sudo systemctl enable wg-quick@wg0
sudo systemctl restart wg-quick@wg0
log "WireGuard-Interface wg0 gestartet."

# --- Ausgabe ----------------------------------------------------------------------
cat <<EOF

=====================================================================
WireGuard auf dem Jetson eingerichtet.

Jetson VPN-IP:        ${JETSON_IP%%/*}  (Interface wg0)
Oeffentlicher Key:    $JETSON_PUB

Naechste Schritte:
1. Router: UDP ${LISTEN_PORT} an die Jetson-LAN-IP weiterleiten
   (oder DDNS + Port-Forward, falls keine statische IP).
2. Handy: WireGuard-App installieren, Peer anlegen mit:
     PrivateKey = <privater Key des Handys>
     Address    = 10.0.0.2/24
     Peer       = PublicKey oben, Endpoint = <deine-oeffentliche-IP>:${LISTEN_PORT}
     AllowedIPs = 10.0.0.0/24
3. Handys-Public-Key als [Peer] in $WG_CONF ergaenzen
   (AllowedIPs = 10.0.0.2/32), dann: sudo systemctl restart wg-quick@wg0
4. Zugriff: http://10.0.0.1:8080 im Browser (nach Web-Login).

Details: docs/wireguard-setup.md
=====================================================================
EOF
