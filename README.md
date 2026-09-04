# hls-security-probe

Local tools + **Docker UI** to test video delivery protection (HLS + token auth).

Paste a **club lesson URL** (uses your saved Hotmart login) or an embed iframe → auto exports → probe / clip / **full download (1h timeout)**.

Use only on content/servers you are authorized to test.

## Automate embed extraction (your login)

### One-time setup

```bash
cd ~/studies/hls-security-probe
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Login once (browser window opens)

```bash
./bin/extract-embed.sh --login \
  'https://hotmart.com/es/club/direito-cacd/products/2039476/content/64lK6nWbOj'
```

Log into Hotmart in that window. Profile is saved in `.playwright-profile/` (gitignored).

### Later lessons (reuse login)

```bash
# print embed URL
./bin/extract-embed.sh 'https://hotmart.com/es/club/.../content/PAGE_HASH'

# extract → exports → 5s clip
./bin/extract-embed.sh 'https://hotmart.com/es/club/.../content/PAGE_HASH' --run-clip

# extract → exports → full download (1h)
TIMEOUT_SEC=3600 ./bin/extract-embed.sh 'URL' --run-full
```

### Bookmarklet (on a page you’re already viewing)

Drag **Copy Hotmart Embed** from the UI, or use `examples/bookmarklet.js`, onto your bookmarks bar. On the lesson page, click it → embed URL is copied → paste into the UI.

## Quick start (UI on host — needed for browser login)

```bash
cd ~/studies/hls-security-probe
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open **http://localhost:8080**

1. Paste club URL → **Extract embed (saved login)**
2. Or paste iframe / embed URL
3. **Run download** (default timeout 3600s)

## Docker UI (download/parse only)

Browser login extraction works best **on the host** (Playwright needs a GUI profile). Docker still works if you paste the embed/iframe:

```bash
docker compose up --build
```

## CLI without UI

```bash
pbpaste | ./bin/hotmart-embed-to-exports.sh
source exports/hotmart-exports.sh
SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip
TIMEOUT_SEC=3600 QUALITY=lowest ./bin/download-hls.sh
```

## Layout

```
bin/extract-embed.py|.sh   # club URL → embed (Playwright profile)
bin/download-hls.sh
bin/fast-hls-security-test.sh
bin/hotmart-embed-to-exports.sh
app/                       # FastAPI UI
.playwright-profile/       # your Hotmart cookies (gitignored)
exports/ out/
```

## Notes

- JWT must be full `eyJ...`
- `.playwright-profile` and `exports/*.sh` contain secrets — gitignored
- If extract fails: run `--login` again (session expired)
# Hls-Sec
