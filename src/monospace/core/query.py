"""Structured message filters shared by the MCP server (and anything else that wants them).

``MessageFilter`` → a SQL WHERE fragment over ``messages`` (alias ``m``). Dates are stored as ISO
strings in the sender's own offset, so comparisons go through ``julianday()``, which normalizes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone

from typing import TYPE_CHECKING

from monospace.core.tag_store import KEY_SQL

if TYPE_CHECKING:
    from monospace.core.store import Envelope

ALL_FOLDERS = "all"
_REL = re.compile(r"^(\d{1,4})\s*([hdwmy])$")
_UNITS = {"h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1),
          "m": timedelta(days=30), "y": timedelta(days=365)}


def _error(msg: str) -> Exception:
    from monospace.core.service import ServiceError

    return ServiceError(msg)


def parse_when(value: str, *, now: datetime | None = None) -> str:
    """``2026-11-01`` / ``2026-11-01T09:00`` / ``7d`` ``12h`` ``2w`` ``3m`` ``1y`` (ago) → ISO UTC."""
    v = value.strip().lower()
    now = now or datetime.now(timezone.utc)
    if v in ("today", "yesterday"):
        day = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return (day - timedelta(days=v == "yesterday")).astimezone(timezone.utc).isoformat()
    m = _REL.match(v)
    if m:
        return (now - int(m.group(1)) * _UNITS[m.group(2)]).isoformat()
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise _error(f"bad date {value!r}: use YYYY-MM-DD[THH:MM], today, yesterday or 7d / 12h / 2w / 3m / 1y")
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive = the user's local time
    return dt.astimezone(timezone.utc).isoformat()


@dataclass
class MessageFilter:
    folder: str | None = "INBOX"  # name, role (drafts/sent/archive/trash/junk) or "all"
    unread: bool | None = None
    flagged: bool | None = None
    has_attachments: bool | None = None
    from_: str | None = None  # substring of the From header (name or address), case-insensitive
    to: str | None = None  # substring of To/Cc
    subject: str | None = None
    since: str | None = None  # inclusive
    before: str | None = None  # exclusive
    tags: list[str] = field(default_factory=list)  # all of them

    @property
    def all_folders(self) -> bool:
        return not self.folder or self.folder.lower() == ALL_FOLDERS

    def describe(self) -> dict:
        return {f.name.rstrip("_"): getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name) not in (None, [], "")}

    def to_sql(self, folder: str | None) -> tuple[str, list[object]]:
        """WHERE fragment (starting with " AND") for ``messages m``; ``folder`` is the resolved name."""
        sql, args = "", []  # type: str, list[object]
        if folder is not None:
            sql, args = sql + " AND m.folder=?", [*args, folder]
        for flag, want in (("\\\\Seen", None if self.unread is None else not self.unread),
                           ("\\\\Flagged", self.flagged)):
            if want is not None:
                sql += f" AND m.flags {'' if want else 'NOT '}LIKE '%{flag}%'"
        if self.has_attachments is not None:
            sql, args = sql + " AND m.has_attachments=?", [*args, int(self.has_attachments)]
        for col, needle in (("sender", self.from_), ("recipients", self.to), ("subject", self.subject)):
            if needle:
                sql, args = sql + f" AND m.{col} LIKE ? ESCAPE '\\'", [*args, f"%{_like(needle)}%"]
        if self.since:
            sql, args = sql + " AND julianday(m.date) >= julianday(?)", [*args, parse_when(self.since)]
        if self.before:
            sql, args = sql + " AND julianday(m.date) < julianday(?)", [*args, parse_when(self.before)]
        mkey = KEY_SQL.replace("message_id", "m.message_id").replace("sender", "m.sender")
        for tag in self.tags:
            sql += f" AND {mkey} IN (SELECT msg_key FROM tags WHERE account=m.account AND tag=?)"
            args.append(tag)
        return sql, args


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ts(date: str) -> float:
    try:
        dt = datetime.fromisoformat(date)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return 0.0


class QueryMixin:
    """Mixed into ``Service``."""

    def find(self, flt: MessageFilter, *, text: str = "", account: str | None = None, limit: int = 50,
             offset: int = 0) -> list["Envelope"]:
        """Structured filter (+ optional full-text ``text``; ``tag:x`` tokens join ``flt.tags``)."""
        from monospace.core.tags import split_tag_query

        text, tags = split_tag_query(text)
        if tags:
            flt = replace(flt, tags=[*flt.tags, *tags])
        accts = [self.account(account)] if account else list(self.cfg.accounts)  # type: ignore[attr-defined]
        if flt.all_folders:
            where, args = flt.to_sql(None)
            return self.store.query([a.name for a in accts], where, args, text=text, limit=limit, offset=offset)  # type: ignore[attr-defined]
        out: list["Envelope"] = []
        for acct in accts:  # folder roles resolve per account
            where, args = flt.to_sql(self.resolve_folder(acct, flt.folder))  # type: ignore[attr-defined]
            out += self.store.query([acct.name], where, args, text=text, limit=limit + offset)  # type: ignore[attr-defined]
        out.sort(key=lambda e: (_ts(e.date), e.id), reverse=True)
        return out[offset:offset + limit]
