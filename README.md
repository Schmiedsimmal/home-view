# Home Monitoring

Apartment monitoring on the **NVIDIA Jetson Nano**: USB webcam →
24/7 monitoring (motion + dog detection) → MJPEG live stream via
web UI → access from your phone over **WireGuard**.

Everything runs in **a single Docker container**, started with `docker compose`.

## Features

- **Live view:** MJPEG stream in the browser, startable/stoppable via button
- **24/7 monitoring:** camera runs permanently, even when nobody is watching
- **Camera:** USB webcam **or** CSI camera (flat ribbon cable, e.g. IMX219)
  via nvargus/GStreamer – backend selectable via `CAMERA_BACKEND` (`auto`/`usb`/`nvargus`)
- **Motion detection:** frame differencing without ML (CPU-friendly)
- **Dog detection:** YOLOv8 via OpenCV `cv2.dnn` (CUDA) with ONNX model,
  detects `dog`/`person` (COCO)
- **Event recording:** MP4 segments (H.264) on the SSD, configurable trigger
  (`motion` / `dog` / `motion_or_dog` / `always`)
- **Auto retention:** delete recordings after X days or when the SSD is too full
- **Push notification:** ntfy.sh on dog detection (optionally with photo)
- **Web UI:** password login, dark theme, English, mobile-friendly
- **Recording playback:** list + playback/download in the browser
- **Autostart:** systemd service starts the container on boot

## Quick start (Jetson Nano)

```bash
# 1. Clone the repo (or copy it) and cd into the directory
cd home-view

# 2. One-time setup (Docker, NVIDIA runtime, SSD check, autostart)
bash scripts/setup-jetson.sh

# 3. Create the configuration
cp .env.example .env
# Edit .env: WEB_PASSWORD, WEB_SECRET, NOTIFY_NTFY_TOPIC, ...

# 4. Start
# On the Jetson: rename the docker-compose.override.yml (it is only for
# x86 development), then:
#   mv docker-compose.override.yml docker-compose.override.yml.disabled
# Important: build with DOCKER_BUILDKIT=0 (the Jetson has IPv6-only internet;
# the legacy builder allows the resolv.conf DNS fix in the Dockerfile).
DOCKER_BUILDKIT=0 docker-compose up --build -d
```

Web UI: `http://<jetson-ip>:8080` → login (password from `.env`) → **Start stream**.

> **Python 3.6 note:** The Jetson base image (JetPack 4.x) ships
> Python 3.6. All dependencies are therefore pinned to 3.6
> (`requirements.txt`). Detection runs via `cv2.dnn` + ONNX instead of
> ultralytics/TensorRT (which require Python 3.7+). The ONNX model lives
> in `./models/yolov8n.onnx` (export: `yolo export model=yolov8n.pt format=onnx imgsz=640`).

## Access from your phone (WireGuard)

See [`docs/wireguard-setup.md`](docs/wireguard-setup.md).

Short version: `bash scripts/setup-wireguard.sh` on the Jetson, forward
UDP 51820 on the router, connect the phone via the WireGuard app, then
open `http://10.0.0.1:8080` in the browser.

## Architecture

See [`docs/architecture.md`](docs/architecture.md).

```
Camera (USB/CSI) ──▶ CameraSource (OpenCV + nvargus/GStreamer)
                        ├─▶ DetectorLoop (YOLOv8/cv2.dnn CUDA, ONNX) ──▶ ntfy.sh
                        ├─▶ MotionLoop (frame diff)
                        ├─▶ MJPEG /stream ──▶ Browser
                        └─▶ RecorderController ──▶ ffmpeg ──▶ /mnt/ssd/recordings
```

## Configuration (.env)

Important variables (all with default values in `.env.example`):

| Variable | Meaning | Default |
|----------|---------|---------|
| `WEB_PASSWORD` | Login password (be sure to change it!) | `changeme` |
| `WEB_SECRET` | Session signing (`openssl rand -hex 32`) | – |
| `CAMERA_SOURCE` | `usb:0` or `rtsp://...` | `usb:0` |
| `CAMERA_WIDTH/HEIGHT/FPS` | Resolution/frame rate | 1280x720@15 |
| `STREAM_MAX_FPS` | MJPEG stream rate | 15 |
| `DETECTOR_ENABLED` | YOLO on/off | `true` |
| `DETECT_FPS` | Inference rate | 5 |
| `DETECT_CLASSES` | COCO classes (0=person, 16=dog) | `0,16` |
| `MOTION_ENABLED` | Motion detection on/off | `true` |
| `RECORD_ENABLED` | Recording on/off | `true` |
| `RECORD_TRIGGER` | `motion`/`dog`/`motion_or_dog`/`always` | `motion_or_dog` |
| `RECORD_FPS` | Recording rate | 10 |
| `RECORD_SEGMENT_S` | Segment length [s] | 300 |
| `RECORD_RETENTION_DAYS` | Auto-deletion [days] | 7 |
| `RECORD_MAX_DISK_PERCENT` | SSD fill limit [%] | 80 |
| `RECORDINGS_HOST_DIR` | Host path of the recordings | `/mnt/ssd/recordings` |
| `NOTIFY_ENABLED` | ntfy.sh on/off | `true` |
| `NOTIFY_NTFY_TOPIC` | ntfy topic | (empty) |
| `NOTIFY_COOLDOWN_S` | Notification interval [s] | 300 |

Full list: [`.env.example`](.env.example).

## Docs

- [`docs/architecture.md`](docs/architecture.md) – Components, thread model, API
- [`docs/jetson-setup.md`](docs/jetson-setup.md) – Jetson setup, SSD, webcam, power
- [`docs/wireguard-setup.md`](docs/wireguard-setup.md) – WireGuard (Jetson/router/VPS), phone

## Development (x86)

Without GPU: `docker-compose.override.yml` is already present (no
`runtime: nvidia`, CPU inference). Adjust the video device in the override
file to match the local camera.

Tests:

```bash
docker compose run --rm home-monitoring python -m pytest tests/ -v
```

## Performance notes (Jetson Nano)

Simultaneous load: YOLO (GPU) + MJPEG encoding (CPU) + x264 (CPU).
If things stutter, lower the `.env` values (`STREAM_MAX_FPS`, `DETECT_FPS`,
`RECORD_FPS`, resolution). Details: [`docs/jetson-setup.md`](docs/jetson-setup.md#7-performance-tuning-jetson-nano).
