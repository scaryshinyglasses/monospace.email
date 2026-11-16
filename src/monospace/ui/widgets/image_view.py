"""One inline image in the reader: placeholder → pixels (Kitty/Sixel/half-cell, auto) → animation.

``textual_image`` probes the terminal at import time, so this module must be imported
before the App starts (``monospace.ui.app`` does that).
"""

from __future__ import annotations

import math

from PIL import Image as PILImage
from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static
from textual_image.widget import Image as TerminalImage
from textual_image.widget import get_cell_size

from monospace.core.mime import ParsedMessage
from monospace.render import gif, images
from monospace.render.html_blocks import ImageBlock
from monospace.ui.glyphs import glyph

MAX_COLS = 100


def _label(block: ImageBlock, note: str) -> str:
    size = f" {block.width}×{block.height}" if block.width and block.height else ""
    alt = escape(block.alt) if block.alt else "image"
    return f"{glyph('image')} {alt}{size} — {escape(note)}"


class ImageView(Widget):
    DEFAULT_CSS = "ImageView { height: auto; }"

    def __init__(self, block: ImageBlock, pm: ParsedMessage | None, *, allow_remote: bool,
                 enabled: bool = True) -> None:
        super().__init__()
        self.block, self.pm = block, pm
        self.allow_remote, self.enabled = allow_remote, enabled
        self._anim: gif.Animation | None = None
        self._frame = 0
        self._timer: Timer | None = None
        self._img: TerminalImage | None = None

    def compose(self) -> ComposeResult:
        note = "loading…" if self.enabled else "images off"
        if self.enabled and self.block.is_remote and not self.allow_remote:
            note = "remote image blocked · press i to load"
        yield Static(_label(self.block, note), classes="placeholder")

    def on_mount(self) -> None:
        if self.enabled and (self.allow_remote or not self.block.is_remote):
            self.load()

    @work(thread=True, exclusive=True, group="image")
    def load(self) -> None:
        try:
            pil = images.load(self.block, self.pm, allow_remote=self.allow_remote)
            anim = gif.frames(pil) if gif.is_animated(pil) else gif.Animation([pil.convert("RGBA")], [0])
        except images.ImageUnavailable as exc:
            self.app.call_from_thread(self._set_note, str(exc))
            return
        except Exception as exc:  # never let a bad image break the reader
            self.app.call_from_thread(self._set_note, f"unreadable image ({exc})")
            return
        self.app.call_from_thread(self._show, anim)

    def _set_note(self, note: str) -> None:
        for s in self.query(".placeholder"):
            s.update(_label(self.block, note))  # type: ignore[attr-defined]

    def _columns(self, frame: PILImage.Image) -> int:
        try:
            cell_w = get_cell_size().width or 10
        except Exception:
            cell_w = 10
        px = self.block.width or frame.width
        avail = max(10, (self.parent.size.width if self.parent else MAX_COLS) - 2)
        return max(6, min(MAX_COLS, avail, math.ceil(px / cell_w)))

    def _show(self, anim: gif.Animation) -> None:
        self._anim = anim
        first = anim.frames[0]
        self.query(".placeholder").remove()
        self._img = TerminalImage(first)
        self._img.styles.width = self._columns(first)
        self._img.styles.height = "auto"
        if self.block.href:
            self._img.tooltip = self.block.href
        self.mount(self._img)
        if anim.animated:
            self._schedule()

    def _schedule(self) -> None:
        assert self._anim is not None
        delay = self._anim.durations[self._frame] / 1000
        self._timer = self.set_timer(delay, self._advance)

    def _advance(self) -> None:
        if self._anim is None or self._img is None or not self.is_mounted:
            return
        self._frame = (self._frame + 1) % len(self._anim.frames)
        if self.is_on_screen:  # don't burn CPU on frames nobody can see
            self._img.image = self._anim.frames[self._frame]
        self._schedule()

    def on_click(self) -> None:
        if self.block.href:
            self.app.open_link(self.block.href)  # type: ignore[attr-defined]

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()
