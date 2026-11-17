# Jetson Nano setup

## Prerequisites

- **Jetson Nano** with JetPack 4.x (Ubuntu 18.04) or 5.x (Ubuntu 20.04)
- **USB webcam** (any, e.g. 720p/1080p UVC compliant)
- **SSD** for recordings (USB or SATA, at least 32 GB recommended)
- **WLAN** connection on the home network

## 1. Docker + NVIDIA Container Runtime

Run once on the Jetson:

```bash
cd ~/git/home-monitoring   # or wherever the repo lives
bash scripts/setup-jetson.sh
```

The script installs/checks:
- Docker + Docker Compose v2
- NVIDIA Container Runtime (`nvidia-docker2`) — for GPU access from the container
- systemd autostart (`home-monitoring.service`)
- SSD mountpoint `/mnt/ssd/recordings`
- Video devices `/dev/video*`

If the NVIDIA runtime is missing, install manually:

```bash
sudo apt-get update
sudo apt-get install -y nvidia-docker2
sudo systemctl restart docker
```

## 2. Set up the SSD for recordings

The SSD is mounted under `/mnt/ssd`; recordings end up in
`/mnt/ssd/recordings` (in `.env`: `RECORDINGS_HOST_DIR`).

### Partition + format a new SSD (one-time)

```bash
# Find the device (e.g. /dev/sda1 or /dev/nvme0n1p1)
lsblk -f

# Optional: create a partition (if the SSD is empty)
# sudo fdisk /dev/sda   # new primary partition, type Linux

# Format (data will be lost!)
sudo mkfs.ext4 -L recordings /dev/sda1

# Create the mountpoint
sudo mkdir -p /mnt/ssd

# Mount
sudo mount /dev/sda1 /mnt/ssd
```

### Persistent mount (fstab)

```bash
# Get the UUID
sudo blkid /dev/sda1

# fstab entry (adjust the UUID!)
echo 'UUID=<uuid-from-blkid> /mnt/ssd ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab

# Create the recordings directory
sudo mkdir -p /mnt/ssd/recordings
sudo chown $USER:$USER /mnt/ssd/recordings

# Test
sudo mount -a && df -h /mnt/ssd
```

> **Important:** `nofail` ensures the Jetson still boots without the SSD
> (recording is then disabled, the live view keeps working).

## 3. Check the camera

### 3a. USB webcam

```bash
# List devices
v4l2-ctl --list-devices    # package: sudo apt-get install v4l-utils
```

- The camera usually appears as `/dev/video0`.
- In `.env`: `CAMERA_BACKEND=usb`, `VIDEO_DEVICE=/dev/video0` (if different).

### 3b. CSI camera (flat ribbon cable, e.g. IMX219)

CSI cameras do **not** appear as `/dev/video*` – they run through the
Argus daemon (`nvarguscamerasrc`). The system detects the backend
automatically (`CAMERA_BACKEND=auto`), or set `CAMERA_BACKEND=nvargus`
explicitly.

Check whether Argus sees the camera:

```bash
gst-launch-1.0 nvarguscamerasrc num-buffers=30 ! \
  'video/x-raw(memory:NVMM),width=1280,height=720,framerate=15/1' ! \
  nvvidconv ! 'video/x-raw,format=BGRx' ! videoconvert ! \
  'video/x-raw,format=BGR' ! fakesink
```

If the stream runs without errors, the camera is OK.

**Error "No cameras available" / i2c errors (-121):**
This is a **hardware/cable problem**, not a software problem:

1. Power off the Jetson, check the flat ribbon cable at **both** ends:
   - Gold contacts must be clean and fully seated in the connector,
     locking tab closed.
   - On the camera side the contacts point toward the camera module,
     on the Jetson side in the correct direction (see the Jetson docs
     for the CSI connector).
2. Try a different CSI port (CAM0/CAM1) or a different cable.
3. After re-plugging: restart the Jetson
   (`sudo systemctl restart nvargus-daemon` is often enough).
4. Check the kernel log: `sudo dmesg | grep imx` –
   `probe of 6-0010 failed with error -121` = disturbed I2C communication.

> Note: A darkened camera lens (black cap) delivers a black image – that
> is normal and not an error.

## 4. Configuration (.env)

```bash
cp .env.example .env
# Edit .env:
#   WEB_PASSWORD  -> set a strong password!
#   WEB_SECRET    -> openssl rand -hex 32
#   NOTIFY_NTFY_TOPIC -> unique ntfy topic
#   RECORDINGS_HOST_DIR -> /mnt/ssd/recordings (or your own path)
```

## 5. Start

