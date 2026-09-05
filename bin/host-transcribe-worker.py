#!/usr/bin/env python3
"""Run mlx-whisper on the Mac; write progress JSON for host-control / Docker UI.

Invoked by host-control with the project .venv python (needs Metal).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _write(status_file: Path, payload: dict) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_file.with_suffix(status_file.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(status_file)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx-id", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--status-file", required=True)
    ap.add_argument("--language", default="pt")
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    status_file = Path(args.status_file)
    media = Path(args.media)
    out_dir = Path(args.out_dir)

    base = {
        "ok": False,
        "tx_id": args.tx_id,
        "status": "running",
        "phase": "starting",
        "progress": 0,
        "progress_label": "starting",
        "result": None,
        "error": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(status_file, base)

    def on_progress(pct: int, label: str) -> None:
        phase = "transcribing"
        low = (label or "").lower()
        if "extract" in low:
            phase = "extracting audio"
        elif "loading" in low:
            phase = "loading model"
        elif "writing" in low:
            phase = "writing"
        elif label == "done":
            phase = "done"
        base.update(
            {
                "status": "running",
                "phase": phase,
                "progress": int(pct),
                "progress_label": label or f"{pct}%",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write(status_file, base)

    try:
        from app import transcribe as tx

        model = args.model.strip() or tx.DEFAULT_MODEL
        language = args.language.strip() or tx.DEFAULT_LANGUAGE
        result = tx.transcribe_file(
            media,
            out_dir=out_dir,
            language=language,
            model=model,
            progress_cb=on_progress,
        )
        # Prefer UI-relative transcript URLs (shared volume under out/transcripts)
        for key in ("txt", "json", "srt"):
            p = result.get(f"{key}_path")
            if p:
                name = Path(p).name
                result[key] = f"/transcripts/{name}"
        base.update(
            {
                "ok": True,
                "status": "ok",
                "phase": "done",
                "progress": 100,
                "progress_label": "100%",
                "result": result,
                "error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write(status_file, base)
        return 0
    except Exception as e:
        base.update(
            {
                "ok": False,
                "status": "error",
                "phase": "done",
                "progress_label": "error",
                "error": str(e),
                "traceback": traceback.format_exc()[-4000:],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write(status_file, base)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
