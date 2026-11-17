"""System clipboard: OSC 52 (works over SSH) plus native tools when a display is available.

OSC 52 alone fails in tmux/screen without passthrough and in terminals that ignore it
(macOS Terminal.app), and terminals never let an app *read* the clipboard — so paste needs
the native tools (pbpaste / wl-paste / xclip / xsel) whenever they can reach a display.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys

from monospace.core import platform

_COPY = {
    "pbcopy": ["pbcopy"],
    "wl-copy": ["wl-copy"],
    "xclip": ["xclip", "-selection", "clipboard", "-in"],
    "xsel": ["xsel", "--clipboard", "--input"],
}
_PASTE = {
    "pbpaste": ["pbpaste"],
    "wl-paste": ["wl-paste", "--no-newline"],
    "xclip": ["xclip", "-selection", "clipboard", "-out"],
    "xsel": ["xsel", "--clipboard", "--output"],
}


def osc52(text: str) -> str:
    """The OSC 52 'set clipboard' sequence, wrapped for tmux / GNU screen passthrough."""
    seq = f"\x1b]52;c;{base64.b64encode(text.encode('utf-8')).decode('ascii')}\a"
    if os.environ.get("TMUX"):
        return "\x1bPtmux;" + seq.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    if os.environ.get("STY"):
        return "\x1bP" + seq + "\x1b\\"
    return seq


def _candidates(table: dict[str, list[str]]) -> list[list[str]]:
    if not platform.has_display():
        return []
    if sys.platform == "darwin":
        names = ["pbcopy", "pbpaste"]
    else:
        names = (["wl-copy", "wl-paste"] if os.environ.get("WAYLAND_DISPLAY") else []) + ["xclip", "xsel"]
        if not os.environ.get("DISPLAY"):
            names = [n for n in names if n.startswith("wl-")]
    return [table[n] for n in names if n in table and shutil.which(table[n][0])]


def native_copy(text: str) -> bool:
    for cmd in _candidates(_COPY):
        try:
            # xclip/wl-copy fork to keep serving the selection: don't wait on their pipes.
            subprocess.run(cmd, input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2, check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def native_paste() -> str | None:
    for cmd in _candidates(_PASTE):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=2, check=True).stdout
            return out.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            continue
    return None
