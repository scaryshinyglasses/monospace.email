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
_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def terminal_safe(text: str) -> str:
    """Drop C0/C1 controls (keeps \\n and \\t) and bidi overrides / zero-width characters."""
    return _CTRL.sub("", text)


def sanitize_html(html: str) -> str:
    tree = LexborHTMLParser(html)
    for node in tree.css(DROP):
        node.decompose()
    body = tree.body
    if body is None:
        return ""
    for node in list(body.traverse(include_text=False)):
        tag = (node.tag or "").lower()
        if tag.startswith("-") or node.is_comment_node:
            node.decompose()
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
    # Comments: Outlook renders <!--[if mso]> blocks, so anything in a comment is unreviewed content.
    return re.sub(r"<!--.*?-->", "", body.inner_html or "", flags=re.S)


def review_text(html: str) -> str:
    """Plain text of (sanitized) HTML with link targets and inline images made explicit."""
    from monospace.core.mime import html_to_text

    tree = LexborHTMLParser(sanitize_html(html))
    for a in tree.css("a[href]"):
        a.insert_after(f" <{a.attributes.get('href')}>")
    for img in tree.css("img"):
        img.insert_after(f"[image {img.attributes.get('alt') or ''} ({img.attributes.get('src')})]")
    body = tree.body
    return terminal_safe(html_to_text(body.inner_html if body else ""))


def words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[^\W_]+", text)]


def html_matches_text(html: str, text: str) -> bool:
    """True when the HTML shows exactly the text part's words, in order (formatting aside)."""
    from monospace.core.mime import html_to_text

    tree = LexborHTMLParser(sanitize_html(html))
    body = tree.body
    return words(html_to_text(body.inner_html if body else "")) == words(text)
