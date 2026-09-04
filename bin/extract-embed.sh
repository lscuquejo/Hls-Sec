#!/usr/bin/env bash
# Extract embed from a Hotmart club URL.
#
# Recommended (your real Brave login):
#   Terminal 1:  ./bin/brave-debug.sh          # quit Brave first
#   Terminal 2:  ./bin/extract-embed.sh --cdp http://127.0.0.1:9222 'CLUB_URL'
#
# Dedicated profile:
#   ./bin/extract-embed.sh --login 'CLUB_URL'

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUN_CLIP=0
RUN_FULL=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --run-clip) RUN_CLIP=1 ;;
    --run-full) RUN_FULL=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  playwright install chromium
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# If user still passes --existing-brave, rewrite to the CDP workflow hint via python exit 2
set +e
EMBED_SRC="$(python3 bin/extract-embed.py "${ARGS[@]}")"
rc=$?
set -e
if [[ $rc -eq 2 ]]; then
  exit 2
fi
if [[ $rc -ne 0 ]]; then
  exit "$rc"
fi

echo "$EMBED_SRC"

if [[ "$RUN_CLIP" -eq 1 || "$RUN_FULL" -eq 1 ]]; then
  echo "$EMBED_SRC" | ./bin/hotmart-embed-to-exports.sh >/dev/null
  # shellcheck disable=SC1091
  source exports/hotmart-exports.sh
  if [[ "$RUN_CLIP" -eq 1 ]]; then
    SECONDS_CLIP="${SECONDS_CLIP:-5}" ./bin/fast-hls-security-test.sh clip
  else
    TIMEOUT_SEC="${TIMEOUT_SEC:-3600}" QUALITY="${QUALITY:-lowest}" ./bin/download-hls.sh
  fi
fi
