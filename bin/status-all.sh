#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/out/pids"
PORT="${PORT:-8080}"
CDP_PORT="${BRAVE_DEBUG_PORT:-9222}"

check() {
  local name="$1" pid_file="$2" url="$3"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name: running (pid $(cat "$pid_file"))"
  else
    echo "$name: not running"
  fi
  if [[ -n "$url" ]]; then
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "  probe $url → OK"
    else
      echo "  probe $url → DOWN"
    fi
  fi
}

check "Brave CDP" "$PID_DIR/brave.pid" "http://127.0.0.1:$CDP_PORT/json/version"
check "UI" "$PID_DIR/ui.pid" "http://127.0.0.1:$PORT/api/config"
echo "UI URL: http://127.0.0.1:$PORT"
echo "logs:   $ROOT/out/logs/"
