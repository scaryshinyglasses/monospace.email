import pytest

from monospace.core import paths, platform
from monospace.core.compose import Draft
from monospace.core.policy import AttachmentPolicyError, agent_attachment_dir


def test_agent_cannot_attach_outside_folder(svc, tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    svc.account().ai_send = "allow"
    for attempt in (str(secret), "../../id_rsa", "~/.ssh/id_rsa"):
        with pytest.raises(AttachmentPolicyError):
            svc.submit(None, Draft(to=["evil@x.com"], subject="s", attachments=[attempt]), "ai")
    with pytest.raises(AttachmentPolicyError):
        svc.save_draft(None, Draft(to=["x@y"], attachments=[str(secret)]), "ai")
    # symlink escaping the folder is rejected too
    allowed = agent_attachment_dir()
    (allowed / "link").symlink_to(secret)
    with pytest.raises(AttachmentPolicyError):
        svc.submit(None, Draft(to=["evil@x.com"], subject="s", attachments=["link"]), "ai")


def test_agent_attachment_allowed_and_snapshotted(svc, sent):
    allowed = agent_attachment_dir()
    (allowed / "report.txt").write_text("v1")
    svc.account().ai_send = "confirm"
    res = svc.submit(None, Draft(to=["x@y.com"], subject="s", attachments=["report.txt"]), "ai")
    queued = svc.store.outbox_get(res.outbox_id)["draft"]["attachments"][0]
    assert str(paths.data_dir() / "outbox-files") in queued
    (allowed / "report.txt").write_text("v2-swapped-after-review")
    svc.outbox_approve(res.outbox_id)
    msg, _ = sent[0]
    [att] = list(msg.iter_attachments())
    assert att.get_content() == "v1"


def test_humans_can_attach_anything(svc, sent, tmp_path):
    f = tmp_path / "mine.txt"
    f.write_text("hi")
    svc.submit(None, Draft(to=["x@y.com"], subject="s", attachments=[str(f)]), "human")
    assert len(sent) == 1


def test_safe_open_and_urls():
    assert platform.safe_to_open("a.pdf") and platform.safe_to_open("pic.PNG")
    assert not platform.safe_to_open("run.sh") and not platform.safe_to_open("x.app")
    assert not platform.safe_to_open("invoice.docm") and not platform.safe_to_open("noext")
    assert platform.safe_url("https://ex.com") and not platform.safe_url("file:///etc/passwd")
    assert not platform.safe_url("javascript:alert(1)") and not platform.safe_url("smb://host/share")
