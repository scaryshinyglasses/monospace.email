"""MCP triage tools: mark, move, tag_message, untag_message, list_tags — one id or many per call.

``mark`` / ``tag_message`` / ``untag_message`` never send, delete or move mail, so they may also target a
filter (``where``, ≤100 matches, ``dry_run`` to preview). ``move`` is destructive, so it takes explicit
ids only. Tag names that could touch system flags/labels are refused in ``core.tags``.
Registered by ``mcp_server.build_server``.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from monospace.core.service import Service, ServiceError
from monospace.mcp_drafts import READ, Run
from monospace.mcp_read import Where, find

MAX_IDS = 100
TAG = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
MOVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def _targets(s: Service, id: int | list[int] | None, where: Where | None) -> list[int]:
    if (id is None) == (where is None):
        raise ServiceError("pass either `id` (one or a list) or `where`, not both")
    if where is not None:
        envs = find(s, where, MAX_IDS + 1)
        if len(envs) > MAX_IDS:
            raise ServiceError(f"`where` matches more than {MAX_IDS} messages; narrow it (e.g. since=, sender=)")
        return [e.id for e in envs]
    ids = [id] if isinstance(id, int) else list(dict.fromkeys(id))
    if len(ids) > MAX_IDS:
        raise ServiceError(f"at most {MAX_IDS} ids per call")
    return ids


def apply(s: Service, id: int | list[int] | None, where: Where | None, dry_run: bool,
          fn: Callable[[Service, int], Any]) -> dict | Any:
    """Run ``fn`` per target. A single int id keeps the old one-result shape; otherwise per-id results."""
    if isinstance(id, int) and where is None:
        return fn(s, id)
    ids = _targets(s, id, where)
    if dry_run:
        return {"dry_run": True, "would_change": [s.envelope(i).to_dict() for i in ids]}
    results = []
    for i in ids:
        try:
            results.append({"id": i, "ok": True, "result": fn(s, i)})
        except Exception as exc:  # keep going: one bad id shouldn't block the rest
            results.append({"id": i, "ok": False, "error": str(exc)})
    ok = sum(r["ok"] for r in results)
    return {"changed": ok, "failed": len(results) - ok, "results": results}


TARGET_NOTE = """ Target: `id` (one, or a list of up to 100) or `where` (a filter like list_messages';
        ≤100 matches; `dry_run=True` lists them without changing anything)."""


def register(server: MCPServer, run: Run) -> None:
    from monospace.mcp_server import _doc

    @server.tool(annotations=TAG, description=_doc(
        "Mark messages read/unread and/or flagged/unflagged." + TARGET_NOTE))
    async def mark(id: int | list[int] | None = None, read: bool | None = None, flagged: bool | None = None,
                   where: Where | None = None, dry_run: bool = False) -> dict:
        return await run(lambda s: apply(s, id, where, dry_run,
                                         lambda s, i: s.mark(i, read=read, flagged=flagged).to_dict()))

    @server.tool(annotations=MOVE)
    async def move(id: int | list[int], folder: str) -> dict:
        """Move messages (one id or a list of up to 100) to a folder name or role (archive, trash,
        junk). Moved messages get new ids once the destination folder is synced."""
        if isinstance(id, int):
            return {"moved_to": await run(lambda s: s.move(id, folder))}
        return await run(lambda s: apply(s, id, None, False, lambda s, i: {"moved_to": s.move(i, folder)}))

    @server.tool(annotations=TAG, description=_doc(
        """Add tags to messages or drafts. Stored as Gmail labels, IMAP keywords or locally, depending on
        the server. Letters, digits, spaces and - _ . / & + only; system names are refused.""" + TARGET_NOTE))
    async def tag_message(tags: list[str], id: int | list[int] | None = None, where: Where | None = None,
                          dry_run: bool = False) -> dict:
        return await run(lambda s: apply(s, id, where, dry_run, lambda s, i: s.add_tags(i, tags).to_dict()))

    @server.tool(annotations=TAG, description=_doc("Remove tags from messages or drafts." + TARGET_NOTE))
    async def untag_message(tags: list[str], id: int | list[int] | None = None, where: Where | None = None,
                            dry_run: bool = False) -> dict:
        return await run(lambda s: apply(s, id, where, dry_run, lambda s, i: s.remove_tags(i, tags).to_dict()))

    @server.tool(annotations=READ)
    async def list_tags(account: str | None = None) -> list[dict]:
        """Every tag on the account with how many cached messages carry it. Filter with
        list_messages(tags=[...]) or search("tag:name …")."""
        return await run(lambda s: s.list_tags(account))
