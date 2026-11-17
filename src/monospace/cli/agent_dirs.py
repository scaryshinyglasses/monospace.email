"""`monospace agent-dirs` — folders AI agents may read attachments / inline images from.

Adding or removing a folder widens what an agent (or a prompt injection in an email) could send, so
it is a human decision: both refuse to run without an interactive terminal.
"""

from __future__ import annotations

import argparse

from monospace.cli.output import confirm, emit_json, info, interactive
from monospace.core import config, policy
from monospace.core.config import Config


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("agent-dirs", help="folders AI agents may attach / embed files from")
    s = p.add_subparsers(dest="dirs_cmd")
    s.add_parser("list").add_argument("--json", action="store_true")
    s.add_parser("add", help="let agents read files in a folder (interactive only)").add_argument("path")
    s.add_parser("rm", help="stop agents reading a folder (interactive only)").add_argument("path")


def run(args: argparse.Namespace, cfg: Config) -> int:
    st = cfg.settings
    cmd = args.dirs_cmd or "list"
    if cmd == "list":
        roots = [str(p) for p in policy.agent_roots(st)]
        if getattr(args, "json", False):
            emit_json({"attachment_dir": roots[0], "read_dirs": st.agent_read_dirs, "effective": roots})
        else:
            print(f"{roots[0]}  (attachment folder)")
            for d in st.agent_read_dirs:
                print(f"{d}{'' if d in roots else '  (missing — ignored)'}")
        return 0
    if not interactive():
        info("agent-dirs add/rm must be run by a human in an interactive terminal")
        return 2
    if cmd == "add":
        real = policy.validate_read_dir(args.path)
        if str(real) in st.agent_read_dirs:
            info(f"already allowed: {real}")
            return 0
        info(f"Agents (and any email that tricks one) will be able to send files from:\n  {real}")
        if not confirm("Allow?"):
            info("cancelled")
            return 0
        st.agent_read_dirs.append(str(real))
    else:
        from pathlib import Path

        target = str(Path(args.path).expanduser().resolve())
        if target not in st.agent_read_dirs and args.path not in st.agent_read_dirs:
            info(f"not in the list: {args.path}")
            return 1
        st.agent_read_dirs = [d for d in st.agent_read_dirs if d not in (target, args.path)]
    config.save(cfg)
    info(f"saved. agents may read: {', '.join(str(p) for p in policy.agent_roots(st))}")
    return 0
