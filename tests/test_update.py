import argparse

import pytest

from monospace.cli import update as cli_update
from monospace.core import config, update


@pytest.fixture
def calls(monkeypatch):
    seen: list[str] = []

    def fake_get(url, accept):
        seen.append(url)
        if url.endswith("/releases/latest"):
            return '{"tag_name": "v0.3.0"}'
        return "b" * 40

    monkeypatch.setattr(update, "_get", fake_get)
    return seen


def test_no_record_means_no_check(calls):
    assert update.check() is None
    assert calls == []  # dev/editable installs never hit the network


def test_branch_update_detected_and_cached_for_a_day(calls):
    update.record("main", "a" * 40)
    assert update.check(now=1000) == "b" * 40
    assert update.check(now=1000 + 3600) == "b" * 40
    assert len(calls) == 1
    update.check(now=1000 + update.CHECK_EVERY + 1)
    assert len(calls) == 2


def test_up_to_date_and_tag_installs(calls):
    update.record("main", "b" * 40)
    assert update.check(force=True) is None
    update.record("v0.3.0", "")
    assert update.check(force=True) is None
    update.record("v0.2.0", "")
    assert update.check(force=True) == "v0.3.0"
    assert update.short("v0.3.0") == "v0.3.0" and update.short("b" * 40) == "bbbbbbb"


def test_network_failure_is_silent(monkeypatch):
    def boom(url, accept):
        raise update.UpdateError("offline")

    monkeypatch.setattr(update, "_get", boom)
    update.record("main", "a" * 40)
    assert update.check(force=True) is None


def test_update_command_installs_and_records(calls, monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(update, "find_uv", lambda: "/bin/uv")
    monkeypatch.setattr(cli_update.subprocess, "run",
                        lambda cmd: ran.append(cmd) or argparse.Namespace(returncode=0))
    update.record("main", "a" * 40)
    assert cli_update.run(argparse.Namespace(check=True, ref=None)) == 0
    assert ran == []  # --check never installs
    assert cli_update.run(argparse.Namespace(check=False, ref=None)) == 0
    assert ran[0][:4] == ["/bin/uv", "tool", "install", "--force"]
    assert ran[0][-1].endswith(f"/archive/{'b' * 40}.tar.gz")
    assert update.installed() == update.Installed("main", "b" * 40)
    assert cli_update.run(argparse.Namespace(check=False, ref=None)) == 0
    assert len(ran) == 1  # already up to date


def test_check_updates_setting_round_trips(tmp_path):
    cfg = config.Config(path=tmp_path / "c.toml")
    cfg.settings.check_updates = False
    config.save(cfg)
    assert config.load(tmp_path / "c.toml").settings.check_updates is False
