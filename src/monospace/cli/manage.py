"""`monospace forward`, `monospace drafts …`, `monospace folders …`.

A human at a terminal acts as ``source="human"``; pipes and agents act as ``source="ai"`` and so
obey ``ai_send`` and the agent-attachment folder, exactly like the MCP tools.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import tempfile

from monospace.cli.mail import _body, add_content_args, apply_content, read_html
from monospace.cli.output import confirm, emit_json, envelope_rows, info, interactive
from monospace.core import compose
from monospace.core.compose import split_addrs
from monospace.core.service import Service

COMMANDS = ("forward", "drafts", "folders")


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("forward", help="forward a message")
    p.add_argument("id", type=int)
    p.add_argument("--to", action="append", default=[], required=True)
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--bcc", action="append", default=[])
    p.add_argument("--body", help="markdown note above the forwarded text (default: none)")
    p.add_argument("--no-attachments", action="store_true", help="don't carry the original's attachments")
    add_content_args(p)
    p.add_argument("--draft", action="store_true", help="save to Drafts instead of sending")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("drafts", help="list, show, edit, send or delete drafts")
    ds = p.add_subparsers(dest="drafts_cmd")
    x = ds.add_parser("ls", help="list drafts")
    x.add_argument("--limit", type=int, default=30)
    x.add_argument("--json", action="store_true")
    x = ds.add_parser("show", help="print a draft")
    x.add_argument("id", type=int)
    x.add_argument("--json", action="store_true")
    x = ds.add_parser("edit", help="change a draft (omitted fields are kept)")
    x.add_argument("id", type=int)
    x.add_argument("--to", action="append", help="replace To (repeatable)")
    x.add_argument("--cc", action="append", help="replace Cc")
    x.add_argument("--bcc", action="append", help="replace Bcc")
    x.add_argument("--subject")
    x.add_argument("--body", help="new markdown body ('-' for stdin); re-renders the HTML")
    x.add_argument("--editor", action="store_true", help="edit the body in $EDITOR")
    add_content_args(x)
    x.add_argument("--detach", action="append", default=[], metavar="NAME", help="remove an attachment by name")
    x.add_argument("--remove-inline", action="append", default=[], metavar="NAME",
                   help="remove an inline image by file name or cid")
    x.add_argument("--json", action="store_true")
    for name, helptext in (("send", "send a draft"), ("rm", "delete a draft")):
        x = ds.add_parser(name, help=helptext)
        x.add_argument("id", type=int)
        x.add_argument("--yes", "-y", action="store_true")
        x.add_argument("--json", action="store_true")

    p = sub.add_parser("folders", help="list folders, or create / rename / delete one")
    p.add_argument("--json", action="store_true")
    fs = p.add_subparsers(dest="folders_cmd")
    fs.add_parser("create", help="create a folder").add_argument("name")
    x = fs.add_parser("rename", help="rename a folder")
    x.add_argument("old")
    x.add_argument("new")
    x = fs.add_parser("rm", help="delete an EMPTY, non-system folder")
    x.add_argument("name")
    x.add_argument("--yes", "-y", action="store_true")


def _source() -> str:
    return "human" if interactive() else "ai"


def _result(args: argparse.Namespace, res) -> None:  # type: ignore[no-untyped-def]
    emit_json(res.to_dict()) if getattr(args, "json", False) else info(
        f"{res.status}: {res.detail}" + (f" (draft id {res.draft_id})" if res.draft_id else ""))


def _show_draft(d: dict) -> None:
    print(f"Draft #{d['id']}  ({d['account']})\nTo:      {', '.join(d['to'])}")
    for label, key in (("Cc", "cc"), ("Bcc", "bcc")):
        if d[key]:
            print(f"{label + ':':8} {', '.join(d[key])}")
    print(f"Subject: {d['subject']}\n\n{d['body']}")
    for a in d["attachments"]:
        print(f"\n📎 {a['filename']} ({a['content_type']}, {a['size']:,} bytes)")
    for i in d["inline_images"]:
        print(f"🖼  inline {i['filename']} ({i['content_type']}, {i['size']:,} bytes, cid {i['cid']})")
    if d["html"] and not d["html_matches_body"]:
        from monospace.core.mime import html_to_text

        print("\n⚠ The HTML part (what most recipients see) differs from the text above. It reads:\n")
        print(html_to_text(d["html"]))
    elif d["html"]:
        print("\n(has an HTML part — `monospace read --raw` to see it)")


def _editor(text: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        subprocess.run([*shlex.split(editor), path], check=True)
        with open(path) as fh:
            return fh.read()
    finally:
        os.unlink(path)


def _drafts(args: argparse.Namespace, svc: Service) -> int:
    cmd = args.drafts_cmd or "ls"
    if cmd == "ls":
        envs = svc.list(args.account, "drafts", limit=getattr(args, "limit", 30))
        emit_json([e.to_dict() for e in envs]) if getattr(args, "json", False) else envelope_rows(envs)
        return 0
    if cmd == "show":
        d = svc.get_draft(args.id)
        emit_json(d) if args.json else _show_draft(d)
        return 0
    if cmd == "edit":
        body = args.body
        if body == "-":
            body = _body(None)
        if args.editor:
            if not interactive():
                info("--editor needs an interactive terminal")
                return 2
            body = _editor(svc.get_draft(args.id)["body"] if body is None else body)
        inline = [compose.parse_inline_arg(raw) for raw in args.inline]
        res = svc.update_draft(
            args.id, source=_source(), to=args.to, cc=args.cc, bcc=args.bcc, subject=args.subject, body=body,
            html_body=read_html(args.html), add_attachments=args.attach or None,
            remove_attachments=args.detach or None, add_inline_images=inline or None,
            remove_inline_images=args.remove_inline or None)
        _result(args, res)
        return 0
    human = _source() == "human"
    if human and not args.yes:
        _show_draft(svc.get_draft(args.id))
        if not confirm("\nSend this draft?" if cmd == "send" else "\nDelete this draft?"):
            info("cancelled")
            return 0
    if cmd == "send":
        _result(args, svc.send_draft(args.id, _source()))  # type: ignore[arg-type]
    else:
        svc.delete_draft(args.id)
        emit_json({"deleted": args.id}) if args.json else info(f"deleted draft {args.id}")
    return 0


def _folders(args: argparse.Namespace, svc: Service) -> int:
    cmd = args.folders_cmd
    if cmd == "create":
        folders = svc.create_folder(args.account, args.name)
    elif cmd == "rename":
        folders = svc.rename_folder(args.account, args.old, args.new)
    elif cmd == "rm":
        if interactive() and not args.yes and not confirm(f"Delete folder {args.name!r}?"):
            info("cancelled")
            return 0
        folders = svc.delete_folder(args.account, args.name)
    else:
        folders = svc.folders(args.account)
    if args.json:
        emit_json(folders)
    else:
        for f in folders:
            print(f"{f['name']:40} {f['unread']:>5} unread / {f['total']:>6}")
    return 0


def run(cmd: str, args: argparse.Namespace, svc: Service) -> int:
    if cmd == "drafts":
        return _drafts(args, svc)
    if cmd == "folders":
        return _folders(args, svc)
    # forward
    env, pm = svc.read(args.id)
    args.account = env.account
    draft = compose.forward(pm, args.body or "", split_addrs(args.to))
    draft.cc += split_addrs(args.cc)
    draft.bcc += split_addrs(args.bcc)
    draft = apply_content(draft, args)
    human = _source() == "human"
    if not args.draft and human and not args.yes:
        info(f"To: {', '.join(draft.to)}  Subject: {draft.subject}")
        if not confirm("Forward now?"):
            info("cancelled")
            return 0
    res = svc.forward(args.id, args.body or "", draft.to, source=_source(), send=not args.draft,  # type: ignore
                      extras=lambda _d: draft, include_attachments=not args.no_attachments)
    _result(args, res)
    return 0
