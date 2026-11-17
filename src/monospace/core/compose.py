"""Build outgoing ``EmailMessage`` objects: new mail, replies, forwards.

Bodies are written in Markdown; we send ``multipart/alternative`` (plain + rendered HTML).
"""

from __future__ import annotations

import html as htmllib
import mimetypes
import re
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
    html_body: str = ""  # raw HTML alternative; overrides the markdown rendering of `body`
    inline_images: dict[str, str] = field(default_factory=dict)  # content-id -> file path
    # content-id -> {alt, width, height, position: top|bottom, link} for images the HTML doesn't place itself
    inline_layout: dict[str, dict] = field(default_factory=dict)
    message_id: str = ""  # reuse (draft edits); empty → a fresh one
    signature: bool = True  # False when body/html_body already contain it (an edited draft)

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


INLINE_DOMAIN = "monospace.email"
LAYOUT_KEYS = ("alt", "width", "height", "position", "align", "link")


def inline_specs(items: list | None) -> list[tuple[str, dict]]:
    """Normalize inline images given as paths or ``{"path", "alt", "width", "height",
    "position": "top"|"bottom", "align": "left"|"center"|"right", "link"}`` dicts."""
    out = []
    for item in items or []:
        if isinstance(item, str):
            out.append((item, {}))
        elif isinstance(item, dict) and item.get("path"):
            out.append((str(item["path"]), {k: item[k] for k in LAYOUT_KEYS if item.get(k) not in (None, "")}))
        else:
            raise ValueError(f"inline image must be a path or an object with 'path': {item!r}")
    return out


def parse_inline_arg(text: str) -> dict:
    """CLI/TUI form ``logo.png;width=120;position=top`` → ``{"path": "logo.png", "width": 120, ...}``."""
    path, *opts = [t.strip() for t in text.split(";")]
    spec: dict = {"path": path}
    for opt in opts:
        key, sep, value = opt.partition("=")
        key = key.strip().lower()
        if not sep or key not in LAYOUT_KEYS:
            raise ValueError(f"bad inline image option {opt!r} (use {', '.join(LAYOUT_KEYS)}=value)")
        spec[key] = int(value) if key in ("width", "height") else value.strip()
    if spec.get("position", "bottom") not in ("top", "bottom"):
        raise ValueError("position must be top or bottom")
    return spec


def format_inline_arg(path: str, spec: dict | None) -> str:
    return ";".join([path] + [f"{k}={v}" for k, v in (spec or {}).items() if k in LAYOUT_KEYS])


_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def html_extra_words(html: str, text: str) -> set[str]:
    """Words a reader of the HTML part would see that the plain-text part doesn't contain.
    Non-empty means a reviewer looking at the text isn't seeing what the recipient sees."""
    from monospace.core.mime import html_to_text

    seen = {w.lower() for w in _WORD.findall(text)}
    return {w.lower() for w in _WORD.findall(html_to_text(html))} - seen


def drop_inline_refs(html: str, cids: set[str] | list[str]) -> str:
    """Remove <img> tags that point at inline parts which no longer exist."""
    for cid in cids:
        html = re.sub(r"<img\b[^>]*cid:%s[^>]*>" % re.escape(cid), "", html)
    return html


def add_inline(draft: "Draft", path: str, spec: dict | None = None) -> str:
    cid = inline_cid(path)
    if cid in draft.inline_images:
        raise ValueError(f"duplicate inline image {Path(path).name!r}")
    draft.inline_images[cid] = path
    if spec:
        draft.inline_layout[cid] = spec
    return cid


