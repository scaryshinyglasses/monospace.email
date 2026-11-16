"""Message reader: headers, rendered body blocks (Markdown + images), attachments."""

from __future__ import annotations

from rich.markup import escape
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from monospace.core.mime import ParsedMessage
from monospace.core.store import Envelope
from monospace.render.html_blocks import ImageBlock, TextBlock, text_to_blocks, to_blocks
from monospace.ui.widgets.attachment_bar import AttachmentBar
from monospace.ui.widgets.image_view import ImageView


def header_markup(pm: ParsedMessage) -> str:
    lines = [f"[b]{escape(pm.subject or '(no subject)')}[/b]",
             f"[dim]From[/]  {escape(pm.from_)}",
             f"[dim]To[/]    {escape(', '.join(pm.to))}"]
    if pm.cc:
        lines.append(f"[dim]Cc[/]    {escape(', '.join(pm.cc))}")
    if pm.date:
        lines.append(f"[dim]Date[/]  {escape(pm.date[:16].replace('T', ' '))}")
    return "\n".join(lines)


class Reader(VerticalScroll):
    can_focus = True

    def __init__(self) -> None:
        super().__init__(id="reader")
        self.envelope: Envelope | None = None
        self.parsed: ParsedMessage | None = None
        self.allow_remote = False
        self.images_enabled = True
        self.show_html = True

    def on_mount(self) -> None:
        self.clear_message()

    def clear_message(self, note: str = "Select a message — j/k to move, Enter or click to open.") -> None:
        self.envelope = self.parsed = None
        self.remove_children()
        self.mount(Static(note, id="empty"))

    def show(self, env: Envelope, pm: ParsedMessage, *, allow_remote: bool | None = None) -> None:
        same = self.envelope is not None and self.envelope.id == env.id
        if allow_remote is not None:
            self.allow_remote = allow_remote
        elif not same:
            self.allow_remote = self.app.remote_images_default  # type: ignore[attr-defined]
        self.envelope, self.parsed = env, pm
        self.remove_children()
        blocks = to_blocks(pm.html) if (pm.html and self.show_html) else text_to_blocks(pm.best_text())
        widgets: list = [Static(header_markup(pm), id="headers")]
        blocked = sum(1 for b in blocks if isinstance(b, ImageBlock) and b.is_remote) if not self.allow_remote else 0
        if blocked and self.images_enabled:
            widgets.append(Static(f"{blocked} remote image(s) blocked to protect your privacy — press "
                                  "[b]i[/b] to load them.", classes="note"))
        for b in blocks:
            if isinstance(b, TextBlock):
                widgets.append(Markdown(b.markdown, open_links=False))
            else:
                widgets.append(ImageView(b, pm, allow_remote=self.allow_remote, enabled=self.images_enabled))
        if not blocks:
            widgets.append(Static("(empty message)", classes="note"))
        if pm.attachments:
            widgets.append(AttachmentBar(pm.attachments))
        self.mount_all(widgets)
        self.scroll_home(animate=False)

    def toggle_remote(self) -> None:
        if self.envelope and self.parsed:
            self.show(self.envelope, self.parsed, allow_remote=not self.allow_remote)

    def toggle_html(self) -> None:
        self.show_html = not self.show_html
        if self.envelope and self.parsed:
            self.show(self.envelope, self.parsed)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        self.app.open_link(event.href)  # type: ignore[attr-defined]
