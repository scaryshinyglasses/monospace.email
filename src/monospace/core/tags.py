"""Tags on messages and drafts (mixed into ``Service``).

Hybrid storage, decided per account and folder:
  * ``gmail``   — the server speaks X-GM-EXT-1: tags are Gmail labels (X-GM-LABELS).
  * ``keyword`` — the folder's PERMANENTFLAGS has ``\\*``: tags are IMAP keywords.
  * ``local``   — neither (Outlook.com, many hosts) or ``account.tags = "local"``: SQLite only.
Whatever the backend, the ``tags`` table is the one read path (see ``tag_store``). Tag names can't
start with ``\\`` or ``$`` and Gmail system names are reserved, so tagging can never touch
``\\Seen``/``\\Inbox``/``$Junk`` & co. (removing ``\\Inbox`` would archive mail).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monospace.core.imap import ImapSession
    from monospace.core.store import Envelope

TAG_MODES = ("auto", "local")
TAG_RE = re.compile(r"^[^\W_][\w .\-/&+]{0,63}$")
KEYWORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/&+]*$")  # an IMAP atom, ASCII only
RESERVED = frozenset({
    "inbox", "sent", "sent mail", "sent items", "draft", "drafts", "trash", "bin", "deleted items",
    "spam", "junk", "nonjunk", "notjunk", "important", "starred", "flagged", "archive", "all mail",
    "chats", "outbox", "seen", "answered", "deleted", "recent",
})


def _error(msg: str) -> Exception:
    from monospace.core.service import ServiceError

    return ServiceError(msg)


def normalize_tags(values: list[str] | str) -> list[str]:
    """Comma-separated or listed tags → validated, de-duplicated (case-insensitively), in order."""
    items = [values] if isinstance(values, str) else list(values)
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        for raw in str(item).split(","):
            tag = " ".join(raw.split()).lstrip("#")
            if not tag:
                continue
            if not TAG_RE.match(tag):
                raise _error(f"invalid tag {tag!r}: start with a letter or digit; then letters, digits, "
                             "spaces or - _ . / & + (max 64)")
            if tag.casefold() in RESERVED:
                raise _error(f"{tag!r} is a reserved name (system folder/label/flag)")
            if tag.casefold() not in seen:
                seen.add(tag.casefold())
                out.append(tag)
    if not out:
        raise _error("no tags given")
    return out


def _visible(tag: str) -> bool:
    return bool(TAG_RE.match(tag)) and tag.casefold() not in RESERVED


def tags_from_flags(flags: list[str]) -> list[str]:
    """IMAP keywords that are user tags (system ``\\…`` and ``$…`` keywords are skipped)."""
    return [f for f in flags if not f.startswith(("\\", "$")) and _visible(f)]


def tags_from_labels(labels: list[str]) -> list[str]:
    """Gmail labels that are user tags (``\\Inbox``, ``\\Important`` … are skipped)."""
    return [lb for lb in labels if not lb.startswith("\\") and _visible(lb)]


def detect_backend(mode: str, sess: "ImapSession") -> str:
    """For the folder currently selected read-write on ``sess``."""
    if mode == "local":
        return "local"
    if sess.has_capability("X-GM-EXT-1"):
        return "gmail"
    return "keyword" if "\\*" in sess.permanentflags else "local"


class TagsMixin:
    def tag_backend(self, account: str | None, folder: str = "INBOX") -> str:
        """Backend for an account/folder (detected on first tag write, then cached)."""
        acct = self.account(account)  # type: ignore[attr-defined]
        if acct.tags == "local":
            return "local"
        cached = self.store.get_meta(acct.name, f"tags:{folder}")  # type: ignore[attr-defined]
        if cached:
            return str(cached)
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            sess.select(self.resolve_folder(acct, folder))  # type: ignore[attr-defined]
            return self._detect(acct, folder, sess)

    def _detect(self, acct, folder: str, sess: "ImapSession") -> str:  # type: ignore[no-untyped-def]
        backend = detect_backend(acct.tags, sess)
        self.store.set_meta(acct.name, f"tags:{folder}", backend)  # type: ignore[attr-defined]
        return backend

    def _tag_env(self, msg_id: int) -> "Envelope":
        env = self.envelope(msg_id)  # type: ignore[attr-defined]
        if not env.message_id:
            raise _error(f"message {msg_id} has no Message-ID header, so it can't be tagged")
        return env

    def add_tags(self, msg_id: int, tags: list[str] | str) -> "Envelope":
        return self._change_tags(msg_id, normalize_tags(tags), add=True)

    def remove_tags(self, msg_id: int, tags: list[str] | str) -> "Envelope":
        return self._change_tags(msg_id, normalize_tags(tags), add=False)

    def set_tags(self, msg_id: int, tags: list[str] | str) -> "Envelope":
        """Make the message's tags exactly ``tags`` (an empty list clears them)."""
        items = [tags] if isinstance(tags, str) else list(tags)
        want = normalize_tags(items) if any(str(t).replace(",", "").strip() for t in items) else []
        env = self._tag_env(msg_id)
        have = {t.casefold(): t for t in env.tags}
        drop = [t for k, t in have.items() if k not in {w.casefold() for w in want}]
        add = [t for t in want if t.casefold() not in have]
        if drop:
            env = self._change_tags(msg_id, drop, add=False)
        if add:
            env = self._change_tags(msg_id, add, add=True)
        return env

    def _change_tags(self, msg_id: int, tags: list[str], *, add: bool) -> "Envelope":
        env = self._tag_env(msg_id)
        acct = self.account(env.account)  # type: ignore[attr-defined]
        cached = "local" if acct.tags == "local" else self.store.get_meta(acct.name, f"tags:{env.folder}")  # type: ignore[attr-defined]
        backend = cached or "local"
        if cached != "local":
            with self.session(acct) as sess:  # type: ignore[attr-defined]
                sess.select(env.folder)
                backend = self._detect(acct, env.folder, sess)
                if backend == "keyword":
                    bad = [t for t in tags if not KEYWORD_RE.match(t)]
                    if add and bad:
                        raise _error(f"this server stores tags as IMAP keywords, which can't hold {bad[0]!r} "
                                     "(ASCII letters, digits, - _ . / & + only; no spaces). "
                                     "Rename it, or `monospace account set tags=local`.")
                    tags_ok = [t for t in tags if KEYWORD_RE.match(t)]
                    if tags_ok:
                        (sess.add_flags if add else sess.remove_flags)([env.uid], tags_ok)
                        flags = set(env.flags) | set(tags_ok) if add else set(env.flags) - set(tags_ok)
                        self.store.set_flags(env.id, sorted(flags))  # type: ignore[attr-defined]
                elif backend == "gmail":
                    (sess.add_gmail_labels if add else sess.remove_gmail_labels)([env.uid], tags)
        if add:
            self.store.add_tag_rows(env.account, env.message_id, tags, backend)  # type: ignore[attr-defined]
        else:
            self.store.remove_tag_rows(env.account, env.message_id, tags)  # type: ignore[attr-defined]
        return self.envelope(msg_id)  # type: ignore[attr-defined]

    def list_tags(self, account: str | None = None) -> list[dict]:
        acct = self.account(account)  # type: ignore[attr-defined]
        return self.store.all_tags(acct.name)  # type: ignore[attr-defined]

    def tagged(self, account: str | None, tag: str, *, limit: int = 50, offset: int = 0,
               folder: str | None = None) -> list["Envelope"]:
        acct = self.account(account)  # type: ignore[attr-defined]
        where = self.resolve_folder(acct, folder) if folder else None  # type: ignore[attr-defined]
        return self.store.list(acct.name, where, limit=limit, offset=offset, tag=tag)  # type: ignore[attr-defined]

    def carry_tags(self, old: "Envelope", new_id: int | None) -> None:
        """Re-apply tags after a draft edit replaced the server copy (best effort)."""
        if not new_id or not old.tags:
            return
        try:
            self._change_tags(new_id, list(old.tags), add=True)
        except Exception:
            pass


def split_tag_query(query: str) -> tuple[str, list[str]]:
    """``"tag:work invoice tag:\\"big client\\""`` → (``"invoice"``, [``work``, ``big client``])."""
    tags = [a or b for a, b in re.findall(r'(?i)\btag:(?:"([^"]+)"|(\S+))', query)]
    text = re.sub(r'(?i)\btag:(?:"[^"]+"|\S+)', " ", query)
    return " ".join(text.split()), [" ".join(t.split()) for t in tags]
