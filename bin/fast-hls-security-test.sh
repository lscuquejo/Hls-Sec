#!/usr/bin/env bash
# Fast security probe for HLS + AES-128 (not full-video rip).
# Proves: authorized client can obtain playable media without a direct .mp4 URL.
#
# Modes:
#   probe  - playlist + key + first segment only (~1-3s)
#   clip   - short local sample via ffmpeg (default)
#
# Usage (Hotmart-style embed parts):
#   source exports/lesson.sh
#   SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip
#
# Usage (direct playlist / your local server):
#   PLAYLIST_URL='http://localhost:8080/video/master.m3u8' \
#     ./bin/fast-hls-security-test.sh clip

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-clip}"          # probe | clip
SECONDS_CLIP="${SECONDS_CLIP:-5400}"
OUT_DIR="${OUT_DIR:-$ROOT/out/hls-security-test}"
mkdir -p "$OUT_DIR"

# Lowercase aliases only work if those vars were exported (or set inline).
JWT="${JWT:-${jwt:-}}"
MEDIA="${MEDIA:-${media:-}}"
APP="${APP:-${app:-${applicationCode:-}}}"
USER_CODE="${USER_CODE:-${user_code:-${userCode:-}}}"
USER_ID="${USER_ID:-${user_id:-${userId:-${user:-}}}}"
REF="${REF:-${ref:-${referer:-}}}"
PLAYLIST_URL="${PLAYLIST_URL:-${playlist_url:-${playlist:-}}}"
EMBED="${EMBED:-${embed:-}}"
ORIGIN="${ORIGIN:-${origin:-https://hotmart.com}}"
REFERER_HEADER="${REFERER_HEADER:-https://cf-embed.play.hotmart.com/}"

need() { command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }; }
need curl
need python3

resolve_playlist() {
  if [[ -n "${PLAYLIST_URL}" ]]; then
    echo "$PLAYLIST_URL"
    return
  fi

  if [[ -z "${JWT}" && -z "${EMBED}" ]]; then
    cat >&2 <<'ERR'
Missing JWT/EMBED/PLAYLIST_URL in this process.

Most common cause: vars set but not exported.
  source exports/<lesson>.sh
  SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip

Or generate exports from an embed URL:
  pbpaste | ./bin/hotmart-embed-to-exports.sh
  source exports/hotmart-exports.sh
ERR
    exit 1
  fi

  local html
  : "${REF:?set REF/ref (club/page referer)}"
  html="$OUT_DIR/embed.html"

  fetch_embed() {
    local url="$1"
    curl -sS -o "$html" \
      -H "Referer: $REF" \
      -H "Origin: ${ORIGIN}" \
      "$url"
  }

  built=""
  if [[ -n "${MEDIA}" && -n "${APP}" && -n "${USER_CODE}" && -n "${USER_ID}" && -n "${JWT}" ]]; then
    if [[ ${#JWT} -lt 100 ]]; then
      echo "JWT looks invalid (len=${#JWT}). Re-export the full eyJ... token, not '...'" >&2
      exit 1
    fi
    built="https://cf-embed.play.hotmart.com/embed/${MEDIA}?applicationCode=${APP}&userCode=${USER_CODE}&jwtToken=${JWT}&user=${USER_ID}&autoplay=false&locale=en"
  fi

  if [[ -n "${EMBED}" ]]; then
    echo "trying EMBED..." >&2
    fetch_embed "$EMBED"
  elif [[ -n "$built" ]]; then
    echo "trying built URL from JWT/MEDIA/APP/..." >&2
    fetch_embed "$built"
  else
    echo "need EMBED or JWT+MEDIA+APP+USER_CODE+USER_ID" >&2
    exit 1
  fi

  extract_playlist() {
    HTML_PATH="$html" EMBED_DEBUG="${1:-}" python3 - <<'PY'
import re, json, os, sys
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

raw = open(os.environ["HTML_PATH"], encoding="utf-8", errors="ignore").read()
title_m = re.search(r"<title[^>]*>(.*?)</title>", raw)
title = title_m.group(1) if title_m else "(no title)"
print(f"embed_title: {title}", file=sys.stderr)

u = urlparse(os.environ.get("EMBED_DEBUG", ""))
qs = parse_qs(u.query, keep_blank_values=True)
for secret in ("jwtToken", "jwt", "token"):
    if secret in qs and qs[secret] and qs[secret][0]:
        v = qs[secret][0]
        qs[secret] = [v[:16] + f"...({len(v)} chars)"]
if u.netloc:
    safe = urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode({k: v[0] for k, v in qs.items()}), u.fragment))
    print(f"embed_url: {safe}", file=sys.stderr)
    print(f"query_keys: {sorted(qs.keys())}", file=sys.stderr)

if re.search(r"\b(400|401|403|404)\b", title):
    sys.exit(2)

m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw)
if not m:
    sys.exit(3)
s = json.dumps(json.loads(m.group(1)))
url = re.search(r'https://vod-akm\.play\.hotmart\.com/video/[^"\s]+m3u8[^"\s]*', s)
if not url:
    url = re.search(r'https://[^"\s]+\.m3u8[^"\s]*', s)
if not url:
    sys.exit(4)
print(url.group(0).encode("utf-8").decode("unicode_escape"))
PY
  }

  set +e
  playlist="$(extract_playlist "${EMBED:-$built}")"
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    echo "$playlist"
    return
  fi

  # Bad EMBED is common (truncated). Retry with JWT-built URL once.
  if [[ -n "${EMBED}" && -n "$built" && "$rc" -eq 2 ]]; then
    echo "EMBED returned error page — retrying with JWT-built URL..." >&2
    fetch_embed "$built"
    set +e
    playlist="$(extract_playlist "$built")"
    rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then
      echo "$playlist"
      return
    fi
  fi

  case "$rc" in
    2) echo "embed page is an error (400/401/...) — fix EMBED or JWT/REF" >&2 ;;
    3) echo "no __NEXT_DATA__ in embed HTML" >&2 ;;
    4) echo "no m3u8 in embed HTML" >&2 ;;
    *) echo "playlist extract failed (rc=$rc)" >&2 ;;
  esac
  return 1
}

