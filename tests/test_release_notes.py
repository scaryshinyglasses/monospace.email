"""scripts/release_notes.py: collect / render / release, plus `monospace update` showing notes."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from monospace.core import update

_SPEC = importlib.util.spec_from_file_location(
    "release_notes", Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py")
rn = importlib.util.module_from_spec(_SPEC)
sys.modules["release_notes"] = rn  # dataclasses look the module up
_SPEC.loader.exec_module(rn)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "website").mkdir()
    (tmp_path / "src" / "monospace").mkdir(parents=True)
    (tmp_path / "README.md").write_text(f"# x\n\n{rn.START}\n{rn.END}\n\n## Install\n")
    (tmp_path / "website" / "index.html").write_text(f"<main>\n{rn.START}\n{rn.END}\n</main>\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "monospace"\nversion = "0.1.0"\n')
    (tmp_path / "src" / "monospace" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    return tmp_path


def _commits(*subjects, author="Ana"):
    return [(f"{i:040x}", author, s) for i, s in enumerate(subjects, start=1)]


def test_collect_dedupes_and_skips_noise():
    sections = rn.parse("")
    commits = _commits("Add tags", "Merge branch 'x'", "chore: bump", "Fix it [skip changelog]")
    commits += [("f" * 40, "github-actions[bot]", "Update release notes [skip ci]")]
    assert rn.collect(sections, commits) == 1
    assert rn.collect(sections, commits) == 0  # same shas again → nothing new
    assert rn.collect(sections, _commits("Newer") [:1] + [("e" * 40, "Ana", "Newest")]) == 1
    assert [e.text for e in sections[0].entries] == ["Newest", "Add tags"]


def test_changelog_round_trip_keeps_manual_entries():
    text = rn.dump([rn.Section("Unreleased", "", [rn.Entry("Hand-written note"), rn.Entry("Add x", "abc1234")]),
                    rn.Section("v0.1.0", "2026-11-17", [rn.Entry("First")])])
    again = rn.parse(text)
    assert [(s.title, s.date, [(e.text, e.sha) for e in s.entries]) for s in again] == [
        ("Unreleased", "", [("Hand-written note", ""), ("Add x", "abc1234")]),
        ("v0.1.0", "2026-11-17", [("First", "")])]
    assert rn.dump(again) == text


def test_render_is_escaped_idempotent_and_checkable(repo):
    sections = rn.parse("")
    rn.collect(sections, _commits('Support <script>alert("x")</script> & co'))
    assert rn.write(rn.render(repo, sections))
    html_out = (repo / "website" / "index.html").read_text()
    assert "<script>" not in html_out and "&lt;script&gt;" in html_out
    assert "What's new" in (repo / "README.md").read_text()
    data = json.loads((repo / "website" / "changelog.json").read_text())
    assert data["sections"][0]["entries"][0]["text"].startswith("Support <script>")
    assert rn.write(rn.render(repo, rn.load(repo)), check=True) == []  # second render: nothing stale
    assert rn.main(["render", "--check"], root=repo) == 0


def test_render_limits_to_latest_entries(repo):
    sections = [rn.Section("Unreleased", "", [rn.Entry(f"u{i}") for i in range(3)]),
                rn.Section("v0.1.0", "2026-11-17", [rn.Entry(f"r{i}") for i in range(10)])]
    shown = rn.latest(sections)
    assert [s.title for s in shown] == ["Unreleased", "v0.1.0"]
    assert sum(len(s.entries) for s in shown) == rn.LATEST


def test_release_cuts_section_and_bumps_version(repo):
    sections = rn.parse("")
    rn.collect(sections, _commits("Add tags"))
    notes = rn.release(repo, sections, "v0.2.0", today="2026-11-19")
    assert "Add tags" in notes
    assert [(s.title, s.date, len(s.entries)) for s in sections] == [("Unreleased", "", 0),
                                                                     ("v0.2.0", "2026-11-19", 1)]
    assert 'version = "0.2.0"' in (repo / "pyproject.toml").read_text()
    assert '__version__ = "0.2.0"' in (repo / "src" / "monospace" / "__init__.py").read_text()
    with pytest.raises(SystemExit):
        rn.release(repo, sections, "0.3.0")  # nothing unreleased
    with pytest.raises(SystemExit):
        rn.release(repo, sections, "latest")


def test_repo_blocks_are_current():
    root = Path(__file__).resolve().parents[1]
    assert rn.write(rn.render(root, rn.load(root)), check=True) == []


def test_update_notes_for_tags_and_branches(monkeypatch):
    def fake_get(url, accept):
        if "/releases?" in url:
            return json.dumps([{"tag_name": "v0.3.0", "body": "- Tags\x1b[31m!\n\n- Faster sync"},
                               {"tag_name": "v0.2.0", "body": "- Older"}])
        return json.dumps({"commits": [{"commit": {"message": "Old one\n\nbody"}},
                                       {"commit": {"message": "New one"}}]})

    monkeypatch.setattr(update, "_get", fake_get)
    assert update.notes(update.Installed("v0.2.0"), "v0.3.0") == ["v0.3.0", "  - Tags[31m!", "  - Faster sync"]
    assert update.notes(update.Installed("main", "a" * 40), "b" * 40) == ["- New one", "- Old one"]
    monkeypatch.setattr(update, "_get", lambda u, a: (_ for _ in ()).throw(update.UpdateError("offline")))
    assert update.notes(update.Installed("main", "a" * 40), "b" * 40) == []
