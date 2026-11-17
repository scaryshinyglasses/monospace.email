"""Add-account wizard: Gmail / Outlook (OAuth or app password) / any IMAP+SMTP server."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from monospace.core import accounts, config, platform
from monospace.core.config import Account

TLS = [("SSL/TLS", "ssl"), ("STARTTLS", "starttls"), ("None (insecure)", "none")]
APP_PW_HELP = {
    "gmail": "Gmail needs an app password (Google Account → Security → 2-Step Verification → App passwords).",
    "outlook": "Outlook needs an app password, or configure OAuth with `monospace oauth --microsoft-client-id`.",
}


class AccountAddScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, cfg: config.Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.provider = ""
        self.acct: Account | None = None
        self.flow = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="form"):
            yield Static("[b]Add an email account[/b]")
            with Horizontal(classes="row", id="providers"):
                yield Button("Gmail", id="p-gmail", variant="primary")
                yield Button("Outlook / Microsoft 365", id="p-outlook", variant="primary")
                yield Button("Other (IMAP/SMTP)", id="p-custom")
            with Vertical(id="details"):
                yield Input(placeholder="you@example.com", id="email")
                yield Input(placeholder="Account name (short, e.g. work)", id="name")
                yield Input(placeholder="Display name (optional)", id="display")
                yield Checkbox("Use an app password instead of OAuth", id="use-pw")
                with Vertical(id="servers"):
                    yield Label("IMAP server")
                    with Horizontal(classes="row"):
                        yield Input(placeholder="imap.example.com", id="imap_host")
                        yield Input("993", id="imap_port", type="integer")
                        yield Select(TLS, value="ssl", allow_blank=False, id="imap_tls")
                    yield Label("SMTP server")
                    with Horizontal(classes="row"):
                        yield Input(placeholder="smtp.example.com", id="smtp_host")
                        yield Input("587", id="smtp_port", type="integer")
                        yield Select(TLS, value="starttls", allow_blank=False, id="smtp_tls")
                    yield Input(placeholder="Username (defaults to the email address)", id="username")
                yield Input(placeholder="Password / app password", password=True, id="password")
                yield Static("", classes="hint", id="hint")
                with Vertical(id="oauth-box"):
                    yield Static("", classes="url", id="oauth-text")
                    with Horizontal(classes="row"):
                        yield Button("Copy link", id="copy")
                    yield Input(placeholder="Paste the full http://localhost… address here", id="paste")
                yield Label("", classes="error", id="error")
                with Horizontal(classes="row"):
                    yield Button("Continue", variant="success", id="go")
                    yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        for wid in ("#details", "#oauth-box"):
            self.query_one(wid).display = False

    # ---- step 1: provider ---------------------------------------------
    @on(Button.Pressed, "#providers Button")
    def choose(self, event: Button.Pressed) -> None:
        self.provider = (event.button.id or "p-custom")[2:]
        oauth = accounts.oauth_available(self.cfg, self.provider)
        self.query_one("#details").display = True
        self.query_one("#servers").display = self.provider == "custom"
        self.query_one("#use-pw", Checkbox).display = oauth
        self.query_one("#use-pw", Checkbox).value = False
        self._sync_password_field()
        self.query_one("#email", Input).focus()

    @on(Checkbox.Changed, "#use-pw")
    def _sync_password_field(self, _event: object = None) -> None:
        pw = self._uses_password()
        self.query_one("#password").display = pw
        hint = APP_PW_HELP.get(self.provider, "") if pw else \
            f"You'll sign in with {'Google' if self.provider == 'gmail' else 'Microsoft'} next."
        self.query_one("#hint", Static).update(hint)

    def _uses_password(self) -> bool:
        if not accounts.oauth_available(self.cfg, self.provider):
            return True
        return self.query_one("#use-pw", Checkbox).value

    def _val(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def _error(self, msg: str) -> None:
        self.query_one("#error", Label).update(msg)

    # ---- step 2: build account + authenticate -------------------------
    @on(Button.Pressed, "#go")
    def go(self) -> None:
        if self.flow is not None:  # finishing a headless Gmail paste
            return self._finish_paste()
        email = self._val("email")
        if "@" not in email:
            return self._error("Enter a valid email address.")
        name = self._val("name") or email.split("@")[0]
        if any(a.name == name for a in self.cfg.accounts):
            return self._error(f"An account named {name!r} already exists.")
        over: dict[str, object] = {"display_name": self._val("display")}
        if self.provider == "custom":
            if not (self._val("imap_host") and self._val("smtp_host")):
                return self._error("IMAP and SMTP hosts are required.")
            over.update(imap_host=self._val("imap_host"), imap_port=int(self._val("imap_port") or 993),
                        imap_tls=self.query_one("#imap_tls", Select).value,
                        smtp_host=self._val("smtp_host"), smtp_port=int(self._val("smtp_port") or 587),
                        smtp_tls=self.query_one("#smtp_tls", Select).value, username=self._val("username"))
        self.acct = config.account_from_preset(name, email, self.provider,
                                               use_oauth=not self._uses_password(), **over)
        self._error("")
        if self.acct.auth == "password":
            if not self.query_one("#password", Input).value:
                return self._error("Enter the password.")
            try:
                accounts.store_password(self.acct, self.query_one("#password", Input).value)
            except Exception as exc:  # e.g. keyring locked mid-session: show it, don't crash
                return self._error(f"Could not store the password: {type(exc).__name__}: {exc}. "
                                   "Restart with MONOSPACE_SECRETS=file to use the encrypted secrets file.")
            self.verify_and_save()
        elif self.acct.auth == "gmail_oauth":
            self._gmail()
        else:
            self._show_oauth("Starting Microsoft sign-in…", paste=False)
            self.outlook()

    def _show_oauth(self, text: str, *, paste: bool) -> None:
        self.query_one("#oauth-box").display = True
        self.query_one("#oauth-text", Static).update(text)
        self.query_one("#paste").display = paste
        self.query_one("#go", Button).disabled = not paste

    def _gmail(self) -> None:
        assert self.acct
        self.flow = accounts.gmail_flow(self.cfg, self.acct)
        if platform.has_display():
            self._show_oauth("Your browser is opening — approve access, then come back here.", paste=False)
            self.gmail_browser()
        else:
            self.oauth_url = self.flow.url()
            self._show_oauth(
                "[b]Headless sign-in[/b]: open this link on any device, approve access, then paste the address "
                f"the browser ends up on (a localhost page that fails to load — expected).\n\n{self.oauth_url}",
                paste=True)
            self.query_one("#paste", Input).focus()

    @work(thread=True, exclusive=True, group="auth")
    def gmail_browser(self) -> None:
        try:
            accounts.save_gmail_tokens(self.acct, self.flow.run_browser())  # type: ignore[arg-type, union-attr]
        except Exception as exc:
            self.app.call_from_thread(self._error, f"Google sign-in failed: {exc}")
            self.flow = None
            return
        self.flow = None
        self._verify()

    def _finish_paste(self) -> None:
        pasted = self._val("paste")
        try:
            accounts.save_gmail_tokens(self.acct, self.flow.finish(pasted))  # type: ignore[arg-type, union-attr]
        except Exception as exc:
            return self._error(f"{exc}")
        self.flow = None
        self.verify_and_save()

    @work(thread=True, exclusive=True, group="auth")
    def outlook(self) -> None:
        def say(msg: str) -> None:
            self.oauth_url = "https://microsoft.com/devicelogin"
            self.app.call_from_thread(self._show_oauth, msg, paste=False)

        try:
            accounts.outlook_login(self.cfg, self.acct, say)  # type: ignore[arg-type]
        except Exception as exc:
            self.app.call_from_thread(self._error, f"Microsoft sign-in failed: {exc}")
            return
        self._verify()

    @on(Button.Pressed, "#copy")
    def copy(self) -> None:
        self.app.copy_to_clipboard(getattr(self, "oauth_url", ""))
        self.notify("Copied to clipboard (OSC 52)")

    # ---- step 3: verify ------------------------------------------------
    @work(thread=True, exclusive=True, group="verify")
    def verify_and_save(self) -> None:
        self._verify()

    def _verify(self) -> None:
        self.app.call_from_thread(self._error, "")
        self.app.call_from_thread(self.query_one("#hint", Static).update, "Connecting to the IMAP server…")
        try:
            report = accounts.verify(self.cfg, self.acct)  # type: ignore[arg-type]
            accounts.save(self.cfg, self.acct)  # type: ignore[arg-type]
        except Exception as exc:
            self.app.call_from_thread(self._error, f"Could not connect: {exc}")
            self.app.call_from_thread(setattr, self.query_one("#go", Button), "disabled", False)
            return
        self.app.call_from_thread(self.notify, f"Added {self.acct.name} · {report['folders']} folders")  # type: ignore
        self.app.call_from_thread(self.dismiss, True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)
