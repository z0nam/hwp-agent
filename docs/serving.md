# Serving hwp-agent for non-technical users (web + AI chat)

Goal: let someone who can't open a terminal fill a Korean HWP/HWPX form with their
saved personal data — by clicking a link or chatting with an AI. Two surfaces, one
core (`ops`), nothing leaves hardware you control.

```
                       ┌──────────── your Mac mini (self-hosted) ────────────┐
  web browser ───────▶ │  hwp-agent serve  →  web UI  +  REST  +  /openapi.json │
  ChatGPT (GPT) ─────▶ │  (converts .hwp via Java, fills from your profile)     │
                       └───────────────────────────────────────────────────────┘
  Claude Desktop ────▶  hwp-agent mcp  (local stdio MCP — reads local files directly)
  Claude Code / Codex ▶  hwp-agent mcp
```

## The honest constraint (read this first)

- **Web chat (ChatGPT, claude.ai) cannot push a file you upload in the chat to an
  external server.** ChatGPT's code sandbox has no internet and no Java; its
  Actions/connectors can't carry your binary form out. So the *fully chat-driven*
  "upload here and it's filled" flow is **not** possible on ChatGPT.
- What works for ChatGPT users: a **web link** to your mini (upload → download),
  optionally wrapped by a custom GPT that hands over the link. The web link is
  also the lowest-friction path for anyone — no AI, no install.
- The genuinely chat-driven, upload-free experience exists on **local clients**
  (Claude Desktop / Claude Code / Codex) via the local MCP server, because they
  read the file from the user's own disk.

---

## A. Run the server on the mini

```bash
# one-time
pipx install "git+https://github.com/z0nam/hwp-agent[serve]"
hwp-agent setup                      # converter jar (needs a JRE 17+ for .hwp)
cp examples/profile.example.json ~/.config/hwp-agent/profile.json   # then edit your data

# run (bind to all interfaces so your tunnel can reach it)
export HWP_AGENT_PROFILE="$HOME/.config/hwp-agent/profile.json"
export HWP_AGENT_WEB_TOKEN="some-long-secret"   # optional: gate the web page
export HWP_AGENT_API_KEY="another-secret"       # optional: gate the REST API (X-API-Key)
hwp-agent serve --host 0.0.0.0 --port 8765
```

Endpoints: `GET /` (web UI), `POST /fill` (web form), `POST /api/analyze`,
`POST /api/fill`, `POST /api/autofit`, `POST /api/fill-profile`, `POST /api/convert`,
`GET /openapi.json`, `GET /healthz`.

The form endpoints mirror `hwp-agent form analyze/fill/autofit` (and the MCP tools)
option-for-option — multipart fields named after the CLI flags in snake_case:

| CLI | REST (multipart fields) |
|---|---|
| `form analyze [--grid]` | `POST /api/analyze` `file`, `grid` |
| `form fill --set/--map … [--delete-rows] [--append-rows] [--repeat-group] [--allow-table-flow] [--no-precise]` | `POST /api/fill` `file`, `mapping_json`, `delete_rows`, `append_rows`, `repeat_group` (repeatable), `allow_table_flow`, `precise` |
| `form autofit [--table] [--min-width]` | `POST /api/autofit` `file`, `table`, `min_width` (repeatable) |

`/api/fill` and `/api/autofit` return the edited `.hwpx` as the body and their JSON
report (rows/filled/missing/warnings, or the width summary) in the
`X-HWP-Agent-Report` header. A bad row spec, a vertical merge straddling a range
boundary, or malformed `mapping_json` is a `422` with nothing returned.

```bash
curl -sS -H "X-API-Key: $HWP_AGENT_API_KEY" -D report.txt -o out.hwpx \
  -F file=@신청서.hwpx -F delete_rows=cell:0:5-15 -F repeat_group=cell:0:2-4:+7 \
  -F 'mapping_json={"cell:0:2:0":"Ⅰ. 서론"}' https://hwp.example.com/api/fill
```

### Expose over HTTPS

