#!/usr/bin/env bash
set -euo pipefail
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

for label in \
  com.lcuquejo.hls-security-probe.brave \
  com.lcuquejo.hls-security-probe.ui
do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
  echo "Removed $label"
done

echo "Autostart disabled."
