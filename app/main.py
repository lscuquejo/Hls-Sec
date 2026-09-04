from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from app.parser import parse_embed
    from app import transcribe as tx
except ImportError:
    from parser import parse_embed
    import transcribe as tx

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent.parent))
EXPORTS = ROOT / "exports"
OUT = ROOT / "out" / "downloads"
TRANSCRIPTS = ROOT / "out" / "transcripts"
UPLOADS = ROOT / "out" / "uploads"
STATIC = Path(__file__).resolve().parent / "static"
# "auto" = prefer host Brave, then Docker browserless
DEFAULT_CDP = os.environ.get("BRAVE_CDP", "auto").strip() or "auto"
EXTRACT_MODE = os.environ.get("EXTRACT_MODE", "cdp").strip().lower()  # cdp | profile
PLAYWRIGHT_PROFILE = Path(
    os.environ.get("PLAYWRIGHT_PROFILE", str(ROOT / ".playwright-profile"))
)
HOST_CONTROL_URLS = [
    c.strip().rstrip("/")
    for c in os.environ.get(
        "HOST_CONTROL_URLS",
        "http://host.docker.internal:9277,http://127.0.0.1:9277",
    ).split(",")
    if c.strip()
]

# Prefer real Brave on the host; Docker browserless is fallback only.
CDP_CANDIDATES = [
    c.strip()
    for c in os.environ.get(
        "CDP_CANDIDATES",
        "http://host.docker.internal:9222,http://127.0.0.1:9222,ws://browser:3000",
    ).split(",")
    if c.strip()
]

EXPORTS.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
UPLOADS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="HLS Security Probe UI")
app.mount("/files", StaticFiles(directory=str(OUT)), name="files")
app.mount("/transcripts", StaticFiles(directory=str(TRANSCRIPTS)), name="transcripts")

jobs: dict[str, dict] = {}
batches: dict[str, dict] = {}
tx_jobs: dict[str, dict] = {}
_extract_lock = asyncio.Lock()
_batch_lock = asyncio.Lock()
_tx_lock = asyncio.Lock()  # one heavy whisper job at a time on Mac
BULK_CONCURRENCY = max(1, int(os.environ.get("BULK_CONCURRENCY", "3")))


def _cdp_probe_http_url(cdp: str) -> str:
    raw = cdp.strip().rstrip("/")
    if raw.startswith("ws://"):
        return "http://" + raw[len("ws://") :].split("/", 1)[0] + "/json/version"
    if raw.startswith("wss://"):
        return "https://" + raw[len("wss://") :].split("/", 1)[0] + "/json/version"
    return raw + "/json/version"


