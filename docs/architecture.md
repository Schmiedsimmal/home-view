# Architecture

## Overview

```
                ┌──────────────────────────────────────────────────────────┐
                │                  Jetson Nano (Host)                       │
                │                                                          │
  USB Webcam    │   ┌────────────┐   Frames    ┌─────────────────────────┐ │
  ───/dev/video0┼──▶│ Camera-    │────────────▶│                         │ │
                │   │ Source     │             │   home-monitoring       │ │
                │   │ (OpenCV)   │             │   (Docker Container)    │ │
                │   └────────────┘             │                         │ │
                │        │                     │  ├─ DetectorLoop (YOLO) │ │
                │        │                     │  ├─ MotionLoop (Diff)   │ │
                │        │                     │  ├─ RecorderController  │ │
                │        │                     │  │   └─ ffmpeg ──▶ /recordings
                │        │                     │  │        (SSD /mnt/ssd) │ │
                │        │                     │  ├─ RetentionLoop       │ │
                │        │                     │  ├─ Notifier (ntfy.sh)  │ │
                │        │                     │  └─ FastAPI :8080       │ │
                │        │                     │      (UI, MJPEG, API)   │ │
                └────────┼─────────────────────┴───────────┬─────────────┘ │
                         │                                 │ HTTP :8080    │
                         │                                 ▼               │
                         │                    Browser (LAN or WireGuard)    │
                         │                    Login → Live view, recordings │
                         └──────────────────────────────────────────────────┘
```

## Components

### CameraSource (`app/camera.py`)

Thread-safe frame grabber on OpenCV `VideoCapture`. A background thread
opens the source (`usb:0`, `rtsp://...` or a path) and always holds the
most recent frame. On connection loss it reconnects with backoff.
**The camera is opened exactly once** – all consumers (stream, detector,
motion, recording) read the same frames.

### DetectorLoop (`app/main.py` + `app/detector.py`)

- YOLOv8 (default `yolov8n.onnx`, COCO pretrained, ONNX export) via
  OpenCV `cv2.dnn` with CUDA backend. On the Jetson Nano at `DETECT_FPS=5`
  and `YOLO_IMG_SIZE=640` a good compromise between speed and accuracy.
  (TensorRT/ultralytics is not used because JetPack 4.x only ships
  Python 3.6.)
- Configurable classes (`DETECT_CLASSES`, default `0,16` = person, dog).
- Detects `dog` → sets `dog_active` + `dog_detected_ts` in the shared state and
  triggers the ntfy notification (with cooldown).
- Detects `person` → status display only (no recording trigger needed,
  configurable via `DETECT_CLASSES`).

### MotionLoop (`app/main.py` + `app/motion.py`)

Frame differencing without ML: scale frame to 320x180 → grayscale →
`absdiff` to the previous frame → threshold + contour area. Runs at
`MOTION_FPS=5` with negligible CPU load. Sets `motion_active` +
`motion_area` in the shared state.

### RecorderController (`app/recorder.py`)

State machine `idle → recording` in its own thread (50-ms tick):

1. **Trigger** (`RECORD_TRIGGER`): `motion` | `dog` | `motion_or_dog` | `always`.
   While a trigger is active, `last_trigger_ts` is updated.
2. **Start** (only when triggered): checks free space (`RECORD_MIN_FREE_MB`)
   and starts ffmpeg with a raw video pipe (`-f rawvideo -pix_fmt bgr24`).
3. **Feed**: frames from `camera.latest_frame()` are written to ffmpeg's
   stdin as raw bytes at `RECORD_FPS`.
4. **Stop**: only when no trigger has fired for `RECORD_COOLDOWN_S` **and**
   at least `RECORD_MIN_S` has been recorded. ffmpeg is shut down cleanly
   (stdin close → last segment finalized).
5. **Rotation**: ffmpeg `-f segment -segment_time RECORD_SEGMENT_S
   -strftime 1` produces `YYYYMMDD_HHMMSS.mp4` segments.
   `-movflags +faststart` ⇒ progressive browser playback.

### RetentionLoop (`app/retention.py`)

Hourly: deletes segments older than `RECORD_RETENTION_DAYS` and – as a
safety net – the oldest segments when the SSD is more than
`RECORD_MAX_DISK_PERCENT` full.

### Notifier (`app/notifier.py`)

ntfy.sh client. On dog detection: POST (text) or PUT (image attachment,
single notification with JPEG snapshot). Cooldown `NOTIFY_COOLDOWN_S`
against spam. All errors are logged, never raised.

### Web (`app/web.py` + `app/auth.py` + `templates/`)

FastAPI server on port `HOME_MONITOR_PORT` (default 8080):

| Route | Auth | Description |
|-------|------|-------------|
| `GET /` | yes | Web UI (`index.html`) |
| `GET /login`, `POST /login`, `GET /logout` | no | Login/logout |
| `GET /stream` | yes | MJPEG live stream (409 when stopped) |
| `POST /api/stream/start|stop` | yes | Stream on/off |
| `GET /api/status` | yes | JSON status (camera, stream, REC, motion, dog, SSD) |
| `GET /api/recordings` | yes | List of MP4 segments |
| `GET /recordings/{name}` | yes | Play/download MP4 |
| `GET /healthz` | no | Health check (Docker) |

Auth: signed client-side session cookie (HMAC-SHA256, stdlib only) +
`AuthMiddleware` (all routes except `/login`, `/healthz`). Password
comparison via `hmac.compare_digest`. With an empty `WEB_PASSWORD`, auth
is disabled (only for local tests).

## Thread model

```
Main thread:    uvicorn (FastAPI)
Thread 1:       CameraSource._grab_loop   (camera frames)
Thread 2:       _detector_loop            (YOLO, DETECT_FPS)      [optional]
Thread 3:       _motion_loop              (frame diff, MOTION_FPS) [optional]
Thread 4:       RecorderController._loop  (trigger + ffmpeg feed)
Thread 5:       RetentionLoop._run        (hourly cleanup)
Thread 6+:      ffmpeg subprocess         (per recording)
```

## Data flow

```
Camera frame
   ├─▶ DetectorLoop ──▶ state.detections, state.dog_active ──▶ Notifier (ntfy)
   ├─▶ MotionLoop  ──▶ state.motion_active
   ├─▶ MJPEG /stream (when stream_active) ──▶ Browser
   └─▶ RecorderController ──▶ ffmpeg ──▶ /recordings/*.mp4 (SSD)
state ──▶ /api/status ──▶ Browser UI (polling 1s)
```

## Persistence

- `/recordings` (bind mount `${RECORDINGS_HOST_DIR}` → `/mnt/ssd/recordings`):
  MP4 segments, cleaned up by retention.
- `./models` (bind mount → `/app/models`): `yolov8n.onnx` survives
  container restarts.

## GPU access

On the Jetson the **NVIDIA Container Runtime** must be installed
(`scripts/setup-jetson.sh`). In `docker-compose.yml`, `runtime: nvidia`
is set. On x86 dev machines without the NVIDIA runtime use
`docker-compose.override.yml` — the detector then runs on the CPU
(slow, but functional).