```bash
# On the Jetson: disable the dev override (it is only for x86)
mv docker-compose.override.yml docker-compose.override.yml.disabled

# Build + start (IMPORTANT: DOCKER_BUILDKIT=0, see the note below)
DOCKER_BUILDKIT=0 docker-compose up --build -d
```

> **Why `DOCKER_BUILDKIT=0`?** The Jetson has an **IPv6-only internet**
> connection (no IPv4 default route). Docker/BuildKit gives build
> containers `8.8.8.8` as DNS, which cannot resolve without IPv4. The
> legacy builder allows overriding `/etc/resolv.conf` in the Dockerfile
> to `127.0.0.53` (the host's systemd-resolved stub, reachable in the
> host network) — so `pip install` can reach pypi.org. At runtime the
> container uses `dns: 127.0.0.53` (see `docker-compose.yml`).

> **ONNX model:** `./models/yolov8n.onnx` must exist on the host
> (it is mounted to `/app/models`). Export e.g. with
> `yolo export model=yolov8n.pt format=onnx imgsz=640`.

Web UI: `http://<jetson-ip>:8080` (login with `WEB_PASSWORD`).

Autostart is active automatically after `setup-jetson.sh`. Control manually:

```bash
sudo systemctl start home-monitoring    # start container
sudo systemctl stop home-monitoring     # stop container
sudo systemctl status home-monitoring   # status
journalctl -u home-monitoring -f        # logs
```

## 6. Power mode (optional, for 24/7 operation)

The Jetson Nano has a 5W and a 10W mode. For stable 24/7 performance
(YOLO + MJPEG + ffmpeg) enable the 10W mode:

```bash
sudo nvpmodel -m 0     # MAXN (10W, full power)
sudo nvpmodel -q       # query current mode
```

> Note: In 10W mode the Jetson runs warmer. Make sure it has good
> ventilation (a small heatsink is usually enough).

## 7. Performance tuning (Jetson Nano)

Simultaneous load: YOLO inference (GPU) + MJPEG encoding (CPU) +
x264 recording (CPU). If it stutters:

| Symptom | Measure |
|---------|---------|
| Stream stutters | `STREAM_MAX_FPS=10`, `CAMERA_FPS=10`, `CAMERA_WIDTH/HEIGHT=960x540` |
| Detection slow | `DETECT_FPS=3`, `YOLO_IMG_SIZE=416`, `YOLO_MODEL=yolov8n.onnx` (already default) |
| Recording stutters | `RECORD_FPS=8`, `FFMPEG_PRESET=ultrafast` (already default), `FFMPEG_CRF=26` |
| Everything together too much | `DETECTOR_ENABLED=false` (motion only) or `MOTION_ENABLED=false` |

The `.env` values are picked up on the next `docker compose up -d`
(env change → restart container).

## 8. Deployment status & known limitations (as of 2026-11-16)

On the Jetson (`simon-jetson`, L4T R32.7.6, JetPack 4.6) the stack is
deployed and running:

- Container `home-monitoring` is **healthy**, web UI reachable at
  `http://192.168.178.10:8080` (login with `WEB_PASSWORD` from `.env`).
- Detector loaded: YOLOv8 ONNX via `cv2.dnn` **CUDA** (classes 0,16).
- systemd autostart `home-monitoring.service` is installed + enabled.
- The Docker service is enabled (container starts with `restart: unless-stopped`).

**Open items (hardware, only solvable on-site):**

1. **CSI camera (IMX219):** The kernel reports
   `imx219_board_setup: error during i2c read probe (-121)` and
   `tegra-i2c 7000c700: no acknowledge from address 0x50`.
   `i2cdetect -y -r 6` shows **no** device → the flat ribbon cable is
   not plugged in correctly/aligned or the camera module is defective.
   → Check the ribbon cable at both ends (gold contacts clean,
   locking tab closed, contacts pointing in the right direction),
   try a different CSI port (CAM0/CAM1) if needed, then restart the Jetson.
   Once Argus sees the camera, the stream delivers frames (black cap =
   black image, that is normal).
2. **SSD `/mnt/ssd`:** Currently **no** external SSD is plugged in
   (`lsblk` shows only the internal eMMC). `/mnt/ssd/recordings` therefore
   sits on the root FS (59 GB, ~91% full!). Recordings must be moved to a
   real SSD before productive use (section 2), otherwise recording fills
   up the internal SSD.
3. **Root FS 90% full:** Old Docker images/containers take up
   ~13 GB. Clean up with `docker image prune -a` / `docker container
   prune` (after checking a backup of your own containers!) or remove
   old images.
4. **WireGuard:** Not set up yet (see
   `docs/wireguard-setup.md`). Router port forward for UDP 51820 needed.
5. **Password:** `WEB_PASSWORD` in `.env` is still the default value
   `home-monitoring` → change before productive use.
