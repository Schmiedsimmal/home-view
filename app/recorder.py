"""Event-based recording via an ffmpeg subprocess.

A background thread evaluates the configured trigger (motion / dog /
motion_or_dog / always) against the shared app state. While a trigger is
active, raw BGR frames from the camera are piped into ffmpeg, which encodes
them to H.264 MP4 segments on the recordings volume.

Segments are rotated by ffmpeg itself (``-segment_time``) and named by
their start timestamp (``-strftime 1``), e.g. ``20261116_143005.mp4``.
``-movflags +faststart`` moves the moov atom to the front of each segment
so browsers can play them progressively without HTTP range requests.
"""
import logging
import os
import select
import shutil
import subprocess
import threading
import time
from typing import List, Optional, Tuple

from .camera import CameraSource
from .config import RecordConfig

log = logging.getLogger(__name__)


def _ffmpeg_bin() -> str:
    """Resolve the ffmpeg executable.

    The Jetson base image ships no ffmpeg, so we fall back to the static
    aarch64 binary bundled with the ``imageio-ffmpeg`` pip package.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


class RecorderController:
    """Starts/stops ffmpeg event recordings based on trigger state."""

    def __init__(self, cfg: RecordConfig, camera: CameraSource, state: dict) -> None:
        self.cfg = cfg
        self.camera = camera
        self.state = state  # shared dict, see main.py
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._proc: Optional[subprocess.Popen] = None
        self._stderr_file = None
        self._started_ts: float = 0.0
        self._last_trigger_ts: float = 0.0
        self._last_feed_ts: float = 0.0
        self._frame_size: Optional[Tuple[int, int]] = None
        self._pending = None          # bytes of the frame currently being written
        self._pending_off: int = 0    # write offset into _pending
        self.error: Optional[str] = None
        self._warned_disk: bool = False
        self._next_attempt_ts: float = 0.0

    # -- public state ----------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def elapsed_s(self) -> float:
        if not self.active:
            return 0.0
        return time.time() - self._started_ts

    def status(self) -> dict:
        return {
            "enabled": self.cfg.enabled,
            "trigger": self.cfg.trigger,
            "active": self.active,
            "elapsed_s": round(self.elapsed_s, 1),
            "error": self.error,
        }

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        os.makedirs(self.cfg.dir, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(
            "Recorder controller started (trigger=%s, dir=%s, fps=%d)",
            self.cfg.trigger, self.cfg.dir, self.cfg.fps,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._stop_recording()

    # -- trigger evaluation ------------------------------------------------------
    def _trigger_active(self) -> bool:
        t = self.cfg.trigger
        if t == "always":
            return True
        motion = bool(self.state.get("motion_active", False))
        dog = bool(self.state.get("dog_active", False))
        if t == "motion":
            return motion
        if t == "dog":
            return dog
        return motion or dog  # motion_or_dog (default)

    def _disk_ok(self) -> bool:
        try:
            usage = shutil.disk_usage(self.cfg.dir)
        except OSError as exc:
            if not self._warned_disk:
                log.warning("Cannot check disk usage of %s: %s", self.cfg.dir, exc)
                self._warned_disk = True
            self.error = f"Recordings directory unreachable: {exc}"
            return False
        self._warned_disk = False
        free_mb = usage.free / (1024 * 1024)
        if free_mb < self.cfg.min_free_mb:
            if not self._warned_disk:
                log.warning(
                    "Too little free space on %s: %.0f MB < %d MB",
                    self.cfg.dir, free_mb, self.cfg.min_free_mb,
                )
                self._warned_disk = True
            self.error = f"Not enough free space ({free_mb:.0f} MB)"
            return False
        self.error = None
        return True

    # -- ffmpeg process ----------------------------------------------------------
    def _ffmpeg_cmd(self, width: int, height: int) -> List[str]:
        return [
            _ffmpeg_bin(),
            "-hide_banner",
            "-loglevel", "warning",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(self.cfg.fps),
            "-i", "-",
            "-an",
            "-c:v", "libx264",
            "-preset", self.cfg.ffmpeg_preset,
            "-crf", str(self.cfg.ffmpeg_crf),
            "-pix_fmt", "yuv420p",
            "-g", str(self.cfg.fps * 2),
            "-movflags", "+faststart",
            "-f", "segment",
            "-segment_time", str(self.cfg.segment_s),
            "-reset_timestamps", "1",
            "-strftime", "1",
            os.path.join(self.cfg.dir, "%Y%m%d_%H%M%S.mp4"),
        ]

    def _start_recording(self) -> bool:
        frame = self.camera.latest_frame()
        if frame is None:
            return False
        h, w = frame.shape[:2]
        if w % 2 or h % 2:
            log.warning("Odd frame size %dx%d — x264/yuv420p needs even dims.", w, h)
            return False

        if not self._disk_ok():
            return False

        try:
            self._stderr_file = open("/tmp/ffmpeg-record.log", "ab", buffering=0)
            self._proc = subprocess.Popen(
                self._ffmpeg_cmd(w, h),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
            # Non-blocking stdin: drop frames instead of stalling the
            # recorder thread when ffmpeg can't keep up (CPU contention).
            os.set_blocking(self._proc.stdin.fileno(), False)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to start ffmpeg: %s", exc)
            self.error = f"ffmpeg start failed: {exc}"
            self._close_proc()
            return False

        self._started_ts = time.time()
        self._last_feed_ts = 0.0
        self._frame_size = (w, h)
        self.error = None
        log.info("Recording started: %dx%d @ %d fps -> %s", w, h, self.cfg.fps, self.cfg.dir)
        return True

    def _queue_frame(self, frame) -> None:
        """Queue a full frame for writing; drained by _drain_pending()."""
        self._pending = frame.tobytes()
        self._pending_off = 0

    def _drain_pending(self) -> None:
        """Push the queued frame to ffmpeg stdin.

        os.write() on a non-blocking pipe returns after ~64 KB (pipe
        capacity), so a 921 KB frame needs several rounds. Partial writes
        must be continued — otherwise ffmpeg receives torn frames and
        discards them ("corrupt input packet").
        """
        if self._pending is None:
            return
        if self._proc is None or self._proc.stdin is None:
            self._pending = None
            return
        fd = self._proc.stdin.fileno()
        view = memoryview(self._pending)
        while self._pending_off < len(view):
            try:
                # stdin is the WRITE end of the pipe: check for
                # writability (wlist).
                # select() returns (readfds, writefds, exceptfds) —
                # writability is the SECOND return value.
                _, ready, _ = select.select([], [fd], [], 0.1)
                if not ready:
                    continue
                n = os.write(fd, view[self._pending_off:])
                if n <= 0:
                    continue
                self._pending_off += n
            except (BlockingIOError, InterruptedError):
                continue
            except (BrokenPipeError, OSError) as exc:
                log.warning("ffmpeg stdin write failed: %s", exc)
                self._pending = None
                self._stop_recording()
                return
        self._pending = None
        self._pending_off = 0

    def _stop_recording(self) -> None:
        proc, self._proc = self._proc, None
        self._frame_size = None
        self._pending = None
        self._pending_off = 0
        if proc is None:
            self._close_stderr()
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
            log.info("Recording stopped after %.0fs", time.time() - self._started_ts)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg did not exit in time; killing.")
            proc.kill()
            proc.wait(timeout=5)
        self._close_stderr()

    def _close_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        self._close_stderr()

    def _close_stderr(self) -> None:
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except Exception:  # noqa: BLE001
                pass
            self._stderr_file = None

    # -- main loop ---------------------------------------------------------------
    def _loop(self) -> None:
        feed_interval = 1.0 / max(self.cfg.fps, 1)
        while not self._stop.is_set():
            now = time.time()

            # Detect ffmpeg crash while "active".
            if self._proc is not None and self._proc.poll() is not None:
                log.warning("ffmpeg exited unexpectedly (rc=%s).", self._proc.returncode)
                self.error = "ffmpeg terminated unexpectedly"
                self._close_proc()

            trigger = self.cfg.enabled and self._trigger_active()
            if trigger:
                self._last_trigger_ts = now

            if self.cfg.enabled and not self.active and trigger and now >= self._next_attempt_ts:
                if self._start_recording():
                    self._next_attempt_ts = 0.0
                else:
                    # Retry backoff (e.g. no frame yet, disk full, ffmpeg missing).
                    self._next_attempt_ts = now + 5.0

            if self.active:
                elapsed = now - self._started_ts
                idle = now - self._last_trigger_ts
                if self.cfg.trigger != "always" and elapsed >= self.cfg.min_s and idle >= self.cfg.cooldown_s:
                    self._stop_recording()

            # Feed frames at RECORD_FPS. One frame is queued at a time and
            # fully drained before the next is taken, so ffmpeg always sees
            # complete frames even when the pipe applies backpressure.
            if self._pending is not None:
                self._drain_pending()
            elif self.active and now - self._last_feed_ts >= feed_interval:
                frame = self.camera.latest_frame()
                if frame is not None:
                    if self._frame_size is not None and frame.shape[:2] != (
                        self._frame_size[1], self._frame_size[0]
                    ):
                        pass  # Resolution changed; skip rather than corrupt stream.
                    else:
                        self._queue_frame(frame)
                        self._last_feed_ts = now

            time.sleep(0.05)

        self._stop_recording()
