#!/usr/bin/env python3
"""Tiny host-side control plane so the Docker UI can restart Brave.

  ./bin/host-control.sh          # start (background)
  curl -X POST http://127.0.0.1:9277/restart-brave
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("HOST_CONTROL_PORT", "9277"))
LOG_DIR = ROOT / "out" / "logs"
PID_DIR = ROOT / "out" / "pids"
BRAVE_SCRIPT = ROOT / "bin" / "brave-debug.sh"
BRAVE_PID_FILE = PID_DIR / "brave.pid"
BRAVE_LOG = LOG_DIR / "brave-debug.log"


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)


def brave_cdp_up(port: int = 9222) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def restart_brave() -> dict:
    ensure_dirs()
    if not BRAVE_SCRIPT.exists():
        return {"ok": False, "error": f"missing {BRAVE_SCRIPT}"}

    # Stop previous nohup brave-debug / browser if we tracked a pid
    if BRAVE_PID_FILE.exists():
        try:
            old = int(BRAVE_PID_FILE.read_text().strip())
            os.kill(old, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass

    # brave-debug.sh also pkill's Brave; give it a clean slate
    log_f = BRAVE_LOG.open("ab", buffering=0)
    proc = subprocess.Popen(
        ["bash", str(BRAVE_SCRIPT)],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    BRAVE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    # Wait until CDP answers (Brave replaces the shell via exec, so pid may change)
    ready = False
    for _ in range(40):
        time.sleep(0.25)
        if brave_cdp_up():
            ready = True
            break

    return {
        "ok": True,
        "pid": proc.pid,
        "cdp_ready": ready,
        "cdp": "http://127.0.0.1:9222",
        "log": str(BRAVE_LOG.relative_to(ROOT)),
        "hint": "Log into Hotmart in the Brave window, then retry Download.",
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("", "/health"):
            self._json(
                200,
                {
                    "ok": True,
                    "service": "hls-security-probe-host-control",
                    "brave_cdp": brave_cdp_up(),
                },
            )
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") == "/restart-brave":
            result = restart_brave()
            self._json(200 if result.get("ok") else 500, result)
            return
        self._json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[host-control] " + (fmt % args) + "\n")


def main() -> int:
    ensure_dirs()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"host-control listening on http://127.0.0.1:{PORT}", flush=True)
    print("  POST /restart-brave", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
