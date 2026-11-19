"""MCP tools for tags (Gmail labels / IMAP keywords / local). Registered by ``mcp_server.build_server``.

Tags never send, delete or move mail, and names that could touch system flags or Gmail system
labels (``\\Inbox``, ``$Junk``, "Spam" …) are refused in ``core.tags``, so agents may use these freely.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from monospace.mcp_drafts import Run

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
TAG = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def register(server: MCPServer, run: Run) -> None:
    @server.tool(annotations=TAG)
    async def tag_message(id: int, tags: list[str]) -> dict:
        """Add tags to a message or draft (by id). Stored as Gmail labels, IMAP keywords or locally,
        depending on the server. Letters, digits, spaces and - _ . / & + only; system names are refused."""
        return (await run(lambda s: s.add_tags(id, tags))).to_dict()

    @server.tool(annotations=TAG)
    async def untag_message(id: int, tags: list[str]) -> dict:
        """Remove tags from a message or draft (by id)."""
        return (await run(lambda s: s.remove_tags(id, tags))).to_dict()

    @server.tool(annotations=READ)
    async def list_tags(account: str | None = None) -> list[dict]:
        """Every tag on the account with how many cached messages carry it. Filter with
        list_messages(tag=...) or search("tag:name …")."""
        return await run(lambda s: s.list_tags(account))
