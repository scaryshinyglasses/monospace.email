import argparse
import json

from monospace.cli import mcp_setup
from monospace.cli.main import build_parser


def test_parser_keeps_plain_mcp_and_adds_install():
    assert getattr(build_parser().parse_args(["mcp"]), "mcp_cmd", None) is None
    for client in ("claude", "codex", "agy"):
        args = build_parser().parse_args(["mcp", "install", client, "--print"])
        assert (args.mcp_cmd, args.client, args.dry_run, args.name) == ("install", client, True, "monospace")


def test_commands_use_absolute_path(monkeypatch):
    monkeypatch.setattr(mcp_setup, "monospace_exe", lambda: "/opt/bin/monospace")
    assert mcp_setup.client_command("codex", "mail") == ["codex", "mcp", "add", "mail", "--", "/opt/bin/monospace", "mcp"]
    assert mcp_setup.client_command("agy", "mail") == ["agy", "mcp", "add", "mail", "--", "/opt/bin/monospace", "mcp"]
    assert mcp_setup.client_command("claude", "monospace")[:5] == ["claude", "mcp", "add", "--scope", "user"]
    assert mcp_setup.codex_toml("monospace") == (
        '[mcp_servers.monospace]\ncommand = "/opt/bin/monospace"\nargs = ["mcp"]\n')
    assert json.loads(mcp_setup.agy_json("monospace")) == {
        "mcpServers": {"monospace": {"command": "/opt/bin/monospace", "args": ["mcp"]}}}


def test_install_runs_client_cli(monkeypatch):
    ran = []
    monkeypatch.setattr(mcp_setup, "monospace_exe", lambda: "/opt/bin/monospace")
    monkeypatch.setattr(mcp_setup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(mcp_setup.subprocess, "run", lambda cmd: ran.append(cmd) or argparse.Namespace(returncode=0))
    assert mcp_setup.run(argparse.Namespace(client="codex", name="monospace", dry_run=False)) == 0
    assert mcp_setup.run(argparse.Namespace(client="agy", name="monospace", dry_run=False)) == 0
    assert ran == [
        ["codex", "mcp", "add", "monospace", "--", "/opt/bin/monospace", "mcp"],
        ["agy", "mcp", "add", "monospace", "--", "/opt/bin/monospace", "mcp"],
    ]


def test_missing_client_prints_instructions(monkeypatch, capsys):
    monkeypatch.setattr(mcp_setup, "monospace_exe", lambda: "/opt/bin/monospace")
    monkeypatch.setattr(mcp_setup.shutil, "which", lambda name: None)
    assert mcp_setup.run(argparse.Namespace(client="codex", name="monospace", dry_run=False)) == 1
    out = capsys.readouterr().out
    assert "codex mcp add monospace -- /opt/bin/monospace mcp" in out
    assert "[mcp_servers.monospace]" in out

    assert mcp_setup.run(argparse.Namespace(client="agy", name="monospace", dry_run=False)) == 1
    out = capsys.readouterr().out
    assert "agy mcp add monospace -- /opt/bin/monospace mcp" in out
    assert '"mcpServers"' in out
    assert '"/opt/bin/monospace"' in out

    assert mcp_setup.run(argparse.Namespace(client="agy", name="monospace", dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "agy mcp add monospace -- /opt/bin/monospace mcp" in out
    assert '"mcpServers"' in out
