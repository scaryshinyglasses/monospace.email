"""Tags: validation, the three backends (local / IMAP keyword / Gmail label), sync, search, drafts."""

import pytest

from monospace.core.compose import Draft
from monospace.core.service import ServiceError
from monospace.core.store import Store
from monospace.core.tags import normalize_tags, split_tag_query, tags_from_flags, tags_from_labels
from tests.fakes import make_raw


def _inbox(svc, subject):
    svc.sync()
    return next(e for e in svc.list() if e.subject == subject)


def test_normalize_and_reserved():
    assert normalize_tags(["work, #Big Client", "WORK", " x "]) == ["work", "Big Client", "x"]
    for bad in ("\\Seen", "$Junk", "Inbox", "important", "_x", "a\"b", ""):
        with pytest.raises(ServiceError):
            normalize_tags([bad])
    assert tags_from_flags(["\\Seen", "$Forwarded", "work", "NonJunk"]) == ["work"]
    assert tags_from_labels(["\\Inbox", "\\Important", "work", "Big Client"]) == ["work", "Big Client"]
    assert split_tag_query('invoice tag:work tag:"big client" due') == ("invoice due", ["work", "big client"])


def test_local_backend_survives_move_and_resync(svc, server):
    env = _inbox(svc, "Welcome")
    env = svc.add_tags(env.id, ["work", "later"])
    assert env.tags == ["later", "work"]
    assert server.folders["INBOX"]["msgs"][env.uid]["flags"] == set()  # nothing on the server
    assert svc.tag_backend(None) == "local"
    svc.move(env.id, "archive")
    svc.sync(folders=["archive"])
    moved = svc.list(folder="archive")[0]
    assert moved.tags == ["later", "work"]  # keyed by Message-ID, not rowid
    assert svc.remove_tags(moved.id, "later").tags == ["work"]
    assert svc.list_tags() == [{"tag": "work", "count": 1}]


def test_reused_message_id_from_another_sender_gets_no_tags(svc, server):
    env = svc.add_tags(_inbox(svc, "Welcome").id, ["approved"])
    server.deliver("INBOX", make_raw("Pay me", sender="Eve <eve@evil.test>", msgid="<a@x>"))
    svc.sync()
    fake = next(e for e in svc.list() if e.subject == "Pay me")
    assert fake.tags == []
    assert [e.id for e in svc.tagged(None, "approved")] == [env.id]
    svc.add_tags(fake.id, "approved")
    svc.remove_tags(fake.id, "approved")  # untagging the impostor leaves the original alone
    assert svc.envelope(env.id).tags == ["approved"]


def test_pre_release_tag_rows_are_rekeyed(svc, tmp_path):
    env = svc.add_tags(_inbox(svc, "Welcome").id, ["work"])
    db = svc.store.db
    db.executescript("DROP TABLE tags; CREATE TABLE tags (account TEXT NOT NULL, message_id TEXT NOT NULL,"
                     " tag TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'local',"
                     " PRIMARY KEY (account, message_id, tag));")
    db.execute("INSERT INTO tags VALUES ('work', '<a@x>', 'work', 'local')")
    db.commit()
    assert Store(tmp_path / "cache.db").get(env.id).tags == ["work"]


def test_keyword_backend_writes_flags_and_picks_up_server_tags(svc, server):
    server.mode = "keyword"
    env = _inbox(svc, "Welcome")
    env = svc.add_tags(env.id, "work")
    assert "work" in server.folders["INBOX"]["msgs"][env.uid]["flags"]
    assert env.tags == ["work"] and "work" in env.flags
    with pytest.raises(ServiceError, match="IMAP keywords"):
        svc.add_tags(env.id, "big client")  # spaces can't be keywords
    # another client tags / untags on the server → the next sync mirrors it
    inv = next(e for e in svc.list() if e.subject == "Invoice")
    server.folders["INBOX"]["msgs"][inv.uid]["flags"] |= {"urgent", "$Forwarded"}
    server.folders["INBOX"]["msgs"][env.uid]["flags"].discard("work")
    svc.sync()
    assert svc.envelope(inv.id).tags == ["urgent"]
    assert svc.envelope(env.id).tags == []


def test_gmail_backend_uses_labels_and_hides_system_ones(svc, server):
    server.mode = "gmail"
    env = _inbox(svc, "Welcome")
    server.folders["INBOX"]["msgs"][env.uid]["labels"] |= {"\\Inbox", "\\Important"}
    env = svc.add_tags(env.id, ["Big Client"])
    assert server.folders["INBOX"]["msgs"][env.uid]["labels"] == {"\\Inbox", "\\Important", "Big Client"}
    svc.sync()
    assert svc.envelope(env.id).tags == ["Big Client"]
    svc.remove_tags(env.id, "big client")
    assert "\\Inbox" in server.folders["INBOX"]["msgs"][env.uid]["labels"]
    with pytest.raises(ServiceError):
        svc.remove_tags(env.id, "Inbox")  # can never archive through tags


