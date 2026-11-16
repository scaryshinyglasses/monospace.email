import base64
import io

import pytest
from PIL import Image

from monospace.core import mime
from monospace.render import gif, images
from monospace.render.html_blocks import ImageBlock, TextBlock, text_to_blocks, to_blocks

HTML = """<html><head><style>p{color:red}</style></head><body>
<table><tr><td><h1>Big <i>News</i></h1></td></tr>
<tr><td><p>Hello <b>reader</b>, see <a href="https://ex.com/a">our site</a>.</p>
<a href="https://ex.com/promo"><img src="cid:hero" alt="Hero" width="600"></a>
<img src="https://track.ex.com/p.gif" width="1" height="1">
<ul><li>one</li><li>two</li></ul><div style="display: none">hidden preheader</div>
</td></tr></table></body></html>"""


def test_html_to_blocks():
    blocks = to_blocks(HTML)
    kinds = [type(b).__name__ for b in blocks]
    assert kinds == ["TextBlock", "ImageBlock", "TextBlock"]
    first, img, last = blocks
    assert "# Big *News*" in first.markdown and "**reader**" in first.markdown
    assert "[our site](https://ex.com/a)" in first.markdown and "p{color" not in first.markdown
    assert img.cid == "hero" and img.alt == "Hero" and img.width == 600 and img.href == "https://ex.com/promo"
    assert "- one" in last.markdown and "hidden" not in last.markdown


def test_text_blocks_link_urls():
    [b] = text_to_blocks("see https://ex.com/x_y\n> quoted *not bold*")
    assert "<https://ex.com/x_y>" in b.markdown and "> quoted" in b.markdown and "\\*not" in b.markdown


def _gif(n=3) -> bytes:
    frames = [Image.new("RGB", (20, 10), (i * 80, 0, 0)) for i in range(n)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=[50, 5, 120], loop=0)
    return buf.getvalue()


def test_gif_frames():
    anim = gif.frames(Image.open(io.BytesIO(_gif())))
    assert anim.animated and len(anim.frames) == 3
    assert anim.durations[0] == 50 and anim.durations[1] == gif.DEFAULT_DELAY_MS


def test_image_loading_sources():
    pm = mime.ParsedMessage(inline={"hero": mime.Part("h.gif", "image/gif", _gif(), "hero", True)})
    assert gif.is_animated(images.load(ImageBlock("cid:hero"), pm, allow_remote=False))
    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
    assert images.load(ImageBlock(uri), None, allow_remote=False).size == (4, 4)
    with pytest.raises(images.ImageUnavailable, match="blocked"):
        images.load(ImageBlock("https://ex.com/x.png"), None, allow_remote=False)
    with pytest.raises(images.ImageUnavailable, match="missing"):
        images.load(ImageBlock("cid:nope"), pm, allow_remote=False)
    assert isinstance(TextBlock("x"), TextBlock)
