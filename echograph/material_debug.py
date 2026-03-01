from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def material_debug_path() -> Path:
    try:
        root = Path(__file__).resolve().parents[1]
    except Exception:
        root = Path.cwd()
    log_dir = root / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return log_dir / "material_scene_debug.jsonl"


def material_debug_log(event: str, **fields) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": str(event or ""),
    }
    for key, value in (fields or {}).items():
        record[str(key)] = value
    try:
        with material_debug_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass
