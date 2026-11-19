"""SQLite side of tags (mixed into ``Store``).

Tags are keyed by the Message-ID *header*, not the rowid, so they survive moves, UIDVALIDITY
resets and draft edits (which APPEND a new copy with the same Message-ID). ``source`` says who
owns a row: ``local`` rows are ours alone; ``gmail`` / ``keyword`` rows mirror the server and are
rewritten on every sync of a message.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monospace.core.store import Envelope

TAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
  account TEXT NOT NULL, message_id TEXT NOT NULL, tag TEXT NOT NULL COLLATE NOCASE,
  source TEXT NOT NULL DEFAULT 'local', PRIMARY KEY (account, message_id, tag));
CREATE INDEX IF NOT EXISTS tags_by_tag ON tags(account, tag);
CREATE TABLE IF NOT EXISTS meta (account TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY (account, key));
"""

SERVER_SOURCES = ("gmail", "keyword")


class TagStoreMixin:
    db: sqlite3.Connection
    lock: threading.RLock

    # ---- writes ----------------------------------------------------------
    def set_server_tags(self, account: str, message_id: str, tags: list[str], source: str) -> None:
        """Replace the server-sourced tags of one message (local rows are left alone)."""
        self.set_server_tags_bulk(account, [(message_id, tags)], source)

    def set_server_tags_bulk(self, account: str, rows: list[tuple[str, list[str]]], source: str) -> None:
        rows = [(mid, tags) for mid, tags in rows if mid]
        if not rows:
            return
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.executemany("DELETE FROM tags WHERE account=? AND message_id=? AND source IN (?,?)",
                                    [(account, mid, *SERVER_SOURCES) for mid, _ in rows])
                self.db.executemany(
                    "INSERT INTO tags(account, message_id, tag, source) VALUES(?,?,?,?)"
                    " ON CONFLICT(account, message_id, tag) DO UPDATE SET source=excluded.source",
                    [(account, mid, t, source) for mid, tags in rows for t in tags])
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def uid_rows(self, account: str, folder: str) -> list[tuple[int, str, list[str]]]:
        """(uid, Message-ID, flags) for every cached message in a folder."""
        with self.lock:
            rows = self.db.execute("SELECT uid, message_id, flags FROM messages WHERE account=? AND folder=?",
                                   (account, folder)).fetchall()
        return [(r[0], r[1], json.loads(r[2] or "[]")) for r in rows]

    def add_tag_rows(self, account: str, message_id: str, tags: list[str], source: str) -> None:
        with self.lock:
            self.db.executemany(
                "INSERT INTO tags(account, message_id, tag, source) VALUES(?,?,?,?)"
                " ON CONFLICT(account, message_id, tag) DO NOTHING",
                [(account, message_id, t, source) for t in tags])

    def remove_tag_rows(self, account: str, message_id: str, tags: list[str]) -> None:
        with self.lock:
            self.db.executemany("DELETE FROM tags WHERE account=? AND message_id=? AND tag=?",
                                [(account, message_id, t) for t in tags])

    # ---- reads -----------------------------------------------------------
    def tags_for(self, account: str, message_ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        ids = [m for m in set(message_ids) if m]
        with self.lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for mid, tag in self.db.execute(
                        f"SELECT message_id, tag FROM tags WHERE account=? AND message_id IN ({marks})",
                        (account, *chunk)):
                    out.setdefault(mid, []).append(tag)
        return {k: sorted(v, key=str.casefold) for k, v in out.items()}

    def attach_tags(self, envs: list["Envelope"]) -> list["Envelope"]:
        by_account: dict[str, list["Envelope"]] = {}
        for e in envs:
            by_account.setdefault(e.account, []).append(e)
        for account, group in by_account.items():
            found = self.tags_for(account, [e.message_id for e in group])
            for e in group:
                e.tags = found.get(e.message_id, [])
        return envs

    def all_tags(self, account: str) -> list[dict]:
        """Every tag on the account with how many cached messages carry it."""
        with self.lock:
            rows = self.db.execute(
                "SELECT t.tag, count(DISTINCT m.id) FROM tags t JOIN messages m"
                " ON m.account = t.account AND m.message_id = t.message_id"
                " WHERE t.account=? GROUP BY t.tag COLLATE NOCASE ORDER BY t.tag COLLATE NOCASE",
                (account,)).fetchall()
        return [{"tag": r[0], "count": r[1]} for r in rows]

    # ---- per-account metadata ------------------------------------------
    def get_meta(self, account: str, key: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE account=? AND key=?", (account, key)).fetchone()
        return row[0] if row else None

    def set_meta(self, account: str, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta(account, key, value) VALUES(?,?,?)"
                            " ON CONFLICT(account, key) DO UPDATE SET value=excluded.value", (account, key, value))