pick_lowest_variant() {
  local master="$1"
  MASTER_FILE="$master" python3 - <<'PY'
import os, re
text = open(os.environ["MASTER_FILE"], encoding="utf-8", errors="ignore").read().splitlines()
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
    print(variants[0][1])
PY
}

MASTER_URL="$(resolve_playlist | awk 'NF{u=$0} END{print u}')"
echo "master: ${MASTER_URL%%\?*}"

MASTER_FILE="$OUT_DIR/master.m3u8"
curl -sS -o "$MASTER_FILE" -A "Mozilla/5.0" \
  -H "Referer: ${REFERER_HEADER}" \
  "$MASTER_URL"

if grep -q "AES-128" "$MASTER_FILE"; then
  echo "encryption: AES-128 (client-delivered key — not DRM)"
else
  echo "encryption: none/other (inspect playlist)"
fi
if grep -qiE "widevine|fairplay|playready|SAMPLE-AES|KEYFORMAT" "$MASTER_FILE"; then
  echo "WARNING: real DRM hints found — clip mode may fail (expected for strong protection)"
fi

VARIANT_REL="$(pick_lowest_variant "$MASTER_FILE")"
if [[ -n "$VARIANT_REL" ]]; then
  VARIANT_URL="$(
    MASTER_URL="$MASTER_URL" VARIANT_REL="$VARIANT_REL" python3 - <<'PY'
from urllib.parse import urljoin
import os
print(urljoin(os.environ["MASTER_URL"], os.environ["VARIANT_REL"]))
PY
  )"
  echo "variant(lowest): ${VARIANT_URL%%\?*}"
else
  VARIANT_URL="$MASTER_URL"
  echo "variant: master is already a media playlist"
fi

VARIANT_FILE="$OUT_DIR/variant.m3u8"
curl -sS -o "$VARIANT_FILE" -A "Mozilla/5.0" \
  -H "Referer: ${REFERER_HEADER}" \
  "$VARIANT_URL"

