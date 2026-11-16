"""SASL XOAUTH2 helpers (Google + Microsoft use the same format)."""

from __future__ import annotations

import base64


def sasl_string(user: str, access_token: str) -> str:
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def sasl_b64(user: str, access_token: str) -> str:
    return base64.b64encode(sasl_string(user, access_token).encode()).decode()
