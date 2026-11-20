"""Keep INBOX fresh: IMAP IDLE push where supported, polling otherwise. One thread per account."""

from __future__ import annotations

import threading
from typing import Callable

from monospace.core import sync as syncmod
from monospace.core.config import Account

IDLE_SECONDS = 240     # re-issue IDLE before typical 29-min server timeouts / NAT drops
POLL_SECONDS = 120
BACKOFF_MAX = 600


class Watcher:
    def __init__(self, svc, on_change: Callable[[str, str], None],  # type: ignore[no-untyped-def]
                 on_error: Callable[[str, str], None] | None = None) -> None:
        self.svc = svc
        self.on_change = on_change
        self.on_error = on_error or (lambda a, e: None)
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for acct in self.svc.cfg.accounts:
            t = threading.Thread(target=self._run, args=(acct,), name=f"watch-{acct.name}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self, acct: Account) -> None:
        backoff = 15
        while not self.stop_event.is_set():
            try:
                with self.svc.session(acct) as sess:
                    backoff = 15
                    self._sync(sess, acct)
                    idle = sess.supports_idle()
                    while not self.stop_event.is_set():
                        if idle:
                            sess.select("INBOX", readonly=True)
                            changed = sess.idle_wait(IDLE_SECONDS)
                        else:
                            changed = not self.stop_event.wait(POLL_SECONDS)
                        if self.stop_event.is_set():
                            return
                        if changed:
                            self._sync(sess, acct)
            except Exception as exc:  # network drop, auth expiry … retry with backoff
                self.on_error(acct.name, str(exc))
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    def _sync(self, sess, acct: Account) -> None:  # type: ignore[no-untyped-def]
        sent = self.svc.flush_scheduled()
        before = {f.name for f in self.svc.store.folders(acct.name)}
        syncmod.sync_folder_list(sess, self.svc.store, acct.name)
        new, removed = syncmod.sync_folder(sess, self.svc.store, acct.name, "INBOX")
        if new or removed or sent or not before:
            self.on_change(acct.name, "INBOX")
