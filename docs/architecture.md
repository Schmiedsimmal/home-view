# Architektur

## Übersicht

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
                         │                    Browser (LAN oder WireGuard)  │
                         │                    Login → Live-View, Aufnahmen  │
                         └──────────────────────────────────────────────────┘
```

## Komponenten

### CameraSource (`app/camera.py`)

Thread-sicherer Frame-Grabber auf OpenCV `VideoCapture`. Ein Background-Thread
öffnet die Quelle (`usb:0`, `rtsp://...` oder Pfad) und hält immer den
neuesten Frame. Bei Verbindungsabbruch wird mit Backoff neu verbunden.
**Die Kamera wird genau einmal geöffnet** – alle Consumer (Stream, Detektor,
Bewegung, Aufzeichnung) lesen dieselben Frames.

### DetectorLoop (`app/main.py` + `app/detector.py`)

- YOLOv8 (Standard `yolov8n.onnx`, COCO-vortrainiert, ONNX-Export) via
  OpenCV `cv2.dnn` mit CUDA-Backend. Auf dem Jetson Nano bei `DETECT_FPS=5`
  und `YOLO_IMG_SIZE=640` ein guter Kompromiss aus Geschwindigkeit und
  Genauigkeit. (TensorRT/ultralytics entfällt, weil JetPack 4.x nur
  Python 3.6 liefert.)
- Konfigurierbare Klassen (`DETECT_CLASSES`, Default `0,16` = person, dog).
- Erkennt `dog` → setzt `dog_active` + `dog_detected_ts` im Shared-State und
  löst die ntfy-Benachruptigung aus (mit Cooldown).
- Erkennt `person` → nur Statusanzeige (kein Recording-Trigger nötig,
  konfigurierbar über `DETECT_CLASSES`).

### MotionLoop (`app/main.py` + `app/motion.py`)

Frame-Differenz ohne ML: Frame auf 320x180 skalieren → Graubild →
`absdiff` zum Vorgänger → Schwelle + Kontur-Fläche. Läuft mit
`MOTION_FPS=5` und vernachlässigbarer CPU-Last. Setzt `motion_active` +
`motion_area` im Shared-State.

### RecorderController (`app/recorder.py`)

State-Machine `idle → recording` in einem eigenen Thread (50-ms-Takt):

1. **Trigger** (`RECORD_TRIGGER`): `motion` | `dog` | `motion_or_dog` | `always`.
   Bei aktivem Trigger wird `last_trigger_ts` aktualisiert.
2. **Start** (nur wenn getriggert): Prüft freien Speicher (`RECORD_MIN_FREE_MB`)
   und startet ffmpeg mit Rawvideo-Pipe (`-f rawvideo -pix_fmt bgr24`).
3. **Feed**: Frames aus `camera.latest_frame()` werden mit `RECORD_FPS`
   als Rohbytes in den ffmpeg-stdin geschrieben.
4. **Stop**: erst wenn `RECORD_COOLDOWN_S` lang kein Trigger **und**
   mindestens `RECORD_MIN_S` aufgezeichnet wurde. ffmpeg wird sauber
   beendet (stdin close → letzteres Segment finalisiert).
5. **Rotation**: ffmpeg `-f segment -segment_time RECORD_SEGMENT_S
   -strftime 1` erzeugt `YYYYMMDD_HHMMSS.mp4`-Segmente.
   `-movflags +faststart` ⇒ progressive Browser-Playback.

### RetentionLoop (`app/retention.py`)

Stündlich: löscht Segmente älter als `RECORD_RETENTION_DAYS` und – als
Sicherheitsnetz – die ältesten Segmente, wenn die SSD zu
`RECORD_MAX_DISK_PERCENT` voll ist.

### Notifier (`app/notifier.py`)

ntfy.sh-Client. Bei Hund-Erkennung: POST (Text) oder PUT (Bild-Anhang,
einzelne Notification mit JPEG-Snapshot). Cooldown `NOTIFY_COOLDOWN_S`
gegen Spam. Alle Fehler werden geloggt, nie geworfen.

### Web (`app/web.py` + `app/auth.py` + `templates/`)

FastAPI-Server auf Port `HOME_MONITOR_PORT` (Default 8080):

| Route | Auth | Beschreibung |
|-------|------|--------------|
| `GET /` | ja | Web-UI (`index.html`) |
| `GET /login`, `POST /login`, `GET /logout` | nein | Login/Logout |
| `GET /stream` | ja | MJPEG-Livestream (409 wenn gestoppt) |
| `POST /api/stream/start|stop` | ja | Stream an/aus |
| `GET /api/status` | ja | JSON-Status (Kamera, Stream, REC, Motion, Hund, SSD) |
| `GET /api/recordings` | ja | Liste der MP4-Segmente |
| `GET /recordings/{name}` | ja | MP4 abspielen/downloaden |
| `GET /healthz` | nein | Health-Check (Docker) |

Auth: `SessionMiddleware` (signiertes Cookie) + `AuthMiddleware`
(alle Routen außer `/login`, `/healthz`). Passwort-Vergleich via
`hmac.compare_digest`. Bei leerem `WEB_PASSWORD` ist Auth deaktiviert
(Nur für lokale Tests).

## Thread-Modell

```
Hauptthread:   uvicorn (FastAPI)
Thread 1:      CameraSource._grab_loop   (Kamera-Frames)
Thread 2:      _detector_loop            (YOLO, DETECT_FPS)      [optional]
Thread 3:      _motion_loop              (Frame-Diff, MOTION_FPS) [optional]
Thread 4:      RecorderController._loop  (Trigger + ffmpeg-Feed)
Thread 5:      RetentionLoop._run        (stündlich Cleanup)
Thread 6+:     ffmpeg-Subprozess         (pro Aufnahme)
```

## Datenfluss

```
Kamera-Frame
   ├─▶ DetectorLoop ──▶ state.detections, state.dog_active ──▶ Notifier (ntfy)
   ├─▶ MotionLoop  ──▶ state.motion_active
   ├─▶ MJPEG /stream (wenn stream_active) ──▶ Browser
   └─▶ RecorderController ──▶ ffmpeg ──▶ /recordings/*.mp4 (SSD)
state ──▶ /api/status ──▶ Browser-UI (Polling 1s)
```

## Persistenz

- `/recordings` (Bind-Mount `${RECORDINGS_HOST_DIR}` → `/mnt/ssd/recordings`):
  MP4-Segmente, Retention-bereinigt.
- `./models` (Bind-Mount → `/app/models`): `yolov8n.onnx` bleibt über
  Container-Neustarts erhalten.

## GPU-Zugriff

Auf dem Jetson muss die **NVIDIA Container Runtime** installiert sein
(`scripts/setup-jetson.sh`). Im `docker-compose.yml` ist `runtime: nvidia`
gesetzt. Auf x86-Dev-Maschinen ohne NVIDIA-Runtime die
`docker-compose.override.yml` verwenden — der Detektor läuft dann auf der
CPU (langsam, aber funktional).
