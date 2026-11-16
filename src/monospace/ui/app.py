"""MonospaceApp — the Textual shell: screens, background watcher, links, attachments, sending."""

from __future__ import annotations

from pathlib import Path

import logging

# Import order matters: textual_image probes the terminal for Sixel/Kitty support at import,
# which must happen before Textual takes over stdin. Terminals that never answer the probe
# (bare consoles, some multiplexers) just fall back to half-cell; keep its traceback off screen.
logging.getLogger("textual_image").setLevel(logging.CRITICAL)
from monospace.ui.widgets import image_view  # noqa: E402,F401  (side effect: graphics probe)

from textual import work
from textual.app import App
from textual.binding import Binding

from monospace.core import platform
from monospace.core.compose import Draft
from monospace.core.service import Service
from monospace.core.watch import Watcher
from monospace.ui.screens.account_add import AccountAddScreen
from monospace.ui.screens.compose import ComposeResultData, ComposeScreen
from monospace.ui.screens.main import MainScreen
from monospace.ui.screens.outbox import OutboxScreen


class MonospaceApp(App):
    TITLE = "monospace.email"
    CSS_PATH = Path(__file__).with_name("theme.tcss")
    BINDINGS = [Binding("q", "quit", "Quit"), Binding("ctrl+c", "quit", "Quit", show=False, priority=True)]

    def __init__(self, svc: Service, *, images: bool = True, watch: bool = True) -> None:
        super().__init__()
        self.svc = svc
        self.images = images and svc.cfg.settings.images
        self.remote_images_default = svc.cfg.settings.remote_images
        self._watch = watch
        self.watcher: Watcher | None = None

    def on_mount(self) -> None:
        self.theme = "tokyo-night" if "tokyo-night" in self.available_themes else self.theme
        self.push_screen(MainScreen())
        if not self.svc.cfg.accounts:
            self.open_account_wizard()
        else:
            self.start_watcher()

    def start_watcher(self) -> None:
        if not self._watch:
            return
        if self.watcher:
            self.watcher.stop()
        self.watcher = Watcher(self.svc, self._changed, self._watch_error)
        self.watcher.start()

    def _changed(self, account: str, folder: str) -> None:
        self.call_from_thread(self._refresh_main)

    def _watch_error(self, account: str, err: str) -> None:
        self.call_from_thread(self.notify, f"{account}: {err}", severity="warning", timeout=6)

    def _refresh_main(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, MainScreen):
                screen.refresh_from_cache()

    def on_unmount(self) -> None:
        if self.watcher:
            self.watcher.stop()

    # ---- shared actions -------------------------------------------------
    def open_link(self, href: str) -> None:
        if href.startswith("mailto:"):
            self.open_compose(Draft(to=[href[7:].split("?")[0]]), None)
        elif not platform.safe_url(href):
            self.copy_to_clipboard(href)
            self.notify(f"Not opening a non-web link; copied instead:\n{href[:200]}", severity="warning")
        elif platform.has_display():
            self.open_url(href)
            self.notify(f"Opened {href[:80]}", timeout=3)
        else:
            self.copy_to_clipboard(href)
            self.notify(f"Link copied to clipboard:\n{href[:200]}", timeout=8)

    def open_compose(self, draft: Draft, account: str | None) -> None:
        if not self.svc.cfg.accounts:
            self.notify("Add an account first (A)", severity="warning")
            return
        acct = self.svc.account(account)
        choices = [(a.name, a.email) for a in self.svc.cfg.accounts]
        self.push_screen(ComposeScreen(draft, acct.name, choices), self._compose_done)

    def _compose_done(self, result: ComposeResultData | None) -> None:
        if result:
            self.deliver(result)

    @work(thread=True, group="send")
    def deliver(self, result: ComposeResultData) -> None:
        try:
            if result.action == "draft":
                res = self.svc.save_draft(result.account, result.draft)
            else:
                res = self.svc.send_now(result.account, result.draft)  # a human pressed Send
            self.call_from_thread(self.notify, f"✓ {res.status}: {res.detail}")
        except Exception as exc:
            self.call_from_thread(self.notify, f"Send failed: {exc}", severity="error", timeout=10)
            self.call_from_thread(self.push_screen,
                                  ComposeScreen(result.draft, result.account,
                                                [(a.name, a.email) for a in self.svc.cfg.accounts]),
                                  self._compose_done)

    @work(thread=True, group="attachment")
    def save_attachment(self, msg_id: int, index: int) -> None:
        try:
            path = self.svc.save_attachment(msg_id, index)
        except Exception as exc:
            self.call_from_thread(self.notify, f"Could not save: {exc}", severity="error")
            return
        opened = platform.safe_to_open(str(path)) and platform.open_path(str(path))
        note = " and opened it" if opened else ("" if platform.safe_to_open(str(path)) else
                                                 " (not opened automatically: not a passive file type)")
        self.call_from_thread(self.notify, f"Saved {path}{note}", timeout=8)

    def open_outbox(self) -> None:
        self.push_screen(OutboxScreen())

    def open_account_wizard(self) -> None:
        def done(added: bool | None) -> None:
            if added:
                self._refresh_main()
                self.start_watcher()
                for screen in self.screen_stack:
                    if isinstance(screen, MainScreen):
                        if screen.account is None and self.svc.cfg.accounts:
                            screen.account = self.svc.cfg.accounts[0].name
                        screen.sync_current()

        self.push_screen(AccountAddScreen(self.svc.cfg), done)


def run_app(svc: Service, *, images: bool = True) -> int:
    MonospaceApp(svc, images=images).run()
    return 0
