"""In-memory IMAP stand-in with the ``ImapSession`` surface the service uses."""

from __future__ import annotations

import email
import email.policy
from email.message import EmailMessage

from monospace.core.imap import HeaderInfo, RemoteFolder, parse_header_block


def make_raw(subject: str = "Hello", sender: str = "Ana <ana@example.com>", to: str = "me@example.com",
             body: str = "Hi there", html: str | None = None, attachment: tuple[str, bytes] | None = None,
             date: str = "Mon, 16 Nov 2026 10:00:00 +0000", msgid: str = "<m1@example.com>") -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"], msg["Message-ID"] = sender, to, subject, date, msgid
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment:
        msg.add_attachment(attachment[1], maintype="application", subtype="octet-stream", filename=attachment[0])
    return msg.as_bytes()


class FakeServer:
    def __init__(self) -> None:
        self.folders: dict[str, dict] = {}
        self.uidvalidity = 1
        for name in ("INBOX", "Drafts", "Sent", "Archive", "Trash"):
            self.folders[name] = {"next": 1, "msgs": {}}

    def deliver(self, folder: str, raw: bytes, flags: list[str] | None = None) -> int:
        f = self.folders[folder]
        uid = f["next"]
        f["next"] += 1
        f["msgs"][uid] = {"raw": raw, "flags": set(flags or [])}
        return uid


class FakeSession:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.selected: str | None = None

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def folders(self) -> list[RemoteFolder]:
        roles = {"Drafts": ["\\Drafts"], "Sent": ["\\Sent"], "Archive": ["\\Archive"], "Trash": ["\\Trash"]}
        return [RemoteFolder(n, "/", ["\\HasNoChildren", *roles.get(n, [])]) for n in self.server.folders]

    def _f(self) -> dict:
        assert self.selected
        return self.server.folders[self.selected]

    def select(self, folder: str, readonly: bool = False) -> tuple[int, int, int]:
        self.selected = folder
        f = self._f()
        return self.server.uidvalidity, f["next"], len(f["msgs"])

    def all_uids(self) -> list[int]:
        return sorted(self._f()["msgs"])

    def headers(self, uids: list[int]) -> list[HeaderInfo]:
        out = []
        for u in uids:
            m = self._f()["msgs"][u]
            hdr = m["raw"].split(b"\n\n", 1)[0]
            out.append(parse_header_block(u, hdr, tuple(f.encode() for f in m["flags"]), len(m["raw"])))
        return out

    def flags(self, uids: list[int]) -> dict[int, list[str]]:
        return {u: sorted(self._f()["msgs"][u]["flags"]) for u in uids if u in self._f()["msgs"]}

    def body(self, uid: int) -> bytes:
        return self._f()["msgs"][uid]["raw"]

    def add_flags(self, uids: list[int], flags: list[str]) -> None:
        for u in uids:
            self._f()["msgs"][u]["flags"] |= set(flags)

    def remove_flags(self, uids: list[int], flags: list[str]) -> None:
        for u in uids:
            self._f()["msgs"][u]["flags"] -= set(flags)

    def move(self, uids: list[int], dest: str) -> None:
        for u in uids:
            m = self._f()["msgs"].pop(u)
            self.server.deliver(dest, m["raw"], list(m["flags"]))

    def append(self, folder: str, raw: bytes, flags: list[str]) -> None:
        self.server.deliver(folder, raw, flags)


def parse(raw: bytes) -> EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[return-value]
