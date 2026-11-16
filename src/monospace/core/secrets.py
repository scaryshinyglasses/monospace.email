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


def keyring_usable() -> bool:
    try:
        import keyring
        from keyring.backends import fail, null

        backend = keyring.get_keyring()
        if isinstance(backend, (fail.Keyring, null.Keyring)):
            return False
        # chainer with zero usable backends is also a failure
        return getattr(backend, "priority", 1) > 0
    except Exception:
        return False


_store: SecretStore | None = None
_backend_name = ""


def backend_name() -> str:
    return _backend_name or ("keyring" if keyring_usable() else "file")


def get_store(prompt: Callable[[str], str] | None = None) -> SecretStore:
    global _store, _backend_name
    if _store is not None:
        return _store
    choice = os.environ.get("MONOSPACE_SECRETS", "auto")
    if choice == "memory":
        _store, _backend_name = MemoryStore(), "memory"
    elif choice == "keyring" or (choice == "auto" and keyring_usable()):
        _store, _backend_name = KeyringStore(), "keyring"
    else:
        phrase = os.environ.get("MONOSPACE_PASSPHRASE", "")
        if not phrase and prompt is not None:
            phrase = prompt("Passphrase for monospace secrets file: ")
        if not phrase:
            raise PassphraseError(
                "no OS keyring available; set MONOSPACE_PASSPHRASE to unlock the encrypted secrets file"
            )
        _store, _backend_name = EncryptedFileStore(paths.secrets_file(), phrase), "file"
    return _store


def set_store(store: SecretStore | None, name: str = "custom") -> None:
    """Inject a store (tests) or reset with ``None``."""
    global _store, _backend_name
    _store, _backend_name = store, (name if store else "")


def key(account: str, kind: str) -> str:
    return f"{account}:{kind}"
