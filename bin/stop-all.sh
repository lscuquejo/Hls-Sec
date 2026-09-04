#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/out/pids"

stop_pidfile() {
  local name="$1"
  local f="$PID_DIR/$name.pid"
  if [[ -f "$f" ]]; then
    local pid
    pid="$(cat "$f" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $name (pid $pid)..."
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}

stop_pidfile ui
stop_pidfile brave

# Also stop Brave started by brave-debug if still around with debugging port
if lsof -iTCP:9222 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Note: something still listening on :9222 (maybe Brave). Quit Brave if you want it fully stopped."
fi

echo "Stopped."
