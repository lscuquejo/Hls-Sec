#!/usr/bin/env python3
"""Extract Hotmart player embed src using a persistent browser profile (your login).

First run (login once):
  ./bin/extract-embed.py --login 'https://hotmart.com/es/club/.../content/...'

Later runs (reuse cookies):
  ./bin/extract-embed.py 'https://hotmart.com/es/club/.../content/...'
  ./bin/extract-embed.py URL --json
  ./bin/extract-embed.py URL | ./bin/hotmart-embed-to-exports.sh
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / ".playwright-profile"


def extract(url: str, profile: Path, headed: bool, timeout_ms: int, wait_login: bool) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "Playwright not installed. Run:\n"
            "  pip install playwright && playwright install chromium"
        ) from e

    profile.mkdir(parents=True, exist_ok=True)
    result: dict = {"club_url": url, "embed_src": None, "title": None, "error": None}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not headed,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # Wait for either player iframe or login form
        deadline = time.time() + (timeout_ms / 1000)
        embed = None
        while time.time() < deadline:
            # common selectors
            for sel in (
                "iframe#hotmart-player-embed",
                "iframe[src*='cf-embed.play.hotmart.com/embed/']",
                "iframe[src*='play.hotmart.com/embed/']",
            ):
                loc = page.locator(sel).first
                if loc.count() > 0:
                    src = loc.get_attribute("src")
                    if src and "jwtToken=" in src:
                        embed = src
                        result["title"] = loc.get_attribute("title")
                        break
            if embed:
                break

            # still on login?
            if wait_login:
                loginish = page.locator("input[type='password'], text=Iniciar sesión, text=Log in")
                if loginish.count() > 0:
                    print(
                        "Login required — log in in the opened window. Waiting for player…",
                        file=sys.stderr,
                    )
                    # give user time; keep polling
                    page.wait_for_timeout(2000)
                    continue

            page.wait_for_timeout(1000)

        if not embed:
            # last chance: scan all iframes
            for frame_el in page.locator("iframe").all():
                src = frame_el.get_attribute("src") or ""
                if "cf-embed.play.hotmart.com/embed/" in src and "jwtToken=" in src:
                    embed = src
                    result["title"] = frame_el.get_attribute("title")
                    break

        context.close()

    if not embed:
        result["error"] = "Player iframe not found. Are you logged in? Try --login --headed"
        return result

    result["embed_src"] = embed
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract Hotmart embed src with saved login")
    ap.add_argument("url", help="Hotmart club lesson URL")
    ap.add_argument(
        "--profile",
        default=str(DEFAULT_PROFILE),
        help=f"Persistent browser profile dir (default: {DEFAULT_PROFILE})",
    )
    ap.add_argument("--login", action="store_true", help="Headed mode; wait for manual login")
    ap.add_argument("--headed", action="store_true", help="Show browser window")
    ap.add_argument("--headless", action="store_true", help="Force headless")
    ap.add_argument("--timeout", type=int, default=120000, help="Timeout ms (default 120000)")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of bare URL")
    args = ap.parse_args()

    headed = True if args.login else (False if args.headless else args.headed or args.login)
    if args.login:
        headed = True

    data = extract(
        url=args.url,
        profile=Path(args.profile),
        headed=headed,
        timeout_ms=args.timeout,
        wait_login=args.login or headed,
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
