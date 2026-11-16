"""`monospace outbox` — review messages AI agents queued under ai_send=confirm."""

from __future__ import annotations

import argparse

from monospace.cli.output import confirm, emit_json, info, interactive
from monospace.core.service import Service


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("outbox", help="review AI-queued messages (ai_send=confirm)")
    osub = p.add_subparsers(dest="outbox_cmd")
    osub.add_parser("list").add_argument("--json", action="store_true")
    osub.add_parser("show").add_argument("id", type=int)
    osub.add_parser("approve").add_argument("id", type=int)
    osub.add_parser("reject").add_argument("id", type=int)


def _show(item: dict) -> None:
    d = item["draft"]
    print(f"#{item['id']}  account={item['account']}  queued={item['created']}  source={item['source']}")
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
    print("\n" + d.get("body", ""))
    if item.get("error"):
        print(f"\nlast error: {item['error']}")


def run(args: argparse.Namespace, svc: Service) -> int:
    cmd = args.outbox_cmd or "list"
    if cmd == "list":
        items = svc.outbox()
        if getattr(args, "json", False):
            emit_json(items)
        elif not items:
            info("outbox empty")
        for it in [] if getattr(args, "json", False) else items:
            print(f"#{it['id']:<4} {it['account']:12} → {', '.join(it['draft'].get('to', []))[:40]:40} "
                  f"{it['draft'].get('subject', '')[:50]}")
        return 0
    item = svc.store.outbox_get(args.id)
    if not item:
        info(f"no outbox item {args.id}")
        return 1
    if cmd == "show":
        _show(item)
        return 0
    # Approving is a human decision: never allow it from an agent / pipe.
    if not interactive():
        info("outbox approve/reject must be run by a human in an interactive terminal")
        return 2
    _show(item)
    if cmd == "approve":
        if not confirm("\nSend this message?"):
            info("not sent")
            return 0
        res = svc.outbox_approve(args.id)
        info(f"{res.status}: {res.detail}")
    else:
        svc.outbox_reject(args.id)
        info(f"rejected #{args.id}")
    return 0
