"""Local SQLite cache: folders, message envelopes, raw bodies, FTS5 search, outbox.

The UI and MCP read only from here; ``sync`` is the single writer of server state.
Message ``id`` (rowid) is the stable handle exposed to the CLI/MCP.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from monospace.core.tag_store import KEY_SQL, TAG_SCHEMA, TagStoreMixin, migrate_tags

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
  account TEXT NOT NULL, name TEXT NOT NULL, delimiter TEXT DEFAULT '/', flags TEXT DEFAULT '[]',
  uidvalidity INTEGER DEFAULT 0, uidnext INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
  unread INTEGER DEFAULT 0, PRIMARY KEY (account, name));
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, account TEXT NOT NULL, folder TEXT NOT NULL, uid INTEGER NOT NULL,
  uidvalidity INTEGER DEFAULT 0, message_id TEXT DEFAULT '', subject TEXT DEFAULT '',
  sender TEXT DEFAULT '', recipients TEXT DEFAULT '', date TEXT DEFAULT '', flags TEXT DEFAULT '[]',
  size INTEGER DEFAULT 0, has_attachments INTEGER DEFAULT 0, snippet TEXT DEFAULT '',
  UNIQUE (account, folder, uid));
CREATE INDEX IF NOT EXISTS messages_list ON messages(account, folder, date DESC);
CREATE TABLE IF NOT EXISTS bodies (msg_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  raw BLOB NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(subject, sender, recipients, body,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY, account TEXT NOT NULL, created TEXT DEFAULT (datetime('now')),
  draft TEXT NOT NULL, source TEXT DEFAULT 'ai', status TEXT DEFAULT 'pending', error TEXT DEFAULT '',
  send_at TEXT DEFAULT '', draft_message_id TEXT DEFAULT '');
"""


