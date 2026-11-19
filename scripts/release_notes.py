#!/usr/bin/env python3
"""Release notes: CHANGELOG.md is the source; README and the website show the latest entries.

  collect [RANGE]   add commits in RANGE (``before..after``; default: last 50) to ``## Unreleased``
  render [--check]  rewrite the marked blocks in README.md / website/index.html + website/changelog.json
  release VERSION   move Unreleased under ``## vVERSION — DATE``, bump the package version, print notes

Stdlib only (CI runs it without ``uv sync``). Commit subjects are untrusted text headed for a public
web page, so everything rendered to HTML is escaped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/scaryshinyglasses/monospace.email"
START, END = "<!-- release-notes:start -->", "<!-- release-notes:end -->"
LATEST = 8
UNRELEASED = "Unreleased"
ENTRY = re.compile(r"^- (?P<text>.+?)(?: \(\[(?P<sha>[0-9a-f]{7,40})\]\([^)]*\)\))?$")
HEADING = re.compile(r"^## (?P<title>Unreleased|v\d+\.\d+\.\d+)(?: — (?P<date>\d{4}-\d{2}-\d{2}))?\s*$")
SKIP_SUBJECT = re.compile(r"^(Merge\b|(chore|ci|build|style)(\(.+\))?:)|\[skip changelog\]|\[skip ci\]", re.I)
BOTS = ("github-actions[bot]", "dependabot[bot]")
HEADER = f"""# Changelog

