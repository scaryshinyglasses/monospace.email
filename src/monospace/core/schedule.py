"""Schedule emails and drafts for future delivery (mixed into ``Service``).

Human callers and AI callers on ``ai_send=allow`` accounts create ``status='scheduled'`` items in
``outbox``, which ``flush_scheduled`` sends once ``send_at`` (ISO-8601 UTC) arrives. AI callers on
``ai_send=confirm`` accounts create ``status='pending'`` items with ``send_at`` set; ``flush_scheduled``
never touches ``pending`` rows — a human must approve them first (approving transitions a future
``pending`` item to ``scheduled``, or sends it immediately if ``send_at`` has passed or ``send_now``).
AI callers on ``ai_send=draft`` accounts save ``X-Monospace-Send-At`` on the IMAP draft without queuing.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from monospace.core import compose, paths, policy
from monospace.core.compose import Draft

if TYPE_CHECKING:
    from monospace.core.config import Account
    from monospace.core.service import SendResult

_REL = re.compile(r"^(?:in\s+|\+)?(\d{1,5})\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|w|wk|wks)$")
_REL_UNITS = {
    "s": timedelta(seconds=1), "sec": timedelta(seconds=1), "secs": timedelta(seconds=1),
    "m": timedelta(minutes=1), "min": timedelta(minutes=1), "mins": timedelta(minutes=1),
    "h": timedelta(hours=1), "hr": timedelta(hours=1), "hrs": timedelta(hours=1),
    "d": timedelta(days=1), "day": timedelta(days=1), "days": timedelta(days=1),
    "w": timedelta(weeks=1), "wk": timedelta(weeks=1), "wks": timedelta(weeks=1),
}
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}


def _err(msg: str) -> Exception:
    from monospace.core.service import ServiceError

    return ServiceError(msg)


def _parse_clock(text: str, *, allow_bare_hour: bool = False) -> tuple[int, int] | None:
    m = _CLOCK.match(text.strip().lower())
    if not m or (not allow_bare_hour and m.group(2) is None and m.group(3) is None):
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if minute >= 60:
        return None
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = (0 if hour == 12 else hour) + (12 if ampm == "pm" else 0)
    elif hour >= 24:
        return None
    return hour, minute


def parse_send_at(value: str, *, now: datetime | None = None) -> str:
    """Parse a future schedule string into an ISO-8601 UTC timestamp.

    Accepts relative offsets (``30m``, ``in 2h``, ``+1d``), natural times (``tomorrow``,
    ``tomorrow 14:30``, ``today 6pm``, ``monday 09:00``, ``18:00``, ``9am``), and ISO dates/datetimes
    (``2026-11-21``, ``2026-11-21 09:00``, ``2026-11-21T09:00:00+05:30``).
    """
    raw = (value or "").strip()
    if not raw:
        raise _err("send_at cannot be empty")
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone()
    low = re.sub(r"\s+", " ", raw.lower())

    m = _REL.match(low)
    if m:
        dt = now + int(m.group(1)) * _REL_UNITS[m.group(2)]
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")

    dt_local: datetime | None = None
    if low.startswith(("tomorrow", "today")):
        is_tomorrow = low.startswith("tomorrow")
        rest = low[8 if is_tomorrow else 5:].strip()
        if rest.startswith("at "):
            rest = rest[3:].strip()
        hm = (9, 0) if (is_tomorrow and not rest) else _parse_clock(rest, allow_bare_hour=True) if rest else None
        if hm is None:
            raise _err(f"bad schedule time {value!r}: try 'tomorrow 09:00', 'in 2h', or 'YYYY-MM-DD HH:MM'")
        base = local_now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        dt_local = base + timedelta(days=1 if is_tomorrow else 0)
    elif low.split(" ", 1)[0] in _WEEKDAYS:
        parts = low.split(" ", 1)
        target_wd = _WEEKDAYS[parts[0]]
        rest = parts[1].strip() if len(parts) > 1 else ""
        if rest.startswith("at "):
            rest = rest[3:].strip()
        hm = _parse_clock(rest, allow_bare_hour=True) if rest else (9, 0)
        if hm is None:
            raise _err(f"bad schedule time {value!r}")
        days_ahead = (target_wd - local_now.weekday()) % 7
        candidate = local_now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0) + timedelta(days=days_ahead)
        dt_local = candidate if candidate > local_now else candidate + timedelta(days=7)
    else:
        hm = _parse_clock(low)
        if hm is not None:
            candidate = local_now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            dt_local = candidate if candidate > local_now else candidate + timedelta(days=1)
        elif _DATE_ONLY.match(raw):
            try:
                d = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise _err(f"bad schedule date {value!r}") from exc
            dt_local = d.replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=local_now.tzinfo)
        else:
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise _err(
                    f"bad schedule time {value!r}: use '30m', '2h', 'tomorrow 09:00', or 'YYYY-MM-DD[THH:MM]'"
                ) from exc
            dt_local = parsed.replace(tzinfo=local_now.tzinfo) if parsed.tzinfo is None else parsed

    dt_utc = dt_local.astimezone(timezone.utc)
    if dt_utc <= now:
        raise _err(f"send_at must be in the future (got {value!r} → {dt_utc.isoformat(timespec='seconds')})")
    return dt_utc.isoformat(timespec="seconds")


class ScheduleMixin:
    """Outbox & scheduled-delivery operations mixed into ``Service``."""

    def _verify_ai_snapshot(self, item: dict) -> None:
        if item.get("source") != "ai":
            return
        snap = (paths.data_dir() / "outbox-files").resolve()
        d = item["draft"]
        files = list(d.get("attachments", [])) + list((d.get("inline_images") or {}).values())
        for f in files:
            if not Path(f).resolve().is_relative_to(snap):
                raise _err(f"refusing outbox item {item['id']}: file outside snapshot dir: {f}")

    def _cleanup_linked_draft(self, account: str, draft_message_id: str) -> None:
        if not draft_message_id:
            return
        try:
            acct = self.account(account)  # type: ignore[attr-defined]
            folder = acct.folder("drafts")
            env = self.store.by_message_id(acct.name, folder, draft_message_id)  # type: ignore[attr-defined]
            if env is not None:
                self.delete_draft(env.id)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _set_linked_draft_header(self, account: str, draft_message_id: str, send_at: str) -> str:
        """Best-effort update of ``X-Monospace-Send-At`` on the linked IMAP draft; returns new Message-ID."""
        if not draft_message_id:
            return ""
        try:
            acct = self.account(account)  # type: ignore[attr-defined]
            folder = acct.folder("drafts")
            env = self.store.by_message_id(acct.name, folder, draft_message_id)  # type: ignore[attr-defined]
            if env is None:
                return draft_message_id
            draft, pm = self.load_draft(env.id)  # type: ignore[attr-defined]
            draft.send_at = send_at
            if pm.html:
                draft.body, draft.signature = pm.text, False
            msg = self._build_replacement(acct, draft, "human")  # type: ignore[attr-defined]
            with self.session(acct) as sess:  # type: ignore[attr-defined]
                sess.append(folder, msg.as_bytes(), ["\\Draft", "\\Seen"])
                sess.select(folder)
                sess.delete([env.uid])
            self.store.delete_uids(env.account, folder, [env.uid])  # type: ignore[attr-defined]
            res = self._drafted(acct, msg["Message-ID"], "updated")  # type: ignore[attr-defined]
            self.carry_tags(env, res.draft_id)  # type: ignore[attr-defined]
            return str(msg["Message-ID"])
        except Exception:
            return draft_message_id

    def _sync_draft_schedule(self, acct: "Account", message_id: str, draft: Draft,
                             source: policy.Source, send_at: str, res: "SendResult",
                             old_message_id: str = "") -> "SendResult":
        for mid in {m for m in (old_message_id, message_id) if m}:
            self.store.outbox_cancel_by_draft(acct.name, mid)  # type: ignore[attr-defined]
        if not send_at:
            return res
        res.send_at = send_at
        if not draft.recipients():
            res.detail += f" (send_at {send_at} saved on draft; add recipients to schedule sending)"
            return res
        action = policy.decide(acct.ai_send, source)
        if action == "draft":
            res.detail += f" (send_at {send_at} saved on draft; account policy ai_send=draft)"
            return res
        snap_draft = replace(Draft.from_dict(draft.to_dict()), message_id="", send_at=send_at)
        if source == "ai" and snap_draft.html_body:
            snap_draft.body = compose.review(Draft(html_body=snap_draft.html_body, signature=False))
        self._snapshot(snap_draft)  # type: ignore[attr-defined]
        status = "scheduled" if action == "send" else "pending"
        item_id = self.store.outbox_add(  # type: ignore[attr-defined]
            acct.name, snap_draft.to_dict(), source, status=status, send_at=send_at, draft_message_id=message_id
        )
        res.outbox_id = item_id
        if status == "scheduled":
            res.detail += f" (scheduled for {send_at}, #{item_id})"
        else:
            res.detail += f" (awaiting approval to send at {send_at}, #{item_id})"
        return res

    def schedule_draft(self, msg_id: int, send_at: str, source: policy.Source = "human") -> "SendResult":
        """Schedule a stored draft in Drafts to be sent at ``send_at`` (obeys ``ai_send``)."""
        from monospace.core.service import SendResult

        when = parse_send_at(send_at)
        res = self.update_draft(msg_id, source=source, send_at=when)  # type: ignore[attr-defined]
        acct = self.account(res.account)  # type: ignore[attr-defined]
        action = policy.decide(acct.ai_send, source)
        if action == "draft":
            return SendResult("drafted", acct.name,
                              f"saved send_at {when} on draft {res.draft_id} (account policy ai_send=draft; "
                              "a human must schedule or send it)", draft_id=res.draft_id, send_at=when)
        if not res.outbox_id:
            raise _err(f"draft {msg_id} has no recipients; add recipients before scheduling")
        if action == "queue":
            return SendResult("queued", acct.name,
                              f"awaiting approval: `monospace outbox approve {res.outbox_id}` or the TUI outbox (O) "
                              f"(scheduled for {when}); draft {res.draft_id} kept until then",
                              outbox_id=res.outbox_id, draft_id=res.draft_id, send_at=when)
        return SendResult("scheduled", acct.name,
                          f"scheduled for {when} (#{res.outbox_id}); draft {res.draft_id} kept until sent",
                          outbox_id=res.outbox_id, draft_id=res.draft_id, send_at=when)

    # ---- outbox & schedule queries / mutations -------------------------
    def outbox(self, *, include_scheduled: bool = False) -> list[dict]:
        if include_scheduled:
            return self.store.outbox(("pending", "scheduled"))  # type: ignore[attr-defined]
        return self.store.outbox("pending")  # type: ignore[attr-defined]

    def scheduled(self, account: str | None = None) -> list[dict]:
        acct_name = self.account(account).name if account else None  # type: ignore[attr-defined]
        items = [i for i in self.store.outbox(("pending", "scheduled"))  # type: ignore[attr-defined]
                 if i.get("send_at") or i["status"] == "scheduled"]
        if acct_name:
            items = [i for i in items if i["account"] == acct_name]
        return sorted(items, key=lambda i: (i.get("send_at") or "", i["id"]))

    def reschedule(self, item_id: int, send_at: str) -> dict:
        item = self.store.outbox_get(item_id)  # type: ignore[attr-defined]
        if not item or item["status"] not in ("pending", "scheduled"):
            raise _err(f"no pending or scheduled item {item_id}")
        when = parse_send_at(send_at)
        draft_dict = {**item["draft"], "send_at": when}
        new_mid = self._set_linked_draft_header(item["account"], item.get("draft_message_id", ""), when)
        self.store.outbox_reschedule(item_id, when, draft_dict, new_mid)  # type: ignore[attr-defined]
        return self.store.outbox_get(item_id)  # type: ignore[attr-defined]

    def outbox_approve(self, item_id: int, *, send_now: bool = False,
                       now: datetime | None = None) -> "SendResult":
        from monospace.core.service import SendResult

        item = self.store.outbox_get(item_id)  # type: ignore[attr-defined]
        if not item or item["status"] not in ("pending", "scheduled"):
            raise _err(f"no pending outbox item {item_id}")
        self._verify_ai_snapshot(item)
        send_at = item.get("send_at") or ""
        ref_now = now or datetime.now(timezone.utc)
        if item["status"] == "pending" and send_at and not send_now:
            try:
                due = datetime.fromisoformat(send_at) <= ref_now
            except ValueError:
                due = True
            if not due:
                self.store.outbox_set(item_id, "scheduled")  # type: ignore[attr-defined]
                return SendResult("scheduled", item["account"],
                                  f"approved; scheduled for {send_at} (#{item_id})",
                                  outbox_id=item_id, send_at=send_at)
        try:
            res = self.send_now(item["account"], Draft.from_dict(item["draft"]),  # type: ignore[attr-defined]
                                untrusted=item["source"] == "ai")
        except Exception as exc:
            self.store.outbox_set(item_id, item["status"], str(exc))  # type: ignore[attr-defined]
            raise
        self.store.outbox_set(item_id, "sent")  # type: ignore[attr-defined]
        self._cleanup_linked_draft(item["account"], item.get("draft_message_id", ""))
        return res

    def outbox_review(self, item: dict) -> str:
        """What the queued message will show recipients, as plain text (sanitized for terminals)."""
        acct = self.account(item["account"])  # type: ignore[attr-defined]
        return compose.review(Draft.from_dict(item["draft"]), signature=acct.signature)

    def outbox_cancel(self, item_id: int) -> None:
        """Withdraw a pending or scheduled item (AI callers may do this — it never sends anything)."""
        item = self.store.outbox_get(item_id)  # type: ignore[attr-defined]
        if not item or item["status"] not in ("pending", "scheduled"):
            raise _err(f"no pending or scheduled outbox item {item_id}")
        self.store.outbox_set(item_id, "cancelled")  # type: ignore[attr-defined]
        if item.get("draft_message_id"):
            self._set_linked_draft_header(item["account"], item["draft_message_id"], "")

    def outbox_reject(self, item_id: int) -> None:
        item = self.store.outbox_get(item_id)  # type: ignore[attr-defined]
        if not item:
            raise _err(f"no outbox item {item_id}")
        self.store.outbox_set(item_id, "rejected")  # type: ignore[attr-defined]
        if item.get("draft_message_id"):
            self._set_linked_draft_header(item["account"], item["draft_message_id"], "")

    def flush_scheduled(self, *, now: datetime | None = None) -> list["SendResult"]:
        """Send all ``status='scheduled'`` items whose ``send_at`` has arrived. Never touches ``pending``."""
        ref_now = now or datetime.now(timezone.utc)
        sent_results: list["SendResult"] = []
        for item in self.store.outbox("scheduled"):  # type: ignore[attr-defined]
            send_at = item.get("send_at") or ""
            if send_at:
                try:
                    if datetime.fromisoformat(send_at) > ref_now:
                        continue
                except ValueError:
                    pass
            try:
                self._verify_ai_snapshot(item)
                res = self.send_now(item["account"], Draft.from_dict(item["draft"]),  # type: ignore[attr-defined]
                                    untrusted=item["source"] == "ai")
                res.outbox_id = item["id"]
                res.send_at = send_at or None
                self.store.outbox_set(item["id"], "sent")  # type: ignore[attr-defined]
                self._cleanup_linked_draft(item["account"], item.get("draft_message_id", ""))
                sent_results.append(res)
            except Exception as exc:
                self.store.outbox_set(item["id"], "scheduled", str(exc))  # type: ignore[attr-defined]
        return sent_results
