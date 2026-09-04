from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from app.parser import parse_embed
except ImportError:
    from parser import parse_embed

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent.parent))
EXPORTS = ROOT / "exports"
OUT = ROOT / "out" / "downloads"
STATIC = Path(__file__).resolve().parent / "static"
# "auto" = prefer host Brave, then Docker browserless
DEFAULT_CDP = os.environ.get("BRAVE_CDP", "auto").strip() or "auto"
EXTRACT_MODE = os.environ.get("EXTRACT_MODE", "cdp").strip().lower()  # cdp | profile
PLAYWRIGHT_PROFILE = Path(
    os.environ.get("PLAYWRIGHT_PROFILE", str(ROOT / ".playwright-profile"))
)

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

app = FastAPI(title="HLS Security Probe UI")
app.mount("/files", StaticFiles(directory=str(OUT)), name="files")

jobs: dict[str, dict] = {}


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

app = FastAPI(title="HLS Security Probe UI")
app.mount("/files", StaticFiles(directory=str(OUT)), name="files")

jobs: dict[str, dict] = {}


class ParseRequest(BaseModel):
    snippet: str = Field(..., min_length=20)


class ExtractRequest(BaseModel):
    club_url: str = Field(..., min_length=20)
    cdp: str | None = None
    timeout_ms: int = Field(default=180000, ge=10000, le=600000)


class DownloadRequest(BaseModel):
    """Provide club_url (preferred) and/or embed snippet."""

    club_url: str | None = None
    snippet: str | None = None
    mode: str = Field(default="full")  # full | clip | probe
    timeout_sec: int = Field(default=3600, ge=30, le=7200)
    quality: str = Field(default="lowest")
    seconds_clip: int = Field(default=5, ge=1, le=600)
    cdp: str | None = None
    ref_override: str | None = None


def _safe_name(media: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", media)[:80] or "lesson"


def _commands(export_rel: str, timeout_sec: int = 3600, quality: str = "lowest") -> dict:
    return {
        "source": f"source {export_rel}",
        "probe": f"source {export_rel} && ./bin/fast-hls-security-test.sh probe",
        "clip": f"source {export_rel} && SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip",
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
    return {
        "brave_cdp": selected or "http://host.docker.internal:9222",
        "cdp_auto": True,
        "cdp_selected": selected,
        "cdp_candidates": probed,
        "brave_ok": brave_ok,
        "extract_mode": EXTRACT_MODE,
        "playwright_profile": str(PLAYWRIGHT_PROFILE),
        "login_hint": (
            "Brave ready — stay logged into Hotmart in that window."
            if brave_ok
            else "Start Brave: quit Brave → ./bin/brave-debug.sh → log into Hotmart"
        ),
        "browser_debugger": "http://127.0.0.1:3000/debugger/",
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


@app.post("/api/download")
async def api_download(body: DownloadRequest):
    club_url = (body.club_url or "").strip() or None
    snippet = (body.snippet or "").strip() or None

    if not club_url and not snippet:
        raise HTTPException(status_code=400, detail="Provide club_url or embed snippet")

    job_id = uuid.uuid4().hex[:12]
    log_path = OUT / f"job-{job_id}.log"
    jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "phase": "starting",
        "mode": body.mode,
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
    }

    async def runner() -> None:
        log_f = log_path.open("w", encoding="utf-8")
        vars_ = None
        use_cdp = None
        try:
            embed = snippet
            if club_url and (not embed or "jwtToken=" not in embed):
                jobs[job_id]["phase"] = "extracting"
                try:
                    use_cdp = await resolve_cdp(body.cdp) if EXTRACT_MODE == "cdp" else None
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
            if body.ref_override:
                vars_.ref = body.ref_override.strip()

            export_path, export_rel = _save_exports(vars_)
            jobs[job_id]["media"] = vars_.media
            jobs[job_id]["title"] = vars_.description or vars_.title
            jobs[job_id]["export_path"] = export_rel
            jobs[job_id]["embed_src"] = embed
            jobs[job_id]["commands"] = _commands(export_rel, body.timeout_sec, body.quality)
            jobs[job_id]["phase"] = "downloading"

            name = _safe_name(vars_.media)
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
                    "OUT_DIR": str(OUT),
                    "TIMEOUT_SEC": str(body.timeout_sec),
                    "QUALITY": body.quality,
                    "SECONDS_CLIP": str(body.seconds_clip),
                }
            )

            if body.mode == "probe":
                cmd = ["bash", str(ROOT / "bin" / "fast-hls-security-test.sh"), "probe"]
                env["OUT_DIR"] = str(OUT / f"probe-{name}")
            elif body.mode == "clip":
                cmd = ["bash", str(ROOT / "bin" / "fast-hls-security-test.sh"), "clip"]
                env["OUT_DIR"] = str(OUT / f"clip-{name}")
            else:
                cmd = ["bash", str(ROOT / "bin" / "download-hls.sh")]

            log_f.write(f"$ {' '.join(cmd)}\n")
            log_f.write(
                f"MEDIA={vars_.media} TIMEOUT_SEC={body.timeout_sec} QUALITY={body.quality} MODE={body.mode}\n\n"
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
            async for line in proc.stdout:
                log_f.write(line.decode("utf-8", errors="replace"))
                log_f.flush()
            rc = await proc.wait()
            jobs[job_id]["exit_code"] = rc
            jobs[job_id]["status"] = "ok" if rc == 0 else ("timeout" if rc == 124 else "error")

            candidates = sorted(
                OUT.rglob(f"*{vars_.media}*.mp4"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                candidates = sorted(OUT.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                rel = candidates[0].relative_to(OUT)
                jobs[job_id]["output_file"] = f"/files/{rel.as_posix()}"
        except Exception as e:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["exit_code"] = -1
            log_f.write(f"\nERROR: {e}\n")
        finally:
            jobs[job_id]["phase"] = "done"
            jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            log_f.close()

    asyncio.create_task(runner())
    return {"ok": True, "job": jobs[job_id], "brave_cdp": "auto"}


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
