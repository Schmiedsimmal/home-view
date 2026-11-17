# WireGuard-Setup (Zugriff vom Handy)

Ziel: Vom Handy aus (über das Internet) auf die Web-UI des Jetson zugreifen,
**ohne** Ports im Heimnetz offen zu legen. WireGuard verschlüsselt den
Zugriff und überwindet NAT.

Es gibt drei Varianten — **Variante A (WireGuard auf dem Jetson)** ist die
empfohlene und wird von `scripts/setup-wireguard.sh` unterstützt.

---

## Variante A: WireGuard auf dem Jetson (empfohlen)

Der Jetson wird selbst zum WireGuard-Server. Das Handy verbindet sich als
Peer. Am Router muss nur UDP 51820 auf die Jetson-IP weitergeleitet werden.

### A1. Auf dem Jetson ausführen

```bash
bash scripts/setup-wireguard.sh
```

Das Skript:
- installiert `wireguard` + `wireguard-tools`
- generiert Server-Keys (`/etc/wireguard/jetson_private.key`, `jetson_public.key`)
- legt `/etc/wireguard/wg0.conf` an (Address `10.0.0.1/24`, Port 51820)
- aktiviert `wg-quick@wg0` (autostart beim Booten)

Ausgabe beachten: **öffentlicher Key des Jetson** + VPN-IP `10.0.0.1`.

### A2. Router: Port-Forward einrichten

Im Heimrouter (z.B. Fritz!Box, EasyBox, OpenWrt):

- **UDP 51820** an die **LAN-IP des Jetson** weiterleiten.
- Optional: Dem Jetson eine **statische DHCP-Reservation** geben (damit die
  LAN-IP nicht wechselt).
- Falls der Jetson keine feste öffentliche IP hat: **DDNS** einrichten
  (z.B. DuckDNS, afraid.org) und im Phone-Config als Endpoint nutzen.

### A3. Handy: WireGuard-App konfigurieren

1. WireGuard-App installieren (iOS App Store / Android Play Store).
2. Peer hinzufügen (QR-Code oder manuell):

   | Feld | Wert |
   |------|------|
   | PrivateKey | eigener privater Key des Handys (in der App generierbar) |
   | Address | `10.0.0.2/24` |
   | DNS | (leer lassen) |
   | Peer PublicKey | **öffentlicher Key des Jetson** (aus A1) |
   | Endpoint | `<deine-oeffentliche-IP-oder-DDNS>:51820` |
   | AllowedIPs | `10.0.0.0/24` |
   | Keepalive | `25` (empfohlen, hält NAT-Verbindung offen) |

3. Handys-**Public-Key** kopieren (in der App sichtbar).

### A4. Peer auf dem Jetson hinterlegen

Öffentlichen Key des Handys in `/etc/wireguard/wg0.conf` ergänzen:

```ini
[Peer]
# Handy (z.B. iPhone)
PublicKey = <public-key-des-handys>
AllowedIPs = 10.0.0.2/32
```

Dann neu laden:

```bash
sudo systemctl restart wg-quick@wg0
wg show   # Peer sollte sichtbar sein
```

### A5. Zugriff testen

- Handy: WireGuard-Tunnel aktivieren.
- Browser: `http://10.0.0.1:8080` → Login mit `WEB_PASSWORD`.

---

## Variante B: WireGuard auf dem Router

Falls der Router bereits WireGuard unterstützt (z.B. Fritz!Box mit
WireGuard-Erweiterung, OpenWrt, ASUS Merlin):

1. WireGuard-Server **auf dem Router** einrichten (nicht auf dem Jetson).
2. Jetson und Handy als Peers eintragen.
3. Zugriff: `http://<jetson-lan-ip>:8080` (z.B. `http://192.168.1.50:8080`).

Vorteil: Ein VPN für das ganze Heimnetz, keine Port-Forwards nötig.
Nachteil: Mehr Konfiguration am Router, Jetson bleibt "dumm".

## Variante C: WireGuard auf einem VPS

Falls bereits ein VPS existiert (z.B. für andere Dienste):

1. WireGuard-Server auf dem VPS (z.B. via `wg-easy` oder manuell).
2. Jetson + Handy als Peers.
3. Zugriff über die VPN-IP des Jetson.

Vorteil: Funktioniert auch ohne Port-Forward am Heimrouter (Outbound-only).
Nachteil: VPS-Kosten, etwas mehr Latenz.

---

## Sicherheitshinweise

- **Web-Passwort nicht leer lassen** (`WEB_PASSWORD` in `.env`). WireGuard
  schützt den Tunnel, aber das Passwort ist die zweite Barriere.
- **ntfy-Topic** als geheim behandeln: Jeder, der den Topic kennt, kann die
  Benachrichtigungen lesen. Einen eindeutigen Topic wählen
  (z.B. `mein-name-hund-monitor-<zufall>`).
- **Keys nicht committen**: `/etc/wireguard/*.key` und `.env` sind
  gitignored — niemals in ein Repo pushen.
- **WireGuard-Keys rotieren** falls ein Handy verloren geht:
  neuen Key generieren, alten Peer aus `wg0.conf` entfernen.

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| `http://10.0.0.1:8080` nicht erreichbar | `sudo wg show` auf dem Jetson: Peer-Handshake vorhanden? Sonst: Endpoint/Port-Forward prüfen, Keepalive=25 setzen. |
| Verbindet, aber Seite lädt nicht | Web-UI-Container läuft? `docker compose ps`, `docker logs home-monitoring`. |
| Langsame Verbindung | MJPEG ist bandbreitenintensiv (~2-5 Mbit/s). `STREAM_MAX_FPS=10` oder `CAMERA_FPS=10` in `.env` senken. |
| WireGuard-Interface startet nicht | `sudo journalctl -u wg-quick@wg0 -n 50` — oft Port-Konflikt oder Syntaxfehler in `wg0.conf`. |
