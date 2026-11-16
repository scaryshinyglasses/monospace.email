"""Review and approve/reject messages AI agents queued (accounts with ai_send=confirm)."""

from __future__ import annotations

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Markdown, Static


class OutboxScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("y", "approve", "Approve & send"),
                Binding("n", "reject", "Reject")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="form"):
            yield Static("[b]AI outbox[/b]  [dim]messages agents asked to send · y approve · n reject · Esc close[/]")
            yield DataTable(cursor_type="row", id="items")
            with VerticalScroll(id="preview"):
                yield Static("", id="meta")
                yield Markdown("", id="body")
            with Horizontal(classes="row"):
                yield Button("Approve & send", variant="success", id="approve")
                yield Button("Reject", variant="error", id="reject")
                yield Button("Close", id="close")

    @property
    def svc(self):  # type: ignore[no-untyped-def]
        return self.app.svc  # type: ignore[attr-defined]

    def on_mount(self) -> None:
        table = self.query_one("#items", DataTable)
        table.add_columns("#", "Account", "To", "Subject", "Queued")
        self.reload()

    def reload(self) -> None:
        table = self.query_one("#items", DataTable)
        table.clear()
        self.items = {str(i["id"]): i for i in self.svc.outbox()}
        for key, it in self.items.items():
            d = it["draft"]
            table.add_row(key, it["account"], ", ".join(d.get("to", []))[:30], d.get("subject", "")[:40],
                          it["created"], key=key)
        if not self.items:
            self.query_one("#meta", Static).update("Nothing waiting. 🎉")
            self.query_one("#body", Markdown).update("")
        table.focus()

    def _selected(self) -> dict | None:
        table = self.query_one("#items", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self.items.get(key or "")

    @on(DataTable.RowHighlighted, "#items")
    def preview(self) -> None:
        it = self._selected()
        if not it:
            return
        d = it["draft"]
        meta = [f"[dim]To[/]   {escape(', '.join(d.get('to', [])))}"]
        for k in ("cc", "bcc"):
            if d.get(k):
                meta.append(f"[dim]{k.title()}[/]   {escape(', '.join(d[k]))}")
        meta.append(f"[dim]Subj[/] [b]{escape(d.get('subject', ''))}[/b]")
        if d.get("attachments"):
            from pathlib import Path

            files = [f"{Path(f).name} ({Path(f).stat().st_size:,} B)" if Path(f).exists() else f"{f} (missing!)"
                     for f in d["attachments"]]
            meta.append(f"[dim]Files[/] {escape(', '.join(files))}")
        if it.get("error"):
            meta.append(f"[red]last error: {escape(it['error'])}[/]")
        self.query_one("#meta", Static).update("\n".join(meta))
        self.query_one("#body", Markdown).update(d.get("body", ""))

    @on(Button.Pressed, "#approve")
    def action_approve(self) -> None:
        it = self._selected()
        if it:
            self._approve(it["id"])

    @work(thread=True, exclusive=True)
    def _approve(self, item_id: int) -> None:
        try:
            res = self.svc.outbox_approve(item_id)
            self.app.call_from_thread(self.notify, f"Sent · {res.detail}")
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Send failed: {exc}", severity="error")
        self.app.call_from_thread(self.reload)

    @on(Button.Pressed, "#reject")
    def action_reject(self) -> None:
        it = self._selected()
        if it:
            self.svc.outbox_reject(it["id"])
            self.notify(f"Rejected #{it['id']}")
            self.reload()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
