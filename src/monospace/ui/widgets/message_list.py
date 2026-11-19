"""Message list (DataTable). Highlight = preview; Enter / click = open."""

from __future__ import annotations

from rich.text import Text
from textual.message import Message
from textual.widgets import DataTable

from monospace.core.store import Envelope
from monospace.ui.glyphs import glyph


def short_date(iso: str) -> str:
    return iso[:16].replace("T", " ") if iso else ""


def sender_name(sender: str) -> str:
    first = sender.split(",")[0]
    return first.split("<")[0].strip().strip('"') or first.strip("<> ")


class MessageList(DataTable):
    class Opened(Message):
        def __init__(self, msg_id: int) -> None:
            super().__init__()
            self.msg_id = msg_id

    def __init__(self) -> None:
        super().__init__(cursor_type="row", zebra_stripes=False, id="messages")
        self.envelopes: dict[int, Envelope] = {}

    def on_mount(self) -> None:
        self.add_column(" ", key="mark", width=4)
        self.add_column("From", key="from", width=24)
        self.add_column("Subject", key="subject")
        self.add_column("Date", key="date", width=16)

    def load(self, envs: list[Envelope], keep_cursor: bool = True) -> None:
        current = self.current_id() if keep_cursor else None
        self.clear()
        self.envelopes = {e.id: e for e in envs}
        for e in envs:
            self.add_row(*self._cells(e), key=str(e.id))
        if current in self.envelopes:
            self.move_cursor(row=self.get_row_index(str(current)))

    def _cells(self, e: Envelope) -> tuple:
        style = "bold" if e.unread else ""
        mark = (glyph("unread") if e.unread else " ") + \
            (glyph("flag") if e.flagged else (glyph("attach") if e.has_attachments else " "))
        subject = Text(e.subject or "(no subject)", style=style)
        for t in e.tags:
            subject.append(f"  #{t}", style="dim italic")
        return (Text(mark, style="bold $accent" if e.unread else ""), Text(sender_name(e.sender), style=style),
                subject, Text(short_date(e.date), style="dim"))

    def update_envelope(self, e: Envelope) -> None:
        if str(e.id) not in self.rows:
            return
        self.envelopes[e.id] = e
        for key, cell in zip(("mark", "from", "subject", "date"), self._cells(e)):
            self.update_cell(str(e.id), key, cell)

    def current_id(self) -> int | None:
        if not self.row_count:
            return None
        try:
            key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key
            return int(key.value) if key.value else None
        except Exception:
            return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if event.row_key.value:
            self.post_message(self.Opened(int(event.row_key.value)))
