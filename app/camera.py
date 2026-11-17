"""Thread-safe camera source backed by OpenCV.

Two backends:

- ``usb`` / ``rtsp``: OpenCV ``VideoCapture`` on an index or URL.
- ``nvargus``: CSI cameras on Jetson via GStreamer ``nvarguscamerasrc``
  (JetPack 4.x does not expose CSI sensors as ``/dev/video*``).

OpenCV's ``VideoCapture`` blocks on reconnects, so the grab loop runs in a
background thread and always holds the most recent frame.
"""
import logging
import os
import threading
import time
from typing import Iterator, Optional, Union

import cv2

log = logging.getLogger(__name__)

ARGUS_SOCKET = "/tmp/argus_socket"


def nvargus_available() -> bool:
    """Return True if the Argus daemon socket exists (CSI cameras possible)."""
    return os.path.exists(ARGUS_SOCKET)


def resolve_backend(requested: str) -> str:
    """Resolve ``auto`` to ``nvargus`` or ``usb`` based on the host."""
    if requested != "auto":
        return requested
    return "nvargus" if nvargus_available() else "usb"


def nvargus_pipeline(width: int, height: int, fps: int) -> str:
    """GStreamer pipeline: nvarguscamerasrc -> BGR appsink for OpenCV."""
    return (
        "nvarguscamerasrc ! "
        f"video/x-raw(memory:NVMM),width={width},height={height},framerate={fps}/1 ! "
        "nvvidconv ! "
        f"video/x-raw,format=BGRx,width={width},height={height} ! "
        "videoconvert ! "
        "video/x-raw,format=BGR ! "
        "appsink drop=1 max-buffers=1 sync=0"
    )


class CameraError(RuntimeError):
    """Raised when the camera source cannot be opened."""


class CameraSource:
    """Thread-safe frame grabber for USB, RTSP and CSI (nvargus) cameras."""

    def __init__(
        self,
        source: str,
        width: int,
        height: int,
        fps: int,
        backend: str = "auto",
    ) -> None:
        self._backend = resolve_backend(backend)
        self._index = self._resolve_source(source)
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        log.info("Camera backend: %s", self._backend)

    @staticmethod
    def _resolve_source(source: str) -> Union[int, str]:
        s = source.strip()
        if s.lower().startswith("usb:"):
            return int(s.split(":", 1)[1])
        if s.lower().startswith("rtsp"):
            return s
        try:
            return int(s)
        except ValueError:
            return s

    @property
    def backend(self) -> str:
        return self._backend

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # -- internal ------------------------------------------------------------
    def _open(self) -> None:
        if self._backend == "nvargus":
            pipeline = nvargus_pipeline(self._width, self._height, self._fps)
            log.info("Opening nvargus camera: %s", pipeline)
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                raise CameraError("Cannot open nvargus camera (GStreamer pipeline failed)")
        else:
            log.info("Opening camera source: %r", self._index)
            # CAP_V4L2 erzwingen: Auf dem Jetson waehlt OpenCV sonst den
            # GStreamer-Backend, dessen v4l2src-Pipeline bei manchen
            # USB-Kameras fehlschlaegt ("Internal data stream error").
            cap = cv2.VideoCapture(self._index, cv2.CAP_V4L2)
            if not cap.isOpened():
                raise CameraError(f"Cannot open camera source: {self._index!r}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            cap.set(cv2.CAP_PROP_FPS, self._fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

    def _grab_loop(self) -> None:
        backoff = 1.0
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                try:
                    self._open()
                    backoff = 1.0
                except CameraError as exc:
                    log.warning("Camera open failed (%s), retry in %.1fs", exc, backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue

            ok, frame = self._cap.read()
            if not ok or frame is None:
                log.warning("Frame read failed, reopening.")
                self._cap.release()
                self._cap = None
                time.sleep(0.5)
                continue

            with self._lock:
                self._frame = frame

    # -- public API ----------------------------------------------------------
    def latest_frame(self):
        """Return the most recent frame (BGR) or ``None``."""
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def is_open(self) -> bool:
        """Return ``True`` if at least one frame has been captured."""
        with self._lock:
            return self._frame is not None

    def frames(self) -> Iterator:
        """Yield the latest frame in a loop (for streaming consumers)."""
        while self._running:
            frame = self.latest_frame()
            if frame is None:
                time.sleep(0.05)
                continue
            yield frame
