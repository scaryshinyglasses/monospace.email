"""Accounts, provider presets and the TOML config file (never holds secrets)."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from monospace.core import paths

AuthKind = Literal["password", "gmail_oauth", "outlook_oauth"]
AiSend = Literal["draft", "confirm", "allow"]
AI_SEND_VALUES: tuple[str, ...] = ("draft", "confirm", "allow")

PRESETS: dict[str, dict[str, object]] = {
    "gmail": {
        "imap_host": "imap.gmail.com", "imap_port": 993, "imap_tls": "ssl",
        "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_tls": "ssl",
        "oauth": "gmail_oauth",
        "folders": {"drafts": "[Gmail]/Drafts", "sent": "[Gmail]/Sent Mail",
                    "trash": "[Gmail]/Trash", "archive": "[Gmail]/All Mail"},
    },
    "outlook": {
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_tls": "ssl",
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_tls": "starttls",
        "oauth": "outlook_oauth",
        "folders": {"drafts": "Drafts", "sent": "Sent Items", "trash": "Deleted Items",
                    "archive": "Archive"},
    },
    "custom": {
        "imap_host": "", "imap_port": 993, "imap_tls": "ssl",
        "smtp_host": "", "smtp_port": 587, "smtp_tls": "starttls",
        "oauth": None,
        "folders": {"drafts": "Drafts", "sent": "Sent", "trash": "Trash", "archive": "Archive"},
    },
}


@dataclass
class Account:
    name: str
    email: str
    provider: str = "custom"
    display_name: str = ""
    username: str = ""
    auth: AuthKind = "password"
    imap_host: str = ""
    imap_port: int = 993
    imap_tls: str = "ssl"  # ssl | starttls | none
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls: str = "starttls"
    ai_send: AiSend = "draft"
    folders: dict[str, str] = field(default_factory=dict)
    signature: str = ""

    @property
    def login(self) -> str:
        return self.username or self.email

    def folder(self, role: str) -> str:
        defaults = PRESETS.get(self.provider, PRESETS["custom"])["folders"]
        return self.folders.get(role) or defaults.get(role, role.capitalize())  # type: ignore[union-attr]

    @property
    def from_header(self) -> str:
        return f"{self.display_name} <{self.email}>" if self.display_name else self.email


def account_from_preset(name: str, email: str, provider: str, *, use_oauth: bool, **over: object) -> Account:
    preset = PRESETS[provider]
    auth = preset["oauth"] if (use_oauth and preset["oauth"]) else "password"
    fields = {k: v for k, v in preset.items() if k not in ("oauth", "folders")}
    fields.update(over)
    return Account(name=name, email=email, provider=provider, auth=auth, **fields)  # type: ignore[arg-type]


@dataclass
class OAuthClients:
    google_client_id: str = ""
    google_client_secret: str = ""  # Google "Desktop app" secrets are not confidential.
    microsoft_client_id: str = ""
    microsoft_tenant: str = "common"


@dataclass
class Settings:
    images: bool = True
    remote_images: bool = False
    download_dir: str = ""
    agent_attachment_dir: str = ""  # the only folder AI agents may attach files from
    theme: str = "tokyo-night"  # any Textual theme; ctrl+p → Theme saves it here
    check_updates: bool = True  # daily GitHub check → in-app notice; never installs


@dataclass
class Config:
    accounts: list[Account] = field(default_factory=list)
    oauth: OAuthClients = field(default_factory=OAuthClients)
    settings: Settings = field(default_factory=Settings)
    path: Path | None = None

    def get(self, name: str | None) -> Account:
        if not self.accounts:
            raise LookupError("no accounts configured — run `monospace account add`")
        if name is None:
            return self.accounts[0]
        for acct in self.accounts:
            if acct.name == name or acct.email == name:
                return acct
        raise LookupError(f"no account named {name!r}")

    def upsert(self, account: Account) -> None:
        self.accounts = [a for a in self.accounts if a.name != account.name] + [account]

    def remove(self, name: str) -> bool:
        before = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.name != name]
        return len(self.accounts) != before


def _build(cls, raw: dict):  # type: ignore[no-untyped-def]
    known = cls.__dataclass_fields__
    return cls(**{k: v for k, v in raw.items() if k in known})


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    if not path.exists():
        return Config(path=path)
    raw = tomllib.loads(path.read_text())
    accounts = [_build(Account, a) for a in raw.get("accounts", [])]
    for a in accounts:
        if a.ai_send not in AI_SEND_VALUES:
            raise ValueError(f"account {a.name}: ai_send must be one of {AI_SEND_VALUES}")
    return Config(
        accounts=accounts,
        oauth=_build(OAuthClients, raw.get("oauth", {})),
        settings=_build(Settings, raw.get("settings", {})),
        path=path,
    )


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dict):
        inner = ", ".join(f"{_toml_key(k)} = {_toml_value(x)}" for k, x in v.items())
        return "{ " + inner + " }" if inner else "{}"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _toml_key(k: str) -> str:
    return k if k.replace("_", "").replace("-", "").isalnum() else _toml_value(k)


def dumps(cfg: Config) -> str:
    out = ["# monospace.email configuration — secrets live in the keychain / secrets.enc, never here.", ""]
    for section, obj in (("settings", cfg.settings), ("oauth", cfg.oauth)):
        out.append(f"[{section}]")
        out += [f"{k} = {_toml_value(v)}" for k, v in asdict(obj).items()]
        out.append("")
    for acct in cfg.accounts:
        out.append("[[accounts]]")
        out += [f"{k} = {_toml_value(v)}" for k, v in asdict(acct).items()]
        out.append("")
    return "\n".join(out)


def save(cfg: Config) -> Path:
    paths.ensure_dirs()
    path = cfg.path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(cfg))
    return path
