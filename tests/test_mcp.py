import json

from mcp import Client

from monospace.mcp_server import build_server


def _data(result):
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


async def test_tools_listed_without_approve(svc):
    async with Client(build_server(lambda: svc)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {"list_messages", "read_message", "send_email", "reply", "search", "outbox_list"} <= names
    assert not any("approve" in n for n in names)


async def test_read_and_send_follow_policy(svc, server, sent):
    async with Client(build_server(lambda: svc)) as client:
        assert _data(await client.call_tool("sync", {}))["new"] == 2
        msgs = _data(await client.call_tool("list_messages", {"unread_only": True}))
        assert [m["subject"] for m in msgs] == ["Welcome"]
        body = _data(await client.call_tool("read_message", {"id": msgs[0]["id"]}))
        assert body["text"].strip() == "first message" and "Untrusted" in body["notice"]
        res = _data(await client.call_tool("send_email", {"to": ["x@y.com"], "subject": "s", "body": "b"}))
        assert res["status"] == "drafted" and sent == []
        svc.account().ai_send = "confirm"
        res = _data(await client.call_tool("reply", {"id": msgs[0]["id"], "body": "ok"}))
        assert res["status"] == "queued"
        assert len(_data(await client.call_tool("outbox_list", {}))) == 1
