"""Review and approve/reject AI-queued messages (ai_send=confirm) and manage scheduled emails/drafts."""

from __future__ import annotations

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from monospace.ui.screens.prompt import PromptScreen


class OutboxScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("y", "approve", "Approve / Send now"),
        Binding("s", "reschedule", "Reschedule"),
        Binding("n", "reject", "Reject / Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="form"):
            yield Static("[b]Outbox & Scheduled[/b]  [dim]y approve/send now · s reschedule · n reject/cancel · "
                         "Esc close[/]")
            yield DataTable(cursor_type="row", id="items")
            with VerticalScroll(id="preview"):
                yield Static("", id="meta")
                yield Static("", id="body", markup=False)  # untrusted: never interpret markup
            with Horizontal(classes="row"):
                yield Button("Approve / Send now", variant="success", id="approve")
                yield Button("Reschedule", id="reschedule")
                yield Button("Reject / Cancel", variant="error", id="reject")
                yield Button("Close", id="close")

    @property
    def svc(self):  # type: ignore[no-untyped-def]
        return self.app.svc  # type: ignore[attr-defined]

    def on_mount(self) -> None:
        table = self.query_one("#items", DataTable)
        table.add_columns("#", "Account", "Status", "Send at", "To", "Subject")
        self.reload()

    def reload(self) -> None:
        table = self.query_one("#items", DataTable)
        table.clear()
        self.items = {str(i["id"]): i for i in self.svc.outbox(include_scheduled=True)}
        for key, it in self.items.items():
            d = it["draft"]
            send_at = (it.get("send_at") or "")[:16].replace("T", " ") or "now (on approve)"
            table.add_row(key, it["account"], it["status"], send_at,
                          ", ".join(d.get("to", []))[:26], d.get("subject", "")[:36], key=key)
        if not self.items:
            self.query_one("#meta", Static).update("Nothing waiting or scheduled. 🎉")
            self.query_one("#body", Static).update("")
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
        meta = [f"[dim]To[/]     {escape(', '.join(d.get('to', [])))}"]
        for k in ("cc", "bcc"):
            if d.get(k):
                meta.append(f"[dim]{k.title():6}[/] {escape(', '.join(d[k]))}")
        meta.append(f"[dim]Subj[/]   [b]{escape(d.get('subject', ''))}[/b]")
        if it.get("send_at"):
            meta.append(f"[dim]Send[/]   [$accent]{escape(it['send_at'])}[/] ({escape(it['status'])})")
        if d.get("attachments"):
            from pathlib import Path

            files = [f"{Path(f).resolve()} ({Path(f).stat().st_size:,} B)" if Path(f).exists()
                     else f"{f} (missing!)" for f in d["attachments"]]
            meta += [f"[dim]File[/]   {escape(f)}" for f in files]
        for f in (d.get("inline_images") or {}).values():
            meta.append(f"[dim]Inline[/] {escape(str(f))}")
        if it.get("error"):
            meta.append(f"[red]last error: {escape(it['error'])}[/]")
        self.query_one("#meta", Static).update("\n".join(meta))
        try:  # what recipients will actually see (sanitized HTML as text, links spelled out)
            text = self.svc.outbox_review(it)
        except Exception as exc:
            text = f"(could not render preview: {exc})"
        self.query_one("#body", Static).update(text)

    @on(Button.Pressed, "#approve")
    def action_approve(self) -> None:
        it = self._selected()
        if it:
            self._approve(it["id"], send_now=it["status"] == "scheduled")

    @work(thread=True, exclusive=True)
    def _approve(self, item_id: int, *, send_now: bool = False) -> None:
        try:
            res = self.svc.outbox_approve(item_id, send_now=send_now)
            self.app.call_from_thread(self.notify, f"✓ {res.status}: {res.detail}")
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Send failed: {exc}", severity="error")
        self.app.call_from_thread(self.reload)

    @on(Button.Pressed, "#reschedule")
    def action_reschedule(self) -> None:
        it = self._selected()
        if not it:
            return

        def picked(when: str | None) -> None:
            if not when:
                return
            try:
                updated = self.svc.reschedule(it["id"], when)
                self.notify(f"Rescheduled #{it['id']} for {updated['send_at']}")
                self.reload()
            except Exception as exc:
                self.notify(f"Could not reschedule: {exc}", severity="error")

        self.app.push_screen(
            PromptScreen("Send at (e.g. 30m, 2h, tomorrow 09:00, YYYY-MM-DD HH:MM):",
                         it.get("send_at") or "", "Schedule"),
            picked,
        )

    @on(Button.Pressed, "#reject")
    def action_reject(self) -> None:
        it = self._selected()
        if it:
            if it["status"] == "scheduled":
                self.svc.outbox_cancel(it["id"])
                self.notify(f"Cancelled #{it['id']}")
            else:
                self.svc.outbox_reject(it["id"])
                self.notify(f"Rejected #{it['id']}")
            self.reload()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
