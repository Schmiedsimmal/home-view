"""Motion detection via frame differencing (no ML).

Compares each frame against the previous one on a downscaled grayscale
image. Cheap enough to run 24/7 on the Jetson's CPU.
"""
import time

import cv2

from .config import MotionConfig


class MotionDetector:
    """Detects motion between consecutive frames."""

    ANALYSIS_W = 320
    ANALYSIS_H = 180
    # Motion stays "active" this long after the last detection so the
    # recorder cooldown logic sees a clean signal.
    ACTIVE_WINDOW_S = 1.0

    def __init__(self, cfg: MotionConfig) -> None:
        self.cfg = cfg
        self._prev_gray = None
        self.active = False
        self.last_motion_ts: float = 0.0
        self.moved_area: int = 0

    def update(self, frame) -> bool:
        """Process *frame* (BGR); return True if motion is active."""
        small = cv2.resize(
            frame, (self.ANALYSIS_W, self.ANALYSIS_H),
            interpolation=cv2.INTER_AREA,
        )
        gray = cv2.GaussianBlur(
            cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0,
        )

        motion_now = False
        if self._prev_gray is not None:
            diff = cv2.absdiff(gray, self._prev_gray)
            _, thresh = cv2.threshold(diff, self.cfg.threshold, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(
                thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
            )
            area = sum(cv2.contourArea(c) for c in contours)
            self.moved_area = int(area)
            motion_now = area >= self.cfg.min_area
        self._prev_gray = gray

        if motion_now:
            self.last_motion_ts = time.time()
        self.active = (time.time() - self.last_motion_ts) < self.ACTIVE_WINDOW_S

        return self.active
