# Jetson-Nano-Setup

## Voraussetzungen

- **Jetson Nano** mit JetPack 4.x (Ubuntu 18.04) oder 5.x (Ubuntu 20.04)
- **USB-Webcam** (beliebig, z.B. 720p/1080p UVC-konform)
- **SSD** für Aufnahmen (USB oder SATA, mind. 32 GB empfohlen)
- **WLAN**-Verbindung im Heimnetz

## 1. Docker + NVIDIA Container Runtime

Einmalig auf dem Jetson ausführen:

```bash
cd ~/git/home-monitoring   # oder wo immer das Repo liegt
bash scripts/setup-jetson.sh
```

Das Skript installiert/prüft:
- Docker + Docker Compose v2
- NVIDIA Container Runtime (`nvidia-docker2`) — für GPU-Zugriff aus dem Container
- systemd-Autostart (`home-monitoring.service`)
- SSD-Mountpoint `/mnt/ssd/recordings`
- Video-Geräte `/dev/video*`

Wenn die NVIDIA Runtime fehlt, manuell:

```bash
sudo apt-get update
sudo apt-get install -y nvidia-docker2
sudo systemctl restart docker
```

## 2. SSD für Aufnahmen einrichten

Die SSD wird unter `/mnt/ssd` gemountet; Aufnahmen landen in
`/mnt/ssd/recordings` (in `.env`: `RECORDINGS_HOST_DIR`).

### Neue SSD partitionieren + formatieren (einmalig)

```bash
# Gerät finden (z.B. /dev/sda1 oder /dev/nvme0n1p1)
lsblk -f

# Optional: Partition anlegen (falls die SSD leer ist)
# sudo fdisk /dev/sda   # neue primäre Partition, Typ Linux

# Formatieren (Daten gehen verloren!)
sudo mkfs.ext4 -L recordings /dev/sda1

# Mountpoint anlegen
sudo mkdir -p /mnt/ssd

# Mounten
sudo mount /dev/sda1 /mnt/ssd
```

### Persistent mounten (fstab)

```bash
# UUID ermitteln
sudo blkid /dev/sda1

# fstab-Eintrag (UUID anpassen!)
echo 'UUID=<uuid-aus-blkid> /mnt/ssd ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab

# Aufnahmen-Verzeichnis anlegen
sudo mkdir -p /mnt/ssd/recordings
sudo chown $USER:$USER /mnt/ssd/recordings

# Testen
sudo mount -a && df -h /mnt/ssd
```

> **Wichtig:** `nofail` sorgt dafür, dass der Jetson auch ohne SSD bootet
> (Aufzeichnung ist dann deaktiviert, Live-View funktioniert weiter).

## 3. Kamera prüfen

### 3a. USB-Webcam

```bash
# Geräte auflisten
v4l2-ctl --list-devices    # Paket: sudo apt-get install v4l-utils
```

- Die Kamera erscheint üblicherweise als `/dev/video0`.
- In `.env`: `CAMERA_BACKEND=usb`, `VIDEO_DEVICE=/dev/video0` (falls abweichend).

### 3b. CSI-Kamera (Flachband-Kabel, z.B. IMX219)

CSI-Kameras erscheinen **nicht** als `/dev/video*` – sie laufen über den
Argus-Daemon (`nvarguscamerasrc`). Das System erkennt das Backend automatisch
(`CAMERA_BACKEND=auto`), oder explizit `CAMERA_BACKEND=nvargus` setzen.

Prüfen, ob Argus die Kamera sieht:

```bash
gst-launch-1.0 nvarguscamerasrc num-buffers=30 ! \
  'video/x-raw(memory:NVMM),width=1280,height=720,framerate=15/1' ! \
  nvvidconv ! 'video/x-raw,format=BGRx' ! videoconvert ! \
  'video/x-raw,format=BGR' ! fakesink
```

Läuft der Stream ohne Fehler, ist die Kamera OK.

**Fehler "No cameras available" / i2c-Fehler (-121):**
Das ist ein **Hardware-/Kabel-Problem**, kein Software-Problem:

1. Jetson ausschalten, Flachband-Kabel an **beiden** Enden prüfen:
   - Goldkontakte müssen sauber und vollständig im Stecker sitzen,
     Verschlusslasche zu.
   - Auf der Kamera-Seite zeigen die Kontakte zum Kameramodul,
     auf der Jetson-Seite in die richtige Richtung (siehe Jetson-Doku
     für den CSI-Stecker).
2. Anderen CSI-Port (CAM0/CAM1) oder anderes Kabel testen.
3. Nach dem Wiedereinstecken: Jetson neu starten
   (`sudo systemctl restart nvargus-daemon` reicht oft).
4. Kernel-Log prüfen: `sudo dmesg | grep imx` –
   `probe of 6-0010 failed with error -121` = I2C-Kommunikation gestört.

> Hinweis: Eine abgedunkelte Kamera-Linse (schwarze Kappe) liefert ein
> schwarzes Bild – das ist normal und kein Fehler.

## 4. Konfiguration (.env)

```bash
cp .env.example .env
# .env bearbeiten:
#   WEB_PASSWORD  -> starkes Passwort setzen!
#   WEB_SECRET    -> openssl rand -hex 32
#   NOTIFY_NTFY_TOPIC -> eindeutiger ntfy-Topic
#   RECORDINGS_HOST_DIR -> /mnt/ssd/recordings (oder eigener Pfad)
```

## 5. Starten

```bash
# Auf dem Jetson: Dev-Override deaktivieren (die ist nur fuer x86)
mv docker-compose.override.yml docker-compose.override.yml.disabled

# Build + Start (WICHTIG: DOCKER_BUILDKIT=0, siehe Hinweis unten)
DOCKER_BUILDKIT=0 docker-compose up --build -d
```

