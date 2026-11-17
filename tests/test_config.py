"""Tests for env-based configuration."""
import os

import pytest

from app.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("CAMERA_", "WEB_", "DETECT", "MOTION_", "RECORD_",
                           "NOTIFY_", "STREAM_", "YOLO_", "HOME_MONITOR", "VIDEO_")):
            monkeypatch.delenv(key, raising=False)


def test_defaults() -> None:
    cfg = load_config()
    assert cfg.camera.source == "usb:0"
    assert cfg.camera.width == 1280
    assert cfg.camera.height == 720
    assert cfg.record.trigger == "motion_or_dog"
    assert cfg.record.retention_days == 7
    assert cfg.detector.classes == [0, 16]
    assert cfg.web.port == 8080


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://example.com/stream")
    monkeypatch.setenv("RECORD_TRIGGER", "dog")
    monkeypatch.setenv("RECORD_FPS", "8")
    monkeypatch.setenv("DETECT_CLASSES", "0")
    monkeypatch.setenv("MOTION_THRESHOLD", "50")
    monkeypatch.setenv("WEB_PASSWORD", "secret")
    monkeypatch.setenv("NOTIFY_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("HOME_MONITOR_PORT", "9090")

    cfg = load_config()
    assert cfg.camera.source == "rtsp://example.com/stream"
    assert cfg.record.trigger == "dog"
    assert cfg.record.fps == 8
    assert cfg.detector.classes == [0]
    assert cfg.motion.threshold == 50
    assert cfg.web.password == "secret"
    assert cfg.notify.topic == "test-topic"
    assert cfg.web.port == 9090


def test_bool_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DETECTOR_ENABLED", "false")
    monkeypatch.setenv("MOTION_ENABLED", "0")
    monkeypatch.setenv("NOTIFY_ATTACH_IMAGE", "yes")

    cfg = load_config()
    assert cfg.detector.enabled is False
    assert cfg.motion.enabled is False
    assert cfg.notify.attach_image is True
