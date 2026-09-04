#!/usr/bin/env bash
# Install LaunchAgents so Brave CDP + UI start after login/reboot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/out/logs"

# Ensure venv exists
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Create venv first: ./bin/run-ui.sh  (Ctrl+C after it starts once), or:"
  echo "  python3.13 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

for src in \
  "$ROOT/launchd/com.lcuquejo.hls-security-probe.brave.plist" \
  "$ROOT/launchd/com.lcuquejo.hls-security-probe.ui.plist"
do
  base="$(basename "$src")"
  dst="$AGENTS/$base"
  sed "s|__ROOT__|$ROOT|g" "$src" >"$dst"
  launchctl bootout "gui/$(id -u)/${base%.plist}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dst"
  launchctl enable "gui/$(id -u)/${base%.plist}" 2>/dev/null || true
  launchctl kickstart -k "gui/$(id -u)/${base%.plist}" 2>/dev/null || launchctl load -w "$dst" 2>/dev/null || true
  echo "Installed $dst"
done

echo
echo "Autostart enabled (runs at login after reboot)."
echo "  UI: http://127.0.0.1:8080"
echo "  Uninstall: ./bin/uninstall-autostart.sh"
echo "  Note: you must be logged into macOS GUI; log into Hotmart once in the Brave window."
