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
