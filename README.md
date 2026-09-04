# hls-security-probe

Paste a Hotmart **club lesson link** → manage everything from the UI.

## One-time setup (then only use the UI)

```bash
cd ~/studies/hls-security-probe
./bin/enable-ui.sh
```

That installs macOS LaunchAgents (UI + Brave). After reboot/login they come back automatically.

Then open **http://127.0.0.1:8080** and stay there:

| UI control | What it does |
|---|---|
| **Restart Brave** | Quits Brave and starts it with debugging |
| **Download** | Extracts embed via Brave + downloads the lesson |
| **Refresh status** | Re-checks Brave CDP |

No more terminal for daily use. Disable with `./bin/uninstall-autostart.sh`.

## Optional: Docker UI

Only if you prefer containers (Restart Brave needs `./bin/host-control.sh` or `./bin/enable-ui.sh` instead):

```bash
./bin/docker-up.sh
```

## Automate embed extraction (CLI)

### One-time setup

```bash
cd ~/studies/hls-security-probe
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Login once (Brave window opens)

```bash
./bin/extract-embed.sh --login \
  'https://hotmart.com/es/club/direito-cacd/products/2039476/content/64lK6nWbOj'
```

Log into Hotmart in Brave. Profile saved in `.playwright-brave-profile/`.

### Reuse your real Brave login (recommended)

`--existing-brave` often **hangs** on full profiles. Use CDP instead:

```bash
# Terminal 1 — quit Brave completely first
./bin/brave-debug.sh

# Terminal 2
./bin/extract-embed.sh --cdp http://127.0.0.1:9222 \
  'https://hotmart.com/es/club/direito-cacd/products/2039476/content/64lK6nWbOj'
```

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
