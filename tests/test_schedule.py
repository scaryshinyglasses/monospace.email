"""Tests for scheduling emails and drafts across core Service, CLI, MCP and Textual TUI."""

from datetime import datetime, timedelta, timezone

import pytest
from mcp import Client

from monospace.cli import main as cli
from monospace.core import policy
from monospace.core.compose import Draft
from monospace.core.schedule import parse_send_at
from monospace.core.service import ServiceError
from monospace.mcp_server import build_server
from monospace.ui.app import MonospaceApp
from monospace.ui.screens.compose import ComposeScreen
from monospace.ui.screens.main import MainScreen
from monospace.ui.screens.outbox import OutboxScreen
from monospace.ui.screens.prompt import PromptScreen
from monospace.ui.widgets.message_list import MessageList
from tests.test_cli_tui_drafts import _cli
from tests.test_drafts import PNG, _drafts
from tests.test_mcp import _data


def test_parse_send_at_formats():
    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    assert parse_send_at("30m", now=now) == "2026-11-20T10:30:00+00:00"
    assert parse_send_at("in 2h", now=now) == "2026-11-20T12:00:00+00:00"
    assert parse_send_at("+1d", now=now) == "2026-11-21T10:00:00+00:00"
    assert datetime.fromisoformat(parse_send_at("tomorrow", now=now)) > now
    assert datetime.fromisoformat(parse_send_at("tomorrow 14:30", now=now)) > now
    assert datetime.fromisoformat(parse_send_at("tomorrow at 9am", now=now)) > now
    assert datetime.fromisoformat(parse_send_at("monday 10:30", now=now)) > now
    assert datetime.fromisoformat(parse_send_at("23:45", now=now)) > now
    assert datetime.fromisoformat(parse_send_at("2026-11-25", now=now)) > now
    assert parse_send_at("2026-11-21T09:00:00Z", now=now) == "2026-11-21T09:00:00+00:00"
    with pytest.raises(ServiceError, match="future"):
        parse_send_at("2026-11-19T09:00:00Z", now=now)
    with pytest.raises(ServiceError):
        parse_send_at("not-a-date", now=now)


def test_schedule_mail_and_ai_policies(svc, server, sent):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    d = Draft(to=["bob@example.com"], subject="Scheduled hello", body="See you soon")

    # 1. Human schedules an email → status='scheduled', not sent until due
    res = svc.submit(None, d, "human", send_at=future)
    assert res.status == "scheduled" and res.send_at == future and res.outbox_id
    assert sent == [] and len(svc.scheduled()) == 1
    assert svc.flush_scheduled() == [] and sent == []
    flushed = svc.flush_scheduled(now=later)
    assert len(flushed) == 1 and len(sent) == 1 and svc.scheduled() == []

    # 2. AI with ai_send=draft → saves draft with X-Monospace-Send-At, does NOT schedule active send
    acct = svc.account()
    acct.ai_send = "draft"
    res_draft = svc.submit(None, Draft(to=["a@b.c"], subject="AI draft sched", body="hi"), "ai", send_at=future)
    assert res_draft.status == "drafted" and res_draft.send_at == future and res_draft.outbox_id is None
    assert svc.scheduled() == []
    got = svc.get_draft(res_draft.draft_id)
    assert got["send_at"] == future and got["outbox_id"] is None
    assert svc.flush_scheduled(now=later) == [] and len(sent) == 1

    # 3. AI with ai_send=confirm → status='queued' (pending); flush_scheduled NEVER sends unapproved items!
    acct.ai_send = "confirm"
    res_q = svc.submit(None, Draft(to=["a@b.c"], subject="AI confirm sched", body="hi"), "ai", send_at=future)
    assert res_q.status == "queued" and res_q.send_at == future and res_q.outbox_id
    assert svc.flush_scheduled(now=later) == [] and len(sent) == 1
    # Human approves while send_at is still in the future → transitions to 'scheduled'
    app_res = svc.outbox_approve(res_q.outbox_id)
    assert app_res.status == "scheduled" and len(sent) == 1
    assert len(svc.flush_scheduled(now=later)) == 1 and len(sent) == 2

    # 4. AI with ai_send=allow → schedules directly
    acct.ai_send = "allow"
    res_allow = svc.submit(None, Draft(to=["a@b.c"], subject="AI allow sched", body="hi"), "ai", send_at=future)
    assert res_allow.status == "scheduled" and len(sent) == 2
    assert len(svc.flush_scheduled(now=later)) == 1 and len(sent) == 3


