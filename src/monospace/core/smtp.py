"""Send a built ``EmailMessage`` over SMTP (password or XOAUTH2)."""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from monospace.core.auth.tokens import Credential
from monospace.core.config import Account


class SmtpError(Exception):
    pass


async def send(acct: Account, cred: Credential | None, msg: EmailMessage, recipients: list[str]) -> None:
    if not recipients:
        raise SmtpError("no recipients")
    tls = acct.smtp_tls
    client = aiosmtplib.SMTP(hostname=acct.smtp_host, port=acct.smtp_port, use_tls=tls == "ssl",
                             start_tls=True if tls == "starttls" else False, timeout=60)
    try:
        await client.connect()
        if cred is not None:
            if cred.kind == "xoauth2":
                await client.auth_xoauth2(cred.user, cred.secret)
            else:
                await client.login(cred.user, cred.secret)
        await client.send_message(msg, sender=acct.email, recipients=recipients)
    except aiosmtplib.SMTPException as exc:
        raise SmtpError(f"{acct.name}: SMTP send failed: {exc}") from exc
    except OSError as exc:
        raise SmtpError(f"{acct.name}: cannot reach {acct.smtp_host}:{acct.smtp_port}: {exc}") from exc
    finally:
        if client.is_connected:
            try:
                await client.quit()
            except Exception:
                pass
