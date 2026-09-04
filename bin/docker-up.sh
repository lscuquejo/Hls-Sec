#!/usr/bin/env bash
# UI in Docker. Prefer host Brave for Hotmart login (easier than the debugger).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Host helper so the UI "Restart Brave" button works from Docker
./bin/host-control.sh

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
  echo "Brave CDP: not running — use the UI button “Restart Brave”, or:"
  echo "  ./bin/brave-debug.sh"
  echo "Then log into Hotmart in that Brave window."
fi

echo
echo "  logs: docker compose logs -f"
echo "  stop: ./bin/docker-down.sh"
