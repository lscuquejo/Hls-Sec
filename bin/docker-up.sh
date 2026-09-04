#!/usr/bin/env bash
# UI in Docker. Prefer host Brave for Hotmart login (easier than the debugger).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

docker compose up -d --build

echo
echo "Running in Docker (background)."
echo "  UI: http://127.0.0.1:8080"
echo

# Prefer Brave CDP on the host
if curl -fsS -m 2 "http://127.0.0.1:9222/json/version" >/dev/null 2>&1; then
  echo "Brave CDP: OK on :9222 (will be used first)."
  echo "  Stay logged into Hotmart in that Brave window."
else
  echo "Brave CDP: not running yet — start it (recommended):"
  echo "  1) Quit Brave if it is open"
  echo "  2) ./bin/brave-debug.sh"
  echo "  3) Log into Hotmart in that Brave window"
  echo "  4) Retry Download in the UI"
  echo
  echo "Fallback only: http://127.0.0.1:3000/debugger/"
fi

echo
echo "  logs: docker compose logs -f"
echo "  stop: ./bin/docker-down.sh"
