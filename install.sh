#!/bin/sh
# monospace.email installer — macOS & Linux (incl. headless servers).
#
#   curl -fsSL https://monospace.arp244.dev/install.sh | sh
#
# Env overrides:
#   MONOSPACE_REF=v0.2.0      install a tag/branch/commit (default: main)
#   MONOSPACE_SOURCE=<spec>   install from any pip-style spec or local path (for testing)
#   MONOSPACE_PYTHON=3.12     Python version uv should use (downloaded automatically if missing)
#   sh install.sh --uninstall remove the command (config + mail cache are kept)
set -eu

REPO="scaryshinyglasses/monospace.email"
REF="${MONOSPACE_REF:-main}"
PY="${MONOSPACE_PYTHON:-3.12}"

if [ -t 1 ]; then
  B="$(printf '\033[1m')"; D="$(printf '\033[2m')"; G="$(printf '\033[32m')"; R="$(printf '\033[31m')"
  C="$(printf '\033[36m')"; N="$(printf '\033[0m')"
else
  B=""; D=""; G=""; R=""; C=""; N=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '%s›%s %s\n' "$C" "$N" "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
die()  { printf '%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

banner() {
  say ""
  say "${B}  ┌─────────────────────────────────┐${N}"
  say "${B}  │  monospace${D}.email${N}${B}                │${N}"
  say "${B}  │${N}  ${D}email, in your terminal${N}        ${B}│${N}"
  say "${B}  └─────────────────────────────────┘${N}"
  say ""
}

find_uv() {
  if has uv; then UV="$(command -v uv)"; return 0; fi
  for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "${XDG_BIN_HOME:-}/uv"; do
    [ -x "$p" ] && { UV="$p"; return 0; }
  done
  return 1
}

case "$(uname -s)" in
  Darwin) OS="macOS" ;;
  Linux)  OS="Linux" ;;
  *) die "unsupported OS $(uname -s) — monospace supports macOS and Linux (use WSL on Windows)" ;;
esac
ARCH="$(uname -m)"

if [ "${1:-}" = "--uninstall" ]; then
  find_uv || die "uv not found; nothing to uninstall"
  "$UV" tool uninstall monospace && ok "monospace removed (config in ~/.config/monospace kept)"
  exit 0
fi

banner
step "Detected ${B}${OS}${N} (${ARCH})"

if ! has curl && ! has wget; then
  die "need curl or wget"
fi

if find_uv; then
  ok "uv found at ${D}${UV}${N}"
else
  step "Installing uv (Python toolchain manager, from astral.sh)…"
  if has curl; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  else
    wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  fi
  find_uv || die "uv installation failed"
  ok "uv installed"
fi

# Pin branch installs to a commit so `monospace update` knows exactly what's installed.
# Tags (v*) are already immutable. A failed lookup (offline, rate limit) just skips the pin.
SHA=""
case "$REF" in
  v[0-9]*) ;;
  *)
    if [ -z "${MONOSPACE_SOURCE:-}" ]; then
      API="https://api.github.com/repos/${REPO}/commits/${REF}"
      if has curl; then
        SHA="$(curl -fsSL -H 'Accept: application/vnd.github.sha' "$API" 2>/dev/null || true)"
      else
        SHA="$(wget -qO- --header='Accept: application/vnd.github.sha' "$API" 2>/dev/null || true)"
      fi
      case "$SHA" in *[!0-9a-f]* | "") SHA="" ;; esac
    fi
    ;;
esac

# A source archive avoids requiring git on minimal servers.
SPEC="${MONOSPACE_SOURCE:-monospace @ https://github.com/${REPO}/archive/${SHA:-$REF}.tar.gz}"
step "Installing monospace (${D}${REF}${N}, Python ${PY})…"
"$UV" tool install --force --python "$PY" "$SPEC" >/dev/null 2>"${TMPDIR:-/tmp}/monospace-install.log" \
  || { cat "${TMPDIR:-/tmp}/monospace-install.log" >&2; die "install failed"; }

BIN_DIR="$("$UV" tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"

# Record what was installed for the daily update check (skipped for local/test sources).
if [ -z "${MONOSPACE_SOURCE:-}" ]; then
  if [ -n "${MONOSPACE_HOME:-}" ]; then DATA="$MONOSPACE_HOME/data"; else DATA="${XDG_DATA_HOME:-$HOME/.local/share}/monospace"; fi
  mkdir -p "$DATA" && chmod 700 "$DATA" 2>/dev/null
  printf '{"ref": "%s", "sha": "%s", "installed_at": %s}\n' "$REF" "$SHA" "$(date +%s)" > "$DATA/install.json" || true
fi
ok "installed ${B}monospace${N} → ${D}${BIN_DIR}/monospace${N}"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    "$UV" tool update-shell >/dev/null 2>&1 || true
    say ""
    say "  ${B}Add it to your PATH${N} (open a new shell, or run):"
    say "    export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

say ""
say "  ${B}Next${N}"
say "    ${C}monospace${N}              open the client — first run adds an account"
say "    ${C}monospace doctor${N}       check keyring, terminal graphics and connectivity"
say "    ${C}monospace mcp install claude${N}   let Claude Code use your mail (or: codex, agy)"
if [ "$OS" = "Linux" ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  say ""
  say "  ${D}Headless box detected: secrets go to an encrypted file —"
  say "  export MONOSPACE_PASSPHRASE=… (or you'll be prompted).${N}"
fi
say ""
