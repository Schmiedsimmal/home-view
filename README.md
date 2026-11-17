# Home Monitoring

Wohnungsüberwachung auf dem **NVIDIA Jetson Nano**: USB-Webcam →
24/7-Überwachung (Bewegung + Hund-Erkennung) → MJPEG-Livestream per
Web-UI → Zugriff vom Handy über **WireGuard**.

Alles läuft in **einem Docker-Container**, gestartet per `docker compose`.

## Features

- **Live-View:** MJPEG-Stream im Browser, per Knopf startbar/stopbar
- **24/7-Überwachung:** Kamera läuft permanent, auch wenn niemand zuschaut
- **Kamera:** USB-Webcam **oder** CSI-Kamera (Flachband-Kabel, z.B. IMX219)
  via nvargus/GStreamer – Backend per `CAMERA_BACKEND` (`auto`/`usb`/`nvargus`)
- **Bewegungserkennung:** Frame-Differenz ohne ML (CPU-schonend)
- **Hund-Erkennung:** YOLOv8 via OpenCV `cv2.dnn` (CUDA) mit ONNX-Modell,
  erkennt `dog`/`person` (COCO)
- **Event-Aufzeichnung:** MP4-Segmente (H.264) auf der SSD, Trigger
  konfigurierbar (`motion` / `dog` / `motion_or_dog` / `always`)
- **Auto-Retention:** Aufnahmen nach X Tagen oder bei SSD-Füllstand löschen
- **Push-Benachrichtigung:** ntfy.sh bei Hund-Erkennung (optional mit Foto)
- **Web-UI:** Passwort-Login, dunkles Theme, deutsch, mobil-tauglich
- **Aufnahmen-Playback:** Liste + Abspielen/Download im Browser
- **Autostart:** systemd-Service startet Container beim Booten

## Schnellstart (Jetson Nano)

```bash
# 1. Repo klonen (oder kopieren) und ins Verzeichnis wechseln
cd home-monitoring

# 2. Einmalig einrichten (Docker, NVIDIA Runtime, SSD-Check, Autostart)
bash scripts/setup-jetson.sh

# 3. Konfiguration anlegen
cp .env.example .env
# .env bearbeiten: WEB_PASSWORD, WEB_SECRET, NOTIFY_NTFY_TOPIC, ...

# 4. Starten
# Auf dem Jetson: docker-compose.override.yml umbenennen (die ist nur für
# x86-Development), dann:
#   mv docker-compose.override.yml docker-compose.override.yml.disabled
# Wichtig: Build mit DOCKER_BUILDKIT=0 (der Jetson hat IPv6-only Internet;
# der Legacy-Builder erlaubt das resolv.conf-DNS-Fix im Dockerfile).
DOCKER_BUILDKIT=0 docker-compose up --build -d
```

Web-UI: `http://<jetson-ip>:8080` → Login (Passwort aus `.env`) → **Stream starten**.

> **Hinweis Python 3.6:** Das Jetson-Base-Image (JetPack 4.x) liefert
> Python 3.6. Alle Abhängigkeiten sind daher auf 3.6 gepinnt
> (`requirements.txt`). Die Detektion läuft über `cv2.dnn` + ONNX statt
> ultralytics/TensorRT (die Python 3.7+ benötigen). Das ONNX-Modell liegt
> in `./models/yolov8n.onnx` (Export: `yolo export model=yolov8n.pt format=onnx imgsz=640`).

## Zugriff vom Handy (WireGuard)

Siehe [`docs/wireguard-setup.md`](docs/wireguard-setup.md).

Kurz: `bash scripts/setup-wireguard.sh` auf dem Jetson, Port-Forward
UDP 51820 am Router, Handy per WireGuard-App verbinden, dann
`http://10.0.0.1:8080` im Browser.

## Architektur

Siehe [`docs/architecture.md`](docs/architecture.md).

```
Kamera (USB/CSI) ──▶ CameraSource (OpenCV + nvargus/GStreamer)
                        ├─▶ DetectorLoop (YOLOv8/cv2.dnn CUDA, ONNX) ──▶ ntfy.sh
                        ├─▶ MotionLoop (Frame-Diff)
                        ├─▶ MJPEG /stream ──▶ Browser
                        └─▶ RecorderController ──▶ ffmpeg ──▶ /mnt/ssd/recordings
```

## Konfiguration (.env)

Wichtige Variablen (alle mit Default-Werten in `.env.example`):

| Variable | Bedeutung | Default |
|----------|-----------|---------|
| `WEB_PASSWORD` | Login-Passwort (unbedingt ändern!) | `changeme` |
| `WEB_SECRET` | Session-Signierung (`openssl rand -hex 32`) | – |
| `CAMERA_SOURCE` | `usb:0` oder `rtsp://...` | `usb:0` |
| `CAMERA_WIDTH/HEIGHT/FPS` | Auflösung/Framerate | 1280x720@15 |
| `STREAM_MAX_FPS` | MJPEG-Stream-Rate | 15 |
| `DETECTOR_ENABLED` | YOLO an/aus | `true` |
| `DETECT_FPS` | Inferenz-Rate | 5 |
| `DETECT_CLASSES` | COCO-Klassen (0=person, 16=dog) | `0,16` |
| `MOTION_ENABLED` | Bewegungserkennung an/aus | `true` |
| `RECORD_ENABLED` | Aufzeichnung an/aus | `true` |
| `RECORD_TRIGGER` | `motion`/`dog`/`motion_or_dog`/`always` | `motion_or_dog` |
| `RECORD_FPS` | Aufzeichnungs-Rate | 10 |
| `RECORD_SEGMENT_S` | Segment-Länge [s] | 300 |
| `RECORD_RETENTION_DAYS` | Auto-Löschung [Tage] | 7 |
| `RECORD_MAX_DISK_PERCENT` | SSD-Füllstand-Limit [%] | 80 |
| `RECORDINGS_HOST_DIR` | Host-Pfad der Aufnahmen | `/mnt/ssd/recordings` |
| `NOTIFY_ENABLED` | ntfy.sh an/aus | `true` |
| `NOTIFY_NTFY_TOPIC` | ntfy-Topic | (leer) |
| `NOTIFY_COOLDOWN_S` | Benachrichtigungs-Abstand [s] | 300 |

Vollständige Liste: [`.env.example`](.env.example).

## Docs

- [`docs/architecture.md`](docs/architecture.md) – Komponenten, Thread-Modell, API
- [`docs/jetson-setup.md`](docs/jetson-setup.md) – Jetson-Einrichtung, SSD, Webcam, Power
- [`docs/wireguard-setup.md`](docs/wireguard-setup.md) – WireGuard (Jetson/Router/VPS), Handy

## Entwicklung (x86)

Ohne GPU: `docker-compose.override.yml` ist bereits vorhanden (kein
`runtime: nvidia`, CPU-Inferenz). Video-Device in der Override-Datei an
die lokale Kamera anpassen.

Tests:

```bash
docker compose run --rm home-monitoring python -m pytest tests/ -v
```

## Performance-Hinweise (Jetson Nano)

Gleichzeitige Last: YOLO (GPU) + MJPEG-Encoding (CPU) + x264 (CPU).
Bei Rucklern die `.env`-Werte senken (`STREAM_MAX_FPS`, `DETECT_FPS`,
`RECORD_FPS`, Auflösung). Details: [`docs/jetson-setup.md`](docs/jetson-setup.md#7-performance-tuning-jetson-nano).
