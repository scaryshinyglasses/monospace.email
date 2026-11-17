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


def test_safe_open_and_urls(tmp_path):
    def f(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    assert platform.safe_to_open(f("a.pdf", b"%PDF-1.7 ..."))
    assert platform.safe_to_open(f("pic.PNG", b"\x89PNG\r\n\x1a\n...."))
    assert platform.safe_to_open(f("notes.txt", "héllo".encode()))
    assert not platform.safe_to_open(f("fake.pdf", b"#!/bin/sh\nrm -rf ~"))  # extension lies
    assert not platform.safe_to_open(f("logo.svg", b"<svg onload=alert(1)>"))
    for name in ("run.sh", "x.app", "invoice.docm", "noext"):
        assert not platform.safe_to_open(f(name, b"%PDF-"))
    assert platform.safe_url("https://ex.com") and not platform.safe_url("file:///etc/passwd")
    assert not platform.safe_url("javascript:alert(1)") and not platform.safe_url("smb://host/share")


def test_approve_refuses_unsnapshotted_ai_item(svc, tmp_path):
    from monospace.core.service import ServiceError

    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    item = svc.store.outbox_add("work", Draft(to=["x@y.com"], attachments=[str(secret)]).to_dict(), "ai")
    with pytest.raises(ServiceError, match="snapshot"):
        svc.outbox_approve(item)


def test_agent_read_dirs(svc, tmp_path, monkeypatch):
    import pytest

    from monospace.core import compose, policy
    from monospace.core.compose import Draft

    brand = tmp_path / "brand"
    brand.mkdir()
    (brand / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    d = Draft(to=["x@y.com"], subject="s", body="b")
    compose.add_inline(d, str(brand / "logo.png"))
    with pytest.raises(policy.AttachmentPolicyError, match="agent-dirs add"):
        svc.save_draft(None, Draft(**{**d.to_dict()}), "ai")
    svc.cfg.settings.agent_read_dirs = [str(brand)]
    assert svc.save_draft(None, d, "ai").status == "drafted"  # used in place, no copy
    # unsafe folders are refused
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    for bad in (str(tmp_path), str(tmp_path / ".ssh"), "/"):
        with pytest.raises(policy.AttachmentPolicyError):
            policy.validate_read_dir(bad)
    assert policy.validate_read_dir(str(brand)) == brand.resolve()


def test_agent_dirs_cli_refuses_agents(svc, monkeypatch, tmp_path):
    from monospace.cli import main as cli

    monkeypatch.setattr("monospace.core.config.load", lambda: svc.cfg)
    monkeypatch.setenv("MONOSPACE_AGENT", "1")
    assert cli.main(["agent-dirs", "add", str(tmp_path)]) == 2
    assert svc.cfg.settings.agent_read_dirs == []


def test_hidden_and_key_files_refused_inside_roots(svc, tmp_path, monkeypatch):
    import pytest

    from monospace.core import policy

    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "projects"
    (proj / ".git").mkdir(parents=True)
    for rel in (".env", ".git/config", "server.pem", "id_ed25519", "logo.png"):
        (proj / rel).write_text("x")
    roots = [proj]
    for rel in (".env", ".git/config", "server.pem", "id_ed25519"):
        with pytest.raises(policy.AttachmentPolicyError):
            policy.check_agent_attachments([str(proj / rel)], roots)
    assert policy.check_agent_attachments([str(proj / "logo.png")], roots)
    with pytest.raises(policy.AttachmentPolicyError):
        policy.validate_read_dir("/etc")  # outside home
    (tmp_path / "Library" / "Mail").mkdir(parents=True)
    with pytest.raises(policy.AttachmentPolicyError):
        policy.validate_read_dir(str(tmp_path / "Library" / "Mail"))
