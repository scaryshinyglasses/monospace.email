"""Three-pane mail screen: folders │ message list / reader."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header, Input

from monospace.core import compose
from monospace.core.compose import Draft
from monospace.ui.screens.prompt import PromptScreen
from monospace.ui.widgets.attachment_bar import AttachmentBar
from monospace.ui.widgets.folder_tree import FolderTree
from monospace.ui.widgets.message_list import MessageList
from monospace.ui.widgets.reader import Reader


class MainScreen(Screen):
    BINDINGS = [
        Binding("c", "compose", "Compose"),
        Binding("r", "reply(False)", "Reply"),
        Binding("R", "reply(True)", "Reply all", show=False),
        Binding("f", "forward", "Forward", show=False),
        Binding("e", "edit_draft", "Edit draft", show=False),
        Binding("S", "schedule_draft", "Schedule draft", show=False),
        Binding("N", "new_folder", "New folder", show=False),
        Binding("E", "rename_folder", "Rename folder", show=False),
        Binding("X", "delete_folder", "Delete folder", show=False),
        Binding("a", "archive", "Archive"),
        Binding("d,delete,number_sign", "trash", "Delete"),
        Binding("u", "toggle_read", "Unread", show=False),
        Binding("asterisk", "toggle_flag", "Flag", show=False),
        Binding("t", "tag", "Tags"),
        Binding("slash", "search", "Search"),
        Binding("i", "remote_images", "Images"),
        Binding("H", "toggle_html", "HTML/text", show=False),
        Binding("ctrl+r,g", "sync", "Sync"),
        Binding("O", "outbox", "Outbox"),
        Binding("A", "add_account", "Add account", show=False),
        Binding("j", "cursor(1)", "Down", show=False),
        Binding("k", "cursor(-1)", "Up", show=False),
        Binding("escape", "clear_search", "Back", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.account: str | None = None
        self.folder = "INBOX"
        self.tag = ""  # set when a Tags leaf is chosen: list that tag across folders
        self.searching = ""
        self._preview: Timer | None = None

    @property
    def svc(self):  # type: ignore[no-untyped-def]
        return self.app.svc  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield FolderTree()
            with Vertical(id="content"):
                yield Input(placeholder="Search all mail… (Enter to search, Esc to go back)", id="search")
                yield MessageList()
                yield Reader()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Reader).images_enabled = self.app.images  # type: ignore[attr-defined]
        if self.svc.cfg.accounts:
            self.account = self.svc.cfg.accounts[0].name
        self.refresh_from_cache()
        self.query_one(MessageList).focus()

    # ---- data ----------------------------------------------------------
    def refresh_from_cache(self) -> None:
        self._populate_tree()
        if self.searching:
            return
        self.sub_title = f"{self.account or 'no account'} · {'#' + self.tag if self.tag else self.folder}"
        if self.account:
            envs = (self.svc.tagged(self.account, self.tag, limit=500) if self.tag
                    else self.svc.list(self.account, self.folder, limit=500))
            self.query_one(MessageList).load(envs)

    def _populate_tree(self) -> None:
        accts = self.svc.cfg.accounts
        self.query_one(FolderTree).populate(accts, {a.name: self.svc.store.folders(a.name) for a in accts},
                                            {a.name: self.svc.store.all_tags(a.name) for a in accts})

    @work(thread=True, exclusive=True, group="sync")
    def sync_current(self) -> None:
        if not self.account:
            return
        stats = self.svc.sync(self.account, [self.folder])
        self.app.call_from_thread(self.refresh_from_cache)
        for err in stats.errors:
            self.app.call_from_thread(self.notify, err, severity="error", timeout=8)

    @on(FolderTree.Chosen)
    def folder_chosen(self, event: FolderTree.Chosen) -> None:
        self.account, self.tag, self.searching = event.account, event.tag, ""
        self.folder = event.folder or self.folder
        self.query_one(Reader).clear_message()
        self.refresh_from_cache()
        if not self.tag:
            self.sync_current()

    # ---- opening -------------------------------------------------------
    @on(DataTable.RowHighlighted, "#messages")
    def highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._preview:
            self._preview.stop()
        if event.row_key and event.row_key.value:
            msg_id = int(event.row_key.value)
            self._preview = self.set_timer(0.25, lambda: self.open_message(msg_id))

    @on(MessageList.Opened)
    def opened(self, event: MessageList.Opened) -> None:
        self.open_message(event.msg_id)
        self.query_one(Reader).focus()

    @work(thread=True, exclusive=True, group="open")
    def open_message(self, msg_id: int) -> None:
        try:
            env, pm = self.svc.read(msg_id, mark_read=True)
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Could not open message: {exc}", severity="error")
            return
        self.app.call_from_thread(self._show, env, pm)

    def _show(self, env, pm) -> None:  # type: ignore[no-untyped-def]
        self.query_one(Reader).show(env, pm)
        self.query_one(MessageList).update_envelope(env)
        self._populate_tree()

    def _current(self) -> int | None:
        cur = self.query_one(MessageList).current_id()
        if cur is not None:
            return cur
        reader = self.query_one(Reader)
        return reader.envelope.id if reader.envelope else None

    # ---- actions -------------------------------------------------------
    def action_cursor(self, delta: int) -> None:
        table = self.query_one(MessageList)
        table.move_cursor(row=max(0, table.cursor_row + delta))

    def action_sync(self) -> None:
        self.notify("Syncing…", timeout=2)
        self.sync_current()

    def action_search(self) -> None:
        box = self.query_one("#search", Input)
        box.add_class("visible")
        box.focus()

    @on(Input.Submitted, "#search")
    def run_search(self, event: Input.Submitted) -> None:
        self.searching = event.value.strip()
        if not self.searching:
            return self.action_clear_search()
        results = self.svc.search(self.searching, limit=200)
        self.sub_title = f"search: {self.searching} · {len(results)} result(s)"
        table = self.query_one(MessageList)
        table.load(results, keep_cursor=False)
        table.focus()

    def action_clear_search(self) -> None:
        box = self.query_one("#search", Input)
        if not self.searching and "visible" not in box.classes:
            return
        box.value, self.searching = "", ""
        box.remove_class("visible")
        self.refresh_from_cache()
        self.query_one(MessageList).focus()

    def action_remote_images(self) -> None:
        self.query_one(Reader).toggle_remote()

    def action_toggle_html(self) -> None:
        self.query_one(Reader).toggle_html()

    def action_compose(self) -> None:
        self.app.open_compose(Draft(), self.account)  # type: ignore[attr-defined]

    def action_reply(self, reply_all: bool) -> None:
        reader = self.query_one(Reader)
        if not (reader.envelope and reader.parsed):
            return self.notify("Open a message first")
        acct = self.svc.account(reader.envelope.account)
        draft = compose.reply(reader.parsed, "", acct.email, reply_all=reply_all)
        self.app.open_compose(draft, acct.name)  # type: ignore[attr-defined]

    def action_forward(self) -> None:
        reader = self.query_one(Reader)
        if not (reader.envelope and reader.parsed):
            return self.notify("Open a message first")
        self.app.open_compose(compose.forward(reader.parsed, "", []), reader.envelope.account)  # type: ignore

    def action_edit_draft(self) -> None:
        msg_id = self._current()
        if msg_id is None:
            return self.notify("Select a draft first (Drafts folder)")
        self.app.edit_draft(msg_id)  # type: ignore[attr-defined]

    def action_schedule_draft(self) -> None:
        msg_id = self._current()
        if msg_id is None:
            return self.notify("Select a draft first (Drafts folder)")
        env = self.svc.envelope(msg_id)
        if env.folder != self.svc.account(env.account).folder("drafts"):
            return self.notify("Only messages in Drafts can be scheduled (press c to compose)", severity="warning")
        self.app.push_screen(
            PromptScreen("Schedule draft — send at (e.g. 30m, 2h, tomorrow 09:00, YYYY-MM-DD HH:MM):", "", "Schedule"),
            lambda when: when and self.app.schedule_draft(msg_id, when),  # type: ignore[attr-defined]
        )

    # ---- folders -------------------------------------------------------
    def action_new_folder(self) -> None:
        if not self.account:
            return
        self.app.push_screen(PromptScreen(f"New folder in [b]{self.account}[/b]:", "", "Create"),
                             lambda name: name and self._folder_op("create", name))

    def action_rename_folder(self) -> None:
        if not self.account:
            return
        old = self.folder
        self.app.push_screen(PromptScreen(f"Rename [b]{old}[/b] to:", old, "Rename"),
                             lambda new: new and new != old and self._folder_op("rename", old, new))

    def action_delete_folder(self) -> None:
        if not self.account:
            return
        name = self.folder
        self.app.push_screen(PromptScreen(f"Delete folder [b]{name}[/b]? It must be empty.", None, "Delete"),
                             lambda ok: ok and self._folder_op("delete", name))

    @work(thread=True, group="folders")
    def _folder_op(self, op: str, *names: str) -> None:
        try:
            if op == "create":
                self.svc.create_folder(self.account, names[0])
            elif op == "rename":
                self.svc.rename_folder(self.account, *names)
                if self.folder == names[0]:
                    self.folder = names[1]
            else:
                self.svc.delete_folder(self.account, names[0])
                if self.folder == names[0]:
                    self.folder = "INBOX"
            self.app.call_from_thread(self.notify, f"Folder {op}d: {names[-1]}")
        except Exception as exc:
            self.app.call_from_thread(self.notify, str(exc), severity="error")
        self.app.call_from_thread(self.refresh_from_cache)

    def action_archive(self) -> None:
        self._mutate("archive")

    def action_trash(self) -> None:
        self._mutate("trash")

    def action_toggle_read(self) -> None:
        self._mutate("read")

    def action_toggle_flag(self) -> None:
        self._mutate("flag")

    # ---- tags ----------------------------------------------------------
    def action_tag(self) -> None:
        msg_id = self._current()
        if msg_id is None:
            return self.notify("Select a message first")
        current = ", ".join(self.svc.envelope(msg_id).tags)
        self.app.push_screen(PromptScreen("Tags (comma-separated; empty clears):", current, "Save", allow_empty=True),
                             lambda value: value is not None and value != current and self._set_tags(msg_id, value))

    @work(thread=True, group="mutate")
    def _set_tags(self, msg_id: int, value: str) -> None:
        try:
            env = self.svc.set_tags(msg_id, value)
            self.app.call_from_thread(self.notify, "Tags: " + (", ".join(env.tags) or "(none)"))
            reader = self.query_one(Reader)
            if reader.envelope and reader.envelope.id == msg_id and reader.parsed:
                self.app.call_from_thread(reader.show, env, reader.parsed)
        except Exception as exc:
            self.app.call_from_thread(self.notify, str(exc), severity="error")
        self.app.call_from_thread(self.refresh_from_cache)

    @work(thread=True, group="mutate")
    def _mutate(self, what: str) -> None:
        msg_id = self._current()
        if msg_id is None:
            return
        try:
            env = self.svc.envelope(msg_id)
            if what in ("archive", "trash"):
                dest = self.svc.move(msg_id, what)
                self.app.call_from_thread(self.notify, f"Moved to {dest}")
                self.app.call_from_thread(self.query_one(Reader).clear_message)
            elif what == "read":
                self.svc.mark(msg_id, read=env.unread)  # toggle
            else:
                self.svc.mark(msg_id, flagged=not env.flagged)
        except Exception as exc:
            self.app.call_from_thread(self.notify, str(exc), severity="error")
        self.app.call_from_thread(self.refresh_from_cache)

    @on(AttachmentBar.Chosen)
    def attachment(self, event: AttachmentBar.Chosen) -> None:
        reader = self.query_one(Reader)
        if reader.envelope:
            self.app.save_attachment(reader.envelope.id, event.index)  # type: ignore[attr-defined]

    def action_outbox(self) -> None:
        self.app.open_outbox()  # type: ignore[attr-defined]

    def action_add_account(self) -> None:
        self.app.open_account_wizard()  # type: ignore[attr-defined]