def inline_cid(path: str) -> str:
    """Content-ID for an inline image file: ``logo.png`` → ``logo.png@monospace.email``."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(path).name) or "image"
    return f"{name}@{INLINE_DOMAIN}"


def _img_tag(cid: str, path: str, spec: dict) -> str:
    alt = htmllib.escape(str(spec.get("alt") or Path(path).stem), quote=True)
    attrs = f'src="cid:{cid}" alt="{alt}"'
    style = ["display:block", "border:0"]
    for dim in ("width", "height"):
        if spec.get(dim):
            px = int(spec[dim])
            attrs += f' {dim}="{px}"'
            style.append(f"{dim}:{px}px")
    tag = f'<img {attrs} style="{";".join(style)}">'
    link = str(spec.get("link") or "")
    if link.startswith(("https://", "http://", "mailto:")):
        tag = f'<a href="{htmllib.escape(link, quote=True)}">{tag}</a>'
    align = {"left": "left", "center": "center", "right": "right"}.get(str(spec.get("align", "")), "")
    return f'<p style="text-align:{align}">{tag}</p>' if align else f"<p>{tag}</p>"


def _link_inline(html: str, inline: dict[str, str], layout: dict[str, dict] | None = None) -> str:
    """Point <img src> at each inline part; place any image the HTML doesn't reference itself
    at the top or bottom (default) of the body."""
    layout = layout or {}
    top, bottom = [], []
    for cid, path in inline.items():
        short = cid.split("@", 1)[0]
        names = {re.escape(Path(path).name), re.escape("cid:" + short), re.escape("cid:" + cid)}
        html = re.sub(r'src=(["\'])(?:%s)\1' % "|".join(names), f'src="cid:{cid}"', html)
        if f"cid:{cid}" not in html:
            spec = layout.get(cid, {})
            (top if spec.get("position") == "top" else bottom).append(_img_tag(cid, path, spec))
    if top:
        m = re.search(r"<body[^>]*>", html, re.I)
        at = m.end() if m else 0
        html = html[:at] + "".join(top) + html[at:]
    if bottom:
        extra = "".join(bottom)
        html = html.replace("</body>", extra + "</body>") if "</body>" in html else html + extra
    return html


_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _plain_inline(text: str, inline: dict[str, str]) -> str:
    """In the text/plain part, show referenced inline images as their alt text."""
    refs = {Path(p).name for p in inline.values()} | {"cid:" + c for c in inline} \
        | {"cid:" + c.split("@", 1)[0] for c in inline}
    return _MD_IMG.sub(lambda m: f"[{m.group(1) or 'image'}]" if m.group(2) in refs else m.group(0), text)


def _wrap_html(fragment: str) -> str:
    return fragment if "<body" in fragment.lower() else _HTML_SHELL.format(fragment)


def build(draft: Draft, from_header: str, *, signature: str = "") -> EmailMessage:
    body = draft.body
    if not body.strip() and draft.html_body:
        from monospace.core.mime import html_to_text

        body = html_to_text(draft.html_body)
    sig = signature if draft.signature else ""
    if sig:
        body = f"{body.rstrip()}\n\n-- \n{sig}"
    msg = EmailMessage()
    msg["From"] = from_header
    if draft.to:
        msg["To"] = ", ".join(draft.to)
    if draft.cc:
        msg["Cc"] = ", ".join(draft.cc)
    msg["Subject"] = draft.subject
    msg["Date"] = formatdate(localtime=True)
    domain = from_header.rsplit("@", 1)[-1].strip("> ") or "monospace.email"
    msg["Message-ID"] = draft.message_id or make_msgid(domain=domain)
    msg["X-Mailer"] = "monospace.email"
    if draft.in_reply_to:
        msg["In-Reply-To"] = draft.in_reply_to
        msg["References"] = (draft.references + " " + draft.in_reply_to).strip()
    msg.set_content(_plain_inline(body, draft.inline_images))
    if draft.html or draft.html_body or draft.inline_images:
        if draft.html_body:
            html = _wrap_html(draft.html_body)
            if sig:
                tail = "<p>-- <br>" + htmllib.escape(sig).replace("\n", "<br>") + "</p>"
                html = html.replace("</body>", tail + "</body>") if "</body>" in html else html + tail
        else:
            html = render_html(body)
        msg.add_alternative(_link_inline(html, draft.inline_images, draft.inline_layout), subtype="html")
        html_part = msg.get_payload()[1]
        for cid, path_str in draft.inline_images.items():
            path = Path(path_str).expanduser()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if not ctype.startswith("image/"):
                raise ValueError(f"inline image must be an image file: {path.name}")
            maintype, subtype = ctype.split("/", 1)
            html_part.add_related(path.read_bytes(), maintype=maintype, subtype=subtype, cid=f"<{cid}>",
                                  filename=path.name, disposition="inline")
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
