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
