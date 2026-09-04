#!/usr/bin/env bash
# Start Brave CDP + UI in the background (one terminal).
# Logs: out/logs/
#
#   ./bin/start-all.sh
#   ./bin/stop-all.sh
#   ./bin/status-all.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/out/logs"
PID_DIR="$ROOT/out/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

BRAVE_PID_FILE="$PID_DIR/brave.pid"
UI_PID_FILE="$PID_DIR/ui.pid"
PORT="${PORT:-8080}"
CDP_PORT="${BRAVE_DEBUG_PORT:-9222}"

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

if is_running "$UI_PID_FILE" && is_running "$BRAVE_PID_FILE"; then
  echo "Already running."
  echo "  UI:    http://127.0.0.1:$PORT"
  echo "  Brave: http://127.0.0.1:$CDP_PORT"
  echo "  status: ./bin/status-all.sh"
  exit 0
fi

# Ensure venv
if [[ ! -d .venv ]]; then
  if command -v python3.13 >/dev/null; then PY=python3.13
  elif command -v python3.12 >/dev/null; then PY=python3.12
  else PY=python3
  fi
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip
  pip install -r requirements.txt
  playwright install chromium
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# Brave with CDP (background)
if ! is_running "$BRAVE_PID_FILE"; then
  # clear stale brave locks if no brave process
  if ! pgrep -x "Brave Browser" >/dev/null 2>&1; then
    USER_DATA="${BRAVE_USER_DATA:-$HOME/Library/Application Support/BraveSoftware/Brave-Browser}"
    rm -f "$USER_DATA/SingletonLock" "$USER_DATA/SingletonSocket" "$USER_DATA/SingletonCookie" 2>/dev/null || true
  fi

  echo "Starting Brave CDP on :$CDP_PORT ..."
  nohup "$ROOT/bin/brave-debug.sh" >"$LOG_DIR/brave.log" 2>&1 &
  echo $! >"$BRAVE_PID_FILE"
  # wait for CDP
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then
      echo "Brave CDP ready."
      break
    fi
    sleep 0.5
    if [[ $i -eq 30 ]]; then
      echo "WARNING: Brave CDP not responding yet. Check $LOG_DIR/brave.log" >&2
    fi
  done
else
  echo "Brave already running (pid $(cat "$BRAVE_PID_FILE"))."
fi

# UI (background)
if ! is_running "$UI_PID_FILE"; then
  echo "Starting UI on :$PORT ..."
  nohup env BRAVE_CDP="http://127.0.0.1:$CDP_PORT" \
    "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
    >"$LOG_DIR/ui.log" 2>&1 &
  echo $! >"$UI_PID_FILE"
  sleep 1
else
  echo "UI already running (pid $(cat "$UI_PID_FILE"))."
fi

echo
echo "Running in background."
echo "  UI:     http://127.0.0.1:$PORT"
echo "  Brave:  keep logged into Hotmart in the Brave window"
echo "  logs:   $LOG_DIR/"
echo "  stop:   ./bin/stop-all.sh"
echo
echo "For auto-start after reboot/login:"
echo "  ./bin/install-autostart.sh"
