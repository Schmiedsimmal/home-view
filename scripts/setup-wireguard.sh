#!/usr/bin/env bash
# =============================================================================
# Optional: Set up WireGuard directly on the Jetson Nano as a server.
#
# This makes the Jetson reachable at a fixed VPN IP (e.g. 10.0.0.1),
# and the phone connects as a peer. On the router, UDP 51820 must be
# forwarded to the Jetson IP (or use DDNS + port forward).
#
# See docs/wireguard-setup.md for details and alternatives
# (WireGuard on the router or on a VPS).
#
# Idempotent: can be run multiple times safely.
# =============================================================================
set -euo pipefail

log()  { printf '\033[1;34m[wg     ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn   ]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[err    ]\033[0m %s\n' "$*" >&2; exit 1; }

WG_CONF_DIR=/etc/wireguard
WG_CONF="$WG_CONF_DIR/wg0.conf"
JETSON_IP="10.0.0.1/24"
LISTEN_PORT="${WG_LISTEN_PORT:-51820}"

[[ "$(uname -m)" == "aarch64" ]] || warn "Not an aarch64 host (not a Jetson?). The script will run anyway."

# --- Install WireGuard ---------------------------------------------------------
if ! command -v wg >/dev/null 2>&1; then
    log "Installing WireGuard..."
    sudo apt-get update
    sudo apt-get install -y wireguard wireguard-tools
else
    log "WireGuard vorhanden: $(wg --version | head -1)"
fi

# --- Generate keys (if not already present) -----------------------------------
if [[ ! -f "$WG_CONF_DIR/jetson_private.key" ]]; then
    log "Generating WireGuard keys..."
    sudo mkdir -p "$WG_CONF_DIR"
    sudo sh -c 'umask 077; wg genkey | tee jetson_private.key | wg pubkey > jetson_public.key' \
        --chdir="$WG_CONF_DIR" 2>/dev/null || {
        # Fallback without --chdir (older wg versions)
        sudo bash -c "umask 077; wg genkey > $WG_CONF_DIR/jetson_private.key && wg pubkey < $WG_CONF_DIR/jetson_private.key > $WG_CONF_DIR/jetson_public.key"
    }
fi

JETSON_PRIV=$(sudo cat "$WG_CONF_DIR/jetson_private.key")
JETSON_PUB=$(sudo cat "$WG_CONF_DIR/jetson_public.key")

# --- Create wg0.conf (if not present) -------------------------------------------
if [[ ! -f "$WG_CONF" ]]; then
    log "Creating $WG_CONF (without phone peer — add the peer later)."
    sudo tee "$WG_CONF" >/dev/null <<EOF
# WireGuard server on the Jetson Nano (home-monitoring)
# See docs/wireguard-setup.md
[Interface]
Address = $JETSON_IP
ListenPort = $LISTEN_PORT
PrivateKey = $JETSON_PRIV
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Add the phone peer here (see docs/wireguard-setup.md):
# [Peer]
# PublicKey = <public-key-of-the-phone>
# AllowedIPs = 10.0.0.2/32
EOF
    sudo chmod 600 "$WG_CONF"
else
    log "$WG_CONF already exists — not overwriting."
fi

# --- Enable service ------------------------------------------------------------
sudo systemctl enable wg-quick@wg0
sudo systemctl restart wg-quick@wg0
log "WireGuard interface wg0 started."

# --- Output ---------------------------------------------------------------------
cat <<EOF

=====================================================================
WireGuard set up on the Jetson.

Jetson VPN IP:        ${JETSON_IP%%/*}  (interface wg0)
Public key:           $JETSON_PUB

Next steps:
1. Router: forward UDP ${LISTEN_PORT} to the Jetson LAN IP
   (or DDNS + port forward if there is no static IP).
2. Phone: install the WireGuard app, create a peer with:
     PrivateKey = <private key of the phone>
     Address    = 10.0.0.2/24
     Peer       = public key above, Endpoint = <your-public-IP>:${LISTEN_PORT}
     AllowedIPs = 10.0.0.0/24
3. Add the phone's public key as a [Peer] in $WG_CONF
   (AllowedIPs = 10.0.0.2/32), then: sudo systemctl restart wg-quick@wg0
4. Access: http://10.0.0.1:8080 in the browser (after web login).

Details: docs/wireguard-setup.md
=====================================================================
EOF
