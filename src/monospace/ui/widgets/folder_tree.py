"""Accounts → folders tree with unread counts."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Tree

from monospace.ui.glyphs import glyph

ORDER = {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 4, "trash": 5}


class FolderTree(Tree[tuple[str, str]]):
    class Chosen(Message):
        def __init__(self, account: str, folder: str) -> None:
            super().__init__()
            self.account, self.folder = account, folder

    def __init__(self) -> None:
        super().__init__("accounts", id="folders")
        self.show_root = False
        self.guide_depth = 2

    def populate(self, accounts: list, folders_by_account: dict[str, list]) -> None:  # type: ignore[type-arg]
        selected = self.cursor_node.data if self.cursor_node else None
        self.clear()
        for acct in accounts:
            node = self.root.add(f"[b]{acct.name}[/b] [dim]{acct.email}[/dim]", data=(acct.name, ""), expand=True)
            roles = {v: k for k, v in acct.folders.items()}
            roles.setdefault("INBOX", "inbox")
            for folder_name in ("drafts", "sent", "archive", "junk", "trash"):
                roles.setdefault(acct.folder(folder_name), folder_name)
            folders = folders_by_account.get(acct.name, [])
            folders.sort(key=lambda f: (ORDER.get(roles.get(f.name, ""), 9), f.name.lower()))
            for f in folders:
                role = roles.get(f.name, "")
                label = "Inbox" if f.name == "INBOX" else f.name
                count = f" [b $accent]{f.unread}[/]" if f.unread else ""
                leaf = node.add_leaf(f"{glyph(role or 'folder')} {label}{count}", data=(acct.name, f.name))
                if selected == (acct.name, f.name):
                    self.move_cursor(leaf)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        event.stop()
        data = event.node.data
        if data and data[1]:
            self.post_message(self.Chosen(*data))
