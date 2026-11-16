import pytest

from monospace.core import policy
from monospace.core.compose import Draft
from monospace.core.service import ServiceError
from tests.fakes import parse


def test_policy_table():
    assert policy.decide("draft", "human") == "send"
    assert policy.decide("draft", "ai") == "draft"
    assert policy.decide("confirm", "ai") == "queue"
    assert policy.decide("allow", "ai") == "send"


def test_sync_list_read(svc, server):
    stats = svc.sync()
    assert stats.errors == [] and stats.new == 2
    envs = svc.list()
    assert [e.subject for e in envs] == ["Invoice", "Welcome"]
    assert envs[0].has_attachments and not envs[0].unread and envs[1].unread
    assert envs[1].snippet == "first message"  # prefetched
    assert {f["name"] for f in svc.folders()} >= {"INBOX", "Drafts", "Sent"}
    # incremental: a new delivery and a server-side deletion
    server.deliver("INBOX", b"From: z@z\r\nSubject: Third\r\nMessage-ID: <c@x>\r\n\r\nthree")
    del server.folders["INBOX"]["msgs"][1]
    stats = svc.sync()
    assert stats.new == 1 and stats.removed == 1
    assert sorted(e.subject for e in svc.list()) == ["Invoice", "Third"]


def test_read_marks_and_attachment(svc, server, tmp_path):
    svc.sync()
    welcome = next(e for e in svc.list() if e.subject == "Welcome")
    env, pm = svc.read(welcome.id, mark_read=True)
    assert not env.unread and "\\Seen" in server.folders["INBOX"]["msgs"][welcome.uid]["flags"]
    invoice = next(e for e in svc.list() if e.subject == "Invoice")
    path = svc.save_attachment(invoice.id, 0, str(tmp_path / "dl"))
    assert path.read_bytes() == b"%PDF-1"
    with pytest.raises(ServiceError):
        svc.attachment(invoice.id, 5)


def test_archive_moves(svc, server):
    svc.sync()
    e = svc.list()[0]
    assert svc.archive(e.id) == "Archive"
    assert len(server.folders["Archive"]["msgs"]) == 1 and svc.store.get(e.id) is None


def test_ai_send_policies(svc, server, sent):
    d = Draft(to=["x@y.com"], subject="From AI", body="hello")
    acct = svc.account()
    # default: draft
    res = svc.submit(None, d, "ai")
    assert res.status == "drafted" and sent == []
    drafted = list(server.folders["Drafts"]["msgs"].values())[0]
    assert "\\Draft" in drafted["flags"] and parse(drafted["raw"])["Subject"] == "From AI"
    # confirm: queue, then approve
    acct.ai_send = "confirm"
    res = svc.submit(None, d, "ai")
    assert res.status == "queued" and sent == [] and len(svc.outbox()) == 1
    svc.outbox_approve(res.outbox_id)
    assert len(sent) == 1 and svc.outbox() == []
    # allow: direct
    acct.ai_send = "allow"
    assert svc.submit(None, d, "ai").status == "sent" and len(sent) == 2
    # custom provider appends to Sent
    assert len(server.folders["Sent"]["msgs"]) == 2
    with pytest.raises(ServiceError):
        svc.submit(None, Draft(subject="no rcpt"), "human")


def test_reply_threads(svc, sent):
    svc.sync()
    e = next(x for x in svc.list() if x.subject == "Welcome")
    svc.reply(e.id, "thanks!", source="human")
    msg, rcpts = sent[0]
    assert rcpts == ["ana@example.com"] and msg["In-Reply-To"] == "<a@x>" and msg["Subject"] == "Re: Welcome"
