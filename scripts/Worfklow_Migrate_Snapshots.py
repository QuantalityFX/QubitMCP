"""
Python Node Utility: Snapshot Migration + Diagnostics (EchoGraph)

What this does:
1) Finds the currently-open workflow file path from the running EchoGraph window (_current_path).
2) Derives the snapshots folder next to that workflow: <workflow_folder>/snapshots
3) Scans nodes in the workflow JSON to see how many 'thumbnail' paths are missing on disk.
4) Prints a small diagnostic summary to the Python node console.
5) Runs the project's migration utility:
      echograph.util.migrate_snapshots.migrate_workflow_thumbnails(workflow_path)

Why this is useful:
- No hardcoded paths.
- Lets you run migration directly inside the app, against the workflow you currently have open.
- If "missing thumbs" > 0, it likely means the workflow references thumbnails that were moved/deleted
  or snapshots are stored in a different folder than expected.
"""

import json
from pathlib import Path
from PySide6 import QtWidgets

# Your existing migration utility
from echograph.util.migrate_snapshots import migrate_workflow_thumbnails


def find_workflow_path() -> str | None:
    """
    Try to find the current workflow path from the top-level EchoGraph window.
    EchoGraph stores it on the main window as: window._current_path
    """
    app = QtWidgets.QApplication.instance()
    if not app:
        return None

    for w in app.topLevelWidgets():
        p = getattr(w, "_current_path", None)
        if p:
            return str(p)

    return None


def params_map(params: list[dict] | None) -> dict:
    """Convert node params list into a name->value dict for quick lookups."""
    return {p.get("name"): p.get("value") for p in (params or [])}


# ---- Main ----

wf = find_workflow_path()
print("current workflow:", wf)

# If this fails, you likely need to open/save the workflow so _current_path is set.
if not wf:
    raise RuntimeError("No _current_path found. Open/save a workflow first.")

wf = Path(wf)
data = json.loads(wf.read_text(encoding="utf-8"))

# Snapshots are expected to live beside the workflow folder: <workflow_dir>/snapshots
base_dir = wf.parent
snap_dir = base_dir / "snapshots"

# Collect missing thumbnail file paths referenced by nodes
missing = []
for node in data.get("nodes", []):
    pm = params_map(node.get("params"))
    thumb = (pm.get("thumbnail") or "").strip()
    if not thumb:
        continue

    p = Path(thumb)
    # Support relative paths (resolve relative to workflow dir)
    if not p.is_absolute():
        p = (base_dir / p)

    if not p.exists():
        missing.append(str(p))

# Quick snapshot folder health check (only top-level pngs)
pngs_flat = list(snap_dir.glob("*.png")) if snap_dir.exists() else []

print("snapshots_dir:", snap_dir)
print("png count in snapshots (top-level):", len(pngs_flat))
print("missing thumbs:", len(missing))
print("example missing thumbs (first 10):")
for m in missing[:10]:
    print(" -", m)

# Run the migration utility on the currently open workflow file
migrate_workflow_thumbnails(wf)

print("migrate done")