@pytest.mark.parametrize("provider", ["custom", "gmail"])
def test_schedule_draft_lifecycle(svc, server, sent, provider):
    acct = svc.account()
    acct.provider = provider
    acct.folders["drafts"] = "Drafts"
    t1 = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    t2 = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds")

    # Create a scheduled draft
    saved = svc.save_draft(None, Draft(to=["x@y.com"], subject="V1", body="first"), send_at=t1)
    assert saved.status == "drafted" and saved.send_at == t1 and saved.outbox_id
    assert len(svc.scheduled()) == 1
    info = svc.get_draft(saved.draft_id)
    assert info["send_at"] == t1 and info["outbox_id"] == saved.outbox_id

    # Edit the draft body (keeps existing schedule and updates the scheduled payload even on Gmail)
    edited = svc.update_draft(saved.draft_id, subject="V2", body="revised")
    assert edited.send_at == t1 and edited.outbox_id
    items = svc.scheduled()
    assert len(items) == 1 and items[0]["draft"]["subject"] == "V2"

    # Reschedule via svc.reschedule → updates both outbox and the IMAP draft header
    resched = svc.reschedule(edited.outbox_id, t2)
    assert resched["send_at"] == t2
    new_draft_id = svc.list(folder="drafts")[0].id
    assert svc.get_draft(new_draft_id)["send_at"] == t2

    # When due, flush_scheduled sends the revised draft AND removes it from IMAP Drafts
    due = datetime.now(timezone.utc) + timedelta(hours=5)
    flushed = svc.flush_scheduled(now=due)
    assert len(flushed) == 1 and len(sent) == 1
    assert sent[0][0]["Subject"] == "V2"
    assert sent[0][0]["X-Monospace-Send-At"] is None  # internal header never leaked to recipients
    assert _drafts(server) == [] and svc.list(folder="drafts") == []


def test_unschedule_and_delete_scheduled_draft(svc, server, sent):
    t1 = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    saved = svc.save_draft(None, Draft(to=["x@y.com"], subject="Draft", body="hi"))
    sched = svc.schedule_draft(saved.draft_id, t1)
    assert sched.status == "scheduled" and len(svc.scheduled()) == 1

    # Unschedule via update_draft(send_at="") keeps the draft in Drafts but removes the schedule
    unsched = svc.update_draft(sched.draft_id, send_at="")
    assert unsched.send_at is None and svc.scheduled() == []
    assert svc.get_draft(unsched.draft_id)["send_at"] is None

    # Re-schedule, then delete the draft → cancels the scheduled outbox entry too
    sched2 = svc.send_draft(unsched.draft_id, send_at=t1)
    assert sched2.status == "scheduled" and len(svc.scheduled()) == 1
    svc.delete_draft(sched2.draft_id)
    assert svc.scheduled() == [] and _drafts(server) == []
    assert svc.flush_scheduled(now=datetime.now(timezone.utc) + timedelta(hours=5)) == [] and sent == []


