from __future__ import annotations

import pytest

from monospace.core import secrets
from monospace.core.auth.tokens import Credential
from monospace.core.config import Config, account_from_preset
from monospace.core.service import Service
from monospace.core.store import Store
from tests.fakes import FakeServer, FakeSession, make_raw


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MONOSPACE_HOME", str(tmp_path / "home"))
    secrets.set_store(secrets.MemoryStore(), "memory")
    yield
    secrets.set_store(None)


@pytest.fixture
def server() -> FakeServer:
    s = FakeServer()
    s.deliver("INBOX", make_raw("Welcome", body="first message", msgid="<a@x>"))
    s.deliver("INBOX", make_raw("Invoice", body="see attached", attachment=("inv.pdf", b"%PDF-1"),
                                msgid="<b@x>", date="Tue, 17 Nov 2026 10:00:00 +0000"), ["\\Seen"])
    return s


@pytest.fixture
def sent() -> list:
    return []


@pytest.fixture
def svc(server, sent, tmp_path) -> Service:
    acct = account_from_preset("work", "me@example.com", "custom", use_oauth=False,
                               imap_host="imap.example.com", smtp_host="smtp.example.com")
    cfg = Config(accounts=[acct], path=tmp_path / "config.toml")
    return Service(cfg, Store(tmp_path / "cache.db"),
                   session_factory=lambda a, c: FakeSession(server),
                   sender=lambda a, c, msg, rcpts: sent.append((msg, rcpts)),
                   credentials=lambda a: Credential("password", a.login, "pw"))