VARIANT_FILE="$VARIANT_FILE" VARIANT_URL="$VARIANT_URL" python3 - <<'PY' > "$OUT_DIR/assets.txt"
from urllib.parse import urljoin
import os, re
text = open(os.environ["VARIANT_FILE"], encoding="utf-8", errors="ignore").read().splitlines()
base = os.environ["VARIANT_URL"]
key = None
seg = None
for line in text:
    if line.startswith("#EXT-X-KEY:") and "URI=" in line:
        m = re.search(r'URI="([^"]+)"', line)
        if m:
            key = urljoin(base, m.group(1))
    if not line.startswith("#") and line.strip() and seg is None:
        seg = urljoin(base, line.strip())
print(key or "")
print(seg or "")
PY

KEY_URL="$(sed -n '1p' "$OUT_DIR/assets.txt")"
SEG_URL="$(sed -n '2p' "$OUT_DIR/assets.txt")"

if [[ -n "$KEY_URL" ]]; then
  echo "key: ${KEY_URL%%\?*}"
  curl -sS -o "$OUT_DIR/seg.key" -A "Mozilla/5.0" \
    -H "Referer: ${REFERER_HEADER}" \
    "$KEY_URL"
  echo "key_bytes: $(wc -c < "$OUT_DIR/seg.key" | tr -d ' ')"
fi

if [[ -z "$SEG_URL" ]]; then
  echo "no segment found in variant playlist" >&2
  exit 1
fi
echo "segment: ${SEG_URL%%\?*}"
curl -sS -o "$OUT_DIR/seg0.ts" -A "Mozilla/5.0" \
  -H "Referer: ${REFERER_HEADER}" \
  "$SEG_URL"
echo "segment_bytes: $(wc -c < "$OUT_DIR/seg0.ts" | tr -d ' ')"

if [[ "$MODE" == "probe" ]]; then
  echo
  echo "PROBE OK — playlist/key/segment are fetchable with current auth."
  echo "artifacts: $OUT_DIR"
  exit 0
fi

need ffmpeg
SAFE_NAME="$(
  VIDEO_TITLE="${VIDEO_TITLE:-}" MEDIA="${MEDIA:-}" SECONDS_CLIP="$SECONDS_CLIP" python3 - <<'PY'
import os, re, unicodedata
pretty = (os.environ.get("VIDEO_TITLE") or "").strip() or os.environ.get("MEDIA") or "clip"
# ASCII-safe filename (accents → plain letters)
pretty = unicodedata.normalize("NFKD", pretty)
pretty = pretty.encode("ascii", "ignore").decode("ascii")
safe = re.sub(r"[^A-Za-z0-9._-]+", "_", pretty).strip("._-")
safe = (safe[:100] or "clip")
print(f"{safe}-{os.environ.get('SECONDS_CLIP', 'clip')}s")
PY
)"
OUT_MP4="$OUT_DIR/${SAFE_NAME}.mp4"
echo
echo "clip: fetching ${SECONDS_CLIP}s → $OUT_MP4"
set +e
# -progress pipe:1 emits out_time_ms=... lines for the UI progress bar
ffmpeg -hide_banner -loglevel error -nostats -progress pipe:1 -y \
  -headers $'Referer: https://cf-embed.play.hotmart.com/\r\nUser-Agent: Mozilla/5.0\r\n' \
  -i "$VARIANT_URL" \
  -t "$SECONDS_CLIP" \
  -c copy \
  -bsf:a aac_adtstoasc \
  "$OUT_MP4"
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  echo "ffmpeg clip failed (exit $rc)" >&2
  exit "$rc"
fi
if [[ ! -f "$OUT_MP4" ]]; then
  echo "ffmpeg reported OK but file missing: $OUT_MP4" >&2
  exit 1
fi

ls -lh "$OUT_MP4"
echo
echo "CLIP OK — playable sample written (download path confirmed)."
echo "open $OUT_MP4"
