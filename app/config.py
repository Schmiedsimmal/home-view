"""Configuration for the home-monitoring service.

All values come from environment variables (root ``.env`` file via
docker compose ``env_file``). No hardcoded defaults in modules —
everything goes through this config.
"""
from dataclasses import dataclass, field
from typing import List

from .env import env_bool, env_float, env_int, env_int_list, env_str


@dataclass
class WebConfig:
    port: int = field(default_factory=lambda: env_int("HOME_MONITOR_PORT", 8080))
    password: str = field(default_factory=lambda: env_str("WEB_PASSWORD", ""))
    secret: str = field(default_factory=lambda: env_str("WEB_SECRET", "dev-secret"))


@dataclass
class CameraConfig:
    # Capture backend: "auto" (nvargus if Argus socket present, else usb),
    # "usb" (OpenCV VideoCapture index/RTSP), "nvargus" (CSI via GStreamer).
    backend: str = field(default_factory=lambda: env_str("CAMERA_BACKEND", "auto"))
    # Host device mapped into the container (see docker-compose.yml devices).
    # CAMERA_SOURCE is the OpenCV source: "usb:0", "rtsp://..." or a path.
    # Ignored when backend=nvargus.
    source: str = field(default_factory=lambda: env_str("CAMERA_SOURCE", "usb:0"))
    width: int = field(default_factory=lambda: env_int("CAMERA_WIDTH", 1280))
    height: int = field(default_factory=lambda: env_int("CAMERA_HEIGHT", 720))
    fps: int = field(default_factory=lambda: env_int("CAMERA_FPS", 15))


@dataclass
class StreamConfig:
    jpeg_quality: int = field(default_factory=lambda: env_int("STREAM_JPEG_QUALITY", 80))
    max_fps: float = field(default_factory=lambda: env_float("STREAM_MAX_FPS", 15.0))


@dataclass
class DetectorConfig:
    enabled: bool = field(default_factory=lambda: env_bool("DETECTOR_ENABLED", True))
    fps: float = field(default_factory=lambda: env_float("DETECT_FPS", 5.0))
    # COCO class IDs to detect (comma-separated). Empty = all classes.
    # 0=person, 16=dog.
    classes: List[int] = field(
        default_factory=lambda: env_int_list("DETECT_CLASSES", "0,16")
    )
    model: str = field(default_factory=lambda: env_str("YOLO_MODEL", "yolov8n.onnx"))
    conf_threshold: float = field(
        default_factory=lambda: env_float("YOLO_CONF_THRESHOLD", 0.4)
    )
    nms_threshold: float = field(
        default_factory=lambda: env_float("YOLO_NMS_THRESHOLD", 0.45)
    )
    # Device: "auto" detects CUDA, otherwise "cpu". Explicit e.g. "cuda:0".
    device: str = field(default_factory=lambda: env_str("YOLO_DEVICE", "auto"))
    img_size: int = field(default_factory=lambda: env_int("YOLO_IMG_SIZE", 640))
    overlay: bool = field(default_factory=lambda: env_bool("DETECTION_OVERLAY", True))


@dataclass
class MotionConfig:
    enabled: bool = field(default_factory=lambda: env_bool("MOTION_ENABLED", True))
    fps: float = field(default_factory=lambda: env_float("MOTION_FPS", 5.0))
    # Pixel diff threshold on the downscaled grayscale frame (0-255).
    threshold: int = field(default_factory=lambda: env_int("MOTION_THRESHOLD", 30))
    # Min moved area in pixels on the 320x180 analysis image.
    min_area: int = field(default_factory=lambda: env_int("MOTION_MIN_AREA", 2000))


@dataclass
class RecordConfig:
    enabled: bool = field(default_factory=lambda: env_bool("RECORD_ENABLED", True))
    # "motion" | "dog" | "motion_or_dog" | "always"
    trigger: str = field(default_factory=lambda: env_str("RECORD_TRIGGER", "motion_or_dog"))
    fps: int = field(default_factory=lambda: env_int("RECORD_FPS", 10))
    segment_s: int = field(default_factory=lambda: env_int("RECORD_SEGMENT_S", 300))
    cooldown_s: float = field(default_factory=lambda: env_float("RECORD_COOLDOWN_S", 15.0))
    min_s: float = field(default_factory=lambda: env_float("RECORD_MIN_S", 5.0))
    retention_days: int = field(default_factory=lambda: env_int("RECORD_RETENTION_DAYS", 7))
    max_disk_percent: int = field(default_factory=lambda: env_int("RECORD_MAX_DISK_PERCENT", 80))
    # Container-side recordings dir (bind-mounted from RECORDINGS_HOST_DIR).
    dir: str = field(default_factory=lambda: env_str("RECORD_DIR", "/recordings"))
    ffmpeg_preset: str = field(default_factory=lambda: env_str("FFMPEG_PRESET", "ultrafast"))
    ffmpeg_crf: int = field(default_factory=lambda: env_int("FFMPEG_CRF", 23))
    # Minimum free space required before starting a recording [MB].
    min_free_mb: int = field(default_factory=lambda: env_int("RECORD_MIN_FREE_MB", 500))


@dataclass
class NotifyConfig:
    enabled: bool = field(default_factory=lambda: env_bool("NOTIFY_ENABLED", True))
    server: str = field(default_factory=lambda: env_str("NOTIFY_NTFY_SERVER", "https://ntfy.sh"))
    topic: str = field(default_factory=lambda: env_str("NOTIFY_NTFY_TOPIC", ""))
    cooldown_s: float = field(default_factory=lambda: env_float("NOTIFY_COOLDOWN_S", 300.0))
    attach_image: bool = field(default_factory=lambda: env_bool("NOTIFY_ATTACH_IMAGE", True))


@dataclass
class Config:
    web: WebConfig = field(default_factory=WebConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def load_config() -> Config:
    return Config()
