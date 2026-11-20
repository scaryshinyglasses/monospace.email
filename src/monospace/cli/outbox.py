"""`monospace outbox` and `monospace schedule` — review AI-queued messages and manage scheduled deliveries."""

from __future__ import annotations

import argparse

from monospace.cli.output import confirm, emit_json, info, interactive
from monospace.core.service import Service


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("outbox", help="review AI-queued and scheduled messages")
    osub = p.add_subparsers(dest="outbox_cmd")
    ls = osub.add_parser("list")
    ls.add_argument("--all", action="store_true", help="include scheduled items as well as pending approvals")
    ls.add_argument("--json", action="store_true")
    osub.add_parser("show").add_argument("id", type=int)
    ap = osub.add_parser("approve")
    ap.add_argument("id", type=int)
    ap.add_argument("--now", action="store_true", help="send immediately even if scheduled for later")
    osub.add_parser("reject").add_argument("id", type=int)
    cn = osub.add_parser("cancel")
    cn.add_argument("id", type=int)
    cn.add_argument("--json", action="store_true")
    rs = osub.add_parser("reschedule")
    rs.add_argument("id", type=int)
    rs.add_argument("send_at", metavar="WHEN")
    rs.add_argument("--json", action="store_true")

    sp = sub.add_parser("schedule", help="list, reschedule, cancel or flush scheduled emails and drafts")
    sp.add_argument("--json", action="store_true")
    ssub = sp.add_subparsers(dest="schedule_cmd")
    ssub.add_parser("list", help="list scheduled emails and drafts").add_argument("--json", action="store_true")
    ssub.add_parser("show", help="inspect a scheduled item").add_argument("id", type=int)
    sc = ssub.add_parser("cancel", help="cancel a scheduled send (keeps any linked draft)")
    sc.add_argument("id", type=int)
    sc.add_argument("--json", action="store_true")
    sr = ssub.add_parser("reschedule", help="change the send time of a scheduled item")
    sr.add_argument("id", type=int)
    sr.add_argument("send_at", metavar="WHEN")
    sr.add_argument("--json", action="store_true")
    ssub.add_parser("run", help="send all due scheduled items now").add_argument("--json", action="store_true")


def _show(item: dict, svc: Service) -> None:
    from monospace.core.sanitize import terminal_safe

    d = {k: (terminal_safe(v) if isinstance(v, str) else
             [terminal_safe(x) for x in v] if isinstance(v, list) else v) for k, v in item["draft"].items()}
    sched = f"  send_at={item['send_at']}" if item.get("send_at") else ""
    print(f"#{item['id']}  account={item['account']}  status={item['status']}{sched}"
          f"  queued={item['created']}  source={item['source']}")
    print(f"To: {', '.join(d.get('to', []))}")
    if d.get("cc"):
        print(f"Cc: {', '.join(d['cc'])}")
    if d.get("bcc"):
        print(f"Bcc: {', '.join(d['bcc'])}")
    print(f"Subject: {d.get('subject', '')}")
    if d.get("attachments"):
        from pathlib import Path

        for f in d["attachments"]:
            p = Path(f).resolve()
            print(f"Attachment: {p} ({p.stat().st_size:,} bytes)" if p.exists() else f"Attachment: {f} (missing!)")
    for f in (d.get("inline_images") or {}).values():
        print(f"Inline image: {terminal_safe(str(f))}")
    print("\n--- what recipients will see ---\n" + svc.outbox_review(item))
    if item.get("error"):
        print(f"\nlast error: {item['error']}")


def _print_rows(items: list[dict], empty_msg: str, *, as_json: bool) -> int:
    if as_json:
        emit_json(items)
        return 0
    if not items:
        info(empty_msg)
        return 0
    for it in items:
        when = f" [{it['send_at']}]" if it.get("send_at") else ""
        status = f" ({it['status']})" if it.get("status") != "pending" or when else ""
        print(f"#{it['id']:<4} {it['account']:12} → {', '.join(it['draft'].get('to', []))[:34]:34} "
              f"{it['draft'].get('subject', '')[:40]}{when}{status}")
    return 0


def run(args: argparse.Namespace, svc: Service, *, cmd: str = "outbox") -> int:
    sub_cmd = (args.schedule_cmd if cmd == "schedule" else args.outbox_cmd) or "list"
    as_json = getattr(args, "json", False)
    if sub_cmd == "list":
        if cmd == "schedule":
            return _print_rows(svc.scheduled(getattr(args, "account", None)), "no scheduled messages", as_json=as_json)
        include_sched = getattr(args, "all", False) or not as_json
        return _print_rows(svc.outbox(include_scheduled=include_sched), "outbox empty", as_json=as_json)
    if sub_cmd == "run":
        res = [r.to_dict() for r in svc.flush_scheduled()]
        emit_json(res) if as_json else info(f"sent {len(res)} due scheduled message(s)")
        return 0
    item = svc.store.outbox_get(args.id)
    if not item:
        info(f"no outbox item {args.id}")
        return 1
    if sub_cmd == "show":
        _show(item, svc)
        return 0
    if sub_cmd == "cancel":
        svc.outbox_cancel(args.id)
        emit_json({"cancelled": args.id}) if as_json else info(f"cancelled #{args.id}")
        return 0
    if sub_cmd == "reschedule":
        updated = svc.reschedule(args.id, args.send_at)
        emit_json(updated) if as_json else info(f"rescheduled #{args.id} for {updated['send_at']}")
        return 0
    # Approving or rejecting is a human decision: never allow it from an agent / pipe.
    if not interactive():
        info("outbox approve/reject must be run by a human in an interactive terminal")
        return 2
    _show(item, svc)
    if sub_cmd == "approve":
        send_now = getattr(args, "now", False)
        prompt = ("\nSend this message now?" if (send_now or not item.get("send_at") or item["status"] == "scheduled")
                  else f"\nApprove this message (scheduled for {item['send_at']})?")
        if not confirm(prompt):
            info("not sent")
            return 0
        res = svc.outbox_approve(args.id, send_now=send_now)
        info(f"{res.status}: {res.detail}")
    else:
        svc.outbox_reject(args.id)
        info(f"rejected #{args.id}")
    return 0
