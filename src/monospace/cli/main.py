"""Entry point: `monospace` (TUI) and its subcommands."""

from __future__ import annotations

import argparse
import sys

from monospace import __version__
from monospace.cli import accounts as cli_accounts
from monospace.cli import mail as cli_mail
from monospace.cli import manage as cli_manage
from monospace.cli import mcp_setup as cli_mcp_setup
from monospace.cli import outbox as cli_outbox
from monospace.cli import update as cli_update
from monospace.cli.output import info

MAIL_CMDS = ("sync", "ls", "read", "search", "send", "draft", "reply", "mark", "move", "attachment")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="monospace", description="monospace.email — email in your terminal.")
    p.add_argument("--version", action="version", version=f"monospace {__version__}")
    p.add_argument("-a", "--account", help="account name or email (default: first account)")
    sub = p.add_subparsers(dest="command")
    t = sub.add_parser("tui", help="open the interactive client (default)")
    t.add_argument("--no-images", action="store_true", help="show image placeholders instead of pixels")
    cli_accounts.add_parsers(sub)
    cli_mail.add_parsers(sub)
    cli_manage.add_parsers(sub)
    cli_outbox.add_parsers(sub)
    cli_update.add_parsers(sub)
    d = sub.add_parser("doctor", help="check config, secrets, terminal graphics and connectivity")
    d.add_argument("--json", action="store_true")
    d.add_argument("--offline", action="store_true")
    mcp = sub.add_parser("mcp", help="run the MCP server on stdio (Claude Code, Codex, other AI clients)")
    cli_mcp_setup.add_parsers(mcp)
    dm = sub.add_parser("daemon", help="background sync loop")
    dm.add_argument("--interval", type=int, default=120)
    g = dm.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="install a systemd user unit / launchd agent")
    g.add_argument("--uninstall", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command or "tui"
    try:
        return _dispatch(cmd, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # user-facing errors without tracebacks
        from monospace.core.secrets_file import PassphraseError
        from monospace.core.service import ServiceError

        known = (ServiceError, LookupError, PassphraseError, ValueError, OSError)
        if isinstance(exc, known) or type(exc).__name__.endswith("Error"):
            info(f"error: {exc}")
            return 1
        raise


def _dispatch(cmd: str, args: argparse.Namespace) -> int:
    from monospace.core import config, secrets
    from monospace.cli.output import interactive

    if cmd == "mcp" and getattr(args, "mcp_cmd", None) == "install":
        return cli_mcp_setup.run(args)
    if cmd == "mcp":
        from monospace.mcp_server import serve

        serve()
        return 0
    if cmd == "update":  # needs no config or secrets
        return cli_update.run(args)
    cfg = config.load()
    # Unlock the secrets store up front so a passphrase prompt never lands mid-TUI.
    prompt = (lambda label: __import__("getpass").getpass(label)) if interactive() else None
    confirm = (lambda label: input(label).strip().lower() in ("y", "yes")) if interactive() else None
    if cmd != "doctor":
        secrets.get_store(prompt, confirm)
    if cmd in ("account", "oauth"):
        return cli_accounts.run(args, cfg)
    if cmd == "doctor":
        from monospace.cli import doctor

        return doctor.run(cfg, as_json=args.json, offline=args.offline)

    from monospace.core.service import Service

    if cmd == "tui":
        from monospace.ui.app import run_app

        return run_app(Service(cfg), images=not getattr(args, "no_images", False))
    svc = Service(cfg)
    if cmd in MAIL_CMDS:
        return cli_mail.run(cmd, args, svc)
    if cmd in cli_manage.COMMANDS:
        return cli_manage.run(cmd, args, svc)
    if cmd == "outbox":
        return cli_outbox.run(args, svc)
    if cmd == "daemon":
        from monospace.cli import daemon

        if args.install:
            return daemon.install(args.interval)
        if args.uninstall:
            return daemon.uninstall()
        daemon.loop(svc, args.interval)
        return 0
    info(f"unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
