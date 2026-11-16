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
