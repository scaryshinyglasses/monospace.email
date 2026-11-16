"""Host capabilities: display, opener, downloads. Keeps headless Linux first-class."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def has_display() -> bool:
    if sys.platform == "darwin":
        return not os.environ.get("SSH_CONNECTION")
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def opener() -> list[str] | None:
    if not has_display():
        return None
    if sys.platform == "darwin":
        return ["open"]
    exe = shutil.which("xdg-open")
    return [exe] if exe else None


def open_path(target: str) -> bool:
    cmd = opener()
    if not cmd:
        return False
    try:
        subprocess.Popen([*cmd, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


PASSIVE_TYPES = ("image/", "application/pdf", "text/plain", "audio/", "video/")


def safe_to_open(path: str) -> bool:
    """Only auto-open passive content; executables, scripts, archives and Office docs are just saved."""
    import mimetypes

    ctype, _ = mimetypes.guess_type(path)
    return bool(ctype) and ctype.startswith(PASSIVE_TYPES)  # type: ignore[union-attr]


SAFE_SCHEMES = ("http", "https")


def safe_url(href: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(href).scheme.lower() in SAFE_SCHEMES


def download_dir(configured: str = "") -> Path:
    if configured:
        path = Path(configured).expanduser()
    else:
        path = Path.home() / "Downloads"
        if not path.exists():
            path = Path.home() / "monospace-downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique_path(directory: Path, filename: str) -> Path:
    safe = os.path.basename(filename) or "attachment"
    candidate = directory / safe
    stem, suffix = candidate.stem, candidate.suffix
    n = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){suffix}"
        n += 1
    return candidate
