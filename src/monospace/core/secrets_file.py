"""Encrypted secret store for machines without a keyring (headless Linux, containers).

Format: JSON ``{"v":1,"salt":b64,"data":fernet-token}``; key = scrypt(passphrase, salt).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class PassphraseError(Exception):
    pass


def _derive(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


class EncryptedFileStore:
    def __init__(self, path: Path, passphrase: str) -> None:
        if not passphrase:
            raise PassphraseError("empty passphrase")
        self.path = path
        self._passphrase = passphrase
        self._salt: bytes | None = None
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._salt = os.urandom(16)
            self._cache = {}
            return self._cache
        blob = json.loads(self.path.read_text())
        self._salt = base64.b64decode(blob["salt"])
        try:
            plain = Fernet(_derive(self._passphrase, self._salt)).decrypt(blob["data"].encode())
        except InvalidToken as exc:
            raise PassphraseError("wrong passphrase for secrets file") from exc
        self._cache = json.loads(plain)
        return self._cache

    def _flush(self) -> None:
        assert self._cache is not None and self._salt is not None
        token = Fernet(_derive(self._passphrase, self._salt)).encrypt(json.dumps(self._cache).encode())
        blob = {"v": 1, "salt": base64.b64encode(self._salt).decode(), "data": token.decode()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(blob))
        os.replace(tmp, self.path)

    def get(self, key: str) -> str | None:
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        self._load()[key] = value
        self._flush()

    def delete(self, key: str) -> None:
        if self._load().pop(key, None) is not None:
            self._flush()
