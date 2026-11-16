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


def check_agent_attachments(files: list[str], allowed: "Path") -> list[str]:
    from pathlib import Path

    root = allowed.resolve()
    out = []
    for raw in files:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = root / p
        try:
            real = p.resolve(strict=True)
        except OSError as exc:
            raise AttachmentPolicyError(f"attachment not found: {raw}") from exc
        if not real.is_relative_to(root) or not real.is_file():
            raise AttachmentPolicyError(
                f"agents may only attach files inside {root} (got {raw}); copy the file there first")
        out.append(str(real))
    return out
