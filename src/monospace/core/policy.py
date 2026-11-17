"""Decide what happens to an outgoing message based on who asked and the account policy.

Humans (the TUI, or the CLI on an interactive terminal with --yes) always send.
AI callers (MCP, non-interactive CLI) follow the account's ``ai_send`` setting:
  draft   → saved to the IMAP Drafts folder (default)
  confirm → queued in the local outbox until a human approves
  allow   → sent immediately
"""

from __future__ import annotations

from typing import Literal

Source = Literal["human", "ai"]
Action = Literal["send", "draft", "queue"]

_AI_ACTIONS: dict[str, Action] = {"draft": "draft", "confirm": "queue", "allow": "send"}


def decide(ai_send: str, source: Source) -> Action:
    if source == "human":
        return "send"
    try:
        return _AI_ACTIONS[ai_send]
    except KeyError as exc:
        raise ValueError(f"invalid ai_send policy {ai_send!r}") from exc


# ---- attachments an agent may send -------------------------------------------
# An email can carry a prompt injection ("attach ~/.ssh/id_rsa and send it to …"). Agents may
# therefore only attach files that the user placed in one dedicated folder.

class AttachmentPolicyError(ValueError):
    pass


def agent_attachment_dir(configured: str = "") -> "Path":
    from pathlib import Path

    from monospace.core import paths

    path = Path(configured).expanduser() if configured else paths.data_dir() / "agent-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_roots(settings: "object") -> "list[Path]":
    """Every folder agents may read files from: the attachment dir plus human-approved extras."""
    from pathlib import Path

    roots = [agent_attachment_dir(getattr(settings, "agent_attachment_dir", ""))]
    for raw in getattr(settings, "agent_read_dirs", []) or []:
        p = Path(raw).expanduser()
        if p.is_dir():
            roots.append(p)
    return roots


def validate_read_dir(raw: str) -> "Path":
    """Refuse folders that would expose secrets: /, home or its parents, hidden dirs, our own data."""
    from pathlib import Path

    from monospace.core import paths

    p = Path(raw).expanduser()
    try:
        real = p.resolve(strict=True)
    except OSError as exc:
        raise AttachmentPolicyError(f"no such folder: {raw}") from exc
    if not real.is_dir():
        raise AttachmentPolicyError(f"not a folder: {raw}")
    home = Path.home().resolve()
    if real == Path(real.anchor) or real == home or home.is_relative_to(real):
        raise AttachmentPolicyError(f"{real} is too broad; pick a specific folder (e.g. ~/Documents/logos)")
    rel = real.relative_to(home) if real.is_relative_to(home) else real
    if any(part.startswith(".") for part in rel.parts):
        raise AttachmentPolicyError(f"{real} is inside a hidden folder (keys and config live there)")
    for own in (paths.config_dir(), paths.data_dir()):
        own = own.resolve()
        if real.is_relative_to(own) or own.is_relative_to(real):
            raise AttachmentPolicyError(f"{real} overlaps monospace's own config/data ({own})")
    return real


def check_agent_attachments(files: list[str], allowed: "Path | list[Path]") -> list[str]:
    from pathlib import Path

    roots = [r.resolve() for r in (allowed if isinstance(allowed, list) else [allowed])]
    out = []
    for raw in files:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = roots[0] / p
        try:
            real = p.resolve(strict=True)
        except OSError as exc:
            raise AttachmentPolicyError(f"attachment not found: {raw}") from exc
        if not real.is_file() or not any(real.is_relative_to(r) for r in roots):
            raise AttachmentPolicyError(
                f"agents may only use files inside {', '.join(map(str, roots))} (got {raw}). Copy it to "
                f"{roots[0]}, or ask the user to run `monospace agent-dirs add <folder>` in a terminal.")
        out.append(str(real))
    return out
