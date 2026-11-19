"""Small modal: ask for a line of text, or confirm (no input) — returns the text / "yes" / None."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str | None = "", ok: str = "OK", allow_empty: bool = False) -> None:
        super().__init__()
        self.title_text, self.value, self.ok = title, value, ok  # value None → confirm-only
        self.allow_empty = allow_empty  # True → an empty field returns "" (Cancel still returns None)

    def compose(self) -> ComposeResult:
        with Vertical(classes="form"):
            yield Static(self.title_text)
            if self.value is not None:
                yield Input(self.value, id="value")
            with Horizontal(classes="row"):
                yield Button(self.ok, variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        (self.query_one("#value", Input) if self.value is not None else self.query_one("#cancel", Button)).focus()

    def _ok(self) -> None:
        if self.value is None:
            self.dismiss("yes")
            return
        text = self.query_one("#value", Input).value.strip()
        self.dismiss(text if (text or self.allow_empty) else None)

    @on(Input.Submitted)
    def submitted(self) -> None:
        self._ok()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self._ok() if event.button.id == "ok" else self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