def normalize_cdp_url(cdp: str) -> str:
    """Rewrite host.docker.internal → IP.

    Chrome DevTools rejects Host headers that are not an IP or localhost, so
    http://host.docker.internal:9222 fails from Docker even when Brave is up.
    """
    from urllib.parse import urlparse, urlunparse
    import socket

    raw = cdp.strip()
    parsed = urlparse(raw)
    host = parsed.hostname
    if not host or host in ("localhost", "127.0.0.1") or _looks_like_ip(host):
        return raw
    if host != "host.docker.internal" and not host.endswith(".internal"):
        return raw
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return raw
    port = parsed.port
    netloc = f"{ip}:{port}" if port else ip
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _looks_like_ip(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


async def _cdp_reachable(cdp: str, timeout: float = 1.5) -> bool:
    import urllib.error
    import urllib.request

    url = _cdp_probe_http_url(normalize_cdp_url(cdp))

    def _check() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    return await asyncio.to_thread(_check)


async def _probe_cdp_candidates() -> list[dict]:
    out = []
    for cdp in CDP_CANDIDATES:
        ok = await _cdp_reachable(cdp)
        kind = "brave" if ":9222" in cdp else ("browserless" if "browser" in cdp or ":3000" in cdp else "cdp")
        out.append({"cdp": cdp, "ok": ok, "kind": kind})
    return out


async def resolve_cdp(requested: str | None = None) -> str:
    """Pick CDP endpoint: explicit override, else first live candidate (Brave first)."""
    req = (requested or "").strip()
    if req and req.lower() not in ("auto", "default"):
        normalized = normalize_cdp_url(req)
        if await _cdp_reachable(normalized):
            return normalized
        raise HTTPException(
            status_code=400,
            detail=(
                f"CDP not reachable: {req}\n"
                "Start host Brave first (recommended):\n"
                "  ./bin/brave-debug.sh\n"
                "Then use: http://host.docker.internal:9222"
            ),
        )

    if DEFAULT_CDP.lower() not in ("auto", "default", "") and not req:
        normalized = normalize_cdp_url(DEFAULT_CDP)
        if await _cdp_reachable(normalized):
            return normalized

    probed = await _probe_cdp_candidates()
    for item in probed:
        if item["ok"]:
            return normalize_cdp_url(item["cdp"])

    raise HTTPException(
        status_code=400,
        detail=(
            "No browser CDP reachable.\n"
            "Recommended — start Brave on the host:\n"
            "  1) Quit Brave if open\n"
            "  2) ./bin/brave-debug.sh\n"
            "  3) Log into Hotmart in that window\n"
            "  4) Retry Download (CDP: auto)\n"
            "Fallback: Docker debugger http://127.0.0.1:3000/debugger/"
        ),
    )


class ParseRequest(BaseModel):
    snippet: str = Field(..., min_length=20)


class ExtractRequest(BaseModel):
    club_url: str = Field(..., min_length=20)
    cdp: str | None = None
    timeout_ms: int = Field(default=180000, ge=10000, le=600000)


class DownloadRequest(BaseModel):
    """Provide club_url(s) (preferred) and/or embed snippet."""

    club_url: str | None = None
    club_urls: list[str] | None = None
    snippet: str | None = None
    mode: str = Field(default="full")  # full | clip | probe
    timeout_sec: int = Field(default=5400, ge=30, le=10800)  # default 1h30m
    quality: str = Field(default="lowest")
    seconds_clip: int = Field(default=5400, ge=1, le=10800)  # default 1h30m
    cdp: str | None = None
    ref_override: str | None = None


def _normalize_club_urls(
    club_url: str | None = None,
    club_urls: list[str] | None = None,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        for line in raw.replace(",", "\n").splitlines():
            u = line.strip()
            if not u or u.startswith("#"):
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)

    if club_urls:
        for item in club_urls:
            if item:
                add(item)
    if club_url:
        add(club_url)
    return out


def _safe_name(media: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", media)[:80] or "lesson"


def _commands(export_rel: str, timeout_sec: int = 5400, quality: str = "lowest", seconds_clip: int = 5400) -> dict:
    return {
        "source": f"source {export_rel}",
        "probe": f"source {export_rel} && ./bin/fast-hls-security-test.sh probe",
        "clip": f"source {export_rel} && SECONDS_CLIP={seconds_clip} ./bin/fast-hls-security-test.sh clip",
        "full": f"source {export_rel} && TIMEOUT_SEC={timeout_sec} QUALITY={quality} ./bin/download-hls.sh",
    }


def _save_exports(vars_) -> tuple[Path, str]:
    name = _safe_name(vars_.media)
    export_path = EXPORTS / f"{name}.sh"
    export_path.write_text(vars_.export_script(), encoding="utf-8")
    export_path.chmod(0o755)
    return export_path, str(export_path.relative_to(ROOT))


async def _extract_embed(
    club_url: str,
    cdp: str | None,
    timeout_ms: int,
    mode: str | None = None,
) -> dict:
    script = ROOT / "bin" / "extract-embed.py"
    if not script.exists():
        raise HTTPException(status_code=500, detail="extract-embed.py missing")

    use_mode = (mode or EXTRACT_MODE).strip().lower()
    cmd = [
        sys.executable,
        str(script),
        club_url,
        "--json",
        "--timeout",
        str(timeout_ms),
    ]
    if use_mode == "profile":
        cmd += ["--chromium", "--headless", "--profile", str(PLAYWRIGHT_PROFILE)]
    else:
        cmd += ["--cdp", (cdp or DEFAULT_CDP).strip()]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")).strip()
        hint = (
            "Start host Brave (recommended):\n"
            "  ./bin/brave-debug.sh\n"
            "CDP: http://host.docker.internal:9222\n"
            "Fallback: http://127.0.0.1:3000/debugger/"
            if use_mode == "cdp"
            else "Run a headed login once to seed the profile, or use EXTRACT_MODE=cdp."
        )
        raise HTTPException(
            status_code=400,
            detail=err or f"Extract failed ({use_mode}).\n{hint}",
        )
    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"bad extract output: {e}") from e
    if not data.get("embed_src"):
        raise HTTPException(status_code=400, detail=data.get("error") or "no embed found")
    return data


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
async def api_config():
    probed = await _probe_cdp_candidates()
    selected = next((p["cdp"] for p in probed if p["ok"]), None)
    brave_ok = any(p["ok"] and p["kind"] == "brave" for p in probed)
    host_control = await _host_control_health()
    return {
        "brave_cdp": selected or "http://host.docker.internal:9222",
        "cdp_auto": True,
        "cdp_selected": selected,
        "cdp_candidates": probed,
        "brave_ok": brave_ok,
        "host_control_ok": bool(host_control.get("ok")),
        "extract_mode": EXTRACT_MODE,
        "playwright_profile": str(PLAYWRIGHT_PROFILE),
        "login_hint": (
            "Brave ready — stay logged into Hotmart in that window."
            if brave_ok
            else "Click “Restart Brave”, then log into Hotmart in the Brave window."
        ),
        "browser_debugger": "http://127.0.0.1:3000/debugger/",
        "transcribe": tx.backend_info(),
    }


def _host_control_bases() -> list[str]:
    """Rewrite host.docker.internal → IP so Docker can reach the Mac helper."""
    out: list[str] = []
    for base in HOST_CONTROL_URLS:
        try:
            out.append(normalize_cdp_url(base).rstrip("/"))
        except Exception:
            out.append(base.rstrip("/"))
    # de-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for b in out:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return uniq


async def _host_control_health() -> dict:
    import urllib.error
    import urllib.request

    def _get(url: str) -> dict | None:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None

    for base in _host_control_bases():
        data = await asyncio.to_thread(_get, base)
        if data and data.get("ok"):
            return {**data, "url": base}
    return {"ok": False}


async def _restart_brave_via_host_control() -> dict:
    import shutil
    import urllib.error
    import urllib.request

    # 1) Local Mac: launchd KeepAlive agent (best — survives reboot)
    launchctl = shutil.which("launchctl")
    if launchctl:
        label = "gui/{}/com.lcuquejo.hls-security-probe.brave".format(os.getuid())
        proc = await asyncio.create_subprocess_exec(
            launchctl,
            "kickstart",
            "-k",
            label,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, _err = await proc.communicate()
        if proc.returncode == 0:
            return {
                "ok": True,
                "mode": "launchd",
                "hint": "Brave restarted via LaunchAgent — log into Hotmart, then retry Download.",
            }

    # 2) Local Mac: run brave-debug.sh directly (UI running on host)
    script = ROOT / "bin" / "brave-debug.sh"
    brave_bin = Path(
        os.environ.get(
            "BRAVE_PATH",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        )
    )
    if script.exists() and brave_bin.exists():
        log_dir = ROOT / "out" / "logs"
        pid_dir = ROOT / "out" / "pids"
        log_dir.mkdir(parents=True, exist_ok=True)
        pid_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "brave-debug.log"
        log_f = log_path.open("ab", buffering=0)
        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(script),
            cwd=str(ROOT),
            stdout=log_f,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        (pid_dir / "brave.pid").write_text(str(proc.pid), encoding="utf-8")
        return {
            "ok": True,
            "pid": proc.pid,
            "mode": "local-script",
            "hint": "Brave restarting — log into Hotmart in that window, then retry Download.",
        }

    # 3) Docker UI → host-control bridge on the Mac
    def _post(url: str) -> dict:
        req = urllib.request.Request(
            f"{url}/restart-brave",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))

    errors: list[str] = []
    for base in _host_control_bases():
        try:
            result = await asyncio.to_thread(_post, base)
            result.setdefault("mode", "host-control")
            return result
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            errors.append(f"{base}: {e}")

    raise HTTPException(
        status_code=503,
        detail=(
            "Cannot restart Brave from Docker UI — host-control is not running.\n"
            "On the Mac (once after reboot/setup):\n"
            "  ./bin/install-autostart.sh\n"
            "or:\n"
            "  ./bin/host-control.sh\n"
            "Then click Restart Brave again.\n"
            f"Tried: {'; '.join(errors) or 'none'}"
        ),
    )


@app.post("/api/brave/restart")
async def api_brave_restart():
    result = await _restart_brave_via_host_control()
    # Give CDP a moment, then report status
    await asyncio.sleep(1.0)
    probed = await _probe_cdp_candidates()
    brave_ok = any(p["ok"] and p["kind"] == "brave" for p in probed)
    return {
        "ok": bool(result.get("ok")),
        "brave_ok": brave_ok,
        "result": result,
        "cdp_candidates": probed,
        "hint": result.get("hint")
        or "Log into Hotmart in the Brave window, then retry Download.",
    }


@app.post("/api/parse")
async def api_parse(body: ParseRequest):
    try:
        vars_ = parse_embed(body.snippet)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    export_path, export_rel = _save_exports(vars_)
    return {
        "ok": True,
        "vars": vars_.to_dict(),
        "export_path": export_rel,
        "export_script": vars_.export_script(),
        "commands": _commands(export_rel),
    }


@app.post("/api/extract")
async def api_extract(body: ExtractRequest):
    cdp = await resolve_cdp(body.cdp) if EXTRACT_MODE == "cdp" else None
    data = await _extract_embed(body.club_url, cdp=cdp, timeout_ms=body.timeout_ms)
    vars_ = parse_embed(data["embed_src"])
    vars_.ref = body.club_url
    export_path, export_rel = _save_exports(vars_)
    return {
        "ok": True,
        "embed_src": data["embed_src"],
        "extract": data,
        "vars": vars_.to_dict(),
        "export_path": export_rel,
        "export_script": vars_.export_script(),
        "commands": _commands(export_rel),
    }


def _new_job(mode: str, club_url: str | None = None, batch_id: str | None = None) -> tuple[str, Path]:
    job_id = uuid.uuid4().hex[:12]
    log_path = OUT / f"job-{job_id}.log"
    jobs[job_id] = {
        "id": job_id,
        "batch_id": batch_id,
        "status": "queued" if batch_id else "running",
        "phase": "starting",
        "mode": mode,
        "media": None,
        "title": None,
        "club_url": club_url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "exit_code": None,
        "log_path": str(log_path),
        "output_file": None,
        "export_path": None,
        "embed_src": None,
        "commands": {},
        "progress": 0,
        "progress_label": "",
        "progress_time": "",
    }
    return job_id, log_path


async def _run_download_job(
    job_id: str,
    *,
    club_url: str | None,
    snippet: str | None,
    mode: str,
    timeout_sec: int,
    quality: str,
    seconds_clip: int,
    cdp: str | None,
    ref_override: str | None,
) -> None:
    log_path = Path(jobs[job_id]["log_path"])
    log_f = log_path.open("w", encoding="utf-8")
    jobs[job_id]["status"] = "running"
    try:
        embed = snippet
        if club_url and (not embed or "jwtToken=" not in embed):
            jobs[job_id]["phase"] = "extracting"
            # Brave CDP is shared — only one extract at a time; downloads run in parallel after.
            async with _extract_lock:
                try:
                    use_cdp = await resolve_cdp(cdp) if EXTRACT_MODE == "cdp" else None
                except HTTPException as e:
                    log_f.write(f"[extract] ERROR: {e.detail}\n")
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["exit_code"] = 1
                    return
                log_f.write(f"[extract] club_url={club_url}\n")
                log_f.write(f"[extract] mode={EXTRACT_MODE} cdp={use_cdp}\n\n")
                log_f.flush()
                try:
                    data = await _extract_embed(club_url, cdp=use_cdp, timeout_ms=180000)
                except HTTPException as e:
                    log_f.write(f"[extract] ERROR: {e.detail}\n")
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["exit_code"] = 1
                    return
                embed = data["embed_src"]
                jobs[job_id]["embed_src"] = embed
                log_f.write(f"[extract] OK media embed length={len(embed)}\n\n")
                log_f.flush()

        assert embed
        vars_ = parse_embed(embed)
        if club_url:
            vars_.ref = club_url
        if ref_override:
            vars_.ref = ref_override.strip()

        export_path, export_rel = _save_exports(vars_)
        jobs[job_id]["media"] = vars_.media
        jobs[job_id]["title"] = vars_.description or vars_.title
        jobs[job_id]["export_path"] = export_rel
        jobs[job_id]["embed_src"] = embed
        jobs[job_id]["commands"] = _commands(export_rel, timeout_sec, quality, seconds_clip)
        jobs[job_id]["phase"] = "downloading"
        jobs[job_id]["progress"] = 0
        jobs[job_id]["progress_label"] = "0%"
        jobs[job_id]["progress_time"] = "00:00:00"
        target_sec = float(seconds_clip if mode == "clip" else timeout_sec)

        name = _safe_name(vars_.media)
        video_title = (vars_.description or vars_.title or vars_.media or "").strip()
        env = os.environ.copy()
        env.update(
            {
                "APP": vars_.app,
                "USER_CODE": vars_.user_code,
                "USER_ID": vars_.user_id,
                "MEDIA": vars_.media,
                "JWT": vars_.jwt,
                "REF": vars_.ref,
                "EMBED": vars_.embed,
                "VIDEO_TITLE": video_title,
                "OUT_DIR": str(OUT),
                "TIMEOUT_SEC": str(timeout_sec),
                "QUALITY": quality,
                "SECONDS_CLIP": str(seconds_clip),
            }
        )

        if mode == "probe":
            cmd = ["bash", str(ROOT / "bin" / "fast-hls-security-test.sh"), "probe"]
            env["OUT_DIR"] = str(OUT / f"probe-{name}")
        elif mode == "clip":
            cmd = ["bash", str(ROOT / "bin" / "fast-hls-security-test.sh"), "clip"]
            env["OUT_DIR"] = str(OUT / f"clip-{name}")
        else:
            cmd = ["bash", str(ROOT / "bin" / "download-hls.sh")]

        log_f.write(f"$ {' '.join(cmd)}\n")
        log_f.write(
            f"MEDIA={vars_.media} TIMEOUT_SEC={timeout_sec} QUALITY={quality} "
            f"SECONDS_CLIP={seconds_clip} MODE={mode}\n\n"
        )
        log_f.flush()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None

        def _apply_progress(out_time_ms: int) -> None:
            secs = max(0.0, out_time_ms / 1000.0)
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            s = int(secs % 60)
            pct = int(min(99, (secs / target_sec) * 100)) if target_sec > 0 else 0
            jobs[job_id]["progress"] = pct
            jobs[job_id]["progress_time"] = f"{h:02d}:{m:02d}:{s:02d}"
            jobs[job_id]["progress_label"] = f"{pct}%"

        buf = ""
        async for chunk in proc.stdout:
            text = chunk.decode("utf-8", errors="replace")
            log_f.write(text)
            log_f.flush()
            buf += text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        _apply_progress(int(line.split("=", 1)[1]))
                    except ValueError:
                        pass
                elif line.startswith("out_time="):
                    # out_time=HH:MM:SS.micro
                    try:
                        raw = line.split("=", 1)[1].strip()
                        parts = raw.split(":")
                        if len(parts) == 3:
                            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                            _apply_progress(int(secs * 1000))
                    except ValueError:
                        pass
                elif line == "progress=end":
                    jobs[job_id]["progress"] = 100
                    jobs[job_id]["progress_label"] = "100%"

        rc = await proc.wait()
        jobs[job_id]["exit_code"] = rc
        jobs[job_id]["status"] = "ok" if rc == 0 else ("timeout" if rc == 124 else "error")
        if rc == 0:
            jobs[job_id]["progress"] = 100
            jobs[job_id]["progress_label"] = "100%"

        candidates = sorted(
            OUT.rglob("*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Prefer files matching media id or title slug
        title_slug = _safe_name(video_title) if video_title else ""
        preferred = [
            p
            for p in candidates
            if vars_.media in p.name or (title_slug and title_slug[:40] in p.name)
        ]
        pick = (preferred or candidates)[0] if (preferred or candidates) else None
        if pick:
            rel = pick.relative_to(OUT)
            jobs[job_id]["output_file"] = f"/files/{rel.as_posix()}"
            jobs[job_id]["output_name"] = pick.name
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["exit_code"] = -1
        log_f.write(f"\nERROR: {e}\n")
    finally:
        jobs[job_id]["phase"] = "done"
        jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        log_f.close()


@app.post("/api/download")
async def api_download(body: DownloadRequest):
    urls = _normalize_club_urls(body.club_url, body.club_urls)
    snippet = (body.snippet or "").strip() or None

    if not urls and not snippet:
        raise HTTPException(status_code=400, detail="Provide club URL(s) or embed snippet")

    # Bulk: multiple club URLs → parallel downloads (extract serialized on Brave CDP)
    if len(urls) > 1:
        if snippet:
            raise HTTPException(status_code=400, detail="Bulk download uses club URLs only (no embed snippet)")
        batch_id = uuid.uuid4().hex[:12]
        job_ids: list[str] = []
        for url in urls:
            jid, _ = _new_job(body.mode, club_url=url, batch_id=batch_id)
            job_ids.append(jid)
        batches[batch_id] = {
            "id": batch_id,
            "status": "running",
            "total": len(job_ids),
            "done": 0,
            "ok": 0,
            "error": 0,
            "parallel": True,
            "concurrency": BULK_CONCURRENCY,
            "job_ids": job_ids,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

        async def batch_runner() -> None:
            sem = asyncio.Semaphore(BULK_CONCURRENCY)

            async def one(jid: str) -> None:
                async with sem:
                    url = jobs[jid]["club_url"]
                    await _run_download_job(
                        jid,
                        club_url=url,
                        snippet=None,
                        mode=body.mode,
                        timeout_sec=body.timeout_sec,
                        quality=body.quality,
                        seconds_clip=body.seconds_clip,
                        cdp=body.cdp,
                        ref_override=url,
                    )
                async with _batch_lock:
                    batches[batch_id]["done"] += 1
                    if jobs[jid]["status"] == "ok":
                        batches[batch_id]["ok"] += 1
                    else:
                        batches[batch_id]["error"] += 1

            await asyncio.gather(*(one(jid) for jid in job_ids))
            batches[batch_id]["status"] = "ok" if batches[batch_id]["error"] == 0 else "done"
            batches[batch_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

        asyncio.create_task(batch_runner())
        return {"ok": True, "batch": batches[batch_id], "brave_cdp": "auto"}

    club_url = urls[0] if urls else None
    job_id, _ = _new_job(body.mode, club_url=club_url)
    asyncio.create_task(
        _run_download_job(
            job_id,
            club_url=club_url,
            snippet=snippet,
            mode=body.mode,
            timeout_sec=body.timeout_sec,
            quality=body.quality,
            seconds_clip=body.seconds_clip,
            cdp=body.cdp,
            ref_override=(body.ref_override or club_url),
        )
    )
    return {"ok": True, "job": jobs[job_id], "brave_cdp": "auto"}


@app.get("/api/batches/{batch_id}")
async def api_batch(batch_id: str):
    batch = batches.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")
    items = []
    for jid in batch["job_ids"]:
        job = jobs.get(jid, {})
        log_tail = ""
        log_path = Path(job.get("log_path", ""))
        if log_path.exists():
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        items.append({**job, "log_tail": log_tail})
    return {**batch, "jobs": items}


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    log_path = Path(job["log_path"])
    log_tail = ""
    if log_path.exists():
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-16000:]
    return {**job, "log_tail": log_tail}


@app.get("/api/jobs")
async def api_jobs():
    return {"jobs": list(jobs.values())[-20:]}


class TranscribeJobRequest(BaseModel):
    language: str = Field(default="pt")
    model: str | None = None


def _resolve_media_from_job(job: dict) -> Path:
    out = job.get("output_file") or ""
    # "/files/rel/path.mp4" → OUT / rel/path.mp4
    if out.startswith("/files/"):
        p = OUT / out[len("/files/") :]
        if p.exists():
            return p
    name = job.get("output_name")
    if name:
        hits = sorted(OUT.rglob(name), key=lambda x: x.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    media = job.get("media")
    if media:
        hits = sorted(OUT.rglob(f"*{media}*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    raise HTTPException(status_code=404, detail="Downloaded media file not found for this job")


async def _run_transcribe(tx_id: str, media: Path, language: str, model: str | None) -> None:
    tx_jobs[tx_id]["status"] = "running"
    tx_jobs[tx_id]["phase"] = "transcribing"
    tx_jobs[tx_id]["progress"] = 0
    tx_jobs[tx_id]["progress_label"] = "starting"
    log_path = Path(tx_jobs[tx_id]["log_path"])

    def on_progress(pct: int, label: str) -> None:
        tx_jobs[tx_id]["progress"] = pct
        tx_jobs[tx_id]["progress_label"] = label or f"{pct}%"
        if "extract" in label:
            tx_jobs[tx_id]["phase"] = "extracting audio"
        elif "loading" in label:
            tx_jobs[tx_id]["phase"] = "loading model"
        elif "writing" in label:
            tx_jobs[tx_id]["phase"] = "writing"
        elif label == "done":
            tx_jobs[tx_id]["phase"] = "done"
        else:
            tx_jobs[tx_id]["phase"] = "transcribing"

    try:
        with log_path.open("a", encoding="utf-8") as log_f:
            log_f.write(f"[transcribe] source={media}\n")
            log_f.write(f"[transcribe] language={language} model={model or tx.DEFAULT_MODEL}\n")
            log_f.flush()
        async with _tx_lock:
            result = await asyncio.to_thread(
                tx.transcribe_file,
                media,
                out_dir=TRANSCRIPTS,
                language=language or tx.DEFAULT_LANGUAGE,
                model=model or tx.DEFAULT_MODEL,
                progress_cb=on_progress,
            )
        tx_jobs[tx_id].update(
            {
                "status": "ok",
                "phase": "done",
                "progress": 100,
                "progress_label": "100%",
                "result": result,
                "text_preview": (result.get("text") or "")[:2000],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        with log_path.open("a", encoding="utf-8") as log_f:
            log_f.write(f"[transcribe] OK engine={result.get('engine')} segments={result.get('segments')}\n")
            log_f.write(f"[transcribe] txt={result.get('txt')}\n")
    except Exception as e:
        tx_jobs[tx_id].update(
            {
                "status": "error",
                "phase": "done",
                "progress_label": "error",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        with log_path.open("a", encoding="utf-8") as log_f:
            log_f.write(f"[transcribe] ERROR: {e}\n")


def _new_tx_job(source_label: str, media: Path) -> str:
    tx_id = uuid.uuid4().hex[:12]
    log_path = TRANSCRIPTS / f"tx-{tx_id}.log"
    tx_jobs[tx_id] = {
        "id": tx_id,
        "status": "queued",
        "phase": "starting",
        "source": str(media),
        "source_label": source_label,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "log_path": str(log_path),
        "result": None,
        "text_preview": "",
        "error": None,
        "progress": 0,
        "progress_label": "queued",
    }
    log_path.write_text("", encoding="utf-8")
    return tx_id


@app.post("/api/transcribe/job/{job_id}")
async def api_transcribe_job(job_id: str, body: TranscribeJobRequest = TranscribeJobRequest()):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="download job not found")
    if job.get("status") != "ok":
        raise HTTPException(status_code=400, detail="Download is not finished yet")
    if not tx.backend_info().get("ready"):
        raise HTTPException(
            status_code=503,
            detail="Install mlx-whisper (Mac): pip install mlx-whisper",
        )
    media = _resolve_media_from_job(job)
    tx_id = _new_tx_job(job.get("output_name") or job.get("media") or job_id, media)
    job["transcript_id"] = tx_id
    asyncio.create_task(
        _run_transcribe(tx_id, media, body.language, body.model)
    )
    return {"ok": True, "transcript": tx_jobs[tx_id]}


@app.post("/api/transcribe/upload")
async def api_transcribe_upload(
    file: UploadFile = File(...),
    language: str = "pt",
):
    if not tx.backend_info().get("ready"):
        raise HTTPException(
            status_code=503,
            detail="Install mlx-whisper (Mac): pip install mlx-whisper",
        )
    suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename or "upload").stem)[:80] or "upload"
    dest = UPLOADS / f"{safe}-{uuid.uuid4().hex[:8]}{suffix}"
    data = await file.read()
    if len(data) < 1000:
        raise HTTPException(status_code=400, detail="File too small")
    dest.write_bytes(data)
    tx_id = _new_tx_job(file.filename or dest.name, dest)
    asyncio.create_task(_run_transcribe(tx_id, dest, language or "pt", None))
    return {"ok": True, "transcript": tx_jobs[tx_id]}


@app.get("/api/transcribe/{tx_id}")
async def api_transcribe_status(tx_id: str):
    item = tx_jobs.get(tx_id)
    if not item:
        raise HTTPException(status_code=404, detail="transcript job not found")
    log_tail = ""
    log_path = Path(item.get("log_path") or "")
    if log_path.exists():
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
    return {**item, "log_tail": log_tail}
