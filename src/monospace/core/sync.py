"""Incremental IMAP → SQLite sync (UIDVALIDITY / last-seen-UID based)."""

from __future__ import annotations

from dataclasses import dataclass, field

from monospace.core import mime
from monospace.core.imap import HeaderInfo, ImapSession
from monospace.core.store import Envelope, Folder, Store

INITIAL_LIMIT = 500   # headers fetched on first sync of a folder
PREFETCH = 25         # newest bodies cached per sync (fills snippets + full-text search)
FLAG_REFRESH_MAX = 5000


@dataclass
class SyncStats:
    folders: int = 0
    new: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"folders": self.folders, "new": self.new, "removed": self.removed, "errors": self.errors}


def _envelope(account: str, folder: str, uidvalidity: int, h: HeaderInfo) -> Envelope:
    return Envelope(account=account, folder=folder, uid=h.uid, uidvalidity=uidvalidity, subject=h.subject,
                    sender=h.sender, recipients=h.recipients, date=h.date, flags=h.flags, size=h.size,
                    has_attachments=h.has_attachments, message_id=h.message_id)


def sync_folder_list(sess: ImapSession, store: Store, account: str) -> list[str]:
    remote = [f for f in sess.folders() if f.selectable]
    for f in remote:
        existing = store.folder(account, f.name)
        store.upsert_folder(existing or Folder(account=account, name=f.name, delimiter=f.delimiter,
                                               flags=f.flags))
        if existing and existing.flags != f.flags:
            existing.flags = f.flags
            store.upsert_folder(existing)
    store.prune_folders(account, {f.name for f in remote})
    return [f.name for f in remote]


def cache_body(store: Store, msg_id: int, raw: bytes) -> mime.ParsedMessage:
    pm = mime.parse(raw)
    store.put_body(msg_id, raw, pm.best_text())
    env = store.get(msg_id)
    if env and not env.snippet:
        env.snippet = mime.snippet(pm)
        env.has_attachments = bool(pm.attachments)
        with store.lock:
            store.db.execute("UPDATE messages SET snippet=?, has_attachments=? WHERE id=?",
                             (env.snippet, int(env.has_attachments), msg_id))
            store.db.execute("UPDATE search SET body=? WHERE rowid=?", (pm.best_text()[:20000], msg_id))
    return pm


def sync_folder(sess: ImapSession, store: Store, account: str, folder: str, *,
                initial_limit: int = INITIAL_LIMIT, prefetch: int = PREFETCH) -> tuple[int, int]:
    uidvalidity, uidnext, _exists = sess.select(folder, readonly=True)
    cached = store.folder(account, folder) or Folder(account=account, name=folder)
    if cached.uidvalidity and cached.uidvalidity != uidvalidity:
        store.reset_folder(account, folder)
    known = store.uids(account, folder)
    server_uids = sess.all_uids()
    server_set = set(server_uids)

    removed = [u for u in known if u not in server_set]
    if removed:
        store.delete_uids(account, folder, removed)

    last = max(known) if known else 0
    new_uids = [u for u in server_uids if u > last]
    if not known:
        new_uids = new_uids[-initial_limit:]
    for h in sess.headers(new_uids):
        store.upsert_envelope(_envelope(account, folder, uidvalidity, h))

    still_known = [u for u in known if u in server_set]
    if still_known and len(still_known) <= FLAG_REFRESH_MAX:
        mapping = store.uids(account, folder)
        for uid, fl in sess.flags(still_known).items():
            if uid in mapping:
                store.set_flags(mapping[uid], fl)

    if prefetch:
        for env in store.list(account, folder, limit=prefetch):
            if store.body(env.id) is None:
                try:
                    cache_body(store, env.id, sess.body(env.uid))
                except Exception:
                    break

    cached = store.folder(account, folder) or Folder(account=account, name=folder)
    cached.uidvalidity, cached.uidnext = uidvalidity, uidnext
    store.upsert_folder(cached)
    store.recount(account, folder)
    return len(new_uids), len(removed)


def detect_roles(sess: ImapSession) -> dict[str, str]:
    roles: dict[str, str] = {}
    for f in sess.folders():
        role = f.role
        if role and role != "inbox" and role not in roles:
            roles[role] = f.name
    return roles
