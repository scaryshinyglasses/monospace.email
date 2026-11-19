"""`monospace account …` and `monospace oauth …` — interactive, headless-aware sign-in."""

from __future__ import annotations

import argparse
import getpass

from monospace.cli.output import ask, emit_json, info, interactive
from monospace.core import accounts, config, platform
from monospace.core.auth import gmail
from monospace.core.config import AI_SEND_VALUES, PRESETS, Account, Config

EDITABLE = ("display_name", "username", "ai_send", "signature", "tags", "imap_host", "imap_port", "imap_tls",
            "smtp_host", "smtp_port", "smtp_tls")


def add_parsers(sub: argparse._SubParsersAction) -> None:
    acc = sub.add_parser("account", help="add, list, remove and sign in to accounts")
    asub = acc.add_subparsers(dest="account_cmd", required=True)
    p = asub.add_parser("add", help="add an account (interactive)")
    p.add_argument("--provider", choices=list(PRESETS))
    p.add_argument("--name")
    p.add_argument("--email")
    p.add_argument("--password-auth", action="store_true", help="use an app password even if OAuth is configured")
    p.add_argument("--no-browser", action="store_true", help="headless OAuth: print a URL, paste the redirect")
    p.add_argument("--listen", action="store_true",
                   help="headless OAuth via `ssh -L 8765:localhost:8765` instead of pasting")
    p.add_argument("--no-verify", action="store_true")
    asub.add_parser("list", help="list accounts").add_argument("--json", action="store_true")
    asub.add_parser("remove", help="remove an account and its secrets").add_argument("name")
    p = asub.add_parser("login", help="(re)authenticate an account")
    p.add_argument("name")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--listen", action="store_true")
    p = asub.add_parser("set", help=f"edit fields: {', '.join(EDITABLE)}")
    p.add_argument("name")
    p.add_argument("pairs", nargs="+", metavar="key=value")

    o = sub.add_parser("oauth", help="configure OAuth client IDs for Gmail / Outlook")
    o.add_argument("--google-client-id")
    o.add_argument("--google-client-secret")
    o.add_argument("--microsoft-client-id")
    o.add_argument("--microsoft-tenant")


def _prompt(label: str, default: str = "") -> str:
    val = ask(f"{label}{f' [{default}]' if default else ''}: ").strip()
    return val or default


def login(cfg: Config, acct: Account, *, no_browser: bool = False, listen: bool = False) -> None:
    if acct.auth == "password":
        hint = " (use an app password for Gmail/Outlook)" if acct.provider != "custom" else ""
        accounts.store_password(acct, getpass.getpass(f"Password for {acct.login}{hint}: "))
    elif acct.auth == "gmail_oauth":
        flow = accounts.gmail_flow(cfg, acct)
        if listen:
            info(f"Forward the port first: ssh -L {gmail.HEADLESS_PORT}:localhost:{gmail.HEADLESS_PORT} <this-host>")
            ts = flow.run_browser(open_browser=False, port=gmail.HEADLESS_PORT)
        elif no_browser or not platform.has_display():
            info("Open this URL on any device and approve access:\n")
            print(flow.url())
            info("\nThe browser then lands on a localhost page that fails to load — that's expected.")
            ts = flow.finish(ask("Paste that page's full address here: "))
        else:
            info("Opening your browser to sign in to Google…")
            ts = flow.run_browser()
        accounts.save_gmail_tokens(acct, ts)
    elif acct.auth == "outlook_oauth":
        accounts.outlook_login(cfg, acct, info)
    info(f"✓ signed in: {acct.name}")


