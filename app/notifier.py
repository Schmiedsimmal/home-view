"""Push notifications via ntfy.sh.

Sends a text notification, optionally with the current camera frame as a
JPEG attachment (single PUT request carrying title/tags/priority).
All errors are logged and swallowed — notifications must never crash
the monitoring pipeline.
"""
import logging
import time
import urllib.request
import urllib.error
from typing import Optional

import cv2

from .config import NotifyConfig

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: NotifyConfig) -> None:
        self.cfg = cfg
        self._last_sent_ts: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and bool(self.cfg.topic)

    def _url(self) -> str:
        return f"{self.cfg.server.rstrip('/')}/{self.cfg.topic}"

    def _cooldown_active(self) -> bool:
        return (time.time() - self._last_sent_ts) < self.cfg.cooldown_s

    def notify_dog(self, frame=None, extra: str = "") -> bool:
        """Send a dog-detection notification. Returns True on success."""
        if not self.enabled:
            return False
        if self._cooldown_active():
            return False

        title = "Dog detected"
        message = f"{extra}Dog seen in the apartment.".strip() if extra else "Dog seen in the apartment."
        tags = "dog,house"
        priority = "high"

        try:
            if self.cfg.attach_image and frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    self._put_image(buf.tobytes(), title, message, tags, priority)
                else:
                    self._post_text(title, message, tags, priority)
            else:
                self._post_text(title, message, tags, priority)
            self._last_sent_ts = time.time()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("ntfy notification failed: %s", exc)
            return False

    def _headers(self, title: str, tags: str, priority: str, extra: Optional[dict] = None) -> dict:
        h = {
            "Title": title,
            "Tags": tags,
            "Priority": priority,
        }
        if extra:
            h.update(extra)
        return h

    def _post_text(self, title: str, message: str, tags: str, priority: str) -> None:
        req = urllib.request.Request(
            self._url(),
            data=message.encode("utf-8"),
            headers=self._headers(title, tags, priority, {"Content-Type": "text/plain; charset=utf-8"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("ntfy POST -> %s (%s)", self._url(), resp.status)

    def _put_image(self, jpeg_bytes: bytes, title: str, message: str, tags: str, priority: str) -> None:
        # PUT with attachment: ntfy publishes one notification carrying the
        # image. Title/Tags/Priority headers are honoured as usual.
        headers = self._headers(title, tags, priority, {
            "Filename": "snapshot.jpg",
            "Content-Type": "image/jpeg",
            "Message": message,
        })
        req = urllib.request.Request(
            self._url(),
            data=jpeg_bytes,
            headers=headers,
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("ntfy PUT (image) -> %s (%s)", self._url(), resp.status)
