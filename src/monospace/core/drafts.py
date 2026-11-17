"""Read, edit, delete and send messages in an account's Drafts folder (mixed into ``Service``).

IMAP can't modify a message in place, so an edit appends the new version (same Message-ID) and
expunges the old one. Existing attachments and inline images are written to a private snapshot dir
so they survive the rebuild; only *new* files an agent adds pass the agent-attachment guard.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from monospace.core import compose, mime, paths, platform, policy
from monospace.core import sync as syncmod
from monospace.core.compose import Draft
from monospace.core.config import Account
from monospace.core.store import Envelope

if TYPE_CHECKING:
    from monospace.core.service import SendResult

_KEEP = object()


def _strip_signature(text: str, signature: str) -> str:
    sig = signature.strip()
    if sig and "\n-- \n" in text:
        head, _sep, tail = text.rpartition("\n-- \n")
        if tail.strip() == sig:
            return head.rstrip()
    return text


def _snapshot_dir() -> Path:
    path = paths.data_dir() / "draft-files" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def _write(directory: Path, part: mime.Part) -> str:
    target = platform.unique_path(directory, Path(part.filename).name or "part")
    target.write_bytes(part.data)
    return str(target)


class DraftsMixin:
    # Provided by Service.
    def account(self, name: str | None = None) -> Account: ...
    def envelope(self, msg_id: int) -> Envelope: ...
    def read(self, msg_id: int, *, mark_read: bool = False) -> tuple[Envelope, mime.ParsedMessage]: ...

    def materialize(self, parts: list[mime.Part]) -> list[str]:
        """Write message parts to a private snapshot dir; returns their paths."""
        snap = _snapshot_dir()
        return [_write(snap, p) for p in parts]

    def _draft_env(self, msg_id: int) -> tuple[Envelope, Account]:
        from monospace.core.service import ServiceError

        env = self.envelope(msg_id)  # type: ignore[attr-defined]
        acct = self.account(env.account)
        if env.folder != acct.folder("drafts"):
            raise ServiceError(f"message {msg_id} is in {env.folder!r}, not the Drafts folder "
                               f"({acct.folder('drafts')!r}); only drafts can be edited, deleted or sent")
        return env, acct

    def load_draft(self, msg_id: int) -> tuple[Draft, mime.ParsedMessage]:
        """The stored draft as an editable ``Draft`` (attachments/inline images materialized to files)."""
        self._draft_env(msg_id)
        cleanup_snapshots()
        _env, pm = self.read(msg_id)
        snap = _snapshot_dir() if (pm.attachments or pm.inline) else None
        draft = Draft(
            to=pm.to, cc=pm.cc, bcc=pm.bcc, subject=pm.subject, body=pm.text, html_body=pm.html,
            in_reply_to=pm.in_reply_to, references=pm.references, message_id=pm.message_id,
            signature=False,  # the stored text/HTML already contain it
            attachments=[_write(snap, p) for p in pm.attachments] if snap else [],
            inline_images={cid: _write(snap, p) for cid, p in pm.inline.items()} if snap else {},
        )
        return draft, pm

    def get_draft(self, msg_id: int) -> dict:
        env, acct = self._draft_env(msg_id)
        _env, pm = self.read(msg_id)
        return {
            "id": env.id, "account": acct.name, "to": pm.to, "cc": pm.cc, "bcc": pm.bcc,
            "subject": pm.subject, "body": _strip_signature(pm.text, acct.signature), "html": pm.html,
            "html_matches_body": not (pm.html and compose.html_extra_words(pm.html, pm.text)),
            "attachments": [{"filename": p.filename, "content_type": p.content_type, "size": p.size}
                            for p in pm.attachments],
            "inline_images": [{"cid": cid, "filename": p.filename, "content_type": p.content_type,
                               "size": p.size} for cid, p in pm.inline.items()],
        }

    def update_draft(self, msg_id: int, *, source: policy.Source = "human", to: list[str] | None = None,
                     cc: list[str] | None = None, bcc: list[str] | None = None, subject: str | None = None,
                     body: str | None = None, html_body: str | None = None,
                     add_attachments: list[str] | None = None, remove_attachments: list[str] | None = None,
                     add_inline_images: list | None = None,
                     remove_inline_images: list[str] | None = None) -> "SendResult":
        """Replace a draft. Omitted fields are kept. Giving ``body`` without ``html_body`` re-renders
        the HTML from the markdown (inline images that it doesn't reference are appended at the end)."""
        from monospace.core.service import ServiceError

        env, acct = self._draft_env(msg_id)
        new_files = list(add_attachments or [])
        try:
            specs = compose.inline_specs(add_inline_images)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        new_inline = [p for p, _ in specs]
        if source == "ai":  # guard agent-supplied files before touching anything
            allowed = policy.agent_attachment_dir(self.cfg.settings.agent_attachment_dir)  # type: ignore[attr-defined]
            new_files = policy.check_agent_attachments(new_files, allowed) if new_files else []
            new_inline = policy.check_agent_attachments(new_inline, allowed) if new_inline else []
        draft, pm = self.load_draft(msg_id)
        draft.body = _strip_signature(pm.text, acct.signature)
        draft.signature = True  # re-added below unless we keep the stored HTML verbatim
        for name, value in (("to", to), ("cc", cc), ("bcc", bcc)):
            if value is not None:
                setattr(draft, name, compose.split_addrs(value))
        if subject is not None:
            draft.subject = subject
        if body is not None or html_body is not None:
            draft.body = body if body is not None else ""
            draft.html_body = html_body or ""
        elif pm.html:  # neither changed: keep the stored HTML (it already has the signature)
            draft.body, draft.signature = pm.text, False
        drop = {n.lower() for n in remove_attachments or []}
        draft.attachments = [p for p in draft.attachments if Path(p).name.lower() not in drop] + new_files
        drop_img = {n.lower() for n in remove_inline_images or []}
        draft.inline_images = {cid: p for cid, p in draft.inline_images.items()
                               if Path(p).name.lower() not in drop_img and cid.lower() not in drop_img}
        # New images the HTML doesn't reference are placed by compose (top/bottom), even in kept HTML.
        for path, (_orig, spec) in zip(new_inline, specs):
            try:
                compose.add_inline(draft, path, spec)
            except ValueError as exc:
                raise ServiceError(f"draft {msg_id}: {exc}; remove it first (remove_inline_images)") from exc
        # removed and not re-added: drop its <img>
        draft.html_body = compose.drop_inline_refs(draft.html_body, set(pm.inline) - set(draft.inline_images))
        msg =self._build(acct, draft, for_draft=True)  # type: ignore[attr-defined]
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            sess.append(env.folder, msg.as_bytes(), ["\\Draft", "\\Seen"])
            sess.select(env.folder)
            sess.delete([env.uid])
        self.store.delete_uids(env.account, env.folder, [env.uid])  # type: ignore[attr-defined]
        return self._drafted(acct, msg["Message-ID"], f"updated draft in {env.folder}")

    def replace_draft(self, msg_id: int, draft: Draft, source: policy.Source = "human") -> "SendResult":
        """Swap a stored draft for a fully edited one (the TUI/CLI editor path)."""
        env, acct = self._draft_env(msg_id)
        if source == "ai":
            self._guard_ai(draft)  # type: ignore[attr-defined]
        msg = self._build(acct, draft, for_draft=True)  # type: ignore[attr-defined]
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            sess.append(env.folder, msg.as_bytes(), ["\\Draft", "\\Seen"])
            sess.select(env.folder)
            sess.delete([env.uid])
        self.store.delete_uids(env.account, env.folder, [env.uid])  # type: ignore[attr-defined]
        return self._drafted(acct, msg["Message-ID"], f"updated draft in {env.folder}")

    def delete_draft(self, msg_id: int) -> None:
        env, acct = self._draft_env(msg_id)
        with self.session(acct) as sess:  # type: ignore[attr-defined]
            sess.select(env.folder)
            sess.delete([env.uid])
        self.store.delete_uids(env.account, env.folder, [env.uid])  # type: ignore[attr-defined]
        self.store.recount(env.account, env.folder)  # type: ignore[attr-defined]

    def send_draft(self, msg_id: int, source: policy.Source = "human") -> "SendResult":
        """Send a stored draft (AI callers obey ``ai_send``). The draft is removed once actually sent."""
        from monospace.core.service import SendResult

        env, acct = self._draft_env(msg_id)
        if source == "ai" and policy.decide(acct.ai_send, source) == "draft":
            return SendResult("drafted", acct.name, f"left in {env.folder}: account policy ai_send=draft, "
                              "so a human must send it", draft_id=env.id)
        draft, _pm = self.load_draft(msg_id)
        draft.message_id = ""  # the sent copy gets its own Message-ID
        res = self.submit(acct.name, draft, source, trusted=True)  # type: ignore[attr-defined]
        if res.status == "sent":
            self.delete_draft(msg_id)
        elif res.status == "queued":
            res.detail += f"; draft {env.id} kept until then"
        return res

    def _drafted(self, acct: Account, message_id: str, detail: str) -> "SendResult":
        """Pull the Drafts folder so the caller gets the new draft's local id."""
        from monospace.core.service import SendResult

        folder = acct.folder("drafts")
        draft_id = None
        try:
            with self.session(acct) as sess:  # type: ignore[attr-defined]
                syncmod.sync_folder(sess, self.store, acct.name, folder, prefetch=0)  # type: ignore[attr-defined]
            found = self.store.by_message_id(acct.name, folder, message_id)  # type: ignore[attr-defined]
            draft_id = found.id if found else None
        except Exception:  # the draft is saved; an id is a convenience
            pass
        return SendResult("drafted", acct.name, detail, draft_id=draft_id)


def cleanup_snapshots(max_age_days: int = 7) -> None:
    """Drop old materialized draft parts (they're only needed while an edit/send runs)."""
    import time

    root = paths.data_dir() / "draft-files"
    if not root.is_dir():
        return
    cutoff = time.time() - max_age_days * 86400
    for d in root.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
