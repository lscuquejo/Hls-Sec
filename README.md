# hls-security-probe

Local tools to **test video delivery protection** (HLS + token auth).

Goal: show that “no `.mp4` in DevTools → Media” does **not** mean anti-download when:

- `playDrm: false`
- playback uses signed HLS (`.m3u8` + segments)
- AES-128 keys are delivered to the client

Use only on content/servers you are authorized to test (your own stack, or your own purchased session for security research).

## Layout

```
bin/fast-hls-security-test.sh   # probe | clip
bin/hotmart-embed-to-exports.sh # embed URL → export vars file
exports/                        # generated lesson exports (gitignored)
out/                            # sample clips / playlists (gitignored)
examples/                       # local-server example
```

## Dependencies

```bash
brew install ffmpeg
# curl + python3 already on macOS/Homebrew
```

## Quick start (Hotmart-style embed)

1. Copy the full `cf-embed.play.hotmart.com/embed/...` URL from the player iframe `src`.
2. Generate exports:

```bash
cd ~/studies/hls-security-probe
pbpaste | ./bin/hotmart-embed-to-exports.sh
# or named file:
# OUT=exports/parte03.sh pbpaste | ./bin/hotmart-embed-to-exports.sh
```

3. Load vars and run a **5s** clip test:

```bash
source exports/hotmart-exports.sh
SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip
open out/hls-security-test/sample-5s.mp4
```

Faster auth-only check (no ffmpeg remux):

```bash
source exports/hotmart-exports.sh
./bin/fast-hls-security-test.sh probe
```

### Required vars

| Var | Meaning |
|-----|---------|
| `JWT` | player `jwtToken` |
| `MEDIA` | media code (`/embed/<MEDIA>`) |
| `APP` | `applicationCode` |
| `USER_CODE` | `userCode` |
| `USER_ID` | `user` |
| `REF` | club lesson page URL (Referer) |
| `EMBED` | optional full embed URL |
| `PLAYLIST_URL` | skip Hotmart; hit playlist directly (local server) |

## Test your own server

```bash
source examples/local-playlist.env.sh
# edit PLAYLIST_URL first
SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip
```

## How to read results

| Result | Meaning |
|--------|---------|
| `playDrm: False` + `CLIP OK` | clear/soft-encrypted HLS is obtainable with a valid session |
| `PROBE OK` | playlist + key + segment fetchable |
| embed title `400`/`401` | bad/expired JWT, bad Referer, or truncated EMBED |
| Widevine/FairPlay in playlist | stronger DRM — clip may fail (good for protection) |

## Notes

- Keep Mac awake for longer runs: `caffeinate -i SECONDS_CLIP=30 ./bin/fast-hls-security-test.sh clip`
- JWT must be the full `eyJ...` string (`echo ${#JWT}` should be hundreds of chars, not `3`)
- `exports/*.sh` contain secrets — gitignored on purpose
