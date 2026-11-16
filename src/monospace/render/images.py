"""Resolve ImageBlocks to PIL images: inline cid parts, data: URIs, and (opt-in) remote URLs."""

from __future__ import annotations

import base64
import io
import threading
from urllib.parse import unquote

from PIL import Image

from monospace.core.mime import ParsedMessage
from monospace.render.html_blocks import ImageBlock

MAX_BYTES = 8 * 1024 * 1024
_remote_cache: dict[str, bytes] = {}
_lock = threading.Lock()


class ImageUnavailable(Exception):
    pass


def _open(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load() if not getattr(img, "is_animated", False) else None
        return img
    except Exception as exc:
        raise ImageUnavailable(f"cannot decode image: {exc}") from exc


def _data_uri(src: str) -> bytes:
    header, _, payload = src.partition(",")
    if ";base64" in header:
        return base64.b64decode(payload)
    return unquote(payload).encode()


def fetch_remote(url: str, timeout: float = 10.0) -> bytes:
    import httpx

    if url.startswith("//"):
        url = "https:" + url
    with _lock:
        if url in _remote_cache:
            return _remote_cache[url]
    with httpx.Client(follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": "monospace.email image fetcher"}) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise ImageUnavailable("image larger than 8 MB")
    data = bytes(buf)
    with _lock:
        _remote_cache[url] = data
    return data


def load(block: ImageBlock, pm: ParsedMessage | None, *, allow_remote: bool) -> Image.Image:
    """Blocking; call from a worker thread. Raises ImageUnavailable with a user-facing reason."""
    cid = block.cid
    if cid is not None:
        part = (pm.inline if pm else {}).get(cid)
        if part is None and pm:  # some senders reference attachments by filename
            part = next((p for p in pm.attachments if p.content_id == cid or p.filename == cid), None)
        if part is None:
            raise ImageUnavailable(f"missing inline image {cid}")
        return _open(part.data)
    if block.src.startswith("data:"):
        return _open(_data_uri(block.src))
    if block.is_remote:
        if not allow_remote:
            raise ImageUnavailable("remote image blocked (press i to load)")
        try:
            return _open(fetch_remote(block.src))
        except ImageUnavailable:
            raise
        except Exception as exc:
            raise ImageUnavailable(f"could not fetch image: {exc}") from exc
    raise ImageUnavailable(f"unsupported image source {block.src[:40]}")
