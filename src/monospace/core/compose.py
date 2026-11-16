"""Build outgoing ``EmailMessage`` objects: new mail, replies, forwards.

Bodies are written in Markdown; we send ``multipart/alternative`` (plain + rendered HTML).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from pathlib import Path

import markdown as md

from monospace.core.mime import ParsedMessage


@dataclass
class Draft:
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""  # markdown
    attachments: list[str] = field(default_factory=list)  # file paths
    in_reply_to: str = ""
    references: str = ""
    html: bool = True

    def recipients(self) -> list[str]:
        return [addr for _, addr in getaddresses(self.to + self.cc + self.bcc) if addr]

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, raw: dict) -> "Draft":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def split_addrs(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for name, addr in getaddresses(items):
        if addr:
            out.append(f"{name} <{addr}>" if name else addr)
    return out


_HTML_SHELL = (
    '<!doctype html><html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
    'font-size:14px;line-height:1.5">{}</body></html>'
)


def render_html(body_md: str) -> str:
    inner = md.markdown(body_md, extensions=["extra", "sane_lists", "nl2br"])
    return _HTML_SHELL.format(inner)


def build(draft: Draft, from_header: str, *, signature: str = "") -> EmailMessage:
    body = draft.body
    if signature:
        body = f"{body.rstrip()}\n\n-- \n{signature}"
    msg = EmailMessage()
    msg["From"] = from_header
    if draft.to:
        msg["To"] = ", ".join(draft.to)
    if draft.cc:
        msg["Cc"] = ", ".join(draft.cc)
    msg["Subject"] = draft.subject
    msg["Date"] = formatdate(localtime=True)
    domain = from_header.rsplit("@", 1)[-1].strip("> ") or "monospace.email"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["X-Mailer"] = "monospace.email"
    if draft.in_reply_to:
        msg["In-Reply-To"] = draft.in_reply_to
        msg["References"] = (draft.references + " " + draft.in_reply_to).strip()
    msg.set_content(body)
    if draft.html:
        msg.add_alternative(render_html(body), subtype="html")
    for path_str in draft.attachments:
        path = Path(path_str).expanduser()
        ctype, encoding = mimetypes.guess_type(path.name)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    # Bcc is used for the SMTP envelope only, never written into headers.
    return msg


def _prefixed(subject: str, prefix: str) -> str:
    return subject if subject.lower().startswith(prefix.lower()) else f"{prefix} {subject}"


def quote(original: ParsedMessage) -> str:
    who = original.from_ or "someone"
    when = original.date[:16].replace("T", " ") if original.date else ""
    header = f"On {when}, {who} wrote:" if when else f"{who} wrote:"
    lines = original.best_text().splitlines() or [""]
    return header + "\n" + "\n".join("> " + ln for ln in lines)


def reply(original: ParsedMessage, body: str, self_email: str, *, reply_all: bool = False) -> Draft:
    to = original.reply_to or [original.from_]
    cc: list[str] = []
    if reply_all:
        mine = self_email.lower()
        seen = {a for _, a in getaddresses(to)}
        for addr in original.to + original.cc:
            bare = getaddresses([addr])[0][1].lower()
            if bare and bare != mine and bare not in seen:
                cc.append(addr)
                seen.add(bare)
    return Draft(
        to=to, cc=cc, subject=_prefixed(original.subject, "Re:"),
        body=f"{body.rstrip()}\n\n{quote(original)}",
        in_reply_to=original.message_id, references=original.references,
    )


def forward(original: ParsedMessage, body: str, to: list[str]) -> Draft:
    header = (
        "---------- Forwarded message ----------\n"
        f"From: {original.from_}\nDate: {original.date}\nSubject: {original.subject}\n"
        f"To: {', '.join(original.to)}\n"
    )
    return Draft(to=to, subject=_prefixed(original.subject, "Fwd:"),
                 body=f"{body.rstrip()}\n\n{header}\n{original.best_text()}")
