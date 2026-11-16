"""`monospace doctor` — config, secrets backend, terminal graphics, connectivity."""

from __future__ import annotations

import os
import sys

from monospace.cli.output import emit_json
from monospace.core import paths, platform, secrets
from monospace.core.auth import tokens
from monospace.core.config import Config
from monospace.core.imap import ImapSession


def graphics_protocol() -> str:
    if not sys.__stdout__ or not sys.__stdout__.isatty():
        return "none (not a terminal)"
    try:
        from textual_image.renderable import sixel, tgp

        if sixel.query_terminal_support():
            return "sixel"
        if tgp.query_terminal_support():
            return "kitty (tgp)"
    except Exception as exc:  # pragma: no cover - terminal dependent
        return f"halfcell (probe failed: {exc})"
    colors = "truecolor" if os.environ.get("COLORTERM") in ("truecolor", "24bit") else "256/16 colour"
    return f"halfcell ({colors})"


def run(cfg: Config, *, as_json: bool = False, offline: bool = False) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("config", True, str(cfg.path or paths.config_file())))
    checks.append(("cache", True, str(paths.cache_db())))
    try:
        secrets.get_store()
        checks.append(("secrets", True, secrets.backend_name()))
    except Exception as exc:
        checks.append(("secrets", False, str(exc)))
    checks.append(("display", True, "yes" if platform.has_display() else "headless (paste-URL OAuth, no opener)"))
    checks.append(("terminal", True, f"TERM={os.environ.get('TERM', '?')}  graphics={graphics_protocol()}"))
    checks.append(("oauth.google", True, "configured" if cfg.oauth.google_client_id else "not set (app passwords)"))
    checks.append(("oauth.microsoft", True,
                   "configured" if cfg.oauth.microsoft_client_id else "not set (app passwords)"))
    if not cfg.accounts:
        checks.append(("accounts", False, "none — run `monospace account add`"))
    for acct in cfg.accounts:
        if offline:
            checks.append((f"account:{acct.name}", True, f"{acct.email} ({acct.auth}, ai_send={acct.ai_send})"))
            continue
        try:
            with ImapSession(acct, tokens.resolve(acct, cfg.oauth), timeout=15) as sess:
                n = len(sess.folders())
            checks.append((f"account:{acct.name}", True, f"IMAP ok, {n} folders, ai_send={acct.ai_send}"))
        except Exception as exc:
            checks.append((f"account:{acct.name}", False, str(exc)))
    ok = all(c[1] for c in checks)
    if as_json:
        emit_json({"ok": ok, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]})
    else:
        for name, good, detail in checks:
            print(f"{'✓' if good else '✗'} {name:18} {detail}")
    return 0 if ok else 1
