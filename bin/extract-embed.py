#!/usr/bin/env python3
"""Extract Hotmart player embed src via Brave.

Recommended (reuse your real Brave login):
  # terminal 1 — quit Brave first
  ./bin/brave-debug.sh

  # terminal 2
  ./bin/extract-embed.sh --cdp http://127.0.0.1:9222 \\
    'https://hotmart.com/es/club/.../content/...'

Dedicated profile (login once):
  ./bin/extract-embed.py --login URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / ".playwright-brave-profile"

BRAVE_CANDIDATES = [
    Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    Path.home() / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    Path("/usr/bin/brave-browser"),
    Path("/usr/bin/brave-browser-stable"),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_brave() -> Path | None:
    env = os.environ.get("BRAVE_PATH")
    if env and Path(env).exists():
        return Path(env)
    for p in BRAVE_CANDIDATES:
        if p.exists():
            return p
    return None


def looks_like_login(page) -> bool:
    try:
        if page.locator("input[type='password']").count() > 0:
            return True
        for text in ("Iniciar sesión", "Iniciar sesion", "Log in", "Sign in", "Entrar"):
            if page.get_by_text(text, exact=False).count() > 0:
                return True
    except Exception:
        return False
    return False


def find_embed(page) -> tuple[str | None, str | None]:
    for sel in (
        "iframe#hotmart-player-embed",
        "iframe[src*='cf-embed.play.hotmart.com/embed/']",
        "iframe[src*='play.hotmart.com/embed/']",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            src = loc.get_attribute("src")
            if src and "jwtToken=" in src:
                return src, loc.get_attribute("title")
        except Exception:
            continue

    try:
        for frame_el in page.locator("iframe").all():
            src = frame_el.get_attribute("src") or ""
            if "cf-embed.play.hotmart.com/embed/" in src and "jwtToken=" in src:
                return src, frame_el.get_attribute("title")
    except Exception:
        pass
    return None, None


def wait_for_embed(page, timeout_ms: int, wait_login: bool) -> tuple[str | None, str | None]:
    deadline = time.time() + (timeout_ms / 1000)
    warned_login = False
    last_status = 0.0

    while time.time() < deadline:
        embed, title = find_embed(page)
        if embed:
            return embed, title

        now = time.time()
        if now - last_status > 5:
            remaining = int(deadline - now)
            log(f"… waiting for player iframe ({remaining}s left) url={page.url[:80]}")
            last_status = now

        if wait_login and looks_like_login(page):
            if not warned_login:
                log("Login wall detected — log in in Brave, then open the lesson if needed.")
                warned_login = True
            page.wait_for_timeout(2000)
            continue

        page.wait_for_timeout(1000)

    return None, None


def resolve_cdp_endpoint(cdp: str) -> str:
    """Normalize CDP URL for Brave and Browserless.

    - Rewrites host.docker.internal → IP (Chrome rejects that Host header)
    - Browserless /json/version returns webSocketDebuggerUrl with host 0.0.0.0,
      which makes Playwright's connect_over_cdp(http://...) fail with ECONNREFUSED.
    """
    import socket
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse, urlunparse

    def rewrite_docker_host(url: str) -> str:
        parsed = urlparse(url.strip())
        host = parsed.hostname
        if host == "host.docker.internal" or (host and host.endswith(".internal") and not host.replace(".", "").isdigit()):
            try:
                ip = socket.gethostbyname(host)
            except OSError:
                return url
            port = parsed.port
            netloc = f"{ip}:{port}" if port else ip
            return urlunparse(parsed._replace(netloc=netloc))
        return url

    cdp = rewrite_docker_host(cdp.strip().rstrip("/"))
    if cdp.startswith("ws://") or cdp.startswith("wss://"):
        return cdp

    base = cdp
    version_url = f"{base}/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log(f"CDP /json/version failed ({e}); using endpoint as-is: {cdp}")
        return cdp

    ws = (data.get("webSocketDebuggerUrl") or "").strip()
    if not ws:
        return cdp

    src = urlparse(cdp)
    ws_p = urlparse(ws)
    host = src.hostname or "127.0.0.1"
    if ws_p.hostname in (None, "0.0.0.0", "127.0.0.1", "localhost") and host not in (
        "127.0.0.1",
        "localhost",
    ):
        port = src.port or ws_p.port
        netloc = f"{host}:{port}" if port else host
        ws = urlunparse(ws_p._replace(netloc=netloc))
    elif ws_p.hostname == "0.0.0.0":
        port = src.port or ws_p.port or 3000
        netloc = f"{host}:{port}"
        ws = urlunparse(ws_p._replace(netloc=netloc))

    # If Brave returns ws://127.0.0.1:... but we reached it via Docker host IP,
    # rewrite to that IP so the websocket is reachable from the container.
    if src.hostname and src.hostname not in ("127.0.0.1", "localhost", "0.0.0.0"):
        ws_p2 = urlparse(ws)
        if ws_p2.hostname in ("127.0.0.1", "localhost"):
            port = ws_p2.port or src.port
            netloc = f"{src.hostname}:{port}" if port else src.hostname
            ws = urlunparse(ws_p2._replace(netloc=netloc))

    log(f"Resolved CDP websocket: {ws}")
    return ws


def extract_via_cdp(url: str, cdp: str, timeout_ms: int, wait_login: bool) -> dict:
    from playwright.sync_api import sync_playwright

    result = {
        "club_url": url,
        "embed_src": None,
        "title": None,
        "mode": "cdp",
        "cdp": cdp,
        "error": None,
    }

    endpoint = resolve_cdp_endpoint(cdp)
    log(f"Connecting via CDP: {endpoint}")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(endpoint)
        except Exception as e:
            # Browserless Playwright protocol fallback
            pw_urls = []
            base = cdp.strip().rstrip("/")
            if base.startswith("http://"):
                pw_urls.append("ws://" + base[len("http://") :] + "/chromium/playwright")
            elif base.startswith("https://"):
                pw_urls.append("wss://" + base[len("https://") :] + "/chromium/playwright")
            elif base.startswith("ws://"):
                pw_urls.append(base.rstrip("/") + "/chromium/playwright")
                pw_urls.append(base)

            browser = None
            last_err = e
            for pw_url in pw_urls:
                try:
                    log(f"CDP failed; trying Playwright endpoint: {pw_url}")
                    browser = p.chromium.connect(pw_url)
                    result["mode"] = "playwright"
                    result["cdp"] = pw_url
                    break
                except Exception as e2:
                    last_err = e2
            if browser is None:
                result["error"] = (
                    f"Cannot connect to {cdp}: {last_err}\n"
                    "Docker: ensure the browser service is up (./bin/docker-up.sh)\n"
                    "  Debugger: http://127.0.0.1:3000/debugger/\n"
                    "Host Brave:\n"
                    "  ./bin/brave-debug.sh"
                )
                return result

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        log(f"Opening lesson: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        embed, title = wait_for_embed(page, timeout_ms=timeout_ms, wait_login=wait_login)
        # leave remote browser running; only close the tab we opened
        try:
            page.close()
        except Exception:
            pass

    if not embed:
        result["error"] = (
            "Player iframe not found. Make sure you're logged in "
            "(Docker debugger http://127.0.0.1:3000/debugger/ or host Brave) "
            "and the lesson page shows the video player."
        )
        return result

    result["embed_src"] = embed
    result["title"] = title
    return result


def extract_via_persistent(
    url: str,
    profile: Path,
    headed: bool,
    timeout_ms: int,
    wait_login: bool,
    executable: Path | None,
) -> dict:
    from playwright.sync_api import sync_playwright

    profile.mkdir(parents=True, exist_ok=True)
    result = {
        "club_url": url,
        "embed_src": None,
        "title": None,
        "mode": "persistent",
        "browser": str(executable) if executable else "chromium",
        "profile": str(profile),
        "error": None,
    }

    launch_kwargs = {
        "user_data_dir": str(profile),
        "headless": not headed,
        "viewport": {"width": 1400, "height": 900},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if executable:
        launch_kwargs["executable_path"] = str(executable)

    log(f"Launching Brave persistent profile: {profile}")
    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as e:
            msg = str(e)
            result["error"] = (
                f"Launch failed: {msg}\n"
                "If using your real profile, prefer:\n"
                "  ./bin/brave-debug.sh\n"
                "  ./bin/extract-embed.sh --cdp http://127.0.0.1:9222 URL"
            )
            return result

        page = context.pages[0] if context.pages else context.new_page()
        log(f"Opening lesson: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        embed, title = wait_for_embed(page, timeout_ms=timeout_ms, wait_login=wait_login)
        context.close()

    if not embed:
        result["error"] = (
            "Player iframe not found. Try --login and wait until the video player is visible."
        )
        return result

    result["embed_src"] = embed
    result["title"] = title
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract Hotmart embed src via Brave")
    ap.add_argument("url", help="Hotmart club lesson URL")
    ap.add_argument(
        "--cdp",
        default=None,
        help="Attach to running Brave, e.g. http://127.0.0.1:9222 (recommended)",
    )
    ap.add_argument("--profile", default=None, help=f"Persistent profile (default {DEFAULT_PROFILE})")
    ap.add_argument(
        "--existing-brave",
        action="store_true",
        help="Deprecated/hang-prone. Use ./bin/brave-debug.sh + --cdp instead.",
    )
    ap.add_argument("--chromium", action="store_true", help="Use Playwright Chromium instead of Brave")
    ap.add_argument("--login", action="store_true", help="Headed; wait for manual login")
    ap.add_argument("--headed", action="store_true", help="Show browser window")
    ap.add_argument("--headless", action="store_true", help="Force headless")
    ap.add_argument("--timeout", type=int, default=180000, help="Timeout ms (default 180000)")
    ap.add_argument("--json", action="store_true", help="Print JSON")
    args = ap.parse_args()

    wait_login = bool(args.login or args.headed or args.cdp)

    if args.existing_brave and not args.cdp:
        log(
            "--existing-brave often hangs on full Brave profiles.\n"
            "Use this instead:\n"
            "  1) Quit Brave\n"
            "  2) ./bin/brave-debug.sh\n"
            "  3) ./bin/extract-embed.sh --cdp http://127.0.0.1:9222 'URL'"
        )
        return 2

    if args.cdp:
        data = extract_via_cdp(
            url=args.url,
            cdp=args.cdp,
            timeout_ms=args.timeout,
            wait_login=wait_login,
        )
    else:
        headed = True if args.login else (False if args.headless else (args.headed or args.login))
        if args.login:
            headed = True
        profile = Path(args.profile) if args.profile else DEFAULT_PROFILE
        executable = None if args.chromium else find_brave()
        if not args.chromium and not executable:
            log("Brave not found. Set BRAVE_PATH or pass --chromium")
            return 1
        if executable:
            log(f"Using Brave: {executable}")
        data = extract_via_persistent(
            url=args.url,
            profile=profile,
            headed=headed,
            timeout_ms=args.timeout,
            wait_login=wait_login or headed,
            executable=executable,
        )

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        if data.get("error"):
            print(data["error"], file=sys.stderr)
            return 1
        print(data["embed_src"])
    return 0 if data.get("embed_src") else 1


if __name__ == "__main__":
    raise SystemExit(main())
