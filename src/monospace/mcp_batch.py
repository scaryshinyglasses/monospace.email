"""MCP ``batch``: several tool calls in one request, run in order, one result each.

It dispatches through ``server.call_tool``, so every check the individual tools make still applies.
Two deliberate limits:
  * Only tools that read or change nothing irreversibly are batchable (``BATCHABLE``). Sends, deletes,
    moves and folder changes stay separate calls, so a client's per-tool permission prompts can't be
    side-stepped by wrapping them in a batch.
  * No result references: a call can't consume an earlier call's output. The model has to read untrusted
    email before acting on it, so there's no blind read → act pipeline.
Registered last by ``mcp_server.build_server``.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

MAX_CALLS = 20
BATCHABLE = frozenset({
    "list_accounts", "sync", "list_folders", "list_messages", "search", "read_message", "save_attachment",
    "mark", "tag_message", "untag_message", "list_tags", "outbox_list",
    "create_draft", "get_draft", "update_draft",
})
BATCH = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


class Call(BaseModel):
    tool: str = Field(description="Tool name, e.g. list_messages.")
    args: dict[str, Any] = Field(default_factory=dict, description="That tool's arguments.")


def _payload(result: Any) -> Any:
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    texts = [c.text for c in getattr(result, "content", []) if hasattr(c, "text")]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    return [json.loads(t) if t[:1] in "[{" else t for t in texts]


def _message(exc: BaseException) -> str:
    cause = exc.__cause__ or exc.__context__
    return f"{exc}: {cause}" if cause and str(cause) not in str(exc) else str(exc)


def register(server: MCPServer) -> None:
    @server.tool(annotations=BATCH)
    async def batch(calls: list[Call], stop_on_error: bool = False) -> dict:
        """Run up to 20 tool calls in one request, in order; returns one {tool, ok, result|error} per call.
        Use it to stack independent steps, e.g. sync + list_messages + list_tags, or mark + tag_message.
        Batchable: list_accounts, sync, list_folders, list_messages, search, read_message, save_attachment,
        mark, tag_message, untag_message, list_tags, outbox_list, create_draft, get_draft, update_draft.
        Sending, deleting, moving and folder changes are NOT batchable — call those tools directly.
        A call can't use an earlier call's result; batch again once you've read it."""
        if len(calls) > MAX_CALLS:
            return {"error": f"at most {MAX_CALLS} calls per batch", "results": []}
        results: list[dict] = []
        for n, call in enumerate(calls):
            entry: dict[str, Any] = {"index": n, "tool": call.tool}
            if call.tool not in BATCHABLE:
                entry |= {"ok": False, "error": f"{call.tool!r} can't be batched; call it directly"}
            else:
                try:
                    res = await server.call_tool(call.tool, call.args)
                    if getattr(res, "is_error", False):
                        entry |= {"ok": False, "error": _payload(res)}
                    else:
                        entry |= {"ok": True, "result": _payload(res)}
                except Exception as exc:
                    entry |= {"ok": False, "error": _message(exc)}
            results.append(entry)
            if stop_on_error and not entry["ok"]:
                break
        ok = sum(r["ok"] for r in results)
        return {"ok": ok, "failed": len(results) - ok, "skipped": len(calls) - len(results), "results": results}
