"""`monospace tag add|rm|set|ls|show` — tags on messages and drafts.

Tagging never sends or deletes anything, so agents (non-interactive callers) may use it freely.
"""

from __future__ import annotations

import argparse

from monospace.cli.output import emit_json, envelope_rows, info
from monospace.core.service import Service

COMMANDS = ("tag",)


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("tag", help="tag messages and drafts (Gmail labels / IMAP keywords / local)")
    ts = p.add_subparsers(dest="tag_cmd", required=True)
    for name, helptext in (("add", "add tags to a message"), ("rm", "remove tags from a message"),
                           ("set", "replace a message's tags (no tags = clear)")):
        x = ts.add_parser(name, help=helptext)
        x.add_argument("id", type=int, help="message id (from ls / search / drafts ls)")
        x.add_argument("tags", nargs="*" if name == "set" else "+", metavar="TAG",
                       help="one tag per argument (quote tags with spaces), or comma-separated")
        x.add_argument("--json", action="store_true")
    x = ts.add_parser("ls", help="list every tag with its message count")
    x.add_argument("--json", action="store_true")
    x = ts.add_parser("show", help="list messages carrying a tag (all folders)")
    x.add_argument("tag")
    x.add_argument("--folder", help="only this folder (name or role)")
    x.add_argument("--limit", type=int, default=30)
    x.add_argument("--json", action="store_true")


def run(cmd: str, args: argparse.Namespace, svc: Service) -> int:
    sub = args.tag_cmd
    if sub == "ls":
        tags = svc.list_tags(args.account)
        if args.json:
            emit_json(tags)
        elif not tags:
            info("(no tags)")
        for t in [] if args.json else tags:
            print(f"{t['count']:>6}  {t['tag']}")
        return 0
    if sub == "show":
        envs = svc.tagged(args.account, args.tag, limit=args.limit, folder=args.folder)
        emit_json([e.to_dict() for e in envs]) if args.json else envelope_rows(envs)
        return 0
    action = {"add": svc.add_tags, "rm": svc.remove_tags, "set": svc.set_tags}[sub]
    env = action(args.id, args.tags)
    if args.json:
        emit_json(env.to_dict())
    else:
        info(f"{env.id}: {', '.join(env.tags) if env.tags else '(no tags)'}")
    return 0
