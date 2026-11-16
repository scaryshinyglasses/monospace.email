"""Clickable attachment buttons under a message."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import HorizontalGroup
from textual.message import Message
from textual.widgets import Button

from monospace.core.mime import Part


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n} B"


class AttachmentBar(HorizontalGroup):
    class Chosen(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, parts: list[Part]) -> None:
        super().__init__()
        self.parts = parts

    def compose(self) -> ComposeResult:
        for i, p in enumerate(self.parts):
            yield Button(f"📎 {p.filename} · {human_size(p.size)}", id=f"att-{i}", compact=True,
                         tooltip="save (and open if a desktop is available)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id and event.button.id.startswith("att-"):
            self.post_message(self.Chosen(int(event.button.id[4:])))
