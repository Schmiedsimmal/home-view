"""Retention cleanup for event recordings.

Deletes MP4 segments older than ``retention_days`` and — as a safety net —
the oldest segments when disk usage exceeds ``max_disk_percent``.
Runs hourly in a background thread.
"""
import logging
import os
import shutil
import threading
import time
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


def cleanup_recordings(
    record_dir: str,
    retention_days: int,
    max_disk_percent: int,
    min_keep: int = 1,
) -> dict:
    """Delete old/excess recordings. Returns stats dict."""
    stats = {"deleted_age": 0, "deleted_space": 0, "freed_bytes": 0, "errors": 0}
    if not os.path.isdir(record_dir):
        return stats

    now = time.time()
    max_age_s = retention_days * 86400

    files: List[Tuple[str, float, int]] = []
    for name in os.listdir(record_dir):
        if not name.endswith(".mp4"):
            continue
        path = os.path.join(record_dir, name)
        try:
            st = os.stat(path)
            files.append((path, st.st_mtime, st.st_size))
        except OSError as exc:
            log.warning("Cannot stat %s: %s", path, exc)
            stats["errors"] += 1

    files.sort(key=lambda f: f[1])  # oldest first

    # 1) Age-based deletion.
    remaining: List[Tuple[str, float, int]] = []
    for path, mtime, size in files:
        if now - mtime > max_age_s:
            try:
                os.remove(path)
                stats["deleted_age"] += 1
                stats["freed_bytes"] += size
                log.info("Retention: deleted old file %s", path)
            except OSError as exc:
                log.warning("Cannot delete %s: %s", path, exc)
                stats["errors"] += 1
                remaining.append((path, mtime, size))
        else:
            remaining.append((path, mtime, size))

    # 2) Disk-usage-based deletion (oldest first, keep newest min_keep).
    try:
        usage = shutil.disk_usage(record_dir)
        used_pct = usage.used / usage.total * 100.0
    except OSError:
        return stats

    if used_pct > max_disk_percent and len(remaining) > min_keep:
        for path, _mtime, size in remaining[:-min_keep]:
            try:
                os.remove(path)
                stats["deleted_space"] += 1
                stats["freed_bytes"] += size
                log.info("Retention: deleted file (disk %.0f%% > %d%%): %s", used_pct, max_disk_percent, path)
            except OSError as exc:
                log.warning("Cannot delete %s: %s", path, exc)
                stats["errors"] += 1
            # Re-check after each deletion so we stop as soon as we're below.
            try:
                usage = shutil.disk_usage(record_dir)
                used_pct = usage.used / usage.total * 100.0
            except OSError:
                break
            if used_pct <= max_disk_percent:
                break

    return stats


class RetentionLoop:
    """Runs ``cleanup_recordings`` every hour in a daemon thread."""

    def __init__(self, record_dir: str, retention_days: int, max_disk_percent: int) -> None:
        self._args = (record_dir, retention_days, max_disk_percent)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Retention loop started (dir=%s, days=%d, max_disk=%d%%)",
                 self._args[0], self._args[1], self._args[2])

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                stats = cleanup_recordings(*self._args)
                if stats["deleted_age"] or stats["deleted_space"]:
                    log.info("Retention cleanup: %s", stats)
            except Exception as exc:  # noqa: BLE001
                log.warning("Retention cleanup failed: %s", exc)
            self._stop.wait(timeout=3600)
