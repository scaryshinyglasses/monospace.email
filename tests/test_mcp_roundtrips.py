"""Fewer round trips: filters, sync + inline text, many ids per call, `where` targets, batch."""

from datetime import datetime, timezone

import pytest
from mcp import Client

from monospace.core.query import MessageFilter, parse_when
from monospace.core.service import ServiceError
from monospace.mcp_server import build_server
from tests.fakes import make_raw
from tests.test_mcp import _data


@pytest.fixture
def more(server):
    server.deliver("INBOX", make_raw("Newsletter", sender="News <news@list.test>", msgid="<n@x>",
                                     date="Wed, 18 Nov 2026 08:00:00 +0530"))
    server.deliver("Archive", make_raw("Old report", sender="Ana <ana@example.com>", msgid="<o@x>",
                                       date="Sun, 01 Nov 2026 10:00:00 +0000"))
    return server


def test_parse_when():
    now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    assert parse_when("7d", now=now).startswith("2026-11-12T12:00")
    assert parse_when("2026-11-01T00:00+05:30").startswith("2026-10-31T18:30")
    with pytest.raises(ServiceError):
        parse_when("last tuesday")


def test_find_filters(svc, more):
    svc.sync(all_folders=True)
    subjects = lambda envs: [e.subject for e in envs]  # noqa: E731
    assert subjects(svc.find(MessageFilter(folder="all", from_="ANA@"))) == ["Invoice", "Welcome", "Old report"]
    assert subjects(svc.find(MessageFilter(has_attachments=True))) == ["Invoice"]
    assert subjects(svc.find(MessageFilter(folder="archive"))) == ["Old report"]
    # +0530 is normalized: 08:00+05:30 = 02:30Z on the 18th
    assert subjects(svc.find(MessageFilter(since="2026-11-18T02:00Z", before="2026-11-18T03:00Z"))) == ["Newsletter"]
    assert subjects(svc.find(MessageFilter(folder="all"), text="report")) == ["Old report"]
    svc.add_tags(svc.find(MessageFilter(subject="news"))[0].id, ["later"])
    assert subjects(svc.find(MessageFilter(folder="all", tags=["later"]))) == ["Newsletter"]
    assert subjects(svc.find(MessageFilter(folder="all"), text="tag:later")) == ["Newsletter"]
    assert subjects(svc.find(MessageFilter(subject="100%_"))) == []  # LIKE wildcards are escaped


async def test_list_syncs_and_inlines_text(svc, more):
    async with Client(build_server(lambda: svc)) as client:
        msgs = _data(await client.call_tool("list_messages", {"sync": True, "sender": "ana",
                                                             "include_text": 5}))
        assert [(m["subject"], m["text"], m["truncated"]) for m in msgs] == [
            ("Invoice", "see a", True), ("Welcome", "first", True)]
        assert _data(await client.call_tool("search", {"query": "report"})) == []  # archive not synced yet
        hits = _data(await client.call_tool("search", {"query": "report", "sync": True}))  # folder="all"
        assert [h["subject"] for h in hits] == ["Old report"]
        hits = _data(await client.call_tool("search", {"query": "first", "unread": True}))
        assert [h["subject"] for h in hits] == ["Welcome"]


async def test_read_many(svc):
    svc.sync()
    ids = [e.id for e in svc.list()]
    async with Client(build_server(lambda: svc)) as client:
        res = _data(await client.call_tool("read_message", {"id": [*ids, 999]}))
    assert [m.get("subject") for m in res["messages"]] == ["Invoice", "Welcome", None]
    assert "no message" in res["messages"][2]["error"] and "Untrusted" in res["notice"]


async def test_bulk_and_where(svc, server):
    svc.sync()
    ids = [e.id for e in svc.list()]
    async with Client(build_server(lambda: svc)) as client:
        res = _data(await client.call_tool("mark", {"id": [*ids, 999], "flagged": True}))
        assert (res["changed"], res["failed"]) == (2, 1)
        preview = _data(await client.call_tool("tag_message", {"tags": ["ana"], "dry_run": True,
                                                               "where": {"sender": "ana", "flagged": True}}))
        assert len(preview["would_change"]) == 2 and svc.list_tags() == []
        _data(await client.call_tool("tag_message", {"tags": ["ana"], "where": {"sender": "ana"}}))
        assert svc.list_tags() == [{"tag": "ana", "count": 2}]
        both = await client.call_tool("mark", {"id": ids[0], "where": {}, "read": True})
        assert both.is_error
        res = _data(await client.call_tool("move", {"id": ids, "folder": "archive"}))
        assert res["changed"] == 2 and server.folders["INBOX"]["msgs"] == {}
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "where" not in tools["move"].input_schema["properties"]


async def test_batch_is_read_only(svc, sent):
    svc.sync()
    wid = next(e.id for e in svc.list() if e.subject == "Welcome")
    writes = ["sync", "send_email", "create_draft", "update_draft", "save_attachment", "mark", "move",
              "tag_message", "delete_draft", "batch"]
    async with Client(build_server(lambda: svc)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["batch"].annotations.read_only_hint is True
        res = _data(await client.call_tool("batch", {"calls": [
            {"tool": "list_messages", "args": {"unread_only": True}},
            {"tool": "read_message", "args": {"id": 999}},
            {"tool": "read_message", "args": {"id": wid, "mark_read": True}},
            *({"tool": w, "args": {}} for w in writes),
        ]}))
        r = res["results"]
        assert [x["ok"] for x in r] == [True] + [False] * (2 + len(writes))
        assert [m["subject"] for m in r[0]["result"]] == ["Welcome"]
        assert "no message" in r[1]["error"] and "mark_read" in r[2]["error"]
        assert all("can't be batched" in x["error"] for x in r[3:])
        assert sent == [] and svc.envelope(wid).unread and svc.list(folder="drafts") == []
        stopped = _data(await client.call_tool("batch", {"stop_on_error": True, "calls": [
            {"tool": "read_message", "args": {"id": 999}}, {"tool": "list_tags"}]}))
        assert (stopped["failed"], stopped["skipped"]) == (1, 1)
