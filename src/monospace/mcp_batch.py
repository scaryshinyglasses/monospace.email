"""MCP ``batch``: several tool calls in one request, run in order, one result each.

It dispatches through ``server.call_tool``, so every check the individual tools make still applies.
Two deliberate limits:
  * Read-only tools only: whatever a tool's own ``readOnlyHint`` says, checked at call time (and
    ``read_message(mark_read=true)`` is refused). Any write — even a draft edit or saving a file — stays a
    direct call, so approving ``batch`` once can never stand in for the client's per-tool prompts.
    Bulk writes don't need batch anyway: mark / tag tools take id lists and ``where``.
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
BATCH = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


async def read_only_tools(server: MCPServer) -> set[str]:
    return {t.name for t in await server.list_tools()
            if t.name != "batch" and t.annotations is not None and t.annotations.read_only_hint is True}


def _refusal(tool: str, args: dict[str, Any], allowed: set[str]) -> str | None:
    if tool not in allowed:
        return f"{tool!r} can't be batched (batch is read-only); call it directly"
    if tool == "read_message" and args.get("mark_read"):
        return "read_message with mark_read=true changes state; call it directly or use mark"
    return None


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
        """Run up to 20 READ-ONLY tool calls in one request, in order; returns one {tool, ok, result|error}
        per call. E.g. list_accounts + list_folders + list_tags + list_messages(...) + get_draft(...).
        Batchable: list_accounts, list_folders, list_messages, search, read_message (not mark_read),
        get_draft, list_tags, outbox_list, schedule_list. Anything that writes is refused here — call it directly
        (mark / tag_message / untag_message already take id lists and `where`).
        A call can't use an earlier call's result; batch again once you've read it."""
        if len(calls) > MAX_CALLS:
            return {"error": f"at most {MAX_CALLS} calls per batch", "results": []}
        allowed = await read_only_tools(server)
        results: list[dict] = []
        for n, call in enumerate(calls):
            entry: dict[str, Any] = {"index": n, "tool": call.tool}
            refusal = _refusal(call.tool, call.args, allowed)
            if refusal:
                entry |= {"ok": False, "error": refusal}
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
