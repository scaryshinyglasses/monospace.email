"""Turn email HTML into a flat list of blocks the reader can stack: Markdown text + images.

Email HTML is mostly layout tables; we linearise it, keep emphasis/links/headings/lists,
and lift every ``<img>`` out as its own block (dropping 1×1 tracking pixels).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

SKIP = {"script", "style", "head", "title", "meta", "link", "noscript", "template", "svg"}
BLOCK = {"p", "div", "section", "article", "header", "footer", "table", "tr", "tbody", "thead", "center",
         "ul", "ol", "form", "main", "aside", "nav", "figure"}


@dataclass
class TextBlock:
    markdown: str


@dataclass
class ImageBlock:
    src: str
    alt: str = ""
    width: int | None = None
    height: int | None = None
    href: str = ""

    @property
    def is_remote(self) -> bool:
        return self.src.startswith(("http://", "https://", "//"))

    @property
    def cid(self) -> str | None:
        return self.src[4:].strip("<>") if self.src.lower().startswith("cid:") else None


Block = TextBlock | ImageBlock


def _dim(value: str | None) -> int | None:
    if not value:
        return None
    m = re.match(r"\s*(\d+)", value)
    return int(m.group(1)) if m else None


def _escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]#<>|])", r"\\\1", text)


class _Walker:
    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.list_depth = 0

    def flush(self) -> None:
        text = "".join(self.buf)
        text = re.sub(r"[ \t ]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            self.blocks.append(TextBlock(text))
        self.buf = []

    def nl(self, n: int = 2) -> None:
        self.buf.append("\n" * n)

    def walk(self, node: Node, href: str = "") -> None:
        child = node.child
        while child is not None:
            self.visit(child, href)
            child = child.next

    def inline(self, node: Node, href: str = "") -> str:
        sub = _Walker()
        sub.walk(node, href)
        sub.flush()
        imgs = [b for b in sub.blocks if isinstance(b, ImageBlock)]
        if imgs:
            self.flush()
            self.blocks.extend(imgs)
        return " ".join(b.markdown for b in sub.blocks if isinstance(b, TextBlock)).replace("\n", " ").strip()

    def visit(self, node: Node, href: str) -> None:
        tag = node.tag
        if tag == "-text":
            self.buf.append(_escape(node.text(deep=False) or ""))
            return
        if tag in SKIP or tag in ("-comment", "_comment", "!doctype"):
            return
        attrs = node.attributes
        style = (attrs.get("style") or "").replace(" ", "").lower()
        if "display:none" in style:
            return
        if tag == "img":
            w, h = _dim(attrs.get("width")), _dim(attrs.get("height"))
            if (w is not None and w <= 2) or (h is not None and h <= 2) or not attrs.get("src"):
                return
            self.flush()
            self.blocks.append(ImageBlock(attrs["src"] or "", attrs.get("alt") or "", w, h, href))
            return
        if tag == "br":
            self.buf.append("  \n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.nl()
            self.buf.append("#" * int(tag[1]) + " " + self.inline(node, href))
            self.nl()
        elif tag in ("strong", "b"):
            inner = self.inline(node, href)
            self.buf.append(f"**{inner}**" if inner else "")
        elif tag in ("em", "i"):
            inner = self.inline(node, href)
            self.buf.append(f"*{inner}*" if inner else "")
        elif tag == "a":
            link = attrs.get("href") or ""
            before = len(self.blocks)
            inner = self.inline(node, link)
            for b in self.blocks[before:]:
                if isinstance(b, ImageBlock) and not b.href:
                    b.href = link
            if inner and link and not link.startswith(("javascript:", "#")):
                self.buf.append(f"[{inner}]({link.replace(' ', '%20').replace(')', '%29')})")
            else:
                self.buf.append(inner)
        elif tag in ("ul", "ol"):
            self.nl()
            self.list_depth += 1
            for i, li in enumerate((c for c in node.iter() if c.tag == "li"), 1):
                bullet = f"{i}." if tag == "ol" else "-"
                self.buf.append("  " * (self.list_depth - 1) + f"{bullet} {self.inline(li, href)}\n")
            self.list_depth -= 1
            self.nl(1)
        elif tag == "blockquote":
            self.flush()
            inner = _Walker()
            inner.walk(node, href)
            inner.flush()
            for b in inner.blocks:
                self.blocks.append(TextBlock("\n".join("> " + ln for ln in b.markdown.splitlines()))
                                   if isinstance(b, TextBlock) else b)
        elif tag == "hr":
            self.nl()
            self.buf.append("---")
            self.nl()
        elif tag == "pre":
            self.nl()
            self.buf.append("```\n" + (node.text() or "").strip("\n") + "\n```")
            self.nl()
        elif tag in ("td", "th"):
            self.walk(node, href)
            self.buf.append(" ")
        else:
            if tag in BLOCK:
                self.nl()
            self.walk(node, href)
            if tag in BLOCK:
                self.nl()


def to_blocks(html: str) -> list[Block]:
    tree = HTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return []
    w = _Walker()
    w.walk(root)
    w.flush()
    return _merge(w.blocks)


def _merge(blocks: list[Block]) -> list[Block]:
    out: list[Block] = []
    for b in blocks:
        if isinstance(b, TextBlock) and out and isinstance(out[-1], TextBlock):
            out[-1] = TextBlock(out[-1].markdown + "\n\n" + b.markdown)
        else:
            out.append(b)
    return out


def text_to_blocks(text: str) -> list[Block]:
    """Plain-text bodies: keep line breaks, quote markers, and make bare URLs clickable."""
    if not text.strip():
        return []
    lines = []
    for line in text.splitlines():
        parts = _URL.split(line)
        out = "".join(f"<{p}>" if i % 2 else _escape(p) for i, p in enumerate(parts))
        lines.append(re.sub(r"^((?:\\>\s?)+)", lambda m: m.group(1).replace("\\>", ">"), out))
    return [TextBlock("  \n".join(lines))]


_URL = re.compile(r"(https?://[^\s<>)\]]+)")
