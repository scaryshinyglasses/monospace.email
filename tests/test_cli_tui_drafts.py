"""Draft / inline-image / folder controls through the CLI and the TUI."""

import json

import pytest

from monospace.cli import main as cli
from monospace.core import compose, policy
from monospace.core.compose import Draft
from monospace.ui.app import MonospaceApp
from monospace.ui.screens.compose import ComposeScreen
from monospace.ui.screens.main import MainScreen
from monospace.ui.screens.prompt import PromptScreen
from monospace.ui.widgets.message_list import MessageList
from tests.test_drafts import PNG, _drafts, _parts


@pytest.fixture
def cli_svc(svc, monkeypatch):
    """Route the CLI to the fake-backed service, as an agent (non-interactive)."""
    monkeypatch.setattr("monospace.core.config.load", lambda: svc.cfg)
    monkeypatch.setattr("monospace.core.service.Service", lambda cfg: svc)
    monkeypatch.setenv("MONOSPACE_AGENT", "1")
    d = policy.agent_attachment_dir(svc.cfg.settings.agent_attachment_dir)
    (d / "logo.png").write_bytes(PNG)
    (d / "terms.pdf").write_bytes(b"%PDF-1")
    return svc


def _cli(capsys, *argv) -> object:
    assert cli.main(list(argv)) == 0
    out = capsys.readouterr().out
    return json.loads(out) if out.strip().startswith(("{", "[")) else out


def test_inline_arg_parsing():
    assert compose.parse_inline_arg("logo.png;width=120;position=top") == \
        {"path": "logo.png", "width": 120, "position": "top"}
    with pytest.raises(ValueError):
        compose.parse_inline_arg("logo.png;size=3")


def test_cli_draft_lifecycle(cli_svc, server, capsys):
    made = _cli(capsys, "draft", "--to", "x@y.com", "--subject", "Hi", "--body", "hello",
                "--attach", "terms.pdf", "--json")
    edited = _cli(capsys, "drafts", "edit", str(made["draft_id"]), "--inline",
                  "logo.png;width=90;position=top", "--subject", "Hi there", "--json")
    shown = _cli(capsys, "drafts", "show", str(edited["draft_id"]), "--json")
    assert shown["subject"] == "Hi there"
    assert [a["filename"] for a in shown["attachments"]] == ["terms.pdf"]
    assert [i["filename"] for i in shown["inline_images"]] == ["logo.png"]
    assert 'width="90"' in _parts(_drafts(server)[0])["html"]
    edited = _cli(capsys, "drafts", "edit", str(edited["draft_id"]), "--detach", "terms.pdf", "--json")
    assert _parts(_drafts(server)[0])["attach"] == []
    res = _cli(capsys, "drafts", "send", str(edited["draft_id"]), "--json")
    assert res["status"] == "drafted"  # agent + ai_send=draft: left for the human
    _cli(capsys, "drafts", "rm", str(edited["draft_id"]), "--json")
    assert _drafts(server) == []


def test_cli_send_inline_and_forward(cli_svc, sent, capsys):
    cli_svc.account().ai_send = "allow"
    _cli(capsys, "send", "--to", "x@y.com", "--subject", "s", "--body", "![Logo](logo.png) hi",
         "--inline", "logo.png", "--json")
    assert len(_parts(sent[0][0].as_bytes())["inline"]) == 1
    cli_svc.sync()
    invoice = next(e for e in cli_svc.list() if e.subject == "Invoice")
    _cli(capsys, "forward", str(invoice.id), "--to", "z@y.com", "--json")
    assert _parts(sent[1][0].as_bytes())["attach"] == ["inv.pdf"]


def test_cli_folders(cli_svc, server, capsys):
    cli_svc.sync()
    assert any(f["name"] == "Clients" for f in _cli(capsys, "folders", "--json", "create", "Clients"))
    _cli(capsys, "folders", "rename", "Clients", "Customers")
    _cli(capsys, "folders", "rm", "Customers")
    assert "Customers" not in server.folders


async def test_tui_edit_draft_adds_logo(svc, server, tmp_path):
    svc.sync()
    first = svc.save_draft(None, Draft(to=["x@y.com"], subject="Plan", body="hello"))
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG)
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        main = app.screen
        assert isinstance(main, MainScreen)
        main.account, main.folder = "work", "Drafts"
        main.refresh_from_cache()
        main.query_one(MessageList).focus()
        await pilot.pause()
        await pilot.press("e")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen) and app.screen.draft_id == first.draft_id
        app.screen.query_one("#inline").value = f"{logo};width=100"
        await pilot.press("ctrl+d")
        await pilot.pause()
        await app.workers.wait_for_complete()
    raws = _drafts(server)
    assert len(raws) == 1  # replaced, not duplicated
    parts = _parts(raws[0])
    assert len(parts["inline"]) == 1 and 'width="100"' in parts["html"]


async def test_tui_new_folder(svc, server):
    svc.sync()
    app = MonospaceApp(svc, watch=False)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("N")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        await pilot.press(*"Receipts", "enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
    assert "Receipts" in server.folders
