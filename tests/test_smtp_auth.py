import asyncio

import pytest
from aiosmtpd.controller import Controller

from monospace.core import smtp
from monospace.core.auth import gmail, xoauth2
from monospace.core.compose import Draft, build
from monospace.core.config import Account


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture
def smtp_server():
    sink = Sink()
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ctl = Controller(sink, hostname="127.0.0.1", port=port)
    ctl.start()
    yield ctl, sink
    ctl.stop()


def test_send_over_real_smtp(smtp_server):
    ctl, sink = smtp_server
    acct = Account(name="t", email="me@example.com", smtp_host="127.0.0.1", smtp_port=ctl.port, smtp_tls="none")
    msg = build(Draft(to=["you@example.com"], bcc=["b@example.com"], subject="hi", body="yo"), acct.from_header)
    asyncio.run(smtp.send(acct, None, msg, ["you@example.com", "b@example.com"]))
    env = sink.messages[0]
    assert env.rcpt_tos == ["you@example.com", "b@example.com"] and b"Subject: hi" in env.content
    assert b"b@example.com" not in env.content.split(b"\r\n\r\n")[0]


def test_xoauth2_string():
    assert xoauth2.sasl_string("u@x", "tok") == "user=u@x\x01auth=Bearer tok\x01\x01"


def test_gmail_extract_code():
    assert gmail.extract_code("http://localhost:8765/?state=s&code=4/abc&scope=x") == "4/abc"
    assert gmail.extract_code("  4/raw-code ") == "4/raw-code"
    with pytest.raises(gmail.OAuthError):
        gmail.extract_code("http://localhost:8765/?error=access_denied")


def test_gmail_url_is_offline_consent():
    url = gmail.GmailFlow("cid.apps.googleusercontent.com", "sec", "me@gmail.com").url()
    assert "access_type=offline" in url and "redirect_uri=http%3A%2F%2Flocalhost%3A8765%2F" in url
    assert "mail.google.com" in url and "login_hint=me%40gmail.com" in url
