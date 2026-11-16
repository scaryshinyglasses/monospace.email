"""Compose / reply / forward modal. Body is Markdown; sent as plain + HTML alternative."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from monospace.core.compose import Draft, split_addrs


@dataclass
class ComposeResultData:
    account: str
    draft: Draft
    action: str  # "send" | "draft"


class ComposeScreen(ModalScreen[ComposeResultData | None]):
    BINDINGS = [
        Binding("ctrl+s", "submit('send')", "Send", priority=True),
        Binding("ctrl+d", "submit('draft')", "Save draft", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, draft: Draft, account: str, accounts: list[tuple[str, str]]) -> None:
        super().__init__()
        self.draft, self.account, self.accounts = draft, account, accounts

    def compose(self) -> ComposeResult:
        d = self.draft
        with Vertical(classes="form"):
            title = "Reply" if d.in_reply_to else ("Forward" if d.subject.startswith("Fwd:") else "New message")
            yield Static(f"[b]{title}[/b]  [dim]Ctrl+S send · Ctrl+D save draft · Esc cancel · body is Markdown[/]")
            if len(self.accounts) > 1:
                yield Select([(f"{n} <{e}>", n) for n, e in self.accounts], value=self.account,
                             allow_blank=False, id="from")
            yield Input(", ".join(d.to), placeholder="To", id="to")
            yield Input(", ".join(d.cc), placeholder="Cc", id="cc")
            yield Input(", ".join(d.bcc), placeholder="Bcc", id="bcc")
            yield Input(d.subject, placeholder="Subject", id="subject")
            yield TextArea(d.body, id="body", soft_wrap=True, show_line_numbers=False, tab_behavior="indent")
            yield Input(", ".join(d.attachments), placeholder="Attachments: comma-separated file paths (~ ok)",
                        id="attachments")
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
        missing = [p for p in attachments if not Path(p).expanduser().is_file()]
        if missing:
            return f"Attachment not found: {', '.join(missing)}"
        d = Draft(to=split_addrs(self._value("to")), cc=split_addrs(self._value("cc")),
                  bcc=split_addrs(self._value("bcc")), subject=self._value("subject"),
                  body=self.query_one("#body", TextArea).text, attachments=attachments,
                  in_reply_to=self.draft.in_reply_to, references=self.draft.references)
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
        self.dismiss(ComposeResultData(account, result, action))

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id in ("send", "draft"):
            self.action_submit(event.button.id)
