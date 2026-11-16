"""Thin, synchronous IMAP session over ``imapclient`` (run it in worker threads).

Everything here speaks UIDs. Callers own the session lifetime via ``with ImapSession(...)``.
"""

from __future__ import annotations

import email
import email.policy
import ssl
from dataclasses import dataclass
from email.utils import getaddresses, parsedate_to_datetime

from imapclient import IMAPClient

from monospace.core.auth.tokens import Credential
from monospace.core.config import Account

HEADER_FIELDS = b"BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID CONTENT-TYPE)]"
HEADER_KEY = b"BODY[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID CONTENT-TYPE)]"
SPECIAL_USE = {b"\\Drafts": "drafts", b"\\Sent": "sent", b"\\Trash": "trash",
               b"\\All": "archive", b"\\Archive": "archive", b"\\Junk": "junk"}


class ImapError(Exception):
    pass


@dataclass
class RemoteFolder:
    name: str
    delimiter: str
    flags: list[str]

    @property
    def selectable(self) -> bool:
        return "\\Noselect" not in self.flags and "\\NonExistent" not in self.flags

    @property
    def role(self) -> str | None:
        for f in self.flags:
            role = SPECIAL_USE.get(f.encode())
            if role:
                return role
        return "inbox" if self.name.upper() == "INBOX" else None


@dataclass
class HeaderInfo:
    uid: int
    subject: str
    sender: str
    recipients: str
    date: str
    message_id: str
    flags: list[str]
    size: int
    has_attachments: bool


def _s(v: object) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _fmt_addrs(values: list[str]) -> str:
    out = []
    for name, addr in getaddresses(values):
        if addr:
            out.append(f"{name} <{addr}>" if name else addr)
    return ", ".join(out)


def parse_header_block(uid: int, raw: bytes, flags: tuple, size: int) -> HeaderInfo:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        date = parsedate_to_datetime(str(msg.get("date"))).isoformat() if msg.get("date") else ""
    except (TypeError, ValueError):
        date = ""
    ctype = str(msg.get("content-type", "")).lower()
    return HeaderInfo(
        uid=uid, subject=str(msg.get("subject", "") or ""),
        sender=_fmt_addrs([str(x) for x in msg.get_all("from", [])]),
        recipients=_fmt_addrs([str(x) for x in msg.get_all("to", []) + msg.get_all("cc", [])]),
        date=date, message_id=str(msg.get("message-id", "") or "").strip(),
        flags=sorted(_s(f) for f in flags), size=size,
        has_attachments=ctype.startswith("multipart/mixed"),
    )


class ImapSession:
    def __init__(self, acct: Account, cred: Credential, timeout: float = 30) -> None:
        self.acct, self.cred, self.timeout = acct, cred, timeout
        self.client: IMAPClient | None = None
        self.selected: str | None = None

    def __enter__(self) -> "ImapSession":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        a = self.acct
        try:
            ctx = ssl.create_default_context()
            self.client = IMAPClient(a.imap_host, port=a.imap_port, ssl=a.imap_tls == "ssl",
                                     ssl_context=ctx, timeout=self.timeout)
            if a.imap_tls == "starttls":
                self.client.starttls(ctx)
            if self.cred.kind == "xoauth2":
                self.client.oauth2_login(self.cred.user, self.cred.secret)
            else:
                self.client.login(self.cred.user, self.cred.secret)
        except Exception as exc:
            raise ImapError(f"{a.name}: IMAP connect/login failed: {exc}") from exc

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.logout()
            except Exception:
                pass
            self.client = None

    @property
    def c(self) -> IMAPClient:
        if self.client is None:
            raise ImapError("session not open")
        return self.client

    def folders(self) -> list[RemoteFolder]:
        return [RemoteFolder(_s(name), _s(delim or "/"), [_s(f) for f in flags])
                for flags, delim, name in self.c.list_folders()]

    def select(self, folder: str, readonly: bool = False) -> tuple[int, int, int]:
        info = self.c.select_folder(folder, readonly=readonly)
        self.selected = folder
        return int(info.get(b"UIDVALIDITY", 0)), int(info.get(b"UIDNEXT", 0)), int(info.get(b"EXISTS", 0))

    def all_uids(self) -> list[int]:
        return sorted(self.c.search("ALL"))

    def uids_since(self, uid: int) -> list[int]:
        return sorted(u for u in self.c.search(["UID", f"{uid + 1}:*"]) if u > uid)

    def headers(self, uids: list[int]) -> list[HeaderInfo]:
        out = []
        for i in range(0, len(uids), 200):
            batch = uids[i:i + 200]
            data = self.c.fetch(batch, ["FLAGS", "RFC822.SIZE", HEADER_FIELDS])
            for uid, d in data.items():
                out.append(parse_header_block(uid, d.get(HEADER_KEY, b""), d.get(b"FLAGS", ()),
                                              int(d.get(b"RFC822.SIZE", 0))))
        return out

    def flags(self, uids: list[int]) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for i in range(0, len(uids), 1000):
            for uid, fl in self.c.get_flags(uids[i:i + 1000]).items():
                out[uid] = sorted(_s(f) for f in fl)
        return out

    def body(self, uid: int) -> bytes:
        data = self.c.fetch([uid], ["BODY.PEEK[]"])
        if uid not in data:
            raise ImapError(f"message uid {uid} not found on server")
        return data[uid][b"BODY[]"]

    def add_flags(self, uids: list[int], flags: list[str]) -> None:
        self.c.add_flags(uids, [f.encode() for f in flags], silent=True)

    def remove_flags(self, uids: list[int], flags: list[str]) -> None:
        self.c.remove_flags(uids, [f.encode() for f in flags], silent=True)

    def move(self, uids: list[int], dest: str) -> None:
        if self.c.has_capability("MOVE"):
            self.c.move(uids, dest)
        else:
            self.c.copy(uids, dest)
            self.c.delete_messages(uids)
            self.c.uid_expunge(uids) if self.c.has_capability("UIDPLUS") else self.c.expunge()

    def append(self, folder: str, raw: bytes, flags: list[str]) -> None:
        self.c.append(folder, raw, flags=[f.encode() for f in flags])

    # ---- IDLE ----------------------------------------------------------
    def supports_idle(self) -> bool:
        return self.c.has_capability("IDLE")

    def idle_wait(self, timeout: float) -> bool:
        """Block up to ``timeout``s in IDLE; True if the server reported changes."""
        self.c.idle()
        try:
            responses = self.c.idle_check(timeout=timeout)
        finally:
            self.c.idle_done()
        return bool(responses)
