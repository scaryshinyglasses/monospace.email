"""Resolve a usable credential for an account: password or fresh XOAUTH2 access token.

Secrets layout (see core.secrets): ``<account>:password`` | ``<account>:gmail_refresh`` |
``<account>:msal_cache``. Access tokens are cached in-process until shortly before expiry.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from monospace.core import secrets
from monospace.core.auth import gmail, outlook
from monospace.core.config import Account, OAuthClients


class AuthError(Exception):
    pass


@dataclass
class Credential:
    kind: str  # "password" | "xoauth2"
    user: str
    secret: str


_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def _cached(name: str) -> str | None:
    hit = _cache.get(name)
    return hit[0] if hit and hit[1] - 60 > time.time() else None


def resolve(acct: Account, oauth: OAuthClients) -> Credential:
    store = secrets.get_store()
    if acct.auth == "password":
        pw = store.get(secrets.key(acct.name, "password"))
        if pw is None:
            raise AuthError(f"no password stored for {acct.name}; run `monospace account login {acct.name}`")
        return Credential("password", acct.login, pw)
    with _lock:
        token = _cached(acct.name)
        if token:
            return Credential("xoauth2", acct.login, token)
        try:
            if acct.auth == "gmail_oauth":
                refresh = store.get(secrets.key(acct.name, "gmail_refresh"))
                if not refresh:
                    raise AuthError(f"{acct.name}: not signed in; run `monospace account login {acct.name}`")
                ts = gmail.refresh(oauth.google_client_id, oauth.google_client_secret, refresh)
                _cache[acct.name] = (ts.access_token, ts.expires_at)
                return Credential("xoauth2", acct.login, ts.access_token)
            if acct.auth == "outlook_oauth":
                blob = store.get(secrets.key(acct.name, "msal_cache")) or ""
                res = outlook.silent(oauth.microsoft_client_id, oauth.microsoft_tenant, blob, acct.login)
                store.set(secrets.key(acct.name, "msal_cache"), res.cache_blob)
                _cache[acct.name] = (res.access_token, res.expires_at)
                return Credential("xoauth2", acct.login, res.access_token)
        except (gmail.OAuthError, outlook.OAuthError) as exc:
            raise AuthError(str(exc)) from exc
    raise AuthError(f"unknown auth kind {acct.auth!r}")


def remember_access_token(account: str, token: str, expires_at: float) -> None:
    with _lock:
        _cache[account] = (token, expires_at)


def forget(account: str) -> None:
    with _lock:
        _cache.pop(account, None)
