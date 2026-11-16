"""Mail commands: sync, ls, read, search, send, draft, reply, mark, move, attachment."""

from __future__ import annotations

import argparse
import sys

from monospace.cli.output import confirm, emit_json, envelope_rows, info, interactive
from monospace.core.compose import Draft, split_addrs
from monospace.core.service import Service


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("sync", help="fetch new mail into the local cache")
    p.add_argument("--folder", action="append", help="folder(s) to sync (default INBOX)")
    p.add_argument("--all", action="store_true", help="sync every folder")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("ls", help="list messages")
    p.add_argument("--folder", default="INBOX")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--sync", action="store_true", help="sync this folder first")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("folders", help="list folders")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("read", help="print a message")
    p.add_argument("id", type=int)
    p.add_argument("--mark-read", action="store_true")
    p.add_argument("--raw", action="store_true", help="print the raw RFC 5322 source")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="full-text search the cache")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")

    for name, helptext in (("send", "send a message"), ("draft", "save a message to Drafts")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--to", action="append", default=[])
        p.add_argument("--cc", action="append", default=[])
        p.add_argument("--bcc", action="append", default=[])
        p.add_argument("--subject", default="")
        p.add_argument("--body", help="markdown body; omit to read stdin")
        p.add_argument("--attach", action="append", default=[])
        p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("reply", help="reply to a message")
    p.add_argument("id", type=int)
    p.add_argument("--body", help="markdown body; omit to read stdin")
    p.add_argument("--all", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mark", help="change read/flag state")
    p.add_argument("id", type=int)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--read", dest="read", action="store_true", default=None)
    g.add_argument("--unread", dest="read", action="store_false")
    g2 = p.add_mutually_exclusive_group()
    g2.add_argument("--flag", dest="flagged", action="store_true", default=None)
    g2.add_argument("--unflag", dest="flagged", action="store_false")

    p = sub.add_parser("move", help="move a message (folder name or archive/trash/junk)")
    p.add_argument("id", type=int)
    p.add_argument("folder")

    p = sub.add_parser("attachment", help="save an attachment to disk")
    p.add_argument("id", type=int)
    p.add_argument("index", type=int, nargs="?", default=0)
    p.add_argument("--dir")


def _body(arg: str | None) -> str:
    if arg is not None:
        return arg
    if sys.stdin.isatty():
        info("Enter the body (markdown), finish with Ctrl-D:")
    return sys.stdin.read()


def _print_message(svc: Service, msg_id: int, args: argparse.Namespace) -> None:
    if args.raw:
        sys.stdout.write(svc.raw(msg_id).decode("utf-8", "replace"))
        return
    env, pm = svc.read(msg_id, mark_read=args.mark_read)
    if args.json:
        emit_json({**env.to_dict(), "to": pm.to, "cc": pm.cc, "text": pm.best_text(),
                   "has_html": bool(pm.html),
                   "attachments": [{"index": i, "filename": a.filename, "content_type": a.content_type,
                                    "size": a.size} for i, a in enumerate(pm.attachments)]})
        return
    print(f"From:    {pm.from_}\nTo:      {', '.join(pm.to)}")
    if pm.cc:
        print(f"Cc:      {', '.join(pm.cc)}")
    print(f"Date:    {pm.date}\nSubject: {pm.subject}\n")
    print(pm.best_text())
    for i, a in enumerate(pm.attachments):
        print(f"\n[{i}] 📎 {a.filename} ({a.content_type}, {a.size:,} bytes)")


def _submit(svc: Service, args: argparse.Namespace, draft: Draft, *, as_draft: bool) -> None:
    if as_draft:
        res = svc.save_draft(args.account, draft)
    else:
        human = interactive()
        if human and not args.yes:
            info(f"To: {', '.join(draft.to)}  Subject: {draft.subject}")
            if not confirm("Send now?"):
                info("cancelled")
                return
        res = svc.submit(args.account, draft, "human" if human else "ai")
    emit_json(res.to_dict()) if args.json else info(f"{res.status}: {res.detail}")


def run(cmd: str, args: argparse.Namespace, svc: Service) -> int:
    if cmd == "sync":
        stats = svc.sync(args.account, args.folder, all_folders=args.all)
        emit_json(stats.to_dict()) if args.json else info(
            f"synced {stats.folders} folder(s): {stats.new} new, {stats.removed} removed")
        for e in stats.errors:
            info(f"error: {e}")
        return 1 if stats.errors else 0
    if cmd == "ls":
        if args.sync:
            svc.sync(args.account, [args.folder])
        envs = svc.list(args.account, args.folder, limit=args.limit, unread_only=args.unread)
        emit_json([e.to_dict() for e in envs]) if args.json else envelope_rows(envs)
    elif cmd == "folders":
        folders = svc.folders(args.account)
        if args.json:
            emit_json(folders)
        else:
            for f in folders:
                print(f"{f['name']:40} {f['unread']:>5} unread / {f['total']:>6}")
    elif cmd == "read":
        _print_message(svc, args.id, args)
    elif cmd == "search":
        envs = svc.search(args.query, args.account, args.limit)
        emit_json([e.to_dict() for e in envs]) if args.json else envelope_rows(envs)
    elif cmd in ("send", "draft"):
        draft = Draft(to=split_addrs(args.to), cc=split_addrs(args.cc), bcc=split_addrs(args.bcc),
                      subject=args.subject, body=_body(args.body), attachments=args.attach)
        _submit(svc, args, draft, as_draft=cmd == "draft")
    elif cmd == "reply":
        env, pm = svc.read(args.id)
        from monospace.core import compose

        draft = compose.reply(pm, _body(args.body), svc.account(env.account).email, reply_all=args.all)
        args.account = env.account
        _submit(svc, args, draft, as_draft=False)
    elif cmd == "mark":
        e = svc.mark(args.id, read=args.read, flagged=args.flagged)
        info(f"{e.id}: {'unread' if e.unread else 'read'}{', flagged' if e.flagged else ''}")
    elif cmd == "move":
        info(f"moved to {svc.move(args.id, args.folder)}")
    elif cmd == "attachment":
        print(svc.save_attachment(args.id, args.index, args.dir))
    return 0
