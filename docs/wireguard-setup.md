# WireGuard setup (access from your phone)

Goal: access the Jetson's web UI from your phone (over the internet)
**without** opening ports on the home network. WireGuard encrypts the
access and traverses NAT.

There are three variants — **variant A (WireGuard on the Jetson)** is the
recommended one and is supported by `scripts/setup-wireguard.sh`.

---

## Variant A: WireGuard on the Jetson (recommended)

The Jetson itself becomes the WireGuard server. The phone connects as a
peer. On the router only UDP 51820 needs to be forwarded to the Jetson IP.

### A1. Run on the Jetson

```bash
bash scripts/setup-wireguard.sh
```

The script:
- installs `wireguard` + `wireguard-tools`
- generates server keys (`/etc/wireguard/jetson_private.key`, `jetson_public.key`)
- creates `/etc/wireguard/wg0.conf` (address `10.0.0.1/24`, port 51820)
- enables `wg-quick@wg0` (autostart on boot)

Note the output: **the Jetson's public key** + VPN IP `10.0.0.1`.

### A2. Router: set up the port forward

On the home router (e.g. Fritz!Box, EasyBox, OpenWrt):

- Forward **UDP 51820** to the **Jetson's LAN IP**.
- Optional: give the Jetson a **static DHCP reservation** (so the LAN IP
  does not change).
- If the Jetson has no fixed public IP: set up **DDNS**
  (e.g. DuckDNS, afraid.org) and use it as the endpoint in the phone config.

### A3. Phone: configure the WireGuard app

1. Install the WireGuard app (iOS App Store / Android Play Store).
2. Add a peer (QR code or manually):

   | Field | Value |
   |-------|-------|
   | PrivateKey | your phone's own private key (generatable in the app) |
   | Address | `10.0.0.2/24` |
   | DNS | (leave empty) |
   | Peer PublicKey | **the Jetson's public key** (from A1) |
   | Endpoint | `<your-public-IP-or-DDNS>:51820` |
   | AllowedIPs | `10.0.0.0/24` |
   | Keepalive | `25` (recommended, keeps the NAT connection open) |

3. Copy the phone's **public key** (visible in the app).

### A4. Register the peer on the Jetson

Add the phone's public key to `/etc/wireguard/wg0.conf`:

```ini
[Peer]
# Phone (e.g. iPhone)
PublicKey = <public-key-of-the-phone>
AllowedIPs = 10.0.0.2/32
```

Then reload:

```bash
sudo systemctl restart wg-quick@wg0
wg show   # the peer should be visible
```

### A5. Test access

- Phone: enable the WireGuard tunnel.
- Browser: `http://10.0.0.1:8080` → login with `WEB_PASSWORD`.

---

## Variant B: WireGuard on the router

If the router already supports WireGuard (e.g. Fritz!Box with the
WireGuard extension, OpenWrt, ASUS Merlin):

1. Set up the WireGuard server **on the router** (not on the Jetson).
2. Register the Jetson and the phone as peers.
3. Access: `http://<jetson-lan-ip>:8080` (e.g. `http://192.168.1.50:8080`).

Advantage: one VPN for the whole home network, no port forwards needed.
Disadvantage: more router configuration, the Jetson stays "dumb".

## Variant C: WireGuard on a VPS

If a VPS already exists (e.g. for other services):

1. Set up the WireGuard server on the VPS (e.g. via `wg-easy` or manually).
2. Jetson + phone as peers.
3. Access via the Jetson's VPN IP.

Advantage: works even without a port forward on the home router (outbound-only).
Disadvantage: VPS costs, a bit more latency.

---

## Security notes

- **Do not leave the web password empty** (`WEB_PASSWORD` in `.env`).
  WireGuard protects the tunnel, but the password is the second barrier.
- Treat the **ntfy topic** as a secret: anyone who knows the topic can
  read the notifications. Choose a unique topic
  (e.g. `my-name-dog-monitor-<random>`).
- **Do not commit keys**: `/etc/wireguard/*.key` and `.env` are
  gitignored — never push them to a repo.
- **Rotate WireGuard keys** if a phone is lost:
  generate a new key, remove the old peer from `wg0.conf`.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `http://10.0.0.1:8080` unreachable | `sudo wg show` on the Jetson: peer handshake present? Otherwise: check endpoint/port forward, set Keepalive=25. |
| Connects but page does not load | Is the web UI container running? `docker compose ps`, `docker logs home-monitoring`. |
| Slow connection | MJPEG is bandwidth-intensive (~2-5 Mbit/s). Lower `STREAM_MAX_FPS=10` or `CAMERA_FPS=10` in `.env`. |
| WireGuard interface does not start | `sudo journalctl -u wg-quick@wg0 -n 50` — often a port conflict or syntax error in `wg0.conf`. |
