"""Account sign-in + verification shared by the CLI wizard and the TUI wizard."""

from __future__ import annotations

from typing import Callable

from monospace.core import config, secrets
from monospace.core.auth import gmail, outlook, tokens
from monospace.core.config import Account, Config
from monospace.core.imap import ImapSession
from monospace.core.sync import detect_roles


def oauth_available(cfg: Config, provider: str) -> bool:
    if provider == "gmail":
        return bool(cfg.oauth.google_client_id)
    if provider == "outlook":
        return bool(cfg.oauth.microsoft_client_id)
    return False


def store_password(acct: Account, password: str) -> None:
    secrets.get_store().set(secrets.key(acct.name, "password"), password)
    tokens.forget(acct.name)


def gmail_flow(cfg: Config, acct: Account) -> gmail.GmailFlow:
    return gmail.GmailFlow(cfg.oauth.google_client_id, cfg.oauth.google_client_secret, acct.email)


def save_gmail_tokens(acct: Account, ts: gmail.TokenSet) -> None:
    secrets.get_store().set(secrets.key(acct.name, "gmail_refresh"), ts.refresh_token)
    tokens.remember_access_token(acct.name, ts.access_token, ts.expires_at)


def outlook_login(cfg: Config, acct: Account, say: Callable[[str], None]) -> None:
    res = outlook.device_login(cfg.oauth.microsoft_client_id, cfg.oauth.microsoft_tenant, say)
    secrets.get_store().set(secrets.key(acct.name, "msal_cache"), res.cache_blob)
    tokens.remember_access_token(acct.name, res.access_token, res.expires_at)
    if res.username and not acct.username and res.username.lower() != acct.email.lower():
        acct.username = res.username


def verify(cfg: Config, acct: Account) -> dict:
    """Log in over IMAP, learn special folders, return a short report."""
    cred = tokens.resolve(acct, cfg.oauth)
    with ImapSession(acct, cred, timeout=20) as sess:
        roles = detect_roles(sess)
        count = len(sess.folders())
    for role, name in roles.items():
        acct.folders.setdefault(role, name)
    return {"folders": count, "roles": roles}


def forget_secrets(name: str) -> None:
    store = secrets.get_store()
    for kind in ("password", "gmail_refresh", "msal_cache"):
        store.delete(secrets.key(name, kind))
    tokens.forget(name)


def save(cfg: Config, acct: Account) -> None:
    cfg.upsert(acct)
    config.save(cfg)
