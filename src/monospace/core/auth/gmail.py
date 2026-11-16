"""Gmail OAuth (installed-app flow, scope https://mail.google.com/).

Two ways to finish the flow:
* ``run_browser()``   — loopback server + opens a browser (desktops, or SSH with ``-L``).
* ``url()`` + ``finish(pasted)`` — headless: open the URL on any device, then paste the
  full ``http://localhost…?code=…`` address the browser lands on (the page itself fails
  to load; that is expected).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

SCOPES = ["https://mail.google.com/"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
HEADLESS_PORT = 8765


class OAuthError(Exception):
    pass


def client_config(client_id: str, client_secret: str) -> dict:
    if not client_id:
        raise OAuthError("Google OAuth client id not configured ([oauth] google_client_id)")
    return {"installed": {"client_id": client_id, "client_secret": client_secret,
                          "auth_uri": AUTH_URI, "token_uri": TOKEN_URI,
                          "redirect_uris": ["http://localhost"]}}


def extract_code(pasted: str) -> str:
    """Accept either the full redirect URL or a bare code."""
    pasted = pasted.strip()
    if "://" not in pasted and "code=" not in pasted:
        if not pasted:
            raise OAuthError("nothing pasted")
        return pasted
    query = urlparse(pasted).query if "://" in pasted else pasted.lstrip("?")
    params = parse_qs(query)
    if "error" in params:
        raise OAuthError(f"Google returned an error: {params['error'][0]}")
    codes = params.get("code")
    if not codes:
        raise OAuthError("no ?code= in the pasted address")
    return codes[0]


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float


class GmailFlow:
    def __init__(self, client_id: str, client_secret: str, login_hint: str = "") -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        self._flow = InstalledAppFlow.from_client_config(client_config(client_id, client_secret), SCOPES)
        self._hint = login_hint

    def _kwargs(self) -> dict:
        kw = {"access_type": "offline", "prompt": "consent"}
        if self._hint:
            kw["login_hint"] = self._hint
        return kw

    def url(self, port: int = HEADLESS_PORT) -> str:
        self._flow.redirect_uri = f"http://localhost:{port}/"
        auth_url, _ = self._flow.authorization_url(**self._kwargs())
        return auth_url

    def finish(self, pasted: str) -> TokenSet:
        self._flow.fetch_token(code=extract_code(pasted))
        return self._tokens(self._flow.credentials)

    def run_browser(self, *, open_browser: bool = True, port: int = 0) -> TokenSet:
        creds = self._flow.run_local_server(port=port, open_browser=open_browser,
                                            timeout_seconds=300, **self._kwargs())
        return self._tokens(creds)

    @staticmethod
    def _tokens(creds) -> TokenSet:  # type: ignore[no-untyped-def]
        if not creds.refresh_token:
            raise OAuthError("Google returned no refresh token; remove the app grant and retry")
        expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
        return TokenSet(creds.token, creds.refresh_token, expiry)


def refresh(client_id: str, client_secret: str, refresh_token: str) -> TokenSet:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials(None, refresh_token=refresh_token, token_uri=TOKEN_URI,
                        client_id=client_id, client_secret=client_secret, scopes=SCOPES)
    try:
        creds.refresh(Request())
    except Exception as exc:  # google.auth.exceptions.RefreshError and transport errors
        raise OAuthError(f"Gmail token refresh failed: {exc}") from exc
    expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
    return TokenSet(creds.token, refresh_token, expiry)
