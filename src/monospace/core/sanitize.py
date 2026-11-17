"""Make agent-written HTML reviewable: what a human approves must be what recipients see.

``sanitize_html`` keeps an allowlist of structural tags and attributes and drops everything that can
hide, restyle or exfiltrate content (CSS, scripts, remote/data images, forms, hidden attributes).
``review_text`` renders that HTML as plain text with link targets and images spelled out, and
``terminal_safe`` strips control characters before anything untrusted reaches a terminal.
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

DROP = ("script, style, head, title, meta, link, base, iframe, frame, frameset, object, embed, applet, "
        "form, input, button, textarea, select, option, svg, math, template, noscript, video, audio, "
        "source, track, canvas, picture, map, area, dialog")
KEEP = {"p", "br", "hr", "div", "span", "b", "strong", "i", "em", "u", "s", "del", "ins", "sub", "sup",
        "a", "img", "ul", "ol", "li", "dl", "dt", "dd", "blockquote", "pre", "code",
        "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
        "caption", "html", "body"}
ATTRS = {"a": {"href"}, "img": {"src", "alt", "width", "height"}, "td": {"colspan", "rowspan", "align"},
         "th": {"colspan", "rowspan", "align"}, "p": {"align"}, "div": {"align"}}
_SAFE_HREF = re.compile(r"^(https?://|mailto:)[^\s<>\"'`\\]+$", re.I)
_CID_SRC = re.compile(r"^cid:[A-Za-z0-9._@-]+$")
_ALT_BAD = re.compile(r"[<>\"'`&]|--")
_TEXT_URL = re.compile(r"(?:https?://|mailto:)[^\s<>()\[\]\"'`]+", re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def terminal_safe(text: str) -> str:
    """Drop C0/C1 controls (keeps \\n and \\t) and bidi overrides / zero-width characters."""
    return _CTRL.sub("", text)


def normalize_url(url: str) -> str | None:
    """Canonical form for comparing link targets; None for anything we won't send (userinfo, odd ports)."""
    from urllib.parse import urlsplit, urlunsplit

    url = url.strip().rstrip(".,;:!?")
    if url.lower().startswith("mailto:"):
        return "mailto:" + url[7:].lower()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        return None
    host = parts.hostname.lower() + (f":{port}" if port else "")
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, parts.fragment))


def sanitize_html(html: str) -> str:
    """Allowlist-sanitize, repeated until stable so re-parsing can't resurrect anything."""
    out = _sanitize_once(html)
    for _ in range(3):
        again = _sanitize_once(out)
        if again == out:
            break
        out = again
    else:
        return _text_only(out)  # never converged: fall back to escaped plain text
    if "<!--" in out or "<![" in out or "<!" in out:  # belt and braces: no comment/CDATA markup survives
        return _text_only(out)
    return out


def _text_only(html: str) -> str:
    import html as htmllib

    from monospace.core.mime import html_to_text

    return "<pre>" + htmllib.escape(html_to_text(html)) + "</pre>"


def _sanitize_once(html: str) -> str:
    tree = LexborHTMLParser(html)
    for node in tree.css(DROP):
        node.decompose()
    body = tree.body
    if body is None:
        return ""
    for node in list(body.traverse(include_text=True)):
        if node.is_comment_node:  # Outlook renders <!--[if mso]> blocks: unreviewable content
            node.decompose()
            continue
        tag = (node.tag or "").lower()
        if tag.startswith("-"):  # text nodes
            continue
        if tag not in KEEP:
            node.unwrap()  # keep the text, lose the element (font, center, details, …)
            continue
        allowed = ATTRS.get(tag, set())
        for name in list(node.attributes):
            value = node.attributes.get(name) or ""
            if name not in allowed:
                del node.attrs[name]
            elif name == "href" and (not _SAFE_HREF.match(value.strip()) or normalize_url(value) is None):
                del node.attrs[name]
            elif name == "alt" and _ALT_BAD.search(value):
                node.attrs[name] = _ALT_BAD.sub("", value)[:200]
            elif name == "src" and not _CID_SRC.match(value):
                del node.attrs[name]
            elif name == "align" and value.strip().lower() not in ("left", "right", "center", "justify"):
                del node.attrs[name]
            elif name in ("colspan", "rowspan") and not value.strip().isdigit():
                del node.attrs[name]
            elif name in ("width", "height") and not value.strip().isdigit():
                del node.attrs[name]
        if tag == "img" and not (node.attributes.get("src") or "").startswith("cid:"):
            node.decompose()  # remote / data: images can carry data out or show unreviewed text
    return body.inner_html or ""


_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote",
          "pre", "hr", "ul", "ol", "dl", "dt", "dd", "caption"}


class _Mismatch(Exception):
    pass


def _url_like(text: str) -> bool:
    return bool(_TEXT_URL.fullmatch(text.strip()))


def _render(node, *, images: bool, strict: bool) -> str:  # type: ignore[no-untyped-def]
    """Text a recipient sees, taken from the DOM (never from serialized markup or attributes).
    Each link is followed by its target; ``strict`` raises when a link's text is a different URL."""
    out: list[str] = []
    child = node.child
    while child is not None:
        tag = (child.tag or "").lower()
        if tag == "-text":
            out.append(child.text(deep=False) or "")
        elif tag == "a" and child.attributes.get("href"):
            href = child.attributes.get("href") or ""
            inner = _render(child, images=images, strict=strict)
            if _url_like(inner) and normalize_url(inner) == normalize_url(href):
                out.append(f" <{href}> ")  # the text already is the target
            else:
                if strict and _url_like(inner):
                    raise _Mismatch  # shows one URL, goes to another
                out.append(f"{inner} <{href}> ")
        elif tag == "img":
            if images:
                out.append(f"[image {child.attributes.get('alt') or ''} ({child.attributes.get('src') or ''})]")
        elif not tag.startswith("-"):
            text = _render(child, images=images, strict=strict)
            out.append(f"\n{text}\n" if tag in _BLOCK else text)
        child = child.next
    return "".join(out)


def _tidy(text: str) -> str:
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def review_text(html: str, *, images: bool = True) -> str:
    """Plain text of (sanitized) HTML with link targets (and inline images) made explicit."""
    tree = LexborHTMLParser(sanitize_html(html))
    body = tree.body
    return terminal_safe(_tidy(_render(body, images=images, strict=False))) if body else ""


def words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[^\W_]+", text)]


def tokens(text: str) -> list[str]:
    """Words, with each URL kept in place as one normalized token (never split into words)."""
    out: list[str] = []
    pos = 0
    for m in _TEXT_URL.finditer(text):
        out += words(text[pos:m.start()])
        out.append("url:" + (normalize_url(m.group(0)) or "?"))
        pos = m.end()
    return out + words(text[pos:])


def html_matches_text(html: str, text: str) -> bool:
    """True when the HTML shows exactly the text part: same words in the same order, and every link
    target at the same position as the URL the text spells out there."""
    tree = LexborHTMLParser(sanitize_html(html))
    if tree.body is None:
        return not words(text)
    try:
        rendered = _render(tree.body, images=False, strict=True)
    except _Mismatch:
        return False
    return tokens(rendered) == tokens(text)
