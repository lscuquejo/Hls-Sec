#!/usr/bin/env bash
# Start Brave with remote debugging so extract-embed can reuse your login.
# Quit Brave first, then run this, then in another terminal run extract.
#
#   ./bin/brave-debug.sh
#   ./bin/extract-embed.sh --cdp http://127.0.0.1:9222 'https://hotmart.com/...'

set -euo pipefail

PORT="${BRAVE_DEBUG_PORT:-9222}"
BRAVE="${BRAVE_PATH:-/Applications/Brave Browser.app/Contents/MacOS/Brave Browser}"
USER_DATA="${BRAVE_USER_DATA:-$HOME/Library/Application Support/BraveSoftware/Brave-Browser}"

if [[ ! -x "$BRAVE" ]]; then
  echo "Brave not found at: $BRAVE" >&2
  echo "Set BRAVE_PATH=/path/to/Brave\\ Browser" >&2
  exit 1
fi

# Clear leftover locks / helpers
pkill -x "Brave Browser" 2>/dev/null || true
pkill -x "Brave Browser Helper" 2>/dev/null || true
pkill -x "Brave Browser Helper (GPU)" 2>/dev/null || true
pkill -x "Brave Browser Helper (Renderer)" 2>/dev/null || true
sleep 1
rm -f "$USER_DATA/SingletonLock" "$USER_DATA/SingletonSocket" "$USER_DATA/SingletonCookie" 2>/dev/null || true

echo "Starting Brave with remote debugging on port $PORT ..."
echo "Keep this Brave window open and log into Hotmart."
echo "Docker UI will auto-detect this CDP (preferred over the debugger)."
echo "CLI: ./bin/extract-embed.sh --cdp http://127.0.0.1:$PORT 'CLUB_URL'"
echo

exec "$BRAVE" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins="*" \
  --user-data-dir="$USER_DATA" \
  --no-first-run \
  --no-default-browser-check \
  "about:blank"
