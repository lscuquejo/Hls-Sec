"""Portuguese-first video/audio transcription helpers (Apple Silicon: mlx-whisper)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


DEFAULT_LANGUAGE = os.environ.get("TRANSCRIBE_LANGUAGE", "pt")
DEFAULT_MODEL = os.environ.get(
    "TRANSCRIBE_MODEL",
    "mlx-community/whisper-large-v3-turbo",
)

ProgressCb = Callable[[int, str], None]


def backend_info() -> dict:
    mlx_ok = False
    fw_ok = False
    try:
        import mlx_whisper  # noqa: F401

        mlx_ok = True
    except Exception:
        pass
    try:
        import faster_whisper  # noqa: F401

        fw_ok = True
    except Exception:
        pass
    return {
        "mlx_whisper": mlx_ok,
        "faster_whisper": fw_ok,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "default_language": DEFAULT_LANGUAGE,
        "default_model": DEFAULT_MODEL,
        "ready": mlx_ok or fw_ok,
    }


def _emit(cb: ProgressCb | None, pct: int, label: str) -> None:
    if cb:
        cb(max(0, min(100, int(pct))), label)


def _extract_wav(media: Path, wav: Path, progress_cb: ProgressCb | None = None) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    _emit(progress_cb, 2, "extracting audio")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg failed to extract audio")
    _emit(progress_cb, 8, "audio ready")


def _segments_to_srt(segments: list[dict]) -> str:
    lines: list[str] = []

    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    for i, seg in enumerate(segments, 1):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", start))
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + ("\n" if lines else "")


def _mlx_transcribe(
    wav: Path,
    *,
    model: str,
    language: str,
    progress_cb: ProgressCb | None,
) -> dict[str, Any]:
    import tqdm
    import mlx_whisper

    _emit(progress_cb, 10, "loading model / decoding")

    class _ProgressTqdm(tqdm.tqdm):
        def update(self, n: float | int = 1):  # type: ignore[override]
            r = super().update(n)
            if self.total:
                # Reserve 10–95% for decode progress
                frac = min(1.0, float(self.n) / float(self.total))
                pct = int(10 + frac * 85)
                _emit(progress_cb, pct, f"transcribing {pct}%")
            return r

    original = tqdm.tqdm
    tqdm.tqdm = _ProgressTqdm  # type: ignore[misc, assignment]
    try:
        result = mlx_whisper.transcribe(
            str(wav),
            path_or_hf_repo=model,
            language=language or None,
            verbose=False,  # enables tqdm progress bar in mlx-whisper
        )
    finally:
        tqdm.tqdm = original  # type: ignore[misc]

    return result


def _faster_transcribe(
    wav: Path,
    *,
    model: str,
    language: str,
    progress_cb: ProgressCb | None,
) -> tuple[str, list[dict]]:
    from faster_whisper import WhisperModel

    _emit(progress_cb, 10, "loading model")
    size = "large-v3"
    if "medium" in model:
        size = "medium"
    elif "small" in model:
        size = "small"
    elif "turbo" in model or "large" in model:
        size = "large-v3"
    fw = WhisperModel(size, device="cpu", compute_type="int8")
    _emit(progress_cb, 15, "transcribing")
    segs, info = fw.transcribe(str(wav), language=language or None)
    duration = float(getattr(info, "duration", 0) or 0)
    parts: list[str] = []
    segments: list[dict] = []
    for seg in segs:
        piece = (seg.text or "").strip()
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": piece})
        if piece:
            parts.append(piece)
        if duration > 0:
            pct = int(15 + min(80, (float(seg.end) / duration) * 80))
            _emit(progress_cb, pct, f"transcribing {pct}%")
    return " ".join(parts).strip(), segments


def transcribe_file(
    media_path: Path,
    *,
    out_dir: Path,
    language: str = DEFAULT_LANGUAGE,
    model: str = DEFAULT_MODEL,
    progress_cb: ProgressCb | None = None,
) -> dict:
    """Transcribe media → write .txt / .json / .srt next to stem in out_dir."""
    media_path = media_path.resolve()
    if not media_path.exists():
        raise FileNotFoundError(str(media_path))

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = media_path.stem
    wav = out_dir / f"{stem}.16k.wav"
    txt_path = out_dir / f"{stem}.txt"
    json_path = out_dir / f"{stem}.json"
    srt_path = out_dir / f"{stem}.srt"

    _extract_wav(media_path, wav, progress_cb=progress_cb)

    info = backend_info()
    segments: list[dict] = []
    text = ""
    engine = ""

    if info["mlx_whisper"]:
        engine = "mlx-whisper"
        result = _mlx_transcribe(wav, model=model, language=language, progress_cb=progress_cb)
        text = (result.get("text") or "").strip()
        for seg in result.get("segments") or []:
            segments.append(
                {
                    "start": float(seg.get("start", 0)),
                    "end": float(seg.get("end", 0)),
                    "text": (seg.get("text") or "").strip(),
                }
            )
    elif info["faster_whisper"]:
        engine = "faster-whisper"
        text, segments = _faster_transcribe(
            wav, model=model, language=language, progress_cb=progress_cb
        )
    else:
        raise RuntimeError(
            "No transcription backend installed. On Mac Apple Silicon:\n"
            "  pip install mlx-whisper\n"
            "Or: pip install faster-whisper"
        )

    _emit(progress_cb, 96, "writing transcript files")
    payload = {
        "engine": engine,
        "model": model,
        "language": language,
        "source": str(media_path),
        "text": text,
        "segments": segments,
    }
    txt_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    srt_path.write_text(_segments_to_srt(segments), encoding="utf-8")

    try:
        wav.unlink(missing_ok=True)
    except OSError:
        pass

    _emit(progress_cb, 100, "done")
    return {
        "ok": True,
        "engine": engine,
        "model": model,
        "language": language,
        "text": text,
        "segments": len(segments),
        "txt": f"/transcripts/{txt_path.name}",
        "json": f"/transcripts/{json_path.name}",
        "srt": f"/transcripts/{srt_path.name}",
        "txt_path": str(txt_path),
        "json_path": str(json_path),
        "srt_path": str(srt_path),
    }
