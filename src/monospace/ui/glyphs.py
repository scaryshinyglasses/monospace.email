"""Icons with an ASCII fallback for the bare Linux console (TERM=linux), which has no emoji."""

from __future__ import annotations

import os


def ascii_mode() -> bool:
    return os.environ.get("MONOSPACE_ASCII") == "1" or os.environ.get("TERM", "") in ("linux", "vt100", "vt220")


_FANCY = {
    "inbox": "📥", "drafts": "📝", "sent": "📤", "archive": "📦", "junk": "🚫", "trash": "🧺", "folder": "📁",
    "unread": "●", "flag": "★", "attach": "📎", "image": "🖼 ", "ok": "✓",
}
_ASCII = {
    "inbox": "[I]", "drafts": "[D]", "sent": "[S]", "archive": "[A]", "junk": "[J]", "trash": "[T]",
    "folder": "[ ]", "unread": "*", "flag": "!", "attach": "@", "image": "[img]", "ok": "ok",
}


def glyph(name: str) -> str:
    return (_ASCII if ascii_mode() else _FANCY).get(name, "?")
