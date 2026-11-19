# Changelog

Notable changes to monospace.email. Commits pushed to `main` land under **Unreleased** automatically
(`scripts/release_notes.py`); cutting a release moves them under a version. Edit entries freely: the bot
only adds commits it hasn't seen. Releases: https://github.com/scaryshinyglasses/monospace.email/releases

## Unreleased

- Generate release notes on every push; add a release workflow ([962c2cd](https://github.com/scaryshinyglasses/monospace.email/commit/962c2cd))
- Add tags for messages and drafts (TUI, CLI, MCP, search) ([ae74da8](https://github.com/scaryshinyglasses/monospace.email/commit/ae74da8))
- Serve installer from Monospace website URL ([733a6eb](https://github.com/scaryshinyglasses/monospace.email/commit/733a6eb))
- Create Monospace product website ([788593e](https://github.com/scaryshinyglasses/monospace.email/commit/788593e))
- Sanitizer review path: iterative render, fail closed on odd URLs ([64a995a](https://github.com/scaryshinyglasses/monospace.email/commit/64a995a))
- Compare HTML to text from the DOM with links tokenized in place ([fba7dc8](https://github.com/scaryshinyglasses/monospace.email/commit/fba7dc8))
- Allowlist file types agents may send from approved read dirs ([f075902](https://github.com/scaryshinyglasses/monospace.email/commit/f075902))
- Compare link targets as normalized URLs; restrict img alt text ([f42659a](https://github.com/scaryshinyglasses/monospace.email/commit/f42659a))
- Refuse hidden files and key material inside agent-readable folders ([6681d9f](https://github.com/scaryshinyglasses/monospace.email/commit/6681d9f))
- Harden HTML sanitizer: DOM comment removal, fixed point, link-aware match ([b45df56](https://github.com/scaryshinyglasses/monospace.email/commit/b45df56))
- Let users approve extra folders agents may read files from ([e305f53](https://github.com/scaryshinyglasses/monospace.email/commit/e305f53))
- Sanitize agent-written HTML so review always matches what is sent ([e28489b](https://github.com/scaryshinyglasses/monospace.email/commit/e28489b))
- Make reviewed text match the HTML that gets sent ([b11dc5e](https://github.com/scaryshinyglasses/monospace.email/commit/b11dc5e))
- Add draft editing, inline images and folder controls (MCP, CLI, TUI) ([9ec64a3](https://github.com/scaryshinyglasses/monospace.email/commit/9ec64a3))
- Add `monospace mcp install claude|codex`; document Codex MCP setup ([6bf9ea6](https://github.com/scaryshinyglasses/monospace.email/commit/6bf9ea6))
- Remember theme and passphrase; label tiny images in half-block mode ([6247f55](https://github.com/scaryshinyglasses/monospace.email/commit/6247f55))
- Fix static images rendering 0 rows tall ([4915829](https://github.com/scaryshinyglasses/monospace.email/commit/4915829))
- Add update check and `monospace update` ([0cbc157](https://github.com/scaryshinyglasses/monospace.email/commit/0cbc157))
- Fix copy, paste, and crash on a locked Linux keyring ([2e201d2](https://github.com/scaryshinyglasses/monospace.email/commit/2e201d2))
- Fix reader recomposition during rapid message changes ([199409d](https://github.com/scaryshinyglasses/monospace.email/commit/199409d))
- Tighten attachment auto-open and outbox approval ([63bfbde](https://github.com/scaryshinyglasses/monospace.email/commit/63bfbde))
- Run test SMTP server on the test loop (macOS CI flake) ([b57c706](https://github.com/scaryshinyglasses/monospace.email/commit/b57c706))
- Add curl installer, security hardening and a proper README ([db92a13](https://github.com/scaryshinyglasses/monospace.email/commit/db92a13))
- Initial release of monospace.email ([5463034](https://github.com/scaryshinyglasses/monospace.email/commit/5463034))
