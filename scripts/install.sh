#!/usr/bin/env bash
#
# install.sh — one-shot setup: build the converter, install the CLI, and
# register the Claude Code skill. Re-runnable and idempotent.
#
#   ./scripts/install.sh
#
# What it does:
#   1. builds vendor/hwp2hwpx.jar (via bootstrap.sh) if it's missing,
#   2. installs the `hwp-agent` CLI on your PATH (editable, so the jar is
#      auto-discovered — no env var needed),
#   3. symlinks the hwp-author skill into ~/.claude/skills/.
#
# Skip the converter (HWPX-only, no JDK/Maven) with:  SKIP_JAR=1 ./scripts/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
JAR="$REPO_ROOT/vendor/hwp2hwpx.jar"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Converter jar (optional) -------------------------------------------
if [[ -n "${SKIP_JAR:-}" ]]; then
  log "SKIP_JAR set — skipping converter build (HWP->HWPX convert won't work)."
elif [[ -f "$JAR" ]]; then
  log "Converter jar already built: $JAR"
else
  log "Building converter jar (JDK 17+ and Maven required)..."
  "$SCRIPT_DIR/bootstrap.sh"
fi

# --- 2. Install the CLI on PATH --------------------------------------------
# Editable install: hwp-agent resolves vendor/hwp2hwpx.jar relative to this
# checkout, so `convert` works from any directory with no HWP2HWPX_JAR env var.
if command -v uv >/dev/null 2>&1; then
  log "Installing hwp-agent CLI with uv (editable)..."
  uv tool install --editable "$REPO_ROOT" --force
elif command -v pipx >/dev/null 2>&1; then
  log "Installing hwp-agent CLI with pipx (editable)..."
  pipx install --editable "$REPO_ROOT" --force
else
  die "need 'uv' or 'pipx' to install the CLI on PATH.
       install uv:  curl -LsSf https://astral.sh/uv/install.sh | sh
       or install manually:  pip install -e \"$REPO_ROOT\""
fi

command -v hwp-agent >/dev/null 2>&1 \
  && log "CLI ready: $(hwp-agent --version)" \
  || warn "hwp-agent not on PATH yet — open a new shell, or add the installer's bin dir to PATH."

# --- 3. Register the Claude Code skill -------------------------------------
log "Installing hwp-author skill into $SKILLS_DIR ..."
mkdir -p "$SKILLS_DIR"
ln -sfn "$REPO_ROOT/skills/hwp-author" "$SKILLS_DIR/hwp-author"
log "Skill linked: $SKILLS_DIR/hwp-author -> $REPO_ROOT/skills/hwp-author"

cat <<'DONE'

Done. Start (or restart) Claude Code and type "/" — you should see /hwp-author.
Try:  hwp-agent classify <file.hwpx>
DONE
