"""Parse raw RFC 5322 bytes into a ``ParsedMessage`` the UI, CLI and MCP all share."""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime


@dataclass
class Part:
    filename: str
    content_type: str
    data: bytes
    content_id: str = ""
    inline: bool = False

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")


@dataclass
class ParsedMessage:
    subject: str = ""
    from_: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)  # only present on drafts / sent copies
    reply_to: list[str] = field(default_factory=list)
    date: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    text: str = ""
    html: str = ""
    inline: dict[str, Part] = field(default_factory=dict)  # content-id (no <>) -> part
    attachments: list[Part] = field(default_factory=list)

    def best_text(self) -> str:
        if self.text.strip():
            return self.text
        if self.html:
            return html_to_text(self.html)
        return ""


def _addrs(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out = []
    for name, addr in getaddresses([str(v) for v in values]):
        if addr:
            out.append(f"{name} <{addr}>" if name else addr)
    return out


def _decode_text(part: EmailMessage) -> str:
    try:
        return str(part.get_content())
    except (LookupError, UnicodeDecodeError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def parse(raw: bytes) -> ParsedMessage:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[assignment]
    pm = ParsedMessage(
        subject=str(msg.get("subject", "") or ""),
        from_=(_addrs(msg.get_all("from")) or [""])[0],
        to=_addrs(msg.get_all("to")),
        cc=_addrs(msg.get_all("cc")),
        bcc=_addrs(msg.get_all("bcc")),
        reply_to=_addrs(msg.get_all("reply-to")),
        date=_iso_date(msg.get("date")),
        message_id=str(msg.get("message-id", "") or "").strip(),
        in_reply_to=str(msg.get("in-reply-to", "") or "").strip(),
        references=str(msg.get("references", "") or "").strip(),
    )
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = part.get_content_disposition()
        filename = part.get_filename() or ""
        cid = str(part.get("content-id", "") or "").strip().strip("<>")
        if disp != "attachment" and not filename and ctype == "text/plain" and not pm.text:
            pm.text = _decode_text(part)  # type: ignore[arg-type]
            continue
        if disp != "attachment" and not filename and ctype == "text/html" and not pm.html:
            pm.html = _decode_text(part)  # type: ignore[arg-type]
            continue
        data = part.get_payload(decode=True) or b""
        p = Part(filename=filename or _default_name(ctype, cid), content_type=ctype, data=data,
                 content_id=cid, inline=disp == "inline" or (bool(cid) and disp != "attachment"))
        if cid and p.is_image:
            pm.inline[cid] = p
        if not (cid and p.inline and p.is_image):
            pm.attachments.append(p)
    return pm


def _default_name(ctype: str, cid: str) -> str:
    ext = ctype.split("/")[-1].split("+")[0]
    return f"{cid or 'part'}.{ext}"


def _iso_date(value: object) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(str(value)).isoformat()
    except (TypeError, ValueError):
        return str(value)


_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|tr|li|h[1-6]|table|blockquote)[^>]*>", re.I)
_DROP = re.compile(r"<(script|style|head)[^>]*>.*?</\1>", re.I | re.S)


def html_to_text(html: str) -> str:
    """Cheap HTML→text used for snippets/search; the reader uses render.html_blocks."""
    import html as htmllib

    text = _DROP.sub("", html)
    text = _BLOCK.sub("\n", text)
    text = htmllib.unescape(_TAG.sub("", text))
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def snippet(pm: ParsedMessage, length: int = 160) -> str:
    return " ".join(pm.best_text().split())[:length]
