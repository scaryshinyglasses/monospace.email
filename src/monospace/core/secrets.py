"""Secret storage: OS keyring when usable, else an encrypted file (headless Linux).

Force a backend with ``MONOSPACE_SECRETS=keyring|file``. The file backend reads its
passphrase from ``MONOSPACE_PASSPHRASE`` or, if a prompt callback is supplied, asks once.
"""

from __future__ import annotations

import os
from typing import Callable, Protocol

from monospace.core import paths
from monospace.core.secrets_file import EncryptedFileStore, PassphraseError

SERVICE = "monospace.email"


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringStore:
    def __init__(self) -> None:
        import keyring

        self._kr = keyring

    def get(self, key: str) -> str | None:
        return self._kr.get_password(SERVICE, key)

    def set(self, key: str, value: str) -> None:
        self._kr.set_password(SERVICE, key, value)

    def delete(self, key: str) -> None:
        try:
            self._kr.delete_password(SERVICE, key)
        except Exception:  # keyring raises PasswordDeleteError for missing keys
            pass


class MemoryStore:
    """Used by tests."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


_fallback_reason = ""


def keyring_usable() -> bool:
    """True only if the keyring can actually be read right now.

    A backend can exist but be unusable: on a headless Linux box GNOME Keyring is often
    reachable over D-Bus yet locked, with no way to show the unlock prompt
    (``KeyringLocked: Failed to unlock the collection!``). Probe with a real read.
    """
    global _fallback_reason
    try:
        import keyring
        from keyring.backends import fail, null

        backend = keyring.get_keyring()
        # chainer with zero usable backends is also a failure
        if isinstance(backend, (fail.Keyring, null.Keyring)) or getattr(backend, "priority", 1) <= 0:
            _fallback_reason = "no OS keyring backend"
            return False
        backend.get_password(SERVICE, "__probe__")
        return True
    except Exception as exc:
        _fallback_reason = f"OS keyring unusable ({type(exc).__name__}: {exc})"
        return False


def fallback_reason() -> str:
    """Why the encrypted file store was chosen over the keyring ("" if it wasn't)."""
    return _fallback_reason if _backend_name == "file" else ""


_store: SecretStore | None = None
_backend_name = ""


def backend_name() -> str:
    return _backend_name or ("keyring" if keyring_usable() else "file")


def remembered_passphrase() -> str:
    path = paths.passphrase_file()
    try:
        if path.stat().st_mode & 0o077:  # group/world-readable: refuse rather than trust it
            return ""
        return path.read_text().strip()
    except OSError:
        return ""


def remember_passphrase(phrase: str) -> Path:
    paths.ensure_dirs()
    path = paths.passphrase_file()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(phrase)
    os.chmod(path, 0o600)
    return path


def forget_passphrase() -> bool:
    try:
        paths.passphrase_file().unlink()
        return True
    except FileNotFoundError:
        return False


def get_store(prompt: Callable[[str], str] | None = None,
              confirm: Callable[[str], bool] | None = None) -> SecretStore:
    global _store, _backend_name
    if _store is not None:
        return _store
    choice = os.environ.get("MONOSPACE_SECRETS", "auto")
    if choice == "memory":
        _store, _backend_name = MemoryStore(), "memory"
    elif choice == "keyring" or (choice == "auto" and keyring_usable()):
        _store, _backend_name = KeyringStore(), "keyring"
    else:
        _backend_name = "file"
        phrase = os.environ.get("MONOSPACE_PASSPHRASE", "") or remembered_passphrase()
        asked = False
        if not phrase and prompt is not None:
            import sys

            if _fallback_reason and not paths.secrets_file().exists():  # explain once, on first use
                print(f"monospace: {_fallback_reason}; using an encrypted secrets file instead.", file=sys.stderr)
            phrase, asked = prompt("Passphrase for monospace secrets file: "), True
        if not phrase:
            why = _fallback_reason or "no OS keyring available"
            raise PassphraseError(
                f"{why}; set MONOSPACE_PASSPHRASE to unlock the encrypted secrets file"
            )
        store = EncryptedFileStore(paths.secrets_file(), phrase)
        try:
            store.get("__probe__")  # raises PassphraseError on a wrong passphrase, before remembering it
        except PassphraseError:
            if asked or prompt is None or os.environ.get("MONOSPACE_PASSPHRASE"):
                raise
            forget_passphrase()  # the remembered one no longer matches: drop it and ask
            return get_store(prompt, confirm)
        if asked and confirm is not None and confirm(
                f"Remember it on this machine (saved to {paths.passphrase_file()}, readable only by you)? [y/N] "):
            remember_passphrase(phrase)
        _store, _backend_name = store, "file"
    return _store


def set_store(store: SecretStore | None, name: str = "custom") -> None:
    """Inject a store (tests) or reset with ``None``."""
    global _store, _backend_name
    _store, _backend_name = store, (name if store else "")


def key(account: str, kind: str) -> str:
    return f"{account}:{kind}"
