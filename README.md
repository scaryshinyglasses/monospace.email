# monospace.email

**Email in your terminal, with real images, animated GIFs, a mouse, and an AI interface.**

`monospace` is an IMAP/SMTP mail client for the terminal. You can add Gmail and Outlook with
one sign-in, or any other provider with IMAP/SMTP. HTML mail renders inline, including
pictures and GIFs. It works with a mouse or the keyboard. It also includes a CLI and an
[MCP](https://modelcontextprotocol.io) server, so AI assistants such as Claude can read,
search, draft and send mail under rules you set.

It runs the same way on a macOS desktop, an Ubuntu server over SSH, or a bare Linux console.

```
┌ personal me@example.com ─┬─────────────────────────────────────────────────────────┐
│ 📥 Inbox   3             │ ● News         Animated             2026-11-18 10:00    │
│ 📝 Drafts                │ 📎 Ana         Invoice #2041        2026-11-17 10:00    │
│ 📤 Sent                  │ ● Ana          Welcome to monospace 2026-11-16 10:00    │
│ 📦 Archive               ├─────────────────────────────────────────────────────────┤
│ 🧺 Trash                 │ Animated                                                │
│                          │ From  News <news@example.com>                           │
│                          │ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
│                          │ Hi — look here      [ your GIF, animating, in pixels ]  │
└──────────────────────────┴─────────────────────────────────────────────────────────┘
 c Compose  r Reply  a Archive  d Delete  / Search  i Images  ^r Sync  O Outbox  q Quit
```

## Features

- **Any provider:**
  - Gmail and Outlook / Microsoft 365 presets, using OAuth or an app password.
  - Any other server with IMAP + SMTP (SSL, STARTTLS or plain).
  - Several accounts at once.
- **Images in the terminal:**
  - The terminal's graphics protocol is detected automatically: Kitty/Ghostty (Kitty graphics), or Sixel (iTerm2, WezTerm, foot, Konsole, xterm -ti vt340…).
  - Other terminals fall back to coloured half-blocks, which works everywhere.
  - Animated GIFs play.
  - Remote images are **blocked by default**, which stops tracking pixels. Press `i` to load them.
- **Clickable:**
  - Folders, messages, links, attachments and buttons respond to the mouse.
  - Every action also has a key binding (the bare Linux console has no mouse).
- **Fast and works offline:**
  - A local SQLite cache with full-text search (FTS5).
  - New mail arrives over IMAP IDLE push, with polling as a fallback.
- **Built for AI:**
  - MCP server: `monospace mcp`.
  - Every CLI command takes `--json`.
  - A per-account **AI send policy** controls what an agent can send (`draft` / `confirm` / `allow`).
- **Headless first:**
  - Gmail OAuth works without a browser (paste-URL flow); Outlook uses the device-code flow.
  - Secrets go to an encrypted file when there's no OS keyring.
  - Links copy to the clipboard over SSH (OSC 52).
  - `monospace daemon --install` sets up a systemd/launchd sync service.

## Install

Requires Python 3.11+.

```bash
uv tool install git+https://github.com/scaryshinyglasses/monospace.email
# or
pipx install git+https://github.com/scaryshinyglasses/monospace.email
```

Then run:

```bash
monospace            # opens the TUI; first run walks you through adding an account
monospace doctor     # checks secrets backend, terminal graphics, connectivity
```

## Adding accounts

| Provider | Without setup | With OAuth configured |
|---|---|---|
| Gmail | app password (needs 2-Step Verification) | Sign in with Google |
| Outlook / M365 | app password, where your tenant allows it | Microsoft device-code sign-in |
| Anything else | IMAP/SMTP host + password | — |

Add accounts from the TUI (`A`) or the command line:

```bash
monospace account add                       # interactive
monospace account add --provider gmail --email you@gmail.com --no-browser   # headless
monospace account list
monospace account set work ai_send=confirm  # see "AI policy" below
```

### Gmail OAuth (optional)

To use OAuth for Gmail, register your own client:

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project. Configure the **OAuth consent screen** (External), and add yourself as a test user.
2. Go to **Credentials → Create credentials → OAuth client ID → Desktop app**.
3. Run `monospace oauth --google-client-id <id> --google-client-secret <secret>`. A Desktop-app secret isn't confidential.

Apps in "Testing" status issue refresh tokens that expire after 7 days. Publish the app, or run `monospace account login <name>` again when the token expires.

**Headless:** `monospace account add --no-browser` prints a URL. Approve access on any device, then paste the `http://localhost…` address the browser lands on. The page fails to load, and that's expected. Alternatively, `ssh -L 8765:localhost:8765 server` together with `--listen` lets the loopback flow complete on its own.

### Outlook / Microsoft 365 OAuth (optional)

1. In Entra ID, go to **App registrations → New registration**. Under supported account types choose *Accounts in any organizational directory and personal Microsoft accounts*.
2. Under **Authentication**, set *Allow public client flows* to **Yes**.
3. Under **API permissions**, add *Office 365 Exchange Online* → delegated `IMAP.AccessAsUser.All` and `SMTP.Send`.
4. Run `monospace oauth --microsoft-client-id <id>`. Add `--microsoft-tenant <tenant-id>` to restrict sign-in to one tenant.

The device-code flow shows a code and a URL, so it works on any machine, headless included. Some tenants need an admin to enable SMTP AUTH for the mailbox.

## Using it

| Key | Action | Key | Action |
|---|---|---|---|
| `j` / `k`, arrows | move | `Enter` / click | open |
| `c` | compose | `r` / `R` / `f` | reply / reply-all / forward |
| `a` | archive | `d` / `Del` / `#` | delete (to Trash) |
| `u` | toggle unread | `*` | toggle flag |
| `/` | search all mail | `Esc` | back to folder |
| `i` | load remote images | `H` | HTML ↔ plain text |
| `Ctrl+R` / `g` | sync now | `O` | AI outbox |
| `A` | add account | `q` | quit |

Compose bodies are Markdown, sent as plain text plus an HTML alternative. Use `Ctrl+S` to send and `Ctrl+D` to save a draft.

### CLI

```bash
monospace sync                         # fetch new mail (INBOX; --all for every folder)
monospace ls --unread --json           # list
monospace read 42                      # print a message (--json, --raw)
monospace search "invoice march"
monospace send --to bob@example.com --subject Hi --body "**Hello**" --attach ~/file.pdf
echo "body" | monospace reply 42 --all
monospace mark 42 --read --flag
monospace move 42 archive
monospace attachment 42 0              # save attachment #0 to ~/Downloads
monospace outbox list | approve 7 | reject 7
monospace daemon --install             # background sync via systemd --user / launchd
```

## AI integration

### MCP server

```bash
claude mcp add monospace -- monospace mcp
# headless box without a keyring:
claude mcp add monospace -e MONOSPACE_PASSPHRASE=… -- monospace mcp
```

Tools: `list_accounts`, `sync`, `list_folders`, `list_messages`, `search`, `read_message`,
`save_attachment`, `send_email`, `create_draft`, `reply`, `forward`, `mark`, `move`, `outbox_list`.

### AI policy

Each account has an `ai_send` setting that decides what happens when an **agent** sends. An agent means an MCP call, or the CLI run without an interactive terminal (or with `MONOSPACE_AGENT=1`).

| `ai_send` | What happens |
|---|---|
| `draft` (default) | The message is saved to the account's Drafts folder; you send it yourself. |
| `confirm` | The message is queued in the local outbox. Approve it in the TUI (`O`, then `y`) or with `monospace outbox approve <id>`. |
| `allow` | The message is sent immediately. |

There's deliberately no MCP tool for approving the outbox or changing the policy. `outbox approve` and `account set … ai_send=…` only work from an interactive terminal. Email bodies go to the model marked as untrusted content.

## Headless / servers

- **Secrets:** stored in the OS keyring when one is available. Otherwise they go to an encrypted file, `~/.local/share/monospace/secrets.enc` (Fernet + scrypt, mode 0600), unlocked by `MONOSPACE_PASSPHRASE` or a prompt. Set `MONOSPACE_SECRETS=keyring|file` to choose.
- **Images over SSH:** if your local terminal speaks Kitty graphics or Sixel, images come through SSH at full resolution. The bare console (`TERM=linux`) gets half-block images and ASCII icons; `MONOSPACE_ASCII=1` forces ASCII icons anywhere. Use `monospace --no-images` or `[settings] images = false` to turn images off.
- **Links and attachments:** when there's no display, clicking a link copies it to the clipboard (OSC 52), and attachments are saved to `~/Downloads`, or `~/monospace-downloads` if that folder doesn't exist.

## Files

| Path | What |
|---|---|
| `~/.config/monospace/config.toml` | accounts, OAuth client IDs, settings (never secrets) |
| `~/.local/share/monospace/cache.db` | message cache + search index |
| `~/.local/share/monospace/secrets.enc` | encrypted secrets, only used when there's no keyring |

`MONOSPACE_HOME=/some/dir` moves all three.

## Development

```bash
git clone https://github.com/scaryshinyglasses/monospace.email && cd monospace.email
uv sync
uv run pytest            # unit, SMTP (aiosmtpd), MCP and Textual pilot tests
uv run monospace
uv tool install --editable .   # `monospace` on PATH, tracking your checkout
```

See [CLAUDE.md](CLAUDE.md) for the architecture map.

## License

MIT © Tanmay Sheoran
