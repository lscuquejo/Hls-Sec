#!/usr/bin/env python3
"""Tiny host-side control plane so the Docker UI can restart Brave / transcribe.

  ./bin/host-control.sh          # start (background)
  curl -X POST http://127.0.0.1:9277/restart-brave
  curl -X POST http://127.0.0.1:9277/transcribe -d '{"media":"out/downloads/x.mp4"}'
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("HOST_CONTROL_PORT", "9277"))
LOG_DIR = ROOT / "out" / "logs"
PID_DIR = ROOT / "out" / "pids"
TRANSCRIPTS = ROOT / "out" / "transcripts"
BRAVE_SCRIPT = ROOT / "bin" / "brave-debug.sh"
BRAVE_PID_FILE = PID_DIR / "brave.pid"
BRAVE_LOG = LOG_DIR / "brave-debug.log"
WORKER = ROOT / "bin" / "host-transcribe-worker.py"
VENV_PY = ROOT / ".venv" / "bin" / "python"

_tx_lock = threading.Lock()
_tx_procs: dict[str, subprocess.Popen] = {}
_mlx_cache: dict[str, float | bool] = {"ts": 0.0, "ok": False}


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)


def brave_cdp_up(port: int = 9222) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def mlx_ready(force: bool = False) -> bool:
    """Check project venv can import mlx_whisper (Metal available). Cached ~5min."""
    now = time.time()
    if not force and now - float(_mlx_cache["ts"]) < 300 and _mlx_cache["ts"]:
        return bool(_mlx_cache["ok"])
    py = VENV_PY if VENV_PY.exists() else Path(sys.executable)
    ok = False
    try:
        proc = subprocess.run(
            [str(py), "-c", "import mlx_whisper; print('ok')"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=25,
        )
        ok = proc.returncode == 0 and "ok" in (proc.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _mlx_cache["ts"] = now
    _mlx_cache["ok"] = ok
    return ok


def mlx_ready_cached() -> bool:
    """Non-blocking: last known mlx status (warmed in background)."""
    if not _mlx_cache["ts"]:
        threading.Thread(target=lambda: mlx_ready(force=True), daemon=True).start()
        return False
    # refresh in background when stale
    if time.time() - float(_mlx_cache["ts"]) > 300:
        threading.Thread(target=lambda: mlx_ready(force=True), daemon=True).start()
    return bool(_mlx_cache["ok"])


def restart_brave() -> dict:
    ensure_dirs()
    if not BRAVE_SCRIPT.exists():
        return {"ok": False, "error": f"missing {BRAVE_SCRIPT}"}

    if BRAVE_PID_FILE.exists():
        try:
            old = int(BRAVE_PID_FILE.read_text().strip())
            os.kill(old, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass

    log_f = BRAVE_LOG.open("ab", buffering=0)
    proc = subprocess.Popen(
        ["bash", str(BRAVE_SCRIPT)],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    BRAVE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")

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


def _resolve_under_root(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    resolved = p.resolve()
    root = ROOT.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError(f"path outside project root: {raw}")
    return resolved


def _status_path(tx_id: str) -> Path:
    return TRANSCRIPTS / f"tx-{tx_id}.host.json"


def _read_status(tx_id: str) -> dict | None:
    path = _status_path(tx_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def start_transcribe(body: dict) -> dict:
    ensure_dirs()
    if not WORKER.exists():
        return {"ok": False, "error": f"missing {WORKER}"}
    py = VENV_PY if VENV_PY.exists() else None
    if py is None:
        return {
            "ok": False,
            "error": "Missing .venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
        }

    media_raw = (body.get("media") or "").strip()
    if not media_raw:
        return {"ok": False, "error": "media path required"}
    try:
        media = _resolve_under_root(media_raw)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not media.exists():
        return {"ok": False, "error": f"media not found: {media_raw}"}

    tx_id = (body.get("tx_id") or "").strip() or uuid.uuid4().hex[:12]
    language = (body.get("language") or "pt").strip() or "pt"
    model = (body.get("model") or "").strip()
    out_dir = TRANSCRIPTS
    if body.get("out_dir"):
        try:
            out_dir = _resolve_under_root(str(body["out_dir"]))
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    status_file = _status_path(tx_id)
    log_path = LOG_DIR / f"host-transcribe-{tx_id}.log"

    with _tx_lock:
        old = _tx_procs.get(tx_id)
        if old and old.poll() is None:
            return {"ok": False, "error": f"transcript {tx_id} already running"}

        status_file.write_text(
            json.dumps(
                {
                    "ok": False,
                    "tx_id": tx_id,
                    "status": "queued",
                    "phase": "starting",
                    "progress": 0,
                    "progress_label": "queued",
                    "result": None,
                    "error": None,
                }
            ),
            encoding="utf-8",
        )

        cmd = [
            str(py),
            str(WORKER),
            "--tx-id",
            tx_id,
            "--media",
            str(media),
            "--out-dir",
            str(out_dir),
            "--status-file",
            str(status_file),
            "--language",
            language,
        ]
        if model:
            cmd.extend(["--model", model])

        log_f = log_path.open("ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _tx_procs[tx_id] = proc

    return {
        "ok": True,
        "tx_id": tx_id,
        "pid": proc.pid,
        "status_file": str(status_file.relative_to(ROOT)),
        "log": str(log_path.relative_to(ROOT)),
        "mode": "host-control",
    }


def get_transcribe(tx_id: str) -> dict:
    data = _read_status(tx_id)
    if not data:
        with _tx_lock:
            proc = _tx_procs.get(tx_id)
        if proc and proc.poll() is None:
            return {
                "ok": False,
                "tx_id": tx_id,
                "status": "running",
                "phase": "starting",
                "progress": 0,
                "progress_label": "starting",
            }
        return {"ok": False, "tx_id": tx_id, "status": "error", "error": "not found"}
    data.setdefault("tx_id", tx_id)
    return data


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("", "/health"):
            ready = mlx_ready_cached()
            self._json(
                200,
                {
                    "ok": True,
                    "service": "hls-security-probe-host-control",
                    "brave_cdp": brave_cdp_up(),
                    "mlx_whisper": ready,
                    "transcribe_ready": ready,
                },
            )
            return

        if path.startswith("/transcribe/"):
            tx_id = path.split("/", 2)[-1].strip()
            if not tx_id:
                self._json(404, {"ok": False, "error": "missing tx_id"})
                return
            data = get_transcribe(tx_id)
            code = 200 if data.get("status") != "error" or data.get("result") else 200
            if data.get("error") == "not found":
                code = 404
            self._json(code, data)
            return

        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/restart-brave":
            result = restart_brave()
            self._json(200 if result.get("ok") else 500, result)
            return
        if path == "/transcribe":
            result = start_transcribe(self._read_json())
            self._json(200 if result.get("ok") else 400, result)
            return
        self._json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[host-control] " + (fmt % args) + "\n")


def main() -> int:
    ensure_dirs()
    threading.Thread(target=lambda: mlx_ready(force=True), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"host-control listening on http://127.0.0.1:{PORT}", flush=True)
    print("  POST /restart-brave", flush=True)
    print("  POST /transcribe", flush=True)
    print("  GET  /transcribe/<tx_id>", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
