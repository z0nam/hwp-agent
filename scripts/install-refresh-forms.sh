#!/usr/bin/env bash
# 서식 초벌구이 launchd 잡 설치/재설치 (매일 05:30). 재실행 안전.
set -euo pipefail

PLIST="re.ji.hwp-agent.refresh-forms"
SRC="$(cd "$(dirname "$0")" && pwd)/launchd/${PLIST}.plist"
DST="$HOME/Library/LaunchAgents/${PLIST}.plist"
UID_N="$(id -u)"

[ -f "$SRC" ] || { echo "plist 원본 없음: $SRC" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/share/hwp-agent"
cp "$SRC" "$DST"

# 이미 로드돼 있으면 내리고 다시 올림
launchctl bootout "gui/${UID_N}/${PLIST}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_N}" "$DST"
launchctl enable "gui/${UID_N}/${PLIST}"

echo "설치됨: $DST"
echo "다음 실행: 매일 05:30"
echo "즉시 테스트: launchctl kickstart -k gui/${UID_N}/${PLIST}"
echo "로그: ~/.local/share/hwp-agent/refresh-forms.log"
echo "제거: launchctl bootout gui/${UID_N}/${PLIST} && rm $DST"