Reuse whatever already fronts your Slack bots. Simplest is a Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://localhost:8765      # gives an https URL
# or map a stable hostname: cloudflared tunnel route dns <tunnel> hwp.example.com
```

Keep it alive across reboots with `launchd` (macOS) / `pm2` / a `tmux` session —
same as the bots. ChatGPT Actions **require HTTPS**.

---

## B. The non-technical path: just a web link

Send the person:  `https://hwp.example.com/?token=some-long-secret`

They: open it → drag the form (.hwp or .hwpx) → "내 정보로 채우기" → download. The
filled file (`<name>_채움.hwpx`) comes back; the upload never leaves the mini. This
needs no AI account and no install — it's the recommended path for 컴맹 users.

---

## C. ChatGPT — custom GPT (paid plan to *create*, free to *use* via share link)

A custom GPT can't receive the form binary, so it wraps the web link and answers
questions. Create one:

1. ChatGPT → **Explore GPTs → Create**.
2. **Instructions** (paste, edit the URL):
   > 너는 한국 정부 평가위원 등록신청서 작성을 돕는 도우미야. 사용자가 폼을 채우고
   > 싶다고 하면, 다음 링크에서 폼 파일을 올리면 저장된 본인 정보로 자동으로 채워
   > 받을 수 있다고 안내해: https://hwp.example.com/?token=some-long-secret
   > 파일 자체는 여기 대화에 올리지 말라고 안내해(서버에서 직접 처리됨).
3. (Optional) **Actions** → *Import from URL* `https://hwp.example.com/openapi.json`
   → Authentication: **API Key**, header `X-API-Key`. This lets the GPT call
   `analyze`/`convert` on files already reachable by URL, but **not** chat uploads.
4. **Share → Anyone with the link.** The recipient just signs in (no paid plan)
   and chats; for actual filling they use the web link the GPT gives them.

> Want a fully automated ChatGPT flow (no web link)? That requires the GPT Actions
> backend to receive the file, which ChatGPT can't do from a chat upload. The web
> link is the working answer.

---

## D. Claude / Codex / Gemini CLI — local MCP (true upload-free chat)

On the user's own machine (file stays local, no server needed):

```bash
pipx install "git+https://github.com/z0nam/hwp-agent[mcp]"
hwp-agent setup                                    # converter jar (needs JRE 17+ for .hwp)
hwp-agent setup --register claude,codex,gemini     # idempotently register MCP across all 4 clients
```

`hwp-agent setup --register` updates each client's config file in place without
touching other MCP servers (`--unregister` removes only `hwp-agent`):

- **Claude Desktop** (`claude-desktop`):
  macOS `~/Library/Application Support/Claude/claude_desktop_config.json`,
  Windows `%APPDATA%\Claude\claude_desktop_config.json`
- **Claude Code** (`claude-code`): `~/.claude.json`
- **Codex** (`codex`): `~/.codex/config.toml`
- **Gemini CLI** (`gemini`): `~/.gemini/settings.json`

On Windows, **`hwp-agent-setup.exe`** auto-detects installed AI clients, shows a
checklist, and runs `hwp-agent setup --register` automatically.

Restart the client, then chat: *"바탕화면의 등록신청서.hwpx를 내 프로필로 채워서
등록신청서_채움.hwpx로 저장해줘."* Claude calls `fill_form_from_profile` on the
local path. Tools exposed: `analyze_form_slots`, `fill_form_slots`,
`autofit_table_columns`, `fill_form_from_profile`, `convert_hwp_to_hwpx`,
`normalize_template`, `extract_to_markdown`.

The form tools mirror `hwp-agent form analyze/fill/autofit` option-for-option
(parameter = CLI flag in snake_case): `analyze_form_slots(grid=…)`,
`fill_form_slots(delete_rows=…, append_rows=…, repeat_group=…,
allow_table_flow=…, precise=…)`, `autofit_table_columns(table=…, min_width=…)`.
`tests/test_surface_parity.py` fails when a new CLI flag lacks its MCP parameter
or REST field (see section A for the REST side).

> ChatGPT and Gemini consumer web/mobile apps do **not** support local stdio MCP,
> which is why they use the web link (`https://hwp.namun.net`, sections B/C).
