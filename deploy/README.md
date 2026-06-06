# Deploy hwp-agent on the mini (self-hosted)

Turnkey scaffolding for issue A (run the server so ChatGPT/web users can fill
forms). Full reference: [`../docs/serving.md`](../docs/serving.md).

## One-time on the mini

```bash
# 1. install (server extras) + converter
pipx install "git+https://github.com/z0nam/hwp-agent[serve]"
hwp-agent setup                      # converter jar; install a JRE 17+ for .hwp

# 2. your personal data the server fills from
mkdir -p ~/.config/hwp-agent
cp examples/profile.example.json ~/.config/hwp-agent/profile.json
$EDITOR ~/.config/hwp-agent/profile.json      # fill in your info

# 3. set secrets, then run
$EDITOR deploy/run-mini.sh                     # change-me-web-secret / change-me-api-secret
./deploy/run-mini.sh                           # foreground test → Ctrl-C
```

Test locally: open `http://localhost:8765/?token=<your web secret>`, drag a form,
download the filled result.

## Expose over HTTPS (reuse the Slack-bot tunnel)

```bash
cloudflared tunnel --url http://localhost:8765           # quick ephemeral URL
# or a stable hostname via a named tunnel + DNS route (see docs/serving.md)
```

## Keep it running across reboots (launchd)

```bash
# edit the two __PLACEHOLDER__ paths in the plist first
#   __REPO_PATH__ → absolute path to this checkout,  __HOME__ → your home dir
cp deploy/com.z0nam.hwp-agent.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.z0nam.hwp-agent.plist
tail -f /tmp/hwp-agent.serve.log
# stop: launchctl unload -w ~/Library/LaunchAgents/com.z0nam.hwp-agent.plist
```

Then give the person `https://<your-domain>/?token=<web secret>` (issue B wires a
ChatGPT GPT around the same URL).
