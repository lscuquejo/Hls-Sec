#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
docker compose ps
echo
if curl -fsS -m 2 "http://127.0.0.1:9222/json/version" >/dev/null 2>&1; then
  echo "Brave:   OK   http://127.0.0.1:9222  (preferred)"
else
  echo "Brave:   DOWN  start with: ./bin/brave-debug.sh"
fi
curl -fsS -o /dev/null -w "UI:      %{http_code}  http://127.0.0.1:8080\n" http://127.0.0.1:8080/ || echo "UI:      down"
curl -fsS -o /dev/null -w "Browser: %{http_code}  http://127.0.0.1:3000 (fallback)\n" http://127.0.0.1:3000/json/version || echo "Browser: down"
