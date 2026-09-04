#!/usr/bin/env bash
# Install LaunchAgents so UI + Brave + host-control start after login/reboot.
# host-control is required for "Restart Brave" when the UI runs in Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/out/logs"

UID_NUM="$(id -u)"

install_plist() {
  local src="$1"
  local base label dst
  base="$(basename "$src")"
  label="${base%.plist}"
  dst="$AGENTS/$base"
  sed "s|__ROOT__|$ROOT|g" "$src" >"$dst"
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  if ! launchctl bootstrap "gui/$UID_NUM" "$dst" 2>/dev/null; then
    launchctl load -w "$dst" 2>/dev/null || true
  fi
  launchctl enable "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl kickstart -k "gui/$UID_NUM/$label" 2>/dev/null || true
  echo "Installed $dst"
}

# Always install host-control (no venv needed) — Docker Restart Brave depends on it
install_plist "$ROOT/launchd/com.lcuquejo.hls-security-probe.host-control.plist"

# Brave LaunchAgent (optional but recommended)
install_plist "$ROOT/launchd/com.lcuquejo.hls-security-probe.brave.plist"

# Local UI LaunchAgent is optional — skip by default when using Docker UI
# (both want :8080). Enable with: INSTALL_LOCAL_UI=1 ./bin/install-autostart.sh
if [[ "${INSTALL_LOCAL_UI:-0}" == "1" && -x "$ROOT/.venv/bin/python" ]]; then
  install_plist "$ROOT/launchd/com.lcuquejo.hls-security-probe.ui.plist"
else
  # Ensure local UI agent is not fighting Docker for :8080
  launchctl bootout "gui/$UID_NUM/com.lcuquejo.hls-security-probe.ui" 2>/dev/null || true
  echo "Skipping local UI LaunchAgent (Docker UI on :8080). Set INSTALL_LOCAL_UI=1 to enable."
fi

echo
echo "Autostart enabled (survives reboot after macOS login)."
echo "  host-control :9277  ← Restart Brave from Docker UI"
echo "  Brave debugging agent"
echo "  UI: http://127.0.0.1:8080"
echo "  Uninstall: ./bin/uninstall-autostart.sh"
