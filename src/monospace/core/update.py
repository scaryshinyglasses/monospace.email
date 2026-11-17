"""Update check + self-update. Notify only — installing is always an explicit `monospace update`.

The installer (and `monospace update`) records what was installed in ``data/install.json``:
``{"ref": "main" | "v0.2.0", "sha": "<commit>"}``. A branch ref is tracked by commit SHA; a
``v*`` tag ref is tracked by the latest GitHub release. The GitHub lookup runs at most once
a day (cached in ``data/update-check.json``); no record (dev / editable install) → no check.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from monospace.core import paths

REPO = "scaryshinyglasses/monospace.email"
API = f"https://api.github.com/repos/{REPO}"
CHECK_EVERY = 24 * 3600
_TAG = re.compile(r"^v\d+(\.\d+)*")


class UpdateError(Exception):
    pass


@dataclass
class Installed:
    ref: str
    sha: str = ""

    @property
    def is_tag(self) -> bool:
        return bool(_TAG.match(self.ref))

    @property
    def current(self) -> str:
        """What we compare against `latest()`: the tag itself, or the commit SHA."""
        return self.ref if self.is_tag else self.sha


def _install_file() -> Path:
    return paths.data_dir() / "install.json"


def _cache_file() -> Path:
    return paths.data_dir() / "update-check.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def installed() -> Installed | None:
    data = _read(_install_file())
    ref = data.get("ref")
    return Installed(str(ref), str(data.get("sha") or "")) if ref else None


def record(ref: str, sha: str) -> None:
    paths.ensure_dirs()
    _install_file().write_text(json.dumps({"ref": ref, "sha": sha, "installed_at": int(time.time())}))
    _cache_file().unlink(missing_ok=True)


def _get(url: str, accept: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "monospace-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (fixed https URL)
            return resp.read().decode("utf-8").strip()
    except Exception as exc:
        raise UpdateError(f"could not reach GitHub: {exc}") from exc


def latest(inst: Installed) -> str:
    """Newest tag (for tag installs) or newest commit SHA on the installed branch."""
    if inst.is_tag:
        return str(json.loads(_get(f"{API}/releases/latest", "application/vnd.github+json"))["tag_name"])
    return _get(f"{API}/commits/{inst.ref}", "application/vnd.github.sha")


def check(*, force: bool = False, now: float | None = None) -> str | None:
    """The newer version id if one exists, else None. Never raises; network at most daily."""
    inst = installed()
    if inst is None or not inst.current:
        return None
    now = time.time() if now is None else now
    cache = _read(_cache_file())
    if not force and cache.get("ref") == inst.ref and now - float(cache.get("checked_at", 0)) < CHECK_EVERY:
        newest = str(cache.get("latest") or "")
    else:
        try:
            newest = latest(inst)
        except (UpdateError, KeyError, ValueError):
            return None
        try:
            _cache_file().write_text(json.dumps({"ref": inst.ref, "latest": newest, "checked_at": now}))
        except OSError:
            pass
    return newest if newest and newest != inst.current else None


def short(version: str) -> str:
    return version if _TAG.match(version) else version[:7]


def find_uv() -> str | None:
    for cand in (shutil.which("uv"), Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


def install_command(uv: str, version: str) -> list[str]:
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    spec = f"monospace @ https://github.com/{REPO}/archive/{version}.tar.gz"
    return [uv, "tool", "install", "--force", "--python", py, spec]
