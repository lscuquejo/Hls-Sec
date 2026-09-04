#!/usr/bin/env bash
# Durable host-control for the Docker UI "Restart Brave" button.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p out/logs out/pids

PID_FILE=out/pids/host-control.pid
WATCH_PID_FILE=out/pids/host-control-watch.pid
PORT="${HOST_CONTROL_PORT:-9277}"

alive_pid() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  local pid
  pid="$(cat "$f" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

healthy() {
  curl -fsS -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1
}

stop_old() {
  if alive_pid "$WATCH_PID_FILE"; then
    kill "$(cat "$WATCH_PID_FILE")" 2>/dev/null || true
  fi
  if alive_pid "$PID_FILE"; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
  fi
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill $pids 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE" "$WATCH_PID_FILE"
  sleep 0.3
}

if alive_pid "$WATCH_PID_FILE" && healthy; then
  echo "host-control already running (watch pid $(cat "$WATCH_PID_FILE")) on :$PORT"
  exit 0
fi

stop_old

# Watchdog keeps the HTTP server alive even if the python process exits
nohup bash -c "
while true; do
  python3 '$ROOT/bin/host-control.py' >>'$ROOT/out/logs/host-control.log' 2>&1 &
  echo \$! >'$PID_FILE'
  wait \$!
  echo \"[host-control] exited \$?; restarting in 1s\" >>'$ROOT/out/logs/host-control.log'
  sleep 1
done
" >/dev/null 2>&1 &
echo $! >"$WATCH_PID_FILE"
disown || true

for _ in $(seq 1 20); do
  if healthy; then
    echo "host-control ready on http://127.0.0.1:$PORT"
    exit 0
  fi
  sleep 0.25
done

echo "host-control failed to become healthy — see out/logs/host-control.log" >&2
exit 1
