"""FastAPI web UI: MJPEG live stream, recording controls, recordings list."""
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import cv2
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .camera import CameraSource
from .config import Config
from .detector import CLASS_COLORS, DEFAULT_COLOR
from .recorder import RecorderController

log = logging.getLogger(__name__)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

MAX_RECORDINGS_LISTED = 200


def build_app(
    camera: CameraSource,
    detector,
    recorder: RecorderController,
    cfg: Config,
    state: dict,
) -> FastAPI:
    """Build the FastAPI app.

    *state* is the shared dict populated by the background loops in main.py:
    ``stream_active``, ``fps``, ``motion_active``, ``dog_active``,
    ``detections``, ``dog_detected_ts``.
    """
    from .auth import setup_auth

    app = FastAPI(title="home-monitoring")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    setup_auth(app, cfg.web)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {
            "status": "ok",
            "camera_open": camera.is_open(),
        }

    # -- stream control ---------------------------------------------------------
    @app.post("/api/stream/start")
    async def stream_start() -> dict:
        state["stream_active"] = True
        log.info("Stream activated via UI.")
        return {"stream_active": True}

    @app.post("/api/stream/stop")
    async def stream_stop() -> dict:
        state["stream_active"] = False
        log.info("Stream deactivated via UI.")
        return {"stream_active": False}

    # -- status -------------------------------------------------------------------
    @app.get("/api/status")
    async def status() -> dict:
        now = time.time()
        dog_ts = state.get("dog_detected_ts", 0.0)
        disk = _disk_info(cfg.record.dir)
        return {
            "camera_open": camera.is_open(),
            "stream_active": bool(state.get("stream_active")),
            "fps": round(state.get("fps", 0.0), 1),
            "motion_active": bool(state.get("motion_active")),
            "motion_area": int(state.get("motion_area", 0)),
            "dog_active": bool(state.get("dog_active")),
            "dog_detected_ago_s": round(now - dog_ts, 1) if dog_ts else None,
            "detections": [d["label"] for d in state.get("detections", [])],
            "detector": {
                "enabled": cfg.detector.enabled,
                "loaded": detector.loaded if detector else False,
                "device": detector.device if detector else "",
                "using_engine": detector.using_engine if detector else False,
            },
            "recording": recorder.status(),
            "notify_enabled": _notify_enabled(cfg),
            "disk": disk,
        }

    # -- recordings ---------------------------------------------------------------
    @app.get("/api/recordings")
    async def recordings() -> List[Dict]:
        return _list_recordings(cfg.record.dir)

    @app.delete("/api/recordings/{name}")
    async def delete_recording(name: str) -> dict:
        if "/" in name or "\\" in name or not name.endswith(".mp4") or name.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid filename")
        path = os.path.join(cfg.record.dir, name)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Recording not found")
        os.remove(path)
        return {"deleted": name}

    @app.delete("/api/recordings")
    async def delete_all_recordings() -> dict:
        deleted = 0
        if os.path.isdir(cfg.record.dir):
            for name in os.listdir(cfg.record.dir):
                if name.endswith(".mp4") and not name.startswith("."):
                    try:
                        os.remove(os.path.join(cfg.record.dir, name))
                        deleted += 1
                    except OSError:
                        pass
        return {"deleted": deleted}

    @app.get("/recordings/{name}")
    async def recording_file(name: str, download: bool = Query(False)):
        # Path traversal protection: only plain *.mp4 filenames.
        if "/" in name or "\\" in name or not name.endswith(".mp4") or name.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid filename")
        path = os.path.join(cfg.record.dir, name)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Recording not found")
        headers = {"Content-Length": str(os.path.getsize(path))}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        # StreamingResponse instead of FileResponse: the old Starlette
        # version in requirements.txt needs the aiofiles package for
        # FileResponse, which is not installed. The MP4 has +faststart,
        # so the browser plays it progressively without range requests.
        return StreamingResponse(
            _iter_file(path),
            media_type="video/mp4",
            headers=headers,
        )

    # -- MJPEG stream ---------------------------------------------------------------
    def _annotate(frame) -> None:
        # Detection overlay (boxes from the latest detector run).
        if cfg.detector.overlay and cfg.detector.enabled:
            for det in state.get("detections", []):
                x1, y1, x2, y2 = det["box"]
                cls_id = det["class_id"]
                color = CLASS_COLORS.get(cls_id, DEFAULT_COLOR)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                label = f"{det['label']} {det['conf']:.2f}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(frame, (x1, max(0, y1 - th - 4)), (x1 + tw + 4, y1), color, -1)
                cv2.putText(
                    frame, label, (x1 + 2, max(th, y1 - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
                )
        # REC indicator while recording.
        if recorder.active:
            cv2.circle(frame, (frame.shape[1] - 40, 40), 10, (0, 0, 255), -1)
            cv2.putText(
                frame, "REC", (frame.shape[1] - 90, 45),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2,
            )

    def _generate_mjpeg():
        min_interval = 1.0 / max(cfg.stream.max_fps, 0.1)
        last_send = 0.0
        sent = 0
        window_start = time.time()
        for frame in camera.frames():
            now = time.time()
            if now - last_send < min_interval:
                time.sleep(0.005)
                continue
            last_send = now
            sent += 1
            if now - window_start >= 1.0:
                state["fps"] = sent / (now - window_start)
                sent = 0
                window_start = now
            _annotate(frame)
            ok, buf = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.stream.jpeg_quality]
            )
            if not ok:
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            )

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        if not state.get("stream_active"):
            return JSONResponse(
                {"detail": "Stream is stopped. Please start it first."},
                status_code=409,
            )
        return StreamingResponse(
            _generate_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    return app


# -- helpers ---------------------------------------------------------------------

def _iter_file(path: str, chunk_size: int = 256 * 1024):
    """Yield a file in chunks (sync generator; Starlette runs it in a thread)."""
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            yield data


def _disk_info(path: str) -> dict:
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "used_percent": round(usage.used / usage.total * 100.0, 1),
        }
    except OSError:
        return {"path": path, "free_bytes": 0, "total_bytes": 0, "used_percent": None, "error": "unreachable"}


def _notify_enabled(cfg: Config) -> bool:
    return cfg.notify.enabled and bool(cfg.notify.topic)


def _list_recordings(record_dir: str) -> List[Dict]:
    if not os.path.isdir(record_dir):
        return []
    out = []
    for name in os.listdir(record_dir):
        if not name.endswith(".mp4"):
            continue
        path = os.path.join(record_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        # Filename format: %Y%m%d_%H%M%S.mp4 -> parse start time.
        start = None
        try:
            start = datetime.strptime(name[:15], "%Y%m%d_%H%M%S").isoformat()
        except ValueError:
            pass
        out.append({
            "name": name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "start": start,
        })
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out[:MAX_RECORDINGS_LISTED]
