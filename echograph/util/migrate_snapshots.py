# migrate_snapshots.py
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

# Matches:
#   point_cloud_d7ffc15c8b_20260118_073748.png
#   scene_MyScene_d7ffc15c8b_20260118_113323.json
RX = re.compile(
    r"^(?P<prefix>.+)_(?P<key>[0-9a-f]{10})_(?P<date>\d{8})_(?P<time>\d{6})\.(?P<ext>png|json)$",
    re.IGNORECASE,
)

def _params_to_dict(params_list: list[dict]) -> dict[str, dict]:
    # map param name -> param dict (so we can edit in-place)
    out = {}
    for p in params_list or []:
        n = p.get("name")
        if isinstance(n, str):
            out[n] = p
    return out

def migrate_workflow_thumbnails(workflow_path: str | Path) -> None:
    workflow_path = Path(workflow_path).expanduser().resolve()
    data = json.loads(workflow_path.read_text(encoding="utf-8"))

    base_dir = workflow_path.parent
    snapshots_dir = base_dir / "snapshots"

    moved = 0
    updated = 0
    missing = 0

    nodes = data.get("nodes") or []
    for node in nodes:
        params = _params_to_dict(node.get("params") or [])
        p_thumb = params.get("thumbnail")
        if not p_thumb:
            continue

        thumb_val = (p_thumb.get("value") or "").strip()
        if not thumb_val:
            continue

        src = Path(thumb_val)
        if not src.is_absolute():
            # if stored as relative, resolve relative to workflow dir
            src = (base_dir / src).resolve()

        if not src.exists():
            missing += 1
            continue

        m = RX.match(src.name)
        if not m:
            # filename doesn't match the old timestamp format; skip it
            continue

        prefix = m.group("prefix")
        key = m.group("key")
        dest_dir = snapshots_dir / f"{prefix}_{key}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / src.name

        # Move PNG/JSON pair if exists
        to_move = [src]
        pair = src.with_suffix(".json") if src.suffix.lower() == ".png" else src.with_suffix(".png")
        if pair.exists():
            to_move.append(pair)

        for f in to_move:
            target = dest_dir / f.name
            if target.exists():
                # Don't overwrite, leave it in place
                continue
            shutil.move(str(f), str(target))
            moved += 1

        # Update workflow thumbnail path to the moved png (or json if weird)
        new_thumb_path = dest_dir / src.name
        p_thumb["value"] = str(new_thumb_path)
        updated += 1

    # Write back workflow (make a backup next to it)
    backup = workflow_path.with_suffix(workflow_path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(str(workflow_path), str(backup))  # real backup of the original file

    workflow_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print("Done.")
    print("workflow:", workflow_path)
    print("snapshots_dir:", snapshots_dir)
    print("moved files:", moved)
    print("updated nodes:", updated)
    print("missing thumb files:", missing)

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m echograph.util.migrate_snapshots <path-to-workflow.json>")
        raise SystemExit(2)
    migrate_workflow_thumbnails(sys.argv[1])
