#!/usr/bin/env bash
# Wrapper: extract embed from a Hotmart club URL using your saved login profile.
#
# First time (login in the browser window that opens):
#   ./bin/extract-embed.sh --login 'https://hotmart.com/es/club/.../content/...'
#
# Later:
#   ./bin/extract-embed.sh 'https://hotmart.com/es/club/.../content/...'
#   ./bin/extract-embed.sh URL | ./bin/hotmart-embed-to-exports.sh
#   ./bin/extract-embed.sh URL --run-clip

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

EMBED_SRC="$(python3 bin/extract-embed.py "${ARGS[@]}")"
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
