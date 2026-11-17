# CLAUDE.md — monospace.email

Terminal email client (Textual) + CLI + MCP server. Command `monospace`, package `monospace`,
repo github.com/scaryshinyglasses/monospace.email (MIT, open source). Runs on macOS and
headless Linux (no display, no keyring, `TERM=linux`) — keep it that way.

## Commands

```bash
uv sync                       # once
uv run monospace              # TUI (default command)
uv run monospace doctor --offline
uv run pytest                 # 35+ tests: core, SMTP (aiosmtpd), MCP (in-process Client), Textual pilot
uv tool install --editable .  # `monospace` on PATH
sh install.sh                 # end-user installer (curl | sh); MONOSPACE_SOURCE=<path> to test locally
```

`install.sh` (POSIX sh, macOS + Linux): bootstraps uv if missing, then `uv tool install` from the GitHub
source tarball (no git needed). Test it in ubuntu:24.04 and in a temp HOME on macOS
(`UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` overrides) before changing it. README screenshots in `docs/assets/` are
Textual SVG screenshots of a FakeServer demo mailbox.

## Architecture

```
src/monospace/
  cli/          main.py (argparse dispatch, TUI default) · mail.py (send/draft/reply: --attach ≠ --inline, --html)
                manage.py (forward · drafts ls|show|edit|send|rm · folders create|rename|rm) · accounts.py (+ `oauth`)
                outbox.py · agent_dirs.py (interactive-only) · doctor.py · update.py · mcp_setup.py (`mcp install claude|codex`) · daemon.py (systemd/launchd) · output.py (json, interactive())
  mcp_server.py MCPServer (mcp 2.x — FastMCP was renamed) tools wrapping core.service
  mcp_drafts.py draft edit/send/delete, folder ops, outbox_cancel, InlineImage model, apply_extras
  core/         ← NO UI IMPORTS (tests/test_purity.py enforces; also ≤350 lines/file)
    paths.py        XDG dirs; MONOSPACE_HOME relocates everything
    config.py       Account/OAuthClients/Settings, PRESETS (gmail/outlook/custom), hand-written TOML dump
    secrets.py      keyring → EncryptedFileStore (secrets_file.py) fallback; MONOSPACE_SECRETS=keyring|file|memory
                    keyring_usable() probes a real read (locked GNOME Keyring over SSH → file fallback)
    platform.py     has_display / opener / download dir
    update.py       install.json (ref+sha, written by install.sh / `update`) · daily GitHub check, cached · notify only
    clipboard.py    OSC 52 (tmux/screen passthrough) + native pbcopy/wl-copy/xclip/xsel; native paste
    auth/           xoauth2 · gmail (loopback + headless paste flow) · outlook (MSAL device code) · tokens (resolve())
    accounts.py     sign-in + IMAP verify shared by CLI and TUI wizards
    imap.py         ImapSession over imapclient (sync; run in threads), UID-based
    smtp.py         aiosmtplib send (password / XOAUTH2)
    store.py        SQLite: folders, messages, bodies, FTS5 `search`, outbox
    sync.py         incremental per-folder sync (UIDVALIDITY, last UID, flag refresh, body prefetch)
    watch.py        per-account IDLE/poll threads → callback
    mime.py/compose.py  parse → ParsedMessage; Draft → EmailMessage (markdown or html_body → plain+html), reply/forward
                    inline images = multipart/related parts (cid `<name>@monospace.email`, `inline_layout`
                    places unreferenced ones top/bottom); attachments = multipart/mixed parts
    drafts.py       DraftsMixin: get/update/delete/send drafts (Drafts folder only). Edit = APPEND new
                    (same Message-ID) + expunge old; existing parts materialized to data/draft-files/
    folders.py      FoldersMixin: create/rename/delete (delete: empty, non-system only)
    policy.py       ai_send: draft | confirm(queue) | allow — applies to source="ai" only; agent file roots
    sanitize.py     agent HTML allowlist (built with untrusted=True) · review_text for approval UIs · terminal_safe
    service.py      THE façade (blocking). TUI, CLI and MCP all go through it.
  render/       html_blocks (selectolax lexbor → Text/Image blocks) · images (cid/data/remote opt-in) · gif
  ui/           app.py (MonospaceApp; imports image_view FIRST — textual_image probes the terminal at import)
                screens/ main (e edit draft · N/E/X folders) · compose (attachments + inline images; edits a
                stored draft via draft_id → replace_draft) · prompt · account_add · outbox     widgets/ folder_tree · message_list · reader
                · image_view (GIF animation via set_timer) · attachment_bar     glyphs.py (ASCII fallback)
```

## Rules that matter

- **AI safety:** sends from MCP / non-interactive CLI use `source="ai"` → `policy.decide`. Never add an MCP
  tool that approves the outbox or changes `ai_send`; CLI `outbox approve` and `account set ai_send=` refuse
  non-interactive callers. Human TUI sends call `send_now` directly.
- `submit`/`save_draft(trusted=True)` skips the agent-file guard: use it only for parts *we* materialized
  (a stored draft, a forwarded original), after guarding any agent-supplied files.
- Agent attachments: `policy.check_agent_attachments` confines `source="ai"` attachments (and inline images) to
  `policy.agent_roots` = `agent_attachment_dir` + `settings.agent_read_dirs` (added only via interactive
  `monospace agent-dirs add`, `validate_read_dir` refuses /, home, hidden dirs, our own data);
  resolve + is_relative_to, symlinks rejected; queued outbox items snapshot files into
  `data/outbox-files/`. UI only auto-opens passive types (`platform.safe_to_open`) and http(s) links (`safe_url`).
- Message `id` = SQLite rowid (stable handle for CLI/MCP); IMAP UIDs stay internal.
- Workers: anything touching the network runs in `@work(thread=True)`; update UI via `call_from_thread`.
- Updates are never automatic: the TUI only notifies (`settings.check_updates`); installing is `monospace update`.
  No install.json (dev/editable install) → no network check.
- Never bind `ctrl+c` with `priority=True`: Screen/Input/TextArea use it for copy. `MonospaceApp`
  overrides `copy_to_clipboard` and the `clipboard` property (what `ctrl+v` pastes) → `core/clipboard.py`.
- Images: `ImageView` sets BOTH width and height in cells. Never `height: auto` on a textual_image widget —
  ≥0.14 computes it from a pre-layout width of 0, so static images render 0 rows tall (GIFs hid this).
  In half-cell/unicode mode (`image_view.LOW_FIDELITY`) images < 24 cols stay labels.
- Theme: `settings.theme`, applied on mount and saved via `theme_changed_signal`.
- File-store passphrase: env → remembered `data/passphrase` (0600, opt-in after prompt) → prompt.
- Don't name Textual subclass members after framework internals (`refresh_row`, `_composed` both bit us).
- Tests use `tests/fakes.py` FakeServer/FakeSession + `MONOSPACE_SECRETS` memory store (conftest autouse).
- Headless smoke test: ubuntu:24.04 container, `MONOSPACE_PASSPHRASE`, `TERM=linux` under `script`.
