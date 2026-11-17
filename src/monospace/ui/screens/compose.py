"""Compose / reply / forward / edit-draft modal. Body is Markdown; sent as plain + HTML alternative.

Attachments (downloaded by the recipient) and inline images (shown in the body) are separate fields.
Editing a stored draft keeps its original HTML part until the body text is changed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from monospace.core import compose as composer
from monospace.core.compose import Draft, split_addrs


@dataclass
class ComposeResultData:
    account: str
    draft: Draft
    action: str  # "send" | "draft"
    draft_id: int | None = None  # editing this stored draft


class ComposeScreen(ModalScreen[ComposeResultData | None]):
    BINDINGS = [
        Binding("ctrl+s", "submit('send')", "Send", priority=True),
        Binding("ctrl+d", "submit('draft')", "Save draft", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, draft: Draft, account: str, accounts: list[tuple[str, str]],
                 draft_id: int | None = None) -> None:
        super().__init__()
        self.draft, self.account, self.accounts, self.draft_id = draft, account, accounts, draft_id
        self._cid_by_path = {p: cid for cid, p in draft.inline_images.items()}
        self._html_replaced = False
        if draft.html_body and composer.html_extra_words(draft.html_body, draft.body):
            # The stored HTML says things the text part doesn't: show the HTML's text and rebuild the
            # HTML from what's on screen, so what you review is what gets sent.
            from monospace.core.mime import html_to_text

            draft.body, draft.html_body, self._html_replaced = html_to_text(draft.html_body), "", True

    def compose(self) -> ComposeResult:
        d = self.draft
        with Vertical(classes="form"):
            title = ("Edit draft" if self.draft_id else "Reply" if d.in_reply_to
                     else "Forward" if d.subject.startswith("Fwd:") else "New message")
            yield Static(f"[b]{title}[/b]  [dim]Ctrl+S send · Ctrl+D save draft · Esc cancel · body is Markdown[/]")
            if len(self.accounts) > 1:
                yield Select([(f"{n} <{e}>", n) for n, e in self.accounts], value=self.account,
                             allow_blank=False, id="from")
            yield Input(", ".join(d.to), placeholder="To", id="to")
            yield Input(", ".join(d.cc), placeholder="Cc", id="cc")
            yield Input(", ".join(d.bcc), placeholder="Bcc", id="bcc")
            yield Input(d.subject, placeholder="Subject", id="subject")
            yield TextArea(d.body, id="body", soft_wrap=True, show_line_numbers=False, tab_behavior="indent")
            yield Input(", ".join(d.attachments), placeholder="Attachments (downloadable): comma-separated paths",
                        id="attachments")
            inline = ", ".join(composer.format_inline_arg(p, d.inline_layout.get(cid))
                               for cid, p in d.inline_images.items())
            yield Input(inline, id="inline",
                        placeholder="Inline images (in the body): logo.png;width=120;position=top, … "
                                    "or ![Logo](logo.png) in the body")
            if self._html_replaced:
                yield Static("[b $warning]The HTML part differed from the text — showing the HTML's text; "
                             "the HTML will be rebuilt from this body.[/]")
            elif self.draft_id and d.html_body:
                yield Static("[dim]This draft has an HTML part; it is kept unless you change the body text.[/]")
            yield Label("", classes="error", id="error")
            with Horizontal(classes="row"):
                yield Button("Send", variant="primary", id="send")
                yield Button("Save draft", id="draft")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if self.draft.to:
            body = self.query_one("#body", TextArea)
            body.focus()
            body.move_cursor((0, 0))
        else:
            self.query_one("#to", Input).focus()

    def _value(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def _collect(self) -> Draft | str:
        attachments = [p.strip() for p in self._value("attachments").split(",") if p.strip()]
        try:
            inline = [composer.parse_inline_arg(p) for p in self._value("inline").split(",") if p.strip()]
        except ValueError as exc:
            return str(exc)
        missing = [p for p in attachments + [s["path"] for s in inline] if not Path(p).expanduser().is_file()]
        if missing:
            return f"File not found: {', '.join(missing)}"
        body = self.query_one("#body", TextArea).text
        d = Draft(to=split_addrs(self._value("to")), cc=split_addrs(self._value("cc")),
                  bcc=split_addrs(self._value("bcc")), subject=self._value("subject"),
                  body=body, attachments=attachments, signature=self.draft.signature,
                  in_reply_to=self.draft.in_reply_to, references=self.draft.references,
                  message_id=self.draft.message_id)
        for spec in inline:
            path = str(Path(spec.pop("path")).expanduser())
            cid = self._cid_by_path.get(path)
            if cid:  # an image the draft already had: keep its cid so the stored HTML still points at it
                d.inline_images[cid] = path
                if spec:
                    d.inline_layout[cid] = spec
                continue
            try:
                composer.add_inline(d, path, spec)
            except ValueError as exc:
                return str(exc)
        if body == self.draft.body and self.draft.html_body:
            d.html_body = composer.drop_inline_refs(self.draft.html_body,
                                                    set(self.draft.inline_images) - set(d.inline_images))
        return d

    def action_submit(self, action: str) -> None:
        result = self._collect()
        if isinstance(result, str):
            self.query_one("#error", Label).update(result)
            return
        if action == "send" and not result.recipients():
            self.query_one("#error", Label).update("Add at least one recipient.")
            return
        account = self.account
        if self.query("#from"):
            account = str(self.query_one("#from", Select).value)
        self.dismiss(ComposeResultData(account, result, action, self.draft_id))

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id in ("send", "draft"):
            self.action_submit(event.button.id)
