"""Outlook / Microsoft 365 OAuth via MSAL device-code flow (works without a browser).

The serialized MSAL token cache is stored as the account secret; silent refresh uses it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
]


class OAuthError(Exception):
    pass


@dataclass
class Result:
    access_token: str
    expires_at: float
    cache_blob: str
    username: str = ""


def _app(client_id: str, tenant: str, cache_blob: str = ""):  # type: ignore[no-untyped-def]
    import msal

    if not client_id:
        raise OAuthError("Microsoft OAuth client id not configured ([oauth] microsoft_client_id)")
    cache = msal.SerializableTokenCache()
    if cache_blob:
        cache.deserialize(cache_blob)
    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant or 'common'}", token_cache=cache)
    return app, cache


def _result(token: dict, cache) -> Result:  # type: ignore[no-untyped-def]
    if "access_token" not in token:
        raise OAuthError(token.get("error_description") or token.get("error") or "token request failed")
    claims = token.get("id_token_claims") or {}
    return Result(token["access_token"], time.time() + float(token.get("expires_in", 3000)),
                  cache.serialize(), claims.get("preferred_username", ""))


def device_login(client_id: str, tenant: str, say: Callable[[str], None]) -> Result:
    app, cache = _app(client_id, tenant)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise OAuthError(flow.get("error_description", "could not start device flow"))
    say(flow["message"])  # "To sign in, use a web browser to open … and enter the code …"
    return _result(app.acquire_token_by_device_flow(flow), cache)


def silent(client_id: str, tenant: str, cache_blob: str, username: str) -> Result:
    app, cache = _app(client_id, tenant, cache_blob)
    accounts = app.get_accounts(username=username) or app.get_accounts()
    if not accounts:
        raise OAuthError("no cached Microsoft sign-in; run `monospace account login`")
    token = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not token:
        raise OAuthError("Microsoft session expired; run `monospace account login`")
    return _result(token, cache)
