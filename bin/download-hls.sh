#!/usr/bin/env bash
# Full HLS download using export env vars. Default timeout: 1 hour.
#
#   source exports/lesson.sh
#   ./bin/download-hls.sh
#   TIMEOUT_SEC=3600 QUALITY=lowest ./bin/download-hls.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT/out/downloads}"
TIMEOUT_SEC="${TIMEOUT_SEC:-5400}"
QUALITY="${QUALITY:-lowest}"   # lowest | highest
mkdir -p "$OUT_DIR"

JWT="${JWT:-${jwt:-}}"
MEDIA="${MEDIA:-${media:-}}"
APP="${APP:-${app:-}}"
USER_CODE="${USER_CODE:-${user_code:-}}"
USER_ID="${USER_ID:-${user_id:-${user:-}}}"
REF="${REF:-${ref:-}}"
EMBED="${EMBED:-${embed:-}}"
ORIGIN="${ORIGIN:-https://hotmart.com}"
REFERER_HEADER="${REFERER_HEADER:-https://cf-embed.play.hotmart.com/}"

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need curl
need python3
need ffmpeg

if [[ -z "${JWT}" && -z "${EMBED}" ]]; then
  echo "Missing JWT/EMBED — source an exports file first" >&2
  exit 1
fi
: "${REF:?set REF}"

html="$OUT_DIR/${MEDIA:-embed}-player.html"

if [[ -n "${EMBED}" ]]; then
  embed_url="$EMBED"
else
  embed_url="https://cf-embed.play.hotmart.com/embed/${MEDIA}?applicationCode=${APP}&userCode=${USER_CODE}&jwtToken=${JWT}&user=${USER_ID}&autoplay=false&locale=en"
fi

echo "fetching player HTML..."
curl -sS -o "$html" -H "Referer: $REF" -H "Origin: $ORIGIN" "$embed_url"

TITLE_FILE="$OUT_DIR/${MEDIA:-embed}-title.txt"
set +e
MASTER_URL="$(
  HTML_PATH="$html" TITLE_FILE="$TITLE_FILE" VIDEO_TITLE="${VIDEO_TITLE:-}" python3 - <<'PY'
import re, json, os, sys, html as htmllib, unicodedata

raw = open(os.environ["HTML_PATH"], encoding="utf-8", errors="ignore").read()
title = re.search(r"<title[^>]*>(.*?)</title>", raw)
t = htmllib.unescape(title.group(1).strip()) if title else ""
print(f"embed_title: {t}", file=sys.stderr)
if re.search(r"\b(400|401|403|404)\b", t):
    sys.exit(2)

# Prefer title/description from __NEXT_DATA__ when present
pretty = t
m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw)
if not m:
    sys.exit(3)
data = json.loads(m.group(1))
s = json.dumps(data)

def dig(obj):
    if isinstance(obj, dict):
        for k in ("description", "title", "name"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip() and "http" not in v.lower():
                return v.strip()
        for v in obj.values():
            found = dig(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = dig(v)
            if found:
                return found
    return None

from_json = dig(data)
env_title = (os.environ.get("VIDEO_TITLE") or "").strip()
pretty = env_title or from_json or t or os.environ.get("MEDIA") or "video"
# drop generic Hotmart page titles
if re.search(r"\b(400|401|403|404)\b", pretty) or pretty.lower() in ("hotmart", "player"):
    pretty = from_json or os.environ.get("MEDIA") or "video"

pretty = unicodedata.normalize("NFKD", pretty)
pretty = pretty.encode("ascii", "ignore").decode("ascii")
safe = re.sub(r"[^A-Za-z0-9._-]+", "_", pretty).strip("._-")
safe = safe[:120] or "video"
open(os.environ["TITLE_FILE"], "w", encoding="utf-8").write(safe)

url = re.search(r'https://vod-akm\.play\.hotmart\.com/video/[^"\s]+m3u8[^"\s]*', s)
if not url:
    url = re.search(r'https://[^"\s]+\.m3u8[^"\s]*', s)
if not url:
    sys.exit(4)
print(url.group(0).encode("utf-8").decode("unicode_escape"))
PY
)"
rc=$?
set -e
if [[ $rc -ne 0 || -z "${MASTER_URL}" ]]; then
  echo "failed to extract playlist (bad JWT/REF/EMBED?)" >&2
  exit 1
fi

SAFE_NAME="$(cat "$TITLE_FILE" 2>/dev/null || true)"
SAFE_NAME="${SAFE_NAME:-${MEDIA:-video}}"

echo "master: ${MASTER_URL%%\?*}"
MASTER_FILE="$OUT_DIR/${SAFE_NAME}-master.m3u8"
curl -sS -o "$MASTER_FILE" -A "Mozilla/5.0" -H "Referer: $REFERER_HEADER" "$MASTER_URL"

VARIANT_REL="$(
  MASTER_FILE="$MASTER_FILE" QUALITY="$QUALITY" python3 - <<'PY'
import os, re
text = open(os.environ["MASTER_FILE"], encoding="utf-8", errors="ignore").read().splitlines()
quality = os.environ.get("QUALITY", "lowest")
variants = []
bw = None
for line in text:
    if line.startswith("#EXT-X-STREAM-INF:"):
        m = re.search(r"BANDWIDTH=(\d+)", line)
        bw = int(m.group(1)) if m else 10**18
    elif bw is not None and line and not line.startswith("#"):
        variants.append((bw, line.strip()))
        bw = None
if not variants:
    print("")
else:
    variants.sort(key=lambda x: x[0])
    print(variants[0][1] if quality == "lowest" else variants[-1][1])
PY
)"

if [[ -n "$VARIANT_REL" ]]; then
  VARIANT_URL="$(
    MASTER_URL="$MASTER_URL" VARIANT_REL="$VARIANT_REL" python3 - <<'PY'
from urllib.parse import urljoin
import os
print(urljoin(os.environ["MASTER_URL"], os.environ["VARIANT_REL"]))
PY
  )"
else
  VARIANT_URL="$MASTER_URL"
fi

echo "variant: ${VARIANT_URL%%\?*}"
OUT_MP4="$OUT_DIR/${SAFE_NAME}.mp4"
echo "title/file: $SAFE_NAME"
echo "downloading → $OUT_MP4 (timeout ${TIMEOUT_SEC}s, quality=$QUALITY)"

set +e
FFMPEG_COMMON=(
  ffmpeg -hide_banner -loglevel error -nostats -progress pipe:1 -y
  -headers $'Referer: https://cf-embed.play.hotmart.com/\r\nUser-Agent: Mozilla/5.0\r\n'
  -i "$VARIANT_URL"
  -c copy
  -bsf:a aac_adtstoasc
  "$OUT_MP4"
)
if command -v timeout >/dev/null; then
  timeout --signal=INT "${TIMEOUT_SEC}" "${FFMPEG_COMMON[@]}"
  rc=$?
else
  "${FFMPEG_COMMON[@]}"
  rc=$?
fi
set -e

if [[ $rc -eq 124 ]]; then
  echo "TIMEOUT after ${TIMEOUT_SEC}s — partial file may exist: $OUT_MP4" >&2
  exit 124
elif [[ $rc -ne 0 ]]; then
  exit "$rc"
fi

ls -lh "$OUT_MP4"
echo "DONE $OUT_MP4"
