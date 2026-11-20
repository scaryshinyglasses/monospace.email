"""MCP tools for composing options (attachments vs inline images), draft editing, folders and the outbox.

Registered by ``mcp_server.build_server``. Like every AI path, sends obey ``ai_send``; nothing here
approves the outbox or changes the policy.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from monospace.core import compose
from monospace.core.compose import Draft, split_addrs
from monospace.core.service import Service, ServiceError

Run = Callable[[Callable[[Service], Any]], Awaitable[Any]]

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

class InlineImage(BaseModel):
    """An image embedded IN the body (multipart/related, Content-ID) — not a downloadable attachment."""

    path: str = Field(description="Image file inside attachment_dir (png/jpg/gif/svg/webp).")
    alt: str | None = Field(None, description="Alt text (default: file name).")
    width: int | None = Field(None, description="Display width in px (e.g. 120 for a logo).")
    height: int | None = Field(None, description="Display height in px; omit to keep the aspect ratio.")
    position: Literal["top", "bottom"] = Field(
        "bottom", description="Where to put it if the body doesn't reference it itself.")
    align: Literal["left", "center", "right"] | None = None
    link: str | None = Field(None, description="Optional https:// or mailto: link wrapping the image.")


def inline_arg(items: list[str | InlineImage] | None) -> list:
    return [i.model_dump(exclude_none=True) if isinstance(i, InlineImage) else i for i in items or []]


def apply_extras(draft: Draft, *, cc: list[str] | None = None, bcc: list[str] | None = None,
                 attachments: list[str] | None = None, inline_images: list[str | InlineImage] | None = None,
                 html_body: str | None = None) -> Draft:
    if cc:
        draft.cc = draft.cc + split_addrs(cc)
    if bcc:
        draft.bcc = draft.bcc + split_addrs(bcc)
    draft.attachments = draft.attachments + list(attachments or [])
    if html_body:
        draft.html_body = html_body
    try:
        for path, spec in compose.inline_specs(inline_arg(inline_images)):
            compose.add_inline(draft, path, spec)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    return draft


def register(server: MCPServer, run: Run) -> None:
    @server.tool(annotations=READ)
    async def get_draft(id: int) -> dict:
        """Read a draft for editing: recipients (incl. bcc), subject, markdown/plain `body`, `html`,
        `attachments` (files attached) and `inline_images` (embedded in the body, with their cid)."""
        return await run(lambda s: s.get_draft(id))

    @server.tool(annotations=WRITE)
    async def update_draft(id: int, to: list[str] | None = None, cc: list[str] | None = None,
                           bcc: list[str] | None = None, subject: str | None = None, body: str | None = None,
                           html_body: str | None = None, add_attachments: list[str] | None = None,
                           remove_attachments: list[str] | None = None,
                           add_inline_images: list[str | InlineImage] | None = None,
                           remove_inline_images: list[str] | None = None,
                           send_at: str | None = None) -> dict:
        """Edit a draft in place (it gets a new `draft_id` — use that afterwards). Omitted fields are kept.
        Attachments and inline images are separate: attachments are files the recipient downloads;
        inline images render inside the body (e.g. a logo). Reference one from markdown as
        `![Logo](logo.png)` or from HTML as `<img src="cid:logo.png">`; otherwise it is placed at
        `position` top/bottom. Passing `body` (markdown) without `html_body` re-renders the HTML.
        Remove items by file name. New files must be inside one of `readable_dirs` (list_accounts);
        use them in place by absolute path. Pass `send_at` (e.g. '2h', 'tomorrow 09:00',
        '2026-11-21T09:00') to schedule/reschedule the draft, or `send_at=""` to unschedule it."""
        return (await run(lambda s: s.update_draft(
            id, source="ai", to=to, cc=cc, bcc=bcc, subject=subject, body=body, html_body=html_body,
            add_attachments=add_attachments, remove_attachments=remove_attachments,
            add_inline_images=inline_arg(add_inline_images),
            remove_inline_images=remove_inline_images, send_at=send_at))).to_dict()

    @server.tool(annotations=DESTRUCTIVE)
    async def delete_draft(id: int) -> dict:
        """Permanently delete a message from the Drafts folder (refuses anything outside Drafts)."""
        await run(lambda s: s.delete_draft(id))
        return {"deleted": id}

    @server.tool(annotations=WRITE)
    async def send_draft(id: int, send_at: str | None = None) -> dict:
        """Send or schedule a saved draft exactly as stored. Pass `send_at` (e.g. '30m', '2h',
        'tomorrow 09:00', '2026-11-21T09:00') to schedule future delivery instead of sending now.
        Obeys ai_send: 'draft' leaves it for the human, 'confirm' queues it for approval, 'allow'
        sends or schedules it and removes the draft when sent. Report `status`."""
        return (await run(lambda s: s.send_draft(id, "ai", send_at=send_at))).to_dict()

    @server.tool(annotations=WRITE)
    async def reschedule(outbox_id: int, send_at: str) -> dict:
        """Change the `send_at` time of a scheduled or pending outbox item (never approves a pending item)."""
        return await run(lambda s: s.reschedule(outbox_id, send_at))

    @server.tool(annotations=WRITE)
    async def create_folder(name: str, account: str | None = None) -> list[dict]:
        """Create a mail folder (use the server's delimiter, see list_folders, for nesting)."""
        return await run(lambda s: s.create_folder(account, name))

    @server.tool(annotations=WRITE)
    async def rename_folder(old: str, new: str, account: str | None = None) -> list[dict]:
        """Rename a user folder (system folders like INBOX/Drafts/Sent are refused)."""
        return await run(lambda s: s.rename_folder(account, old, new))

    @server.tool(annotations=DESTRUCTIVE)
    async def delete_folder(name: str, account: str | None = None) -> list[dict]:
        """Delete an EMPTY user folder. Move its messages out first; system folders are refused."""
        return await run(lambda s: s.delete_folder(account, name))

    @server.tool(annotations=WRITE)
    async def outbox_cancel(outbox_id: int) -> dict:
        """Withdraw a message you queued for approval or cancel a scheduled message before it is sent."""
        await run(lambda s: s.outbox_cancel(outbox_id))
        return {"cancelled": outbox_id}