def _add(cfg: Config, args: argparse.Namespace) -> int:
    if not interactive() and not (args.provider and args.email):
        info("account add needs a terminal (or --provider and --email plus MONOSPACE_PASSWORD for password auth)")
        return 2
    provider = args.provider or _prompt("Provider (gmail / outlook / custom)", "gmail")
    if provider not in PRESETS:
        info(f"unknown provider {provider!r}")
        return 2
    email = args.email or _prompt("Email address")
    name = args.name or _prompt("Account name", email.split("@")[0])
    use_oauth = accounts.oauth_available(cfg, provider) and not args.password_auth
    if provider in ("gmail", "outlook") and not use_oauth:
        info(f"No {provider} OAuth client configured → using an app password (see `monospace oauth --help`).")
    over: dict[str, object] = {"display_name": _prompt("Display name", "") if interactive() else ""}
    if provider == "custom":
        over["imap_host"] = _prompt("IMAP host", f"imap.{email.split('@')[-1]}")
        over["imap_port"] = int(_prompt("IMAP port", "993"))
        over["imap_tls"] = _prompt("IMAP TLS (ssl / starttls / none)", "ssl")
        over["smtp_host"] = _prompt("SMTP host", f"smtp.{email.split('@')[-1]}")
        over["smtp_port"] = int(_prompt("SMTP port", "587"))
        over["smtp_tls"] = _prompt("SMTP TLS (ssl / starttls / none)", "starttls")
        over["username"] = _prompt("Username", email)
    acct = config.account_from_preset(name, email, provider, use_oauth=use_oauth, **over)
    import os

    if acct.auth == "password" and os.environ.get("MONOSPACE_PASSWORD"):
        accounts.store_password(acct, os.environ["MONOSPACE_PASSWORD"])
    else:
        login(cfg, acct, no_browser=args.no_browser, listen=args.listen)
    if not args.no_verify:
        info("Checking IMAP…")
        report = accounts.verify(cfg, acct)
        info(f"✓ {report['folders']} folders; special: {report['roles'] or 'none detected'}")
    accounts.save(cfg, acct)
    info(f"Saved {acct.name} (AI send policy: {acct.ai_send}). Next: `monospace sync` or just `monospace`.")
    return 0


def run(args: argparse.Namespace, cfg: Config) -> int:
    if args.command == "oauth":
        for field in ("google_client_id", "google_client_secret", "microsoft_client_id", "microsoft_tenant"):
            val = getattr(args, field)
            if val is not None:
                setattr(cfg.oauth, field, val)
        info(f"OAuth clients saved to {config.save(cfg)}")
        return 0
    sub = args.account_cmd
    if sub == "add":
        return _add(cfg, args)
    if sub == "list":
        rows = [{"name": a.name, "email": a.email, "provider": a.provider, "auth": a.auth, "ai_send": a.ai_send}
                for a in cfg.accounts]
        if args.json:
            emit_json(rows)
        else:
            for r in rows or []:
                print(f"{r['name']:16} {r['email']:32} {r['provider']:8} {r['auth']:14} ai_send={r['ai_send']}")
            if not rows:
                info("no accounts — run `monospace account add`")
        return 0
    acct = cfg.get(args.name)
    if sub == "remove":
        cfg.remove(acct.name)
        accounts.forget_secrets(acct.name)
        config.save(cfg)
        info(f"removed {acct.name}")
    elif sub == "login":
        login(cfg, acct, no_browser=args.no_browser, listen=args.listen)
        accounts.save(cfg, acct)
    elif sub == "set":
        for pair in args.pairs:
            k, _, v = pair.partition("=")
            if k not in EDITABLE:
                info(f"cannot set {k!r}; editable: {', '.join(EDITABLE)}")
                return 2
            if k == "ai_send" and v not in AI_SEND_VALUES:
                info(f"ai_send must be one of {AI_SEND_VALUES}")
                return 2
            if k == "tags" and v not in ("auto", "local"):
                info("tags must be 'auto' (server labels/keywords when available) or 'local'")
                return 2
            if k == "ai_send" and not interactive():
                info("ai_send can only be changed from an interactive terminal")
                return 2
            setattr(acct, k, int(v) if k.endswith("_port") else v)
        accounts.save(cfg, acct)
        info(f"updated {acct.name}")
    return 0