> **Warum `DOCKER_BUILDKIT=0`?** Der Jetson hat ein **IPv6-only Internet**
> (kein IPv4-Default-Route). Docker/BuildKit gibt Build-Containern
> `8.8.8.8` als DNS, das ohne IPv4 nicht aufloest. Der Legacy-Builder
> erlaubt es, im Dockerfile `/etc/resolv.conf` auf `127.0.0.53`
> (systemd-resolved-Stub des Hosts, im Host-Netzwerk erreichbar) zu
> ueberschreiben — so kommt `pip install` an pypi.org. Zur Laufzeit nutzt
> der Container `dns: 127.0.0.53` (siehe `docker-compose.yml`).

> **ONNX-Modell:** `./models/yolov8n.onnx` muss auf dem Host vorhanden sein
> (wird nach `/app/models` gemountet). Export z.B. mit
> `yolo export model=yolov8n.pt format=onnx imgsz=640`.

Web-UI: `http://<jetson-ip>:8080` (Login mit `WEB_PASSWORD`).

Autostart ist nach `setup-jetson.sh` automatisch aktiv. Manuell steuern:

```bash
sudo systemctl start home-monitoring    # Container starten
sudo systemctl stop home-monitoring     # Container stoppen
sudo systemctl status home-monitoring   # Status
journalctl -u home-monitoring -f        # Logs
```

## 6. Power-Modus (optional, für 24/7-Betrieb)

Der Jetson Nano hat einen 5W- und einen 10W-Modus. Für stabile
24/7-Performance (YOLO + MJPEG + ffmpeg) den 10W-Modus aktivieren:

```bash
sudo nvpmodel -m 0     # MAXN (10W, volle Leistung)
sudo nvpmodel -q       # aktuellen Modus abfragen
```

> Hinweis: Im 10W-Modus wird der Jetson wärmer. Auf eine gute Belüftung
> achten (kleiner Kühlkörper reicht meist).

## 7. Performance-Tuning (Jetson Nano)

Gleichzeitige Last: YOLO-Inferenz (GPU) + MJPEG-Encoding (CPU) +
x264-Aufzeichnung (CPU). Falls es ruckelt:

| Symptom | Maßnahme |
|---------|----------|
| Stream ruckelt | `STREAM_MAX_FPS=10`, `CAMERA_FPS=10`, `CAMERA_WIDTH/HEIGHT=960x540` |
| Detection langsam | `DETECT_FPS=3`, `YOLO_IMG_SIZE=416`, `YOLO_MODEL=yolov8n.onnx` (schon Default) |
| Aufzeichnung ruckelt | `RECORD_FPS=8`, `FFMPEG_PRESET=ultrafast` (schon Default), `FFMPEG_CRF=26` |
| Alles zusammen zu viel | `DETECTOR_ENABLED=false` (nur Bewegung) oder `MOTION_ENABLED=false` |

Die `.env`-Werte werden beim nächsten `docker compose up -d` übernommen
(Env-Änderung → Container neu starten).

## 8. Deployment-Status & bekannte Einschränkungen (Stand 2026-11-16)

Auf dem Jetson (`simon-jetson`, L4T R32.7.6, JetPack 4.6) ist der Stack
deployed und läuft:

- Container `home-monitoring` **healthy**, Web-UI erreichbar unter
  `http://192.168.178.10:8080` (Login mit `WEB_PASSWORD` aus `.env`).
- Detektor geladen: YOLOv8 ONNX via `cv2.dnn` **CUDA** (Klassen 0,16).
- systemd-Autostart `home-monitoring.service` ist installiert + enabled.
- Docker-Dienst ist enabled (Container starten mit `restart: unless-stopped`).

**Offene Punkte (Hardware, nur vor Ort lösbar):**

1. **CSI-Kamera (IMX219):** Kernel meldet
   `imx219_board_setup: error during i2c read probe (-121)` und
   `tegra-i2c 7000c700: no acknowledge from address 0x50`.
   `i2cdetect -y -r 6` zeigt **kein** Gerät → das Flachband-Kabel ist
   nicht korrekt gesteckt/ausgerichtet oder das Kameramodul defekt.
   → Flachband-Kabel an beiden Enden prüfen (Goldkontakte sauber,
   Verschlusslasche zu, contacts zeigen in die richtige Richtung),
   ggf. anderen CSI-Port (CAM0/CAM1) testen, dann Jetson neu starten.
   Sobald Argus die Kamera sieht, liefert der Stream (schwarze Kappe =
   schwarzes Bild, das ist normal).
2. **SSD `/mnt/ssd`:** Aktuell **kein** externes SSD eingesteckt
   (`lsblk` zeigt nur die interne eMMC). `/mnt/ssd/recordings` liegt
   damit auf dem Root-FS (59 GB, ~91 % voll!). Aufnahmen müssen vor
   produktivem Einsatz auf eine echte SSD umziehen (Abschnitt 2),
   sonst füllt die Aufzeichnung die interne SSD.
3. **Root-FS zu 90 % voll:** Alte Docker-Images/Container belegen
   ~13 GB. Aufräumen mit `docker image prune -a` / `docker container
   prune` (nach Backup-Prüfung der eigenen Container!) oder alte
   Images entfernen.
4. **WireGuard:** Noch nicht eingerichtet (siehe
   `docs/wireguard-setup.md`). Router-Port-Forward UDP 51820 nötig.
5. **Passwort:** `WEB_PASSWORD` in `.env` ist noch der Standardwert
   `home-monitoring` → vor produktivem Einsatz ändern.
