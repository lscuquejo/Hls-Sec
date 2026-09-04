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
except ImportError:  # running as script from app/
    from parser import parse_embed

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent.parent))
EXPORTS = ROOT / "exports"
OUT = ROOT / "out" / "downloads"
STATIC = Path(__file__).resolve().parent / "static"

EXPORTS.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="HLS Security Probe UI")
app.mount("/files", StaticFiles(directory=str(OUT)), name="files")

jobs: dict[str, dict] = {}


class ParseRequest(BaseModel):
    snippet: str = Field(..., min_length=20)


class ExtractRequest(BaseModel):
    club_url: str = Field(..., min_length=20)
    login: bool = False
    headed: bool = True
    timeout_ms: int = Field(default=120000, ge=10000, le=600000)


class DownloadRequest(BaseModel):
    snippet: str = Field(..., min_length=20)
    mode: str = Field(default="full")  # full | clip | probe
    timeout_sec: int = Field(default=3600, ge=30, le=7200)
    quality: str = Field(default="lowest")  # lowest | highest
    seconds_clip: int = Field(default=5, ge=1, le=600)
    ref_override: str | None = None


def _safe_name(media: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", media)[:80] or "lesson"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.post("/api/parse")
async def api_parse(body: ParseRequest):
    try:
        vars_ = parse_embed(body.snippet)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    name = _safe_name(vars_.media)
    export_path = EXPORTS / f"{name}.sh"
    export_path.write_text(vars_.export_script(), encoding="utf-8")
    export_path.chmod(0o755)

    return {
        "ok": True,
        "vars": vars_.to_dict(),
        "export_path": str(export_path.relative_to(ROOT)),
        "export_script": vars_.export_script(),
        "commands": {
            "source": f"source {export_path.relative_to(ROOT)}",
            "probe": f"source {export_path.relative_to(ROOT)} && ./bin/fast-hls-security-test.sh probe",
            "clip": f"source {export_path.relative_to(ROOT)} && SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip",
            "full": f"source {export_path.relative_to(ROOT)} && TIMEOUT_SEC=3600 ./bin/download-hls.sh",
        },
    }


@app.post("/api/extract")
async def api_extract(body: ExtractRequest):
    """Open a club lesson URL with the persistent Playwright profile and return embed src.

    Works best when the UI runs on the host (not Docker), after:
      ./bin/extract-embed.sh --login 'https://hotmart.com/...'
    """
    script = ROOT / "bin" / "extract-embed.py"
    if not script.exists():
        raise HTTPException(status_code=500, detail="extract-embed.py missing")

    cmd = [
        sys.executable,
        str(script),
        body.club_url,
        "--json",
        "--timeout",
        str(body.timeout_ms),
        "--profile",
        str(ROOT / ".playwright-profile"),
    ]
    if body.login or body.headed:
        cmd.append("--headed")
    if body.login:
        cmd.append("--login")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"extract failed to start: {e}") from e

    if proc.returncode != 0:
        err = (stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")).strip()
        raise HTTPException(
            status_code=400,
            detail=err or "extract failed — run ./bin/extract-embed.sh --login URL once on the host",
        )

    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"bad extract output: {e}") from e

    embed = data.get("embed_src")
    if not embed:
        raise HTTPException(status_code=400, detail=data.get("error") or "no embed found")

    # also parse into exports
    vars_ = parse_embed(embed)
    if body.club_url:
        vars_.ref = body.club_url
    name = _safe_name(vars_.media)
    export_path = EXPORTS / f"{name}.sh"
    export_path.write_text(vars_.export_script(), encoding="utf-8")
    export_path.chmod(0o755)

    return {
        "ok": True,
        "embed_src": embed,
        "extract": data,
        "vars": vars_.to_dict(),
        "export_path": str(export_path.relative_to(ROOT)),
        "export_script": vars_.export_script(),
        "commands": {
            "source": f"source {export_path.relative_to(ROOT)}",
            "probe": f"source {export_path.relative_to(ROOT)} && ./bin/fast-hls-security-test.sh probe",
            "clip": f"source {export_path.relative_to(ROOT)} && SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip",
            "full": f"source {export_path.relative_to(ROOT)} && TIMEOUT_SEC=3600 ./bin/download-hls.sh",
        },
    }


@app.post("/api/download")
async def api_download(body: DownloadRequest):
    try:
        vars_ = parse_embed(body.snippet)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if body.ref_override:
        vars_.ref = body.ref_override.strip()

    name = _safe_name(vars_.media)
    export_path = EXPORTS / f"{name}.sh"
    export_path.write_text(vars_.export_script(), encoding="utf-8")
    export_path.chmod(0o755)

    job_id = uuid.uuid4().hex[:12]
    log_path = OUT / f"{name}-{job_id}.log"
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

    jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "mode": body.mode,
        "media": vars_.media,
        "title": vars_.description or vars_.title,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "exit_code": None,
        "log_path": str(log_path),
        "output_file": None,
        "export_path": str(export_path.relative_to(ROOT)),
        "commands": {
            "source": f"source {export_path.relative_to(ROOT)}",
            "full": f"source {export_path.relative_to(ROOT)} && TIMEOUT_SEC={body.timeout_sec} QUALITY={body.quality} ./bin/download-hls.sh",
        },
    }

    async def runner() -> None:
        log_f = log_path.open("w", encoding="utf-8")
        try:
            log_f.write(f"$ {' '.join(cmd)}\n")
            log_f.write(f"MEDIA={vars_.media} TIMEOUT_SEC={body.timeout_sec} QUALITY={body.quality}\n\n")
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
                text = line.decode("utf-8", errors="replace")
                log_f.write(text)
                log_f.flush()
            rc = await proc.wait()
            jobs[job_id]["exit_code"] = rc
            jobs[job_id]["status"] = "ok" if rc == 0 else ("timeout" if rc == 124 else "error")
            # discover output mp4
            candidates = sorted(OUT.rglob(f"*{vars_.media}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
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
            jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            log_f.close()

    asyncio.create_task(runner())
    return {"ok": True, "job": jobs[job_id], "vars": vars_.to_dict()}


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    log_path = Path(job["log_path"])
    log_tail = ""
    if log_path.exists():
        data = log_path.read_text(encoding="utf-8", errors="replace")
        log_tail = data[-12000:]
    return {**job, "log_tail": log_tail}


@app.get("/api/jobs")
async def api_jobs():
    return {"jobs": list(jobs.values())[-20:]}
