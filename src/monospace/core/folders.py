"""Create, rename and delete IMAP folders (mixed into ``Service``)."""

from __future__ import annotations

from monospace.core import sync as syncmod

ROLES = ("drafts", "sent", "trash", "archive", "junk")


class FoldersMixin:
    def _refresh_folders(self, acct, sess) -> list[str]:  # type: ignore[no-untyped-def]
        return syncmod.sync_folder_list(sess, self.store, acct.name)  # type: ignore[attr-defined]

    def create_folder(self, account: str | None, name: str) -> list[dict]:
        from monospace.core.service import ServiceError

        acct = self.account(account)  # type: ignore[attr-defined]
        if not name.strip():
            raise ServiceError("folder name is empty")
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            if name in [f.name for f in sess.folders()]:
                raise ServiceError(f"folder {name!r} already exists")
            sess.create_folder(name)
            self._refresh_folders(acct, sess)
        return self.folders(acct.name)  # type: ignore[attr-defined]

    def _guard_user_folder(self, acct, name: str) -> None:  # type: ignore[no-untyped-def]
        from monospace.core.service import ServiceError

        special = {acct.folder(r) for r in ROLES} | {"INBOX"}
        if name.upper() == "INBOX" or name in special:
            raise ServiceError(f"{name!r} is a system folder (inbox/drafts/sent/trash/archive/junk)")

    def rename_folder(self, account: str | None, old: str, new: str) -> list[dict]:
        from monospace.core.service import ServiceError

        acct = self.account(account)  # type: ignore[attr-defined]
        self._guard_user_folder(acct, old)
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            names = [f.name for f in sess.folders()]
            if old not in names:
                raise ServiceError(f"no folder {old!r}")
            if new in names:
                raise ServiceError(f"folder {new!r} already exists")
            sess.rename_folder(old, new)
            self._refresh_folders(acct, sess)
        return self.folders(acct.name)  # type: ignore[attr-defined]

    def delete_folder(self, account: str | None, name: str) -> list[dict]:
        """Delete an EMPTY, non-system folder (move or trash its mail first)."""
        from monospace.core.service import ServiceError

        acct = self.account(account)  # type: ignore[attr-defined]
        self._guard_user_folder(acct, name)
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            if name not in [f.name for f in sess.folders()]:
                raise ServiceError(f"no folder {name!r}")
            _v, _n, exists = sess.select(name, readonly=True)
            if exists:
                raise ServiceError(f"folder {name!r} has {exists} message(s); move them out first")
            sess.select("INBOX", readonly=True)  # some servers refuse to delete the selected folder
            sess.delete_folder(name)
            self._refresh_folders(acct, sess)
        return self.folders(acct.name)  # type: ignore[attr-defined]
