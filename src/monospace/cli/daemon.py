"""`monospace daemon` — keep the cache fresh without the TUI (systemd / launchd)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from monospace.cli.output import info
from monospace.core.service import Service

LABEL = "email.monospace.daemon"


def loop(svc: Service, interval: int, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    info(f"monospace daemon: syncing every {interval}s (Ctrl-C to stop)")
    while not stop.is_set():
        stats = svc.sync()
        for err in stats.errors:
            info(f"error: {err}")
        if stats.new:
            info(f"{time.strftime('%H:%M:%S')} {stats.new} new message(s)")
        stop.wait(interval)


def _exe() -> str:
    return shutil.which("monospace") or f"{sys.executable} -m monospace"


def unit_paths() -> tuple[Path, str]:
    if sys.platform == "darwin":
        return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist", "launchd"
    return Path.home() / ".config/systemd/user/monospace.service", "systemd"


def install(interval: int) -> int:
    path, kind = unit_paths()
    path.parent.mkdir(parents=True, exist_ok=True)
    exe = _exe().split()
    if kind == "launchd":
        args = "".join(f"<string>{a}</string>" for a in [*exe, "daemon", "--interval", str(interval)])
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
            f"<key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array>{args}</array>"
            "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/></dict></plist>\n")
        subprocess.run(["launchctl", "load", "-w", str(path)], check=False)
    else:
        path.write_text(
            "[Unit]\nDescription=monospace.email background sync\nAfter=network-online.target\n\n"
            f"[Service]\nExecStart={' '.join(exe)} daemon --interval {interval}\nRestart=on-failure\n"
            "# Headless secrets file: put MONOSPACE_PASSPHRASE in ~/.config/monospace/daemon.env\n"
            "EnvironmentFile=-%h/.config/monospace/daemon.env\n\n[Install]\nWantedBy=default.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "monospace.service"], check=False)
    info(f"installed {kind} unit at {path}")
    return 0


def uninstall() -> int:
    path, kind = unit_paths()
    if kind == "launchd":
        subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "monospace.service"], check=False)
    path.unlink(missing_ok=True)
    info(f"removed {path}")
    return 0
