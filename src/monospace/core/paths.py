"""XDG-style locations. ``MONOSPACE_HOME`` relocates everything (tests, portable installs)."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env: str, default: str) -> Path:
    raw = os.environ.get(env)
    return Path(raw) if raw else Path.home() / default


def home_override() -> Path | None:
    raw = os.environ.get("MONOSPACE_HOME")
    return Path(raw) if raw else None


def config_dir() -> Path:
    base = home_override()
    return (base / "config") if base else _xdg("XDG_CONFIG_HOME", ".config") / "monospace"


def data_dir() -> Path:
    base = home_override()
    return (base / "data") if base else _xdg("XDG_DATA_HOME", ".local/share") / "monospace"


def config_file() -> Path:
    return config_dir() / "config.toml"


def cache_db() -> Path:
    return data_dir() / "cache.db"


def secrets_file() -> Path:
    return data_dir() / "secrets.enc"


def ensure_dirs() -> None:
    for d in (config_dir(), data_dir()):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass
