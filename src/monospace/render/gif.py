"""Decode animated images (GIF/APNG/WebP) into frames + per-frame durations."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageSequence

MAX_FRAMES = 300
MIN_DELAY_MS = 33   # cap at ~30 fps; browsers treat <20ms as 100ms
DEFAULT_DELAY_MS = 100


@dataclass
class Animation:
    frames: list[Image.Image]
    durations: list[int]  # milliseconds

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1


def is_animated(img: Image.Image) -> bool:
    return bool(getattr(img, "is_animated", False)) and getattr(img, "n_frames", 1) > 1


def frames(img: Image.Image, max_width: int = 960) -> Animation:
    out: list[Image.Image] = []
    durations: list[int] = []
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= MAX_FRAMES:
            break
        f = frame.convert("RGBA")
        if f.width > max_width:
            f = f.resize((max_width, max(1, int(f.height * max_width / f.width))))
        out.append(f)
        d = int(frame.info.get("duration") or DEFAULT_DELAY_MS)
        durations.append(DEFAULT_DELAY_MS if d < 20 else max(d, MIN_DELAY_MS))
    if not out:
        out, durations = [img.convert("RGBA")], [DEFAULT_DELAY_MS]
    return Animation(out, durations)