def _migrate_outbox(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(outbox)")}
    if "send_at" not in cols:
        db.execute("ALTER TABLE outbox ADD COLUMN send_at TEXT DEFAULT ''")
    if "draft_message_id" not in cols:
        db.execute("ALTER TABLE outbox ADD COLUMN draft_message_id TEXT DEFAULT ''")


@dataclass
class Envelope:
    account: str
    folder: str
    uid: int
    subject: str = ""
    sender: str = ""
    recipients: str = ""
    date: str = ""
    flags: list[str] = field(default_factory=list)
    size: int = 0
    has_attachments: bool = False
    snippet: str = ""
    message_id: str = ""
    uidvalidity: int = 0
    id: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def unread(self) -> bool:
        return "\\Seen" not in self.flags

    @property
    def flagged(self) -> bool:
        return "\\Flagged" in self.flags

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["unread"], d["flagged"] = self.unread, self.flagged
        return d


@dataclass
class Folder:
    account: str
    name: str
    delimiter: str = "/"
    flags: list[str] = field(default_factory=list)
    uidvalidity: int = 0
    uidnext: int = 0
    total: int = 0
    unread: int = 0

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


_ENV_COLS = ("id, account, folder, uid, uidvalidity, message_id, subject, sender, recipients, date, "
             "flags, size, has_attachments, snippet")


_TAG_FILTER = f" AND {KEY_SQL} IN (SELECT msg_key FROM tags WHERE account=? AND tag=?)"


def _env(row: sqlite3.Row) -> Envelope:
    d = dict(row)
    d["flags"] = json.loads(d["flags"] or "[]")
    d["has_attachments"] = bool(d["has_attachments"])
    return Envelope(**d)


class Store(TagStoreMixin):
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(SCHEMA)
            _migrate_outbox(self.db)
            migrate_tags(self.db)
            self.db.executescript(TAG_SCHEMA)

    # ---- folders -------------------------------------------------------
    def upsert_folder(self, f: Folder) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO folders(account,name,delimiter,flags,uidvalidity,uidnext,total,unread)"
                " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account,name) DO UPDATE SET delimiter=excluded.delimiter,"
                " flags=excluded.flags, uidvalidity=excluded.uidvalidity, uidnext=excluded.uidnext,"
                " total=excluded.total, unread=excluded.unread",
                (f.account, f.name, f.delimiter, json.dumps(f.flags), f.uidvalidity, f.uidnext, f.total, f.unread))

    def folders(self, account: str) -> list[Folder]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM folders WHERE account=? ORDER BY name", (account,)).fetchall()
        return [Folder(**{**dict(r), "flags": json.loads(r["flags"])}) for r in rows]

    def folder(self, account: str, name: str) -> Folder | None:
        return next((f for f in self.folders(account) if f.name == name), None)

    def prune_folders(self, account: str, keep: set[str]) -> None:
        with self.lock:
            for f in self.folders(account):
                if f.name not in keep:
                    self.reset_folder(account, f.name)
                    self.db.execute("DELETE FROM folders WHERE account=? AND name=?", (account, f.name))

    def reset_folder(self, account: str, folder: str) -> None:
        with self.lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT id FROM messages WHERE account=? AND folder=?", (account, folder))]
            self._delete_ids(ids)

    def recount(self, account: str, folder: str) -> None:
        with self.lock:
            total, unread = self.db.execute(
                "SELECT count(*), coalesce(sum(CASE WHEN flags NOT LIKE '%\\\\Seen%' THEN 1 ELSE 0 END),0)"
                " FROM messages WHERE account=? AND folder=?", (account, folder)).fetchone()
            self.db.execute("UPDATE folders SET total=?, unread=? WHERE account=? AND name=?",
                            (total, unread, account, folder))

    # ---- messages ------------------------------------------------------
    def upsert_envelope(self, e: Envelope) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO messages(account,folder,uid,uidvalidity,message_id,subject,sender,recipients,date,"
                "flags,size,has_attachments,snippet) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(account,folder,uid) DO UPDATE SET flags=excluded.flags,"
                " snippet=CASE WHEN excluded.snippet!='' THEN excluded.snippet ELSE messages.snippet END"
                " RETURNING id",
                (e.account, e.folder, e.uid, e.uidvalidity, e.message_id, e.subject, e.sender, e.recipients,
                 e.date, json.dumps(e.flags), e.size, int(e.has_attachments), e.snippet))
            msg_id = cur.fetchone()[0]
            if not self.db.execute("SELECT 1 FROM search WHERE rowid=?", (msg_id,)).fetchone():
                self.db.execute("INSERT INTO search(rowid,subject,sender,recipients,body) VALUES(?,?,?,?,?)",
                                (msg_id, e.subject, e.sender, e.recipients, e.snippet))
            return msg_id

    def set_flags(self, msg_id: int, flags: list[str]) -> None:
        with self.lock:
            self.db.execute("UPDATE messages SET flags=? WHERE id=?", (json.dumps(sorted(set(flags))), msg_id))

    def get(self, msg_id: int) -> Envelope | None:
        with self.lock:
            row = self.db.execute(f"SELECT {_ENV_COLS} FROM messages WHERE id=?", (msg_id,)).fetchone()
        return self.attach_tags([_env(row)])[0] if row else None

    def by_uid(self, account: str, folder: str, uid: int) -> Envelope | None:
        with self.lock:
            row = self.db.execute(f"SELECT {_ENV_COLS} FROM messages WHERE account=? AND folder=? AND uid=?",
                                  (account, folder, uid)).fetchone()
        return _env(row) if row else None

    def by_message_id(self, account: str, folder: str, message_id: str) -> Envelope | None:
        with self.lock:
            row = self.db.execute(f"SELECT {_ENV_COLS} FROM messages WHERE account=? AND folder=?"
                                  " AND message_id=? ORDER BY uid DESC", (account, folder, message_id)).fetchone()
        return _env(row) if row else None

    def uids(self, account: str, folder: str) -> dict[int, int]:
        """uid -> id for a folder."""
        with self.lock:
            rows = self.db.execute("SELECT uid, id FROM messages WHERE account=? AND folder=?", (account, folder))
            return {r[0]: r[1] for r in rows}

    def list(self, account: str, folder: str | None, *, limit: int = 200, offset: int = 0,
             unread_only: bool = False, tag: str | None = None) -> list[Envelope]:
        """Newest first. ``folder=None`` (only with ``tag``) lists across every folder."""
        sql, args = f"SELECT {_ENV_COLS} FROM messages WHERE account=?", [account]
        if folder is not None:
            sql, args = sql + " AND folder=?", [*args, folder]
        if unread_only:
            sql += " AND flags NOT LIKE '%\\\\Seen%'"
        if tag:
            sql += _TAG_FILTER
            args += [account, tag]
        sql += " ORDER BY date DESC, uid DESC LIMIT ? OFFSET ?"
        with self.lock:
            rows = self.db.execute(sql, (*args, limit, offset)).fetchall()
        return self.attach_tags([_env(r) for r in rows])

    def query(self, accounts: list[str], where: str, args: list[object], *, text: str = "",
              limit: int = 50, offset: int = 0) -> list[Envelope]:
        """Newest first over ``messages m``; ``where`` comes from ``MessageFilter.to_sql``. ``text`` = FTS."""
        cols = ", ".join("m." + c.strip() for c in _ENV_COLS.split(","))
        sql, params = f"SELECT {cols} FROM messages m", []  # type: str, list[object]
        fts = " ".join(f'"{t}"*' for t in text.replace('"', " ").split() if t)
        if fts:
            sql, params = sql + " JOIN search s ON s.rowid = m.id WHERE search MATCH ?", [fts]
        else:
            sql += " WHERE 1"
        marks = ",".join("?" * len(accounts))
        sql += f" AND m.account IN ({marks}){where} ORDER BY julianday(m.date) DESC, m.id DESC LIMIT ? OFFSET ?"
        with self.lock:
            rows = self.db.execute(sql, (*params, *accounts, *args, limit, offset)).fetchall()
        return self.attach_tags([_env(r) for r in rows])

    def search(self, query: str, *, account: str | None = None, limit: int = 50,
               tag: str | None = None) -> list[Envelope]:
        fts = " ".join(f'"{t}"*' for t in query.replace('"', " ").split() if t)
        if not fts:
            return []
        sql = (f"SELECT {', '.join('m.' + c.strip() for c in _ENV_COLS.split(','))} FROM search s"
               " JOIN messages m ON m.id = s.rowid WHERE search MATCH ?")
        args: list[object] = [fts]
        if account:
            sql += " AND m.account=?"
            args.append(account)
        if tag:
            sql += (" AND (m.message_id || char(31) || m.sender) IN"
                    " (SELECT msg_key FROM tags WHERE account=m.account AND tag=?)")
            args.append(tag)
        sql += " ORDER BY m.date DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return self.attach_tags([_env(r) for r in rows])

    def delete_uids(self, account: str, folder: str, uids: list[int]) -> None:
        mapping = self.uids(account, folder)
        self._delete_ids([mapping[u] for u in uids if u in mapping])

    def _delete_ids(self, ids: list[int]) -> None:
        with self.lock:
            for i in ids:
                self.db.execute("DELETE FROM search WHERE rowid=?", (i,))
                self.db.execute("DELETE FROM messages WHERE id=?", (i,))

    # ---- bodies --------------------------------------------------------
    def put_body(self, msg_id: int, raw: bytes, full_text: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO bodies(msg_id, raw) VALUES(?,?)", (msg_id, raw))
            if full_text:
                self.db.execute("UPDATE search SET body=? WHERE rowid=?", (full_text[:20000], msg_id))

    def body(self, msg_id: int) -> bytes | None:
        with self.lock:
            row = self.db.execute("SELECT raw FROM bodies WHERE msg_id=?", (msg_id,)).fetchone()
        return bytes(row[0]) if row else None

    # ---- outbox --------------------------------------------------------
    def outbox_add(self, account: str, draft: dict, source: str = "ai", *,
                   status: str = "pending", send_at: str = "", draft_message_id: str = "") -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO outbox(account, draft, source, status, send_at, draft_message_id) VALUES(?,?,?,?,?,?)",
                (account, json.dumps(draft), source, status, send_at, draft_message_id))
            return int(cur.lastrowid or 0)

    def outbox(self, status: str | tuple[str, ...] | None = "pending") -> list[dict]:
        sql, args = "SELECT * FROM outbox", []  # type: str, list[object]
        if isinstance(status, tuple):
            marks = ",".join("?" * len(status))
            sql, args = f"{sql} WHERE status IN ({marks})", list(status)
        elif status:
            sql, args = sql + " WHERE status=?", [status]
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY CASE WHEN send_at!='' THEN send_at ELSE created END, id",
                                   args).fetchall()
        return [{**dict(r), "draft": json.loads(r["draft"])} for r in rows]

    def outbox_get(self, item_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM outbox WHERE id=?", (item_id,)).fetchone()
        return {**dict(row), "draft": json.loads(row["draft"])} if row else None

    def outbox_by_draft(self, account: str, draft_message_id: str) -> dict | None:
        if not draft_message_id:
            return None
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM outbox WHERE account=? AND draft_message_id=? AND status IN ('pending','scheduled')"
                " ORDER BY id DESC LIMIT 1", (account, draft_message_id)).fetchone()
        return {**dict(row), "draft": json.loads(row["draft"])} if row else None

    def outbox_cancel_by_draft(self, account: str, draft_message_id: str) -> None:
        if not draft_message_id:
            return
        with self.lock:
            self.db.execute(
                "UPDATE outbox SET status='cancelled' WHERE account=? AND draft_message_id=?"
                " AND status IN ('pending','scheduled')", (account, draft_message_id))

    def outbox_reschedule(self, item_id: int, send_at: str, draft: dict, draft_message_id: str = "") -> None:
        with self.lock:
            self.db.execute("UPDATE outbox SET send_at=?, draft=?, draft_message_id=?, error='' WHERE id=?",
                            (send_at, json.dumps(draft), draft_message_id, item_id))

    def outbox_set(self, item_id: int, status: str, error: str = "") -> None:
        with self.lock:
            self.db.execute("UPDATE outbox SET status=?, error=? WHERE id=?", (status, error, item_id))

    def close(self) -> None:
        self.db.close()
