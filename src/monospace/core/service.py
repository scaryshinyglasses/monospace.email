"""The one façade the TUI, CLI and MCP server share. Blocking — call from worker threads."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

from monospace.core import compose, config, mime, paths, platform, policy, smtp
from monospace.core import sync as syncmod
from monospace.core.auth import tokens
from monospace.core.compose import Draft
from monospace.core.config import Account, Config
from monospace.core.drafts import DraftsMixin
from monospace.core.folders import FoldersMixin
from monospace.core.imap import ImapSession
from monospace.core.store import Envelope, Store

SessionFactory = Callable[[Account, tokens.Credential], ImapSession]
Sender = Callable[[Account, "tokens.Credential | None", EmailMessage, list[str]], None]


class ServiceError(Exception):
    pass


@dataclass
class SendResult:
    status: str  # sent | drafted | queued
    account: str
    detail: str = ""
    outbox_id: int | None = None
    draft_id: int | None = None  # local id of the saved draft (status "drafted")

    def to_dict(self) -> dict:
        return {"status": self.status, "account": self.account, "detail": self.detail,
                "outbox_id": self.outbox_id, "draft_id": self.draft_id}


def _default_sender(acct: Account, cred: tokens.Credential | None, msg: EmailMessage, rcpts: list[str]) -> None:
    asyncio.run(smtp.send(acct, cred, msg, rcpts))


class Service(DraftsMixin, FoldersMixin):
    def __init__(self, cfg: Config | None = None, store: Store | None = None, *,
                 session_factory: SessionFactory = ImapSession, sender: Sender = _default_sender,
                 credentials: Callable[[Account], tokens.Credential] | None = None) -> None:
        self.cfg = cfg or config.load()
        self.store = store or Store(paths.cache_db())
        self._session_factory = session_factory
        self._sender = sender
        self._credentials = credentials or (lambda a: tokens.resolve(a, self.cfg.oauth))

    # ---- helpers -------------------------------------------------------
    def account(self, name: str | None = None) -> Account:
        try:
            return self.cfg.get(name)
        except LookupError as exc:
            raise ServiceError(str(exc)) from exc

    def session(self, acct: Account) -> ImapSession:
        return self._session_factory(acct, self._credentials(acct))

    def envelope(self, msg_id: int) -> Envelope:
        env = self.store.get(msg_id)
        if env is None:
            raise ServiceError(f"no message with id {msg_id} (run `monospace sync`?)")
        return env

    def resolve_folder(self, acct: Account, folder: str | None) -> str:
        if not folder:
            return "INBOX"
        if folder.lower() in ("drafts", "sent", "trash", "archive", "junk"):
            return acct.folder(folder.lower())
        return folder

    # ---- reading -------------------------------------------------------
    def accounts(self) -> list[dict]:
        return [{"name": a.name, "email": a.email, "provider": a.provider, "auth": a.auth,
                 "ai_send": a.ai_send} for a in self.cfg.accounts]

    def sync(self, account: str | None = None, folders: list[str] | None = None,
             all_folders: bool = False) -> syncmod.SyncStats:
        stats = syncmod.SyncStats()
        targets = [self.account(account)] if account else list(self.cfg.accounts)
        for acct in targets:
            try:
                with self.session(acct) as sess:
                    names = syncmod.sync_folder_list(sess, self.store, acct.name)
                    wanted = names if all_folders else [self.resolve_folder(acct, f) for f in (folders or ["INBOX"])]
                    for name in wanted:
                        if name not in names:
                            stats.errors.append(f"{acct.name}: no folder {name!r}")
                            continue
                        new, removed = syncmod.sync_folder(sess, self.store, acct.name, name)
                        stats.folders += 1
                        stats.new += new
                        stats.removed += removed
            except Exception as exc:  # report per account, keep going
                stats.errors.append(f"{acct.name}: {exc}")
        return stats

    def folders(self, account: str | None = None) -> list[dict]:
        acct = self.account(account)
        return [f.to_dict() for f in self.store.folders(acct.name)]

    def list(self, account: str | None = None, folder: str | None = None, *, limit: int = 50,
             offset: int = 0, unread_only: bool = False) -> list[Envelope]:
        acct = self.account(account)
        return self.store.list(acct.name, self.resolve_folder(acct, folder), limit=limit, offset=offset,
                               unread_only=unread_only)

    def search(self, query: str, account: str | None = None, limit: int = 50) -> list[Envelope]:
        acct_name = self.account(account).name if account else None
        return self.store.search(query, account=acct_name, limit=limit)

    def raw(self, msg_id: int) -> bytes:
        cached = self.store.body(msg_id)
        if cached is not None:
            return cached
        env = self.envelope(msg_id)
        with self.session(self.account(env.account)) as sess:
            sess.select(env.folder, readonly=True)
            raw = sess.body(env.uid)
        syncmod.cache_body(self.store, msg_id, raw)
        return raw

    def read(self, msg_id: int, *, mark_read: bool = False) -> tuple[Envelope, mime.ParsedMessage]:
        env = self.envelope(msg_id)
        pm = mime.parse(self.raw(msg_id))
        if mark_read and env.unread:
            self.mark(msg_id, read=True)
            env = self.envelope(msg_id)
        return env, pm

    def attachment(self, msg_id: int, index: int) -> mime.Part:
        _env, pm = self.read(msg_id)
        if not 0 <= index < len(pm.attachments):
            raise ServiceError(f"message {msg_id} has {len(pm.attachments)} attachment(s); no index {index}")
        return pm.attachments[index]

    def save_attachment(self, msg_id: int, index: int, directory: str | None = None) -> Path:
        part = self.attachment(msg_id, index)
        target = platform.unique_path(platform.download_dir(directory or self.cfg.settings.download_dir),
                                      part.filename)
        target.write_bytes(part.data)
        return target

    # ---- mutations -----------------------------------------------------
    def mark(self, msg_id: int, *, read: bool | None = None, flagged: bool | None = None) -> Envelope:
        env = self.envelope(msg_id)
        add, remove = [], []
        for flag, want in (("\\Seen", read), ("\\Flagged", flagged)):
            if want is True:
                add.append(flag)
            elif want is False:
                remove.append(flag)
        with self.session(self.account(env.account)) as sess:
            sess.select(env.folder)
            if add:
                sess.add_flags([env.uid], add)
            if remove:
                sess.remove_flags([env.uid], remove)
        flags = (set(env.flags) | set(add)) - set(remove)
        self.store.set_flags(msg_id, sorted(flags))
        self.store.recount(env.account, env.folder)
        return self.envelope(msg_id)

    def move(self, msg_id: int, dest: str) -> str:
        env = self.envelope(msg_id)
        acct = self.account(env.account)
        target = self.resolve_folder(acct, dest)
        with self.session(acct) as sess:
            sess.select(env.folder)
            sess.move([env.uid], target)
        self.store.delete_uids(env.account, env.folder, [env.uid])
        self.store.recount(env.account, env.folder)
        return target

    def archive(self, msg_id: int) -> str:
        return self.move(msg_id, "archive")

    def trash(self, msg_id: int) -> str:
        return self.move(msg_id, "trash")

    # ---- sending -------------------------------------------------------
    def _build(self, acct: Account, draft: Draft, *, for_draft: bool = False,
               untrusted: bool = False) -> EmailMessage:
        try:
            msg = compose.build(draft, acct.from_header, signature=acct.signature, untrusted=untrusted)
        except (OSError, ValueError) as exc:
            raise ServiceError(f"attachment error: {exc}") from exc
        if for_draft and draft.bcc:
            msg["Bcc"] = ", ".join(draft.bcc)
        return msg

    def _guard_ai(self, draft: Draft) -> None:
        if draft.attachments or draft.inline_images:
            allowed = policy.agent_roots(self.cfg.settings)
            draft.attachments = policy.check_agent_attachments(draft.attachments, allowed)
            draft.inline_images = dict(zip(draft.inline_images, policy.check_agent_attachments(
                list(draft.inline_images.values()), allowed)))

    def _snapshot(self, draft: Draft) -> None:
        """Copy queued attachments so the approved message is exactly what was reviewed."""
        if not draft.attachments and not draft.inline_images:
            return
        import shutil
        import uuid

        target = paths.data_dir() / "outbox-files" / uuid.uuid4().hex
        target.mkdir(parents=True)

        def copy(src: str) -> str:
            return str(shutil.copy2(src, platform.unique_path(target, Path(src).name)))

        draft.attachments = [copy(src) for src in draft.attachments]
        draft.inline_images = {cid: copy(src) for cid, src in draft.inline_images.items()}

    def save_draft(self, account: str | None, draft: Draft, source: policy.Source = "human", *,
                   trusted: bool = False) -> SendResult:
        acct = self.account(account)
        if source == "ai" and not trusted:
            self._guard_ai(draft)
        msg = self._build(acct, draft, for_draft=True, untrusted=source == "ai")
        folder = acct.folder("drafts")
        with self.session(acct) as sess:
            sess.append(folder, msg.as_bytes(), ["\\Draft", "\\Seen"])
        return self._drafted(acct, msg["Message-ID"], f"saved to {folder}")

    def send_now(self, account: str | None, draft: Draft, *, untrusted: bool = False) -> SendResult:
        acct = self.account(account)
        rcpts = draft.recipients()
        if not rcpts:
            raise ServiceError("no recipients")
        msg = self._build(acct, draft, untrusted=untrusted)
        self._sender(acct, self._credentials(acct), msg, rcpts)
        if acct.provider == "custom":  # Gmail / Microsoft file sent mail themselves
            try:
                with self.session(acct) as sess:
                    sess.append(acct.folder("sent"), msg.as_bytes(), ["\\Seen"])
            except Exception:
                pass
        return SendResult("sent", acct.name, f"sent to {', '.join(rcpts)}")

    def submit(self, account: str | None, draft: Draft, source: policy.Source, *,
               trusted: bool = False) -> SendResult:
        """``trusted`` = attachments were materialized by us (e.g. from a stored draft), not named by an agent."""
        acct = self.account(account)
        if not draft.recipients():
            raise ServiceError("no recipients")
        if source == "ai" and not trusted:
            self._guard_ai(draft)
        if source == "ai" and draft.html_body:
            # What a human reviews (outbox, drafts) must be what recipients see: derive the text part
            # from the agent's (sanitized) HTML instead of trusting a separately supplied body.
            draft.body = compose.review(Draft(html_body=draft.html_body, signature=False))
        action = policy.decide(acct.ai_send, source)
        if action == "send":
            return self.send_now(acct.name, draft, untrusted=source == "ai")
        if action == "draft":
            res = self.save_draft(acct.name, draft, "ai" if source == "ai" else "human", trusted=True)
            res.detail += f" (account policy ai_send=draft; a human must send it)"
            return res
        self._snapshot(draft)
        item = self.store.outbox_add(acct.name, draft.to_dict(), source)
        return SendResult("queued", acct.name,
                          f"awaiting approval: `monospace outbox approve {item}` or the TUI outbox (O)", item)

    def reply(self, msg_id: int, body: str, *, reply_all: bool = False, source: policy.Source = "human",
              send: bool = True, extras: Callable[[Draft], Draft] | None = None) -> SendResult:
        env, pm = self.read(msg_id)
        acct = self.account(env.account)
        draft = compose.reply(pm, body, acct.email, reply_all=reply_all)
        if extras:
            draft = extras(draft)
        return self.submit(acct.name, draft, source) if send else self.save_draft(acct.name, draft, source)

    def forward(self, msg_id: int, body: str, to: list[str], *, source: policy.Source = "human",
                send: bool = True, extras: Callable[[Draft], Draft] | None = None,
                include_attachments: bool = False) -> SendResult:
        env, pm = self.read(msg_id)
        draft = compose.forward(pm, body, to)
        if extras:
            draft = extras(draft)
        if source == "ai":  # guard the agent's own files before adding the original's parts
            self._guard_ai(draft)
        if include_attachments and pm.attachments:
            draft.attachments += self.materialize(pm.attachments)
        if send:
            return self.submit(env.account, draft, source, trusted=True)
        return self.save_draft(env.account, draft, source, trusted=True)

    # ---- outbox --------------------------------------------------------
    def outbox(self) -> list[dict]:
        return self.store.outbox("pending")

    def outbox_approve(self, item_id: int) -> SendResult:
        item = self.store.outbox_get(item_id)
        if not item or item["status"] != "pending":
            raise ServiceError(f"no pending outbox item {item_id}")
        if item["source"] == "ai":  # only send exactly the snapshotted files that were reviewed
            snap = (paths.data_dir() / "outbox-files").resolve()
            for f in item["draft"].get("attachments", []):
                if not Path(f).resolve().is_relative_to(snap):
                    raise ServiceError(f"refusing outbox item {item_id}: attachment outside snapshot dir: {f}")
        try:
            res = self.send_now(item["account"], Draft.from_dict(item["draft"]), untrusted=item["source"] == "ai")
        except Exception as exc:
            self.store.outbox_set(item_id, "pending", str(exc))
            raise
        self.store.outbox_set(item_id, "sent")
        return res

    def outbox_review(self, item: dict) -> str:
        """What the queued message will show recipients, as plain text (sanitized for terminals)."""
        acct = self.account(item["account"])
        return compose.review(Draft.from_dict(item["draft"]), signature=acct.signature)

    def outbox_cancel(self, item_id: int) -> None:
        """Withdraw a pending item (AI callers may do this — it never sends anything)."""
        item = self.store.outbox_get(item_id)
        if not item or item["status"] != "pending":
            raise ServiceError(f"no pending outbox item {item_id}")
        self.store.outbox_set(item_id, "cancelled")

    def outbox_reject(self, item_id: int) -> None:
        if not self.store.outbox_get(item_id):
            raise ServiceError(f"no outbox item {item_id}")
        self.store.outbox_set(item_id, "rejected")
