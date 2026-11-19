"""`monospace update` — check for and install a newer version (always explicit, never automatic)."""

from __future__ import annotations

import argparse
import subprocess

from monospace.cli.output import info
from monospace.core import update


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("update", help="update monospace to the latest version")
    p.add_argument("--check", action="store_true", help="only report whether an update is available")
    p.add_argument("--ref", help="branch or v* tag to track from now on (default: the installed one, else main)")


def run(args: argparse.Namespace) -> int:
    inst = update.installed()
    target = update.Installed(args.ref or (inst.ref if inst else "main"))
    try:
        newest = update.latest(target)
    except (update.UpdateError, KeyError, ValueError) as exc:
        info(f"error: {exc}")
        return 1
    current = inst.current if inst and inst.ref == target.ref else ""
    if newest == current:
        info(f"monospace is up to date ({target.ref} @ {update.short(newest)})")
        return 0
    changes = update.notes(inst if inst and inst.ref == target.ref else None, newest)
    if changes:
        info("what's new:\n" + "\n".join(changes) + "\n")
    if args.check:
        have = update.short(current) if current else "unknown"
        info(f"update available: {have} → {update.short(newest)} ({target.ref}). Run `monospace update`.")
        return 0
    uv = update.find_uv()
    if not uv:
        info("error: uv not found; rerun the installer:\n"
             f"  curl -fsSL https://raw.githubusercontent.com/{update.REPO}/main/install.sh | sh")
        return 1
    info(f"updating to {target.ref} @ {update.short(newest)}…")
    if subprocess.run(update.install_command(uv, newest)).returncode != 0:
        info("error: install failed (see uv output above)")
        return 1
    # a tag install is identified by the tag itself; a branch install by its commit
    update.record(newest if target.is_tag else target.ref, "" if target.is_tag else newest)
    info("✓ updated — restart monospace (and the daemon, if you use it)")
    return 0