def test_cli_schedule_mails_and_drafts( svc, server, sent, monkeypatch, capsys):
    monkeypatch.setattr("monospace.core.config.load", lambda: svc.cfg)
    monkeypatch.setattr("monospace.core.service.Service", lambda cfg: svc)
    monkeypatch.setenv("MONOSPACE_AGENT", "1")
    d = policy.agent_attachment_dir(svc.cfg.settings.agent_attachment_dir)
    (d / "logo.png").write_bytes(PNG)
    svc.account().ai_send = "allow"

    res = _cli(capsys, "send", "--to", "bob@example.com", "--subject", "Later", "--body", "hi",
               "--at", "2h", "--json")
    assert res["status"] == "scheduled" and res["send_at"] and sent == []

    # Draft creation, scheduling, rescheduling, listing, cancelling and flushing via CLI
    dr = _cli(capsys, "draft", "--to", "ana@example.com", "--subject", "Draft later", "--body", "hello", "--json")
    ds = _cli(capsys, "drafts", "schedule", str(dr["draft_id"]), "3h", "--json")
    assert ds["status"] == "scheduled" and ds["outbox_id"]
    listed = _cli(capsys, "schedule", "list", "--json")
    assert len(listed) == 2

    t_new = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
    upd = _cli(capsys, "schedule", "reschedule", str(ds["outbox_id"]), t_new, "--json")
    assert upd["send_at"] == t_new

    _cli(capsys, "schedule", "cancel", str(res["outbox_id"]), "--json")
    assert len(_cli(capsys, "schedule", "list", "--json")) == 1

    # Unschedule the draft via CLI
    curr_draft_id = svc.list(folder="drafts")[0].id
    unsched = _cli(capsys, "drafts", "unschedule", str(curr_draft_id), "--json")
    assert unsched.get("send_at") is None and _cli(capsys, "schedule", "list", "--json") == []


async def test_mcp_schedule_tools_and_batch(svc, server, sent):
    svc.account().ai_send = "allow"
    async with Client(build_server(lambda: svc)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"schedule_list", "reschedule"} <= names
        assert not any("approve" in n for n in names)

        res = _data(await client.call_tool("send_email", {
            "to": ["x@y.com"], "subject": "MCP scheduled", "body": "hello", "send_at": "2h"}))
        assert res["status"] == "scheduled" and res["send_at"] and sent == []

        draft = _data(await client.call_tool("create_draft", {
            "to": ["z@y.com"], "subject": "MCP draft", "body": "draft body", "send_at": "3h"}))
        assert draft["status"] == "drafted" and draft["send_at"] and draft["outbox_id"]

        # schedule_list is read-only and works inside batch
        batched = _data(await client.call_tool("batch", {"calls": [{"tool": "schedule_list", "args": {}}]}))
        assert batched["ok"] == 1 and len(batched["results"][0]["result"]) == 2

        new_time = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(timespec="seconds")
        moved = _data(await client.call_tool("reschedule", {"outbox_id": res["outbox_id"], "send_at": new_time}))
        assert moved["send_at"] == new_time

        await client.call_tool("outbox_cancel", {"outbox_id": res["outbox_id"]})
        await client.call_tool("outbox_cancel", {"outbox_id": draft["outbox_id"]})
        assert _data(await client.call_tool("schedule_list", {})) == []


async def test_tui_compose_schedule_and_outbox_screen(svc, server, sent):
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        # 1. Compose a message and schedule it via #send_at
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        app.screen.query_one("#to").value = "bob@example.com"
        app.screen.query_one("#subject").value = "TUI Scheduled"
        app.screen.query_one("#body").text = "Scheduled from TUI"
        app.screen.query_one("#send_at").value = "2h"
        await pilot.press("ctrl+s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert sent == [] and len(svc.scheduled()) == 1

        # 2. Schedule an existing draft from MainScreen with 'S'
        saved = svc.save_draft(None, Draft(to=["ana@example.com"], subject="Draft to schedule", body="hi"))
        main = app.screen
        assert isinstance(main, MainScreen)
        main.account, main.folder = "work", "Drafts"
        main.refresh_from_cache()
        main.query_one(MessageList).focus()
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        app.screen.query_one("#value").value = "3h"
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(svc.scheduled()) == 2 and saved.draft_id

        # 3. Open OutboxScreen ('O'), send first scheduled item immediately ('y'), cancel second ('n')
        await pilot.press("O")
        await pilot.pause()
        assert isinstance(app.screen, OutboxScreen)
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(sent) == 1 and len(svc.scheduled()) == 1
        await pilot.press("n")
        await pilot.pause()
        assert svc.scheduled() == []
