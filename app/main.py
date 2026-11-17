"""home-monitoring service entry point.

Orchestrates camera, detector, motion detection, recorder, notifier,
retention cleanup and the web server. All heavy work runs in daemon
threads; uvicorn serves the web UI on the main thread.
"""
import logging
import os
import threading
import time
from typing import Optional

import uvicorn

from .camera import CameraSource
from .config import Config, load_config
from .detector import Detector
from .motion import MotionDetector
from .notifier import Notifier
from .recorder import RecorderController
from .retention import RetentionLoop
from .web import build_app

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("home-monitoring")


def _iou(a, b) -> float:
    """Intersection-over-union for boxes ``(x1, y1, x2, y2)``."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / max(1e-6, area_a + area_b - inter)


DOG_PERSON_IOU = 0.3


def _detector_loop(detector: Detector, camera: CameraSource, state: dict,
                   notifier: Notifier, cfg: Config) -> None:
    """Run YOLO inference at DETECT_FPS and update shared state."""
    interval = 1.0 / max(cfg.detector.fps, 0.1)
    log.info("Detector loop started (%.1f fps, classes=%s)",
             cfg.detector.fps, cfg.detector.classes)
    while True:
        frame = camera.latest_frame()
        if frame is None:
            time.sleep(0.1)
            continue
        try:
            _frame, dets = detector.detect(frame, draw=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Detection failed: %s", exc)
            time.sleep(interval)
            continue
        # Suppress "dog" hits that overlap a person box: yolov8n @320px with
        # classes filtered to person/dog frequently labels a person as dog.
        person_boxes = [d["box"] for d in dets if d["label"] == "person"]
        if person_boxes:
            kept = []
            for d in dets:
                if d["label"] == "dog" and any(
                        _iou(d["box"], pb) > DOG_PERSON_IOU for pb in person_boxes):
                    log.info("dog candidate suppressed (overlaps person): "
                             "conf=%.2f box=%s", d["conf"], d["box"])
                    continue
                kept.append(d)
            dets = kept
        state["detections"] = dets
        labels = {d["label"] for d in dets}
        if "dog" in labels:
            dogs = [(round(d["conf"], 2), d["box"]) for d in dets if d["label"] == "dog"]
            log.info("dog candidate: %s", dogs)
        dog_now = "dog" in labels
        # Temporal filter: single-frame "dog" hits are almost always false
        # positives (yolov8n @320px with classes filtered to person/dog).
        # Require 3 consecutive detector runs before flagging dog_active.
        streak = state.get("dog_streak", 0)
        state["dog_streak"] = streak + 1 if dog_now else 0
        was_active = bool(state.get("dog_active"))
        state["dog_active"] = state["dog_streak"] >= 3
        state["person_active"] = "person" in labels
        if state["dog_active"] and not was_active:
            state["dog_active_since"] = time.time()
        elif not state["dog_active"]:
            state["dog_active_since"] = None
        if state["dog_active"]:
            state["dog_detected_ts"] = time.time()
            dogs = sum(1 for d in dets if d["label"] == "dog")
            notifier.notify_dog(frame, extra=f"({dogs}x detected) ")
        time.sleep(interval)


def _motion_loop(motion: MotionDetector, camera: CameraSource, state: dict,
                 cfg: Config) -> None:
    """Run frame-differencing motion detection at MOTION_FPS."""
    interval = 1.0 / max(cfg.motion.fps, 0.1)
    log.info("Motion loop started (%.1f fps)", cfg.motion.fps)
    while True:
        frame = camera.latest_frame()
        if frame is None:
            time.sleep(0.1)
            continue
        try:
            motion.update(frame)
        except Exception as exc:  # noqa: BLE001
            log.warning("Motion detection failed: %s", exc)
            time.sleep(interval)
            continue
        state["motion_active"] = motion.active
        state["motion_area"] = motion.moved_area
        time.sleep(interval)


def main() -> None:
    cfg = load_config()

    camera = CameraSource(
        cfg.camera.source, cfg.camera.width, cfg.camera.height, cfg.camera.fps,
        backend=cfg.camera.backend,
    )
    camera.start()
    log.info("Camera source started: %s (backend=%s)", cfg.camera.source, camera.backend)

    detector: Optional[Detector] = None
    if cfg.detector.enabled:
        detector = Detector(cfg.detector)
        try:
            detector.load()
        except Exception as exc:  # noqa: BLE001
            log.error("Model load failed: %s. Detection disabled.", exc)
            detector = None
    else:
        log.info("Detector disabled (DETECTOR_ENABLED=false).")

    motion = MotionDetector(cfg.motion)
    notifier = Notifier(cfg.notify)
    log.info(
        "Notifier ready: enabled=%s topic=%s",
        notifier.enabled, cfg.notify.topic or "(empty)",
    )

    state: dict = {
        "stream_active": False,
        "fps": 0.0,
        "motion_active": False,
        "motion_area": 0,
        "dog_active": False,
        "dog_streak": 0,
        "dog_active_since": None,
        "person_active": False,
        "dog_detected_ts": 0.0,
        "detections": [],
    }

    recorder = RecorderController(cfg.record, camera, state)
    recorder.start()

    retention = RetentionLoop(
        cfg.record.dir, cfg.record.retention_days, cfg.record.max_disk_percent,
    )
    retention.start()

    if detector is not None:
        threading.Thread(
            target=_detector_loop,
            args=(detector, camera, state, notifier, cfg),
            daemon=True,
        ).start()

    if cfg.motion.enabled:
        threading.Thread(
            target=_motion_loop,
            args=(motion, camera, state, cfg),
            daemon=True,
        ).start()

    app = build_app(camera, detector, recorder, cfg, state)
    log.info("Web UI starting on port %d", cfg.web.port)
    uvicorn.run(app, host="0.0.0.0", port=cfg.web.port, log_level="info")


if __name__ == "__main__":
    main()
