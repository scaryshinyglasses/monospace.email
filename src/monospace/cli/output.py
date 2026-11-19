"""Plain-text and JSON output for CLI commands (stdout is for data, stderr for chatter)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from monospace.core.store import Envelope


def interactive() -> bool:
    """True only for a human at a terminal. ``MONOSPACE_AGENT=1`` forces agent mode."""
    if os.environ.get("MONOSPACE_AGENT") == "1":
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def emit_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def envelope_rows(envs: list[Envelope]) -> None:
    if not envs:
        info("(no messages)")
        return
    for e in envs:
        mark = ("●" if e.unread else " ") + ("★" if e.flagged else " ") + ("📎" if e.has_attachments else "  ")
        date = e.date[:16].replace("T", " ")
        tags = "".join(f"  #{t}" for t in e.tags)
        print(f"{e.id:>6}  {mark} {date:16}  {_short(e.sender, 26):26}  {_short(e.subject, 60)}{tags}")


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def confirm(prompt: str) -> bool:
    return ask(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
