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
`POST /api/fill-profile`, `POST /api/convert`, `GET /openapi.json`, `GET /healthz`.

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

## D. Claude / Codex — local MCP (true upload-free chat)

On the user's own machine (file stays local, no server needed):

```bash
pipx install "git+https://github.com/z0nam/hwp-agent[mcp]"
hwp-agent setup           # only if they need .hwp conversion (JRE 17+)
```

Then register the stdio server (all point at `hwp-agent mcp`):

```bash
# Claude Code
claude mcp add --transport stdio hwp-agent -- hwp-agent mcp
```
```jsonc
// Claude Desktop — claude_desktop_config.json
// macOS: ~/Library/Application Support/Claude/   Windows: %APPDATA%\Claude\
{ "mcpServers": { "hwp-agent": { "command": "hwp-agent", "args": ["mcp"] } } }
```
```toml
# Codex — ~/.codex/config.toml
[mcp_servers.hwp-agent]
command = "hwp-agent"
args = ["mcp"]
```

Restart the client, then chat: *"바탕화면의 등록신청서.hwpx를 내 프로필로 채워서
등록신청서_채움.hwpx로 저장해줘."* Claude calls `fill_form_from_profile` on the
local path. Tools exposed: `analyze_form_slots`, `fill_form_slots`,
`fill_form_from_profile`, `convert_hwp_to_hwpx`, `extract_to_markdown`.

> ChatGPT does **not** support local stdio MCP (remote HTTPS only), which is why
> ChatGPT uses the web link (C) and local MCP is Claude/Codex only.
