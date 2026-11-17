"""Tests for the motion detector (frame differencing)."""
import numpy as np
import pytest

from app.config import MotionConfig
from app.motion import MotionDetector


def _black_frame(w: int = 640, h: int = 360) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _frame_with_box(w: int = 640, h: int = 360) -> np.ndarray:
    frame = _black_frame(w, h)
    # Large bright rectangle -> strong motion signal.
    frame[50:250, 100:400] = 255
    return frame


@pytest.fixture
def detector() -> MotionDetector:
    cfg = MotionConfig()
    # Sensible test values regardless of .env.
    cfg.threshold = 30
    cfg.min_area = 500
    return MotionDetector(cfg)


def test_no_motion_on_identical_frames(detector: MotionDetector) -> None:
    frame = _black_frame()
    detector.update(frame)
    detector.update(frame)
    # Force the active-window to expire.
    detector.last_motion_ts = 0.0
    assert detector.update(frame) is False


def test_motion_detected_on_change(detector: MotionDetector) -> None:
    detector.update(_black_frame())
    assert detector.update(_frame_with_box()) is True
    assert detector.moved_area > 0


def test_motion_area_below_threshold(detector: MotionDetector) -> None:
    detector.cfg.min_area = 10_000_000  # absurdly high -> never triggers
    detector.update(_black_frame())
    assert detector.update(_frame_with_box()) is False
