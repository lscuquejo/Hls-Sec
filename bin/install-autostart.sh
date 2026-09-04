#!/usr/bin/env bash
# Install LaunchAgents so UI + Brave start after login/reboot.
# After this, manage Brave/downloads from http://127.0.0.1:8080
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/out/logs"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Create venv first: ./bin/enable-ui.sh" >&2
  exit 1
fi

UID_NUM="$(id -u)"
for src in \
  "$ROOT/launchd/com.lcuquejo.hls-security-probe.brave.plist" \
  "$ROOT/launchd/com.lcuquejo.hls-security-probe.ui.plist"
do
  base="$(basename "$src")"
  label="${base%.plist}"
  dst="$AGENTS/$base"
  sed "s|__ROOT__|$ROOT|g" "$src" >"$dst"
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$dst"
  launchctl enable "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl kickstart -k "gui/$UID_NUM/$label" 2>/dev/null || launchctl load -w "$dst" 2>/dev/null || true
  echo "Installed $dst"
done

echo
echo "Autostart enabled."
echo "  UI: http://127.0.0.1:8080  ← manage Brave + downloads here"
echo "  Uninstall: ./bin/uninstall-autostart.sh"