Notable changes to monospace.email. Commits pushed to `main` land under **Unreleased** automatically
(`scripts/release_notes.py`); cutting a release moves them under a version. Edit entries freely: the bot
only adds commits it hasn't seen. Releases: {REPO_URL}/releases
"""


@dataclass
class Entry:
    text: str
    sha: str = ""

    def markdown(self) -> str:
        return f"- {self.text} ([{self.sha}]({REPO_URL}/commit/{self.sha}))" if self.sha else f"- {self.text}"


@dataclass
class Section:
    title: str
    date: str = ""
    entries: list[Entry] = field(default_factory=list)

    def heading(self) -> str:
        return f"## {self.title}" + (f" — {self.date}" if self.date else "")


def parse(text: str) -> list[Section]:
    sections: list[Section] = []
    for line in text.splitlines():
        if m := HEADING.match(line):
            sections.append(Section(m["title"], m["date"] or ""))
        elif sections and (m := ENTRY.match(line.rstrip())):
            sections[-1].entries.append(Entry(m["text"], m["sha"] or ""))
    if not sections or sections[0].title != UNRELEASED:
        sections.insert(0, Section(UNRELEASED))
    return sections


def dump(sections: list[Section]) -> str:
    out = [HEADER]
    for s in sections:
        out.append(s.heading() + "\n")
        out += [e.markdown() for e in s.entries]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def changelog_path(root: Path) -> Path:
    return root / "CHANGELOG.md"


def load(root: Path) -> list[Section]:
    path = changelog_path(root)
    return parse(path.read_text()) if path.exists() else [Section(UNRELEASED)]


# ---- collect ------------------------------------------------------------
def git_commits(root: Path, rng: str | None) -> list[tuple[str, str, str]]:
    """(sha, author, subject), newest first, no merges."""
    args = ["git", "-C", str(root), "log", "--no-merges", "--format=%H%x1f%an%x1f%s"]
    if rng and not re.fullmatch(r"0+\.\..+", rng) and _range_ok(root, rng):
        args.append(rng)
    else:
        args += ["-n", "50"]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    return [tuple(line.split("\x1f", 2)) for line in out.splitlines() if line.count("\x1f") == 2]  # type: ignore[misc]


def _range_ok(root: Path, rng: str) -> bool:
    before = rng.split("..")[0]
    return subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{before}^{{commit}}"],
                          capture_output=True).returncode == 0


def collect(sections: list[Section], commits: list[tuple[str, str, str]]) -> int:
    seen = {e.sha[:7] for s in sections for e in s.entries if e.sha}
    fresh = [Entry(subject.strip(), sha[:7]) for sha, author, subject in commits
             if sha[:7] not in seen and author not in BOTS and subject.strip() and not SKIP_SUBJECT.search(subject)]
    sections[0].entries[:0] = fresh
    return len(fresh)


# ---- render -------------------------------------------------------------
def latest(sections: list[Section], n: int = LATEST) -> list[Section]:
    out, left = [], n
    for s in sections:
        if left <= 0:
            break
        if s.entries:
            out.append(Section(s.title, s.date, s.entries[:left]))
            left -= len(out[-1].entries)
    return out


def _label(s: Section) -> str:
    return s.title + (f" — {s.date}" if s.date else "")


def readme_block(sections: list[Section]) -> str:
    lines = ["### What's new", ""]
    for s in latest(sections):
        lines += [f"**{_label(s)}**", ""] + [e.markdown() for e in s.entries] + [""]
    lines.append(f"[Full changelog](CHANGELOG.md) · [Releases]({REPO_URL}/releases)")
    return "\n".join(lines)


def html_block(sections: list[Section]) -> str:
    esc = html.escape
    parts = ['<section class="notes-section wrap" id="whats-new" aria-labelledby="whats-new-title">',
             '<div class="section-heading"><span class="eyebrow">CHANGELOG / WHAT\'S NEW</span>'
             '<h2 id="whats-new-title">Fresh off<br><em>the main branch.</em></h2>'
             '<p>Every push to main lands here. <code>monospace update</code> brings it home.</p></div>',
             '<div class="notes-list">']
    for s in latest(sections):
        parts.append(f'<div class="notes-group"><h3>{esc(_label(s))}</h3><ul>')
        for e in s.entries:
            link = (f' <a href="{esc(REPO_URL)}/commit/{esc(e.sha)}">{esc(e.sha)}</a>' if e.sha else "")
            parts.append(f"<li>{esc(e.text)}{link}</li>")
        parts.append("</ul></div>")
    parts += ['</div>', f'<p class="notes-more"><a href="{REPO_URL}/blob/main/CHANGELOG.md">Full changelog '
              '<span aria-hidden="true">↗</span></a><a href="' + REPO_URL + '/releases">Releases '
              '<span aria-hidden="true">↗</span></a></p>', "</section>"]
    return "\n".join(parts)


def changelog_json(sections: list[Section]) -> str:
    data = {"repo": REPO_URL, "sections": [
        {"title": s.title, "date": s.date or None,
         "entries": [{"text": e.text, "sha": e.sha or None,
                      "url": f"{REPO_URL}/commit/{e.sha}" if e.sha else None} for e in s.entries]}
        for s in sections if s.entries]}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def replace_block(text: str, block: str, where: str) -> str:
    if START not in text or END not in text:
        raise SystemExit(f"{where}: missing {START} … {END} markers")
    head, rest = text.split(START, 1)
    _old, tail = rest.split(END, 1)
    return f"{head}{START}\n{block}\n{END}{tail}"


def render(root: Path, sections: list[Section]) -> dict[Path, str]:
    """Every generated file → its new content."""
    readme, site = root / "README.md", root / "website" / "index.html"
    return {
        changelog_path(root): dump(sections),
        readme: replace_block(readme.read_text(), readme_block(sections), "README.md"),
        site: replace_block(site.read_text(), html_block(sections), "website/index.html"),
        root / "website" / "changelog.json": changelog_json(sections),
    }


def write(files: dict[Path, str], check: bool = False) -> list[Path]:
    stale = [p for p, text in files.items() if not p.exists() or p.read_text() != text]
    if not check:
        for p in stale:
            p.write_text(files[p])
    return stale


# ---- release ------------------------------------------------------------
def release(root: Path, sections: list[Section], version: str, today: str | None = None) -> str:
    version = version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"version must look like 1.2.3, got {version!r}")
    if any(s.title == f"v{version}" for s in sections):
        raise SystemExit(f"v{version} is already in CHANGELOG.md")
    if not sections[0].entries:
        raise SystemExit("nothing under Unreleased to release")
    cut = Section(f"v{version}", today or dt.date.today().isoformat(), sections[0].entries)
    sections[0].entries = []
    sections.insert(1, cut)
    _bump(root / "pyproject.toml", r'(?m)^(version\s*=\s*")[^"]+(")', version)
    _bump(root / "src" / "monospace" / "__init__.py", r'(?m)^(__version__\s*=\s*")[^"]+(")', version)
    return "\n".join(e.markdown() for e in cut.entries) + f"\n\n**Full changelog:** {REPO_URL}/blob/main/CHANGELOG.md\n"


def _bump(path: Path, pattern: str, version: str) -> None:
    text, n = re.subn(pattern, rf"\g<1>{version}\g<2>", path.read_text(), count=1)
    if n != 1:
        raise SystemExit(f"{path}: version line not found")
    path.write_text(text)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    p = argparse.ArgumentParser(prog="release_notes.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect").add_argument("range", nargs="?")
    sub.add_parser("render").add_argument("--check", action="store_true", help="exit 1 if files are stale")
    r = sub.add_parser("release")
    r.add_argument("version")
    r.add_argument("--notes-file", type=Path, help="also write the release notes here")
    args = p.parse_args(argv)
    sections = load(root)
    if args.cmd == "collect":
        added = collect(sections, git_commits(root, args.range))
        print(f"added {added} entr{'y' if added == 1 else 'ies'} to Unreleased", file=sys.stderr)
    elif args.cmd == "release":
        notes = release(root, sections, args.version)
        if args.notes_file:
            args.notes_file.write_text(notes)
        print(notes)
    stale = write(render(root, sections), check=getattr(args, "check", False))
    if getattr(args, "check", False) and stale:
        print("stale: " + ", ".join(str(s.relative_to(root)) for s in stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
