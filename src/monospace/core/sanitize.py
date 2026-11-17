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
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def terminal_safe(text: str) -> str:
    """Drop C0/C1 controls (keeps \\n and \\t) and bidi overrides / zero-width characters."""
    return _CTRL.sub("", text)


def sanitize_html(html: str) -> str:
    """Allowlist-sanitize, repeated until stable so re-parsing can't resurrect anything."""
    out = _sanitize_once(html)
    for _ in range(3):
        again = _sanitize_once(out)
        if again == out:
            return out
        out = again
    return _text_only(out)  # never converged: fall back to escaped plain text


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
            elif name == "href" and not _SAFE_HREF.match(value.strip()):
                del node.attrs[name]
            elif name in ("width", "height") and not value.strip().isdigit():
                del node.attrs[name]
        if tag == "img" and not (node.attributes.get("src") or "").startswith("cid:"):
            node.decompose()  # remote / data: images can carry data out or show unreviewed text
    return body.inner_html or ""


def review_text(html: str, *, images: bool = True) -> str:
    """Plain text of (sanitized) HTML with link targets (and inline images) made explicit."""
    from monospace.core.mime import html_to_text

    tree = LexborHTMLParser(sanitize_html(html))
    for a in tree.css("a[href]"):
        a.insert_after(f" <{a.attributes.get('href')}>")
    for img in tree.css("img"):
        if images:
            img.insert_after(f"[image {img.attributes.get('alt') or ''} ({img.attributes.get('src')})]")
    body = tree.body
    return terminal_safe(html_to_text(body.inner_html if body else ""))


def words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[^\W_]+", text)]


def html_matches_text(html: str, text: str) -> bool:
    """True when the HTML shows exactly the text part's words in order — link targets included, so a
    link reading "invoice" can't quietly point somewhere the text part doesn't mention."""
    return words(review_text(html, images=False)) == words(text)
