"""`monospace mcp install claude|codex|agy` — register the MCP server with an AI client.

Delegates to the client's own CLI (`claude mcp add` / `codex mcp add` / `agy mcp add`) so we never
hand-edit its config. The server is registered by absolute path: clients launch MCP servers with a
minimal environment, where `~/.local/bin` is often not on PATH.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from monospace.cli.output import info
from monospace.core import paths

CLIENTS = ("claude", "codex", "agy")


def add_parsers(mcp: argparse.ArgumentParser) -> None:
    msub = mcp.add_subparsers(dest="mcp_cmd")
    p = msub.add_parser("install", help="register monospace with Claude Code, Codex, or Antigravity CLI (agy)")
    p.add_argument("client", choices=CLIENTS)
    p.add_argument("--name", default="monospace", help="server name in the client (default: monospace)")
    p.add_argument("--print", dest="dry_run", action="store_true",
                   help="only print the command / config instead of running it")


def monospace_exe() -> str:
    """Absolute path of the `monospace` command (falls back to running this Python module)."""
    found = shutil.which("monospace")
    if found:
        return str(Path(found).resolve()) if not Path(found).is_absolute() else found
    argv0 = Path(sys.argv[0])
    if argv0.name == "monospace" and argv0.exists():
        return str(argv0.resolve())
    return ""


def server_command() -> list[str]:
    exe = monospace_exe()
    return [exe, "mcp"] if exe else [sys.executable, "-m", "monospace.cli.main", "mcp"]


def client_command(client: str, name: str) -> list[str]:
    if client == "claude":  # user scope: available in every project, not just this directory
        return ["claude", "mcp", "add", "--scope", "user", name, "--", *server_command()]
    if client in ("codex", "agy"):
        return [client, "mcp", "add", name, "--", *server_command()]
    raise ValueError(f"unknown client: {client}")


def codex_toml(name: str) -> str:
    cmd, *args = server_command()
    arg_list = ", ".join(f'"{a}"' for a in args)
    return f'[mcp_servers.{name}]\ncommand = "{cmd}"\nargs = [{arg_list}]\n'


def agy_json(name: str) -> str:
    cmd, *args = server_command()
    return json.dumps({"mcpServers": {name: {"command": cmd, "args": args}}}, indent=2) + "\n"


def _secrets_hint() -> None:
    from monospace.core import secrets

    if secrets.backend_name() == "file" and not secrets.remembered_passphrase():
        info("note: secrets are in the encrypted file and the passphrase isn't remembered on this machine.\n"
             "      AI clients start the server without your shell's environment, so run `monospace` once\n"
             f"      and answer y to 'Remember it on this machine?' (saved to {paths.passphrase_file()}).")


def run(args: argparse.Namespace) -> int:
    cmd = client_command(args.client, args.name)
    if args.dry_run or not shutil.which(cmd[0]):
        if not args.dry_run:
            info(f"`{cmd[0]}` not found on PATH — run this once it's installed:")
        print(shlex.join(cmd))
        if args.client == "codex":
            info("…or add this to ~/.codex/config.toml:")
            print(codex_toml(args.name))
        elif args.client == "agy":
            info("…or add this to ~/.gemini/config/mcp_config.json:")
            print(agy_json(args.name))
        _secrets_hint()
        return 0 if args.dry_run else 1
    info(f"$ {shlex.join(cmd)}")
    rc = subprocess.run(cmd).returncode
    if rc == 0:
        info(f"✓ registered `{args.name}` with {args.client}. Try: \"Summarise my unread mail.\"")
        _secrets_hint()
    return rc
