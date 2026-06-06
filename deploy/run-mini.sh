#!/usr/bin/env bash
#
# run-mini.sh — start the hwp-agent server on a self-hosted box (the mini).
# Edit the secrets below (or set them in the environment / launchd plist), then:
#   ./deploy/run-mini.sh
#
# Requires: pipx install "git+https://github.com/z0nam/hwp-agent[serve]"
#           hwp-agent setup   (+ a JRE 17+ for .hwp conversion)
#           ~/.config/hwp-agent/profile.json   (your personal data)
set -euo pipefail

# Secrets live OUTSIDE the repo: ~/.config/hwp-agent/serve.env (git-ignored).
# It should export HWP_AGENT_WEB_TOKEN and HWP_AGENT_API_KEY.
ENV_FILE="${HWP_AGENT_ENV_FILE:-$HOME/.config/hwp-agent/serve.env}"
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

# Personal-data profile the server fills from.
export HWP_AGENT_PROFILE="${HWP_AGENT_PROFILE:-$HOME/.config/hwp-agent/profile.json}"
# Gate the web page: visitors need ?token=<this>. Empty = open (rely on tunnel auth).
export HWP_AGENT_WEB_TOKEN="${HWP_AGENT_WEB_TOKEN:-}"
# Gate the REST API (X-API-Key header, used by a ChatGPT Action). Empty = open.
export HWP_AGENT_API_KEY="${HWP_AGENT_API_KEY:-}"

exec hwp-agent serve \
  --host "${HWP_AGENT_HOST:-0.0.0.0}" \
  --port "${HWP_AGENT_PORT:-8765}"
