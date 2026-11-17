import pytest

from monospace.core import config
from monospace.core.secrets_file import EncryptedFileStore, PassphraseError


def test_config_roundtrip(tmp_path):
    cfg = config.Config(path=tmp_path / "c.toml")
    acct = config.account_from_preset("g", "me@gmail.com", "gmail", use_oauth=True, display_name='Me "Q"')
    cfg.upsert(acct)
    cfg.oauth.google_client_id = "abc.apps.googleusercontent.com"
    config.save(cfg)
    back = config.load(tmp_path / "c.toml")
    assert back.accounts[0] == acct
    assert back.accounts[0].auth == "gmail_oauth"
    assert back.accounts[0].folder("drafts") == "[Gmail]/Drafts"
    assert back.oauth.google_client_id == "abc.apps.googleusercontent.com"


def test_preset_without_oauth_uses_password():
    acct = config.account_from_preset("o", "me@outlook.com", "outlook", use_oauth=False)
    assert acct.auth == "password" and acct.smtp_tls == "starttls"


def test_bad_policy_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[[accounts]]\nname="a"\nemail="a@b"\nai_send="yolo"\n')
    with pytest.raises(ValueError):
        config.load(p)


def test_encrypted_store(tmp_path):
    path = tmp_path / "s.enc"
    s = EncryptedFileStore(path, "hunter2")
    s.set("a:password", "secret")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert b"secret" not in path.read_bytes()
    assert EncryptedFileStore(path, "hunter2").get("a:password") == "secret"
    with pytest.raises(PassphraseError):
        EncryptedFileStore(path, "wrong").get("a:password")


def test_locked_keyring_falls_back_to_file(monkeypatch, tmp_path):
    """A keyring that exists but can't be read (locked GNOME Keyring over SSH) must not be chosen."""
    import keyring
    from keyring.backend import KeyringBackend
    from keyring.errors import KeyringLocked

    from monospace.core import secrets

    class Locked(KeyringBackend):
        priority = 5

        def get_password(self, service, username):
            raise KeyringLocked("Failed to unlock the collection!")

        def set_password(self, service, username, password):
            raise KeyringLocked("Failed to unlock the collection!")

        def delete_password(self, service, username):
            raise KeyringLocked("Failed to unlock the collection!")

    monkeypatch.setattr(keyring, "get_keyring", lambda: Locked())
    monkeypatch.setenv("MONOSPACE_SECRETS", "auto")
    monkeypatch.setenv("MONOSPACE_PASSPHRASE", "pw")
    secrets.set_store(None)
    store = secrets.get_store()
    assert secrets.backend_name() == "file"
    assert "KeyringLocked" in secrets.fallback_reason()
    store.set("a:password", "x")
    assert store.get("a:password") == "x"


def test_osc52_wraps_for_tmux(monkeypatch):
    from monospace.core import clipboard

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("STY", raising=False)
    assert clipboard.osc52("hi") == "\x1b]52;c;aGk=\a"
    monkeypatch.setenv("TMUX", "/tmp/tmux-1/default,1,0")
    assert clipboard.osc52("hi") == "\x1bPtmux;\x1b\x1b]52;c;aGk=\a\x1b\\"


def test_no_native_clipboard_when_headless(monkeypatch):
    from monospace.core import clipboard, platform

    monkeypatch.setattr(platform, "has_display", lambda: False)
    assert clipboard.native_paste() is None
    assert clipboard.native_copy("x") is False


def test_passphrase_remembered_once_and_reused(monkeypatch):
    import keyring
    from keyring.backends import fail

    from monospace.core import paths, secrets

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    monkeypatch.setenv("MONOSPACE_SECRETS", "auto")
    monkeypatch.delenv("MONOSPACE_PASSPHRASE", raising=False)
    asked: list[str] = []

    def prompt(label):
        asked.append(label)
        return "pw"

    secrets.set_store(None)
    secrets.get_store(prompt, lambda label: True).set("a:password", "x")
    assert (paths.passphrase_file().stat().st_mode & 0o777) == 0o600
    secrets.set_store(None)
    assert secrets.get_store(prompt, lambda label: True).get("a:password") == "x"
    assert len(asked) == 1  # second start: no prompt

    # a stale remembered passphrase is dropped and the user is asked again
    paths.passphrase_file().write_text("wrong")
    secrets.set_store(None)
    assert secrets.get_store(prompt, lambda label: False).get("a:password") == "x"
    assert len(asked) == 2 and not paths.passphrase_file().exists()
