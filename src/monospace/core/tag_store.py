"""SQLite side of tags (mixed into ``Store``).

Tags are keyed by Message-ID header + sender (``msg_key``), not the rowid, so they survive moves,
UIDVALIDITY resets and draft edits (which APPEND a new copy with the same Message-ID). The sender is
part of the key because anyone can send mail reusing a Message-ID: without it, a stranger's message
would inherit (and untagging it would strip) the tags of the message it imitates. ``source`` says who
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
  account TEXT NOT NULL, msg_key TEXT NOT NULL, tag TEXT NOT NULL COLLATE NOCASE,
  source TEXT NOT NULL DEFAULT 'local', PRIMARY KEY (account, msg_key, tag));
CREATE INDEX IF NOT EXISTS tags_by_tag ON tags(account, tag);
CREATE TABLE IF NOT EXISTS meta (account TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY (account, key));
"""

SERVER_SOURCES = ("gmail", "keyword")
# The same key computed over the ``messages`` table (char(31) = unit separator).
KEY_SQL = "(message_id || char(31) || sender)"


def tag_key(message_id: str, sender: str) -> str:
    return f"{message_id}\x1f{sender}" if message_id else ""


def migrate_tags(db: sqlite3.Connection) -> None:
    """Pre-release DBs keyed tags by bare Message-ID: re-key them through ``messages``."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(tags)")]
    if "message_id" not in cols:
        return
    db.executescript(f"""
      ALTER TABLE tags RENAME TO tags_old;
      DROP INDEX IF EXISTS tags_by_tag;
      {TAG_SCHEMA}
      INSERT OR IGNORE INTO tags(account, msg_key, tag, source)
        SELECT DISTINCT t.account, {KEY_SQL.replace("message_id", "m.message_id").replace("sender", "m.sender")},
               t.tag, t.source
        FROM tags_old t JOIN messages m ON m.account = t.account AND m.message_id = t.message_id;
      DROP TABLE tags_old;""")


class TagStoreMixin:
    db: sqlite3.Connection
    lock: threading.RLock

    # ---- writes ----------------------------------------------------------
    def set_server_tags(self, account: str, key: str, tags: list[str], source: str) -> None:
        """Replace the server-sourced tags of one message (local rows are left alone)."""
        self.set_server_tags_bulk(account, [(key, tags)], source)

    def set_server_tags_bulk(self, account: str, rows: list[tuple[str, list[str]]], source: str) -> None:
        rows = [(mid, tags) for mid, tags in rows if mid]
        if not rows:
            return
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.executemany("DELETE FROM tags WHERE account=? AND msg_key=? AND source IN (?,?)",
                                    [(account, mid, *SERVER_SOURCES) for mid, _ in rows])
                self.db.executemany(
                    "INSERT INTO tags(account, msg_key, tag, source) VALUES(?,?,?,?)"
                    " ON CONFLICT(account, msg_key, tag) DO UPDATE SET source=excluded.source",
                    [(account, mid, t, source) for mid, tags in rows for t in tags])
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def uid_rows(self, account: str, folder: str) -> list[tuple[int, str, list[str]]]:
        """(uid, tag key, flags) for every cached message in a folder (key "" = no Message-ID)."""
        with self.lock:
            rows = self.db.execute("SELECT uid, message_id, sender, flags FROM messages WHERE account=? AND folder=?",
                                   (account, folder)).fetchall()
        return [(r[0], tag_key(r[1], r[2]), json.loads(r[3] or "[]")) for r in rows]

    def add_tag_rows(self, account: str, key: str, tags: list[str], source: str) -> None:
        with self.lock:
            self.db.executemany(
                "INSERT INTO tags(account, msg_key, tag, source) VALUES(?,?,?,?)"
                " ON CONFLICT(account, msg_key, tag) DO NOTHING",
                [(account, key, t, source) for t in tags])

    def remove_tag_rows(self, account: str, key: str, tags: list[str]) -> None:
        with self.lock:
            self.db.executemany("DELETE FROM tags WHERE account=? AND msg_key=? AND tag=?",
                                [(account, key, t) for t in tags])

    # ---- reads -----------------------------------------------------------
    def tags_for(self, account: str, keys: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        ids = [k for k in set(keys) if k]
        with self.lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for mid, tag in self.db.execute(
                        f"SELECT msg_key, tag FROM tags WHERE account=? AND msg_key IN ({marks})",
                        (account, *chunk)):
                    out.setdefault(mid, []).append(tag)
        return {k: sorted(v, key=str.casefold) for k, v in out.items()}

    def attach_tags(self, envs: list["Envelope"]) -> list["Envelope"]:
        by_account: dict[str, list["Envelope"]] = {}
        for e in envs:
            by_account.setdefault(e.account, []).append(e)
        for account, group in by_account.items():
            found = self.tags_for(account, [tag_key(e.message_id, e.sender) for e in group])
            for e in group:
                e.tags = found.get(tag_key(e.message_id, e.sender), [])
        return envs

    def all_tags(self, account: str) -> list[dict]:
        """Every tag on the account with how many cached messages carry it."""
        with self.lock:
            rows = self.db.execute(
                "SELECT t.tag, count(DISTINCT m.id) FROM tags t JOIN messages m"
                f" ON m.account = t.account AND t.msg_key = {KEY_SQL.replace('message_id', 'm.message_id').replace('sender', 'm.sender')}"
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