def test_local_mode_override(svc, server):
    server.mode = "keyword"
    svc.cfg.accounts[0].tags = "local"
    env = _inbox(svc, "Welcome")
    svc.add_tags(env.id, "work")
    assert "work" not in server.folders["INBOX"]["msgs"][env.uid]["flags"]
    assert svc.tag_backend(None) == "local"


def test_set_tags_and_filters(svc):
    env = _inbox(svc, "Welcome")
    inv = next(e for e in svc.list() if e.subject == "Invoice")
    svc.set_tags(env.id, ["a", "b"])
    svc.set_tags(inv.id, "b")
    assert svc.set_tags(env.id, ["b", "c"]).tags == ["b", "c"]
    assert [e.subject for e in svc.list(tag="b")] == ["Invoice", "Welcome"]
    assert [e.subject for e in svc.search("tag:c")] == ["Welcome"]
    assert [e.subject for e in svc.search("tag:b tag:C")] == ["Welcome"]
    assert [e.subject for e in svc.search("first tag:b")] == ["Welcome"]
    assert [e.subject for e in svc.tagged(None, "B")] == ["Invoice", "Welcome"]
    assert svc.set_tags(env.id, []).tags == []


def test_draft_edit_keeps_tags(svc, server):
    server.mode = "keyword"
    res = svc.save_draft(None, Draft(to=["x@example.com"], subject="Plan", body="v1"))
    draft = svc.add_tags(res.draft_id, "project")
    res = svc.update_draft(draft.id, body="v2")
    new = svc.envelope(res.draft_id)
    assert new.uid != draft.uid and new.tags == ["project"]
    assert "project" in server.folders["Drafts"]["msgs"][new.uid]["flags"]


def test_untaggable_without_message_id(svc, server):
    server.deliver("INBOX", b"From: z@z\r\nSubject: NoId\r\n\r\nbody")
    env = _inbox(svc, "NoId")
    with pytest.raises(ServiceError, match="Message-ID"):
        svc.add_tags(env.id, "x")


async def test_mcp_tag_tools(svc):
    from mcp import Client

    from monospace.mcp_server import build_server
    from tests.test_mcp import _data

    async with Client(build_server(lambda: svc)) as client:
        await client.call_tool("sync", {})
        msg = _data(await client.call_tool("list_messages", {}))[0]
        res = _data(await client.call_tool("tag_message", {"id": msg["id"], "tags": ["work", "q4"]}))
        assert res["tags"] == ["q4", "work"]
        assert _data(await client.call_tool("list_tags", {})) == [{"tag": "q4", "count": 1}, {"tag": "work", "count": 1}]
        assert [m["id"] for m in _data(await client.call_tool("list_messages", {"tag": "q4"}))] == [msg["id"]]
        assert [m["id"] for m in _data(await client.call_tool("search", {"query": "tag:work"}))] == [msg["id"]]
        res = _data(await client.call_tool("untag_message", {"id": msg["id"], "tags": ["q4"]}))
        assert res["tags"] == ["work"]
        bad = await client.call_tool("tag_message", {"id": msg["id"], "tags": ["\\Seen"]})
        assert bad.is_error


def test_cli_tag_commands(svc, capsys, monkeypatch):
    import argparse

    from monospace.cli import tags as cli_tags

    svc.sync()
    env = svc.list()[0]
    ns = lambda **kw: argparse.Namespace(account=None, json=False, **kw)  # noqa: E731
    assert cli_tags.run("tag", ns(tag_cmd="add", id=env.id, tags=["a,b", "c"]), svc) == 0
    assert svc.envelope(env.id).tags == ["a", "b", "c"]
    cli_tags.run("tag", ns(tag_cmd="set", id=env.id, tags=["b"]), svc)
    capsys.readouterr()
    cli_tags.run("tag", ns(tag_cmd="ls"), svc)
    assert capsys.readouterr().out.split() == ["1", "b"]
    cli_tags.run("tag", ns(tag_cmd="show", tag="b", folder=None, limit=10), svc)
    assert "#b" in capsys.readouterr().out


async def test_tui_tag_prompt_and_tag_view(svc):
    from monospace.ui.app import MonospaceApp
    from monospace.ui.screens.prompt import PromptScreen
    from monospace.ui.widgets.folder_tree import FolderTree
    from monospace.ui.widgets.message_list import MessageList
    from tests.test_app import _open

    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        env = await _open(pilot, app, "Welcome")
        app.screen.query_one(MessageList).focus()
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        await pilot.press(*"work, q4", "enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert svc.envelope(env.id).tags == ["q4", "work"]
        tree = app.screen.query_one(FolderTree)
        branch = next(n for n in tree.root.children[0].children if "Tags" in str(n.label))
        assert [n.data[2] for n in branch.children] == ["q4", "work"]
        app.screen.post_message(FolderTree.Chosen("work", "", "q4"))
        await pilot.pause()
        table = app.screen.query_one(MessageList)
        assert [e.subject for e in table.envelopes.values()] == ["Welcome"]
        assert "#q4" in str(table.get_row(str(env.id))[2])
