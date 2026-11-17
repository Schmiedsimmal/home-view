"""Environment-variable helpers for config dataclasses.

Centralizes env parsing so the config module doesn't duplicate the same
``_env`` / ``_env_int`` / ``_env_float`` boilerplate.
"""
import os
from typing import List


def env_str(name: str, default: str) -> str:
    """Read a string env var, falling back to *default*."""
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to *default* on parse errors."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to *default* on parse errors."""
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    """Read a bool env var (``true``/``false``, case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def env_int_list(name: str, default: str) -> List[int]:
    """Read a comma-separated list of ints."""
    raw = env_str(name, default)
    if not raw.strip():
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def env_str_list(name: str, default: str) -> List[str]:
    """Read a comma-separated list of strings."""
    raw = env_str(name, default)
    if not raw.strip():
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]
