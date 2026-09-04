#!/usr/bin/env bash
# One-time setup: after this, manage everything from http://127.0.0.1:8080
#
#  - Stops Docker UI on :8080 (avoids port clash)
#  - Ensures Python venv
#  - Installs macOS LaunchAgents (UI + Brave) → survive reboot/login
#  - Opens the UI
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p out/logs out/pids

echo "==> Freeing port 8080 (stop Docker UI if present)..."
docker compose stop probe 2>/dev/null || true
# If something else holds 8080, warn
if lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then
  if ! curl -fsS -m 1 http://127.0.0.1:8080/api/config >/dev/null 2>&1; then
    echo "Port 8080 is in use by something else. Stop it, then re-run." >&2
    lsof -nP -iTCP:8080 -sTCP:LISTEN || true
    exit 1
  fi
fi

echo "==> Ensuring Python venv..."
if [[ ! -x .venv/bin/python ]]; then
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
fi

echo "==> Installing LaunchAgents (UI + Brave at login)..."
"$ROOT/bin/install-autostart.sh"

# Wait for UI
for _ in $(seq 1 40); do
  if curl -fsS -m 1 http://127.0.0.1:8080/api/config >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

echo
echo "Ready — use only the UI from now on:"
echo "  http://127.0.0.1:8080"
echo
echo "In the UI:"
echo "  • Restart Brave  → quit/reopen Brave with debugging"
echo "  • Download       → extract + download lesson"
echo
echo "After reboot: log into macOS; services come back. Open the UI URL again."
echo "Disable: ./bin/uninstall-autostart.sh"

if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:8080" || true
fi
