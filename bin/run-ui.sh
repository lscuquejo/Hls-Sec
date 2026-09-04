#!/usr/bin/env bash
# Start the UI with the project venv (avoids Homebrew uvicorn / wrong Python).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  PY="${PYTHON:-}"
  if [[ -z "$PY" ]]; then
    if command -v python3.13 >/dev/null; then PY=python3.13
    elif command -v python3.12 >/dev/null; then PY=python3.12
    else PY=python3
    fi
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

echo "Python: $(python -V) @ $(which python)"
echo "Open http://127.0.0.1:8080  (Brave CDP: start ./bin/brave-debug.sh first)"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
