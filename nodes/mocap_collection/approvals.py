"""Portable project metadata for BVH approval decisions."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace


def metadata_path(scene):
    from nodes.mocap_import.spec import _workflow_dir_for_node
    if scene is None:
        return None
    for view in scene.views() if hasattr(scene, "views") else []:
        window = view.window()
        for name in ("_pending_project_save_dir", "_current_project_dir"):
            directory = getattr(window, name, None)
            if directory:
                return Path(directory) / "mocap_approvals.json"
    directory = _workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
    return directory / "mocap_approvals.json" if directory else None


def path_key(raw, directory):
    path = Path(raw).expanduser()
    path = (directory / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        return path.relative_to(directory.resolve()).as_posix()
    except ValueError:
        return str(path)


def read(path):
    if not path.exists():
        return {"schema_version": 1, "files": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError(f"Unsupported approval metadata: {path}")
    return data


def save(scene, rows):
    path = metadata_path(scene)
    if path is None or not any("approved" in row for row in rows):
        return
    data = read(path)
    previous = json.dumps(data, sort_keys=True)
    for row in rows:
        if "approved" in row:
            data["files"][path_key(row["path"], path.parent)] = {"approved": bool(row["approved"])}
    if json.dumps(data, sort_keys=True) == previous and path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".mocap-approvals-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore(scene, rows):
    path = metadata_path(scene)
    if path is None:
        return rows
    records = read(path)["files"]
    for row in rows:
        record = records.get(path_key(row["path"], path.parent))
        if isinstance(record, dict) and isinstance(record.get("approved"), bool):
            row["approved"] = record["approved"]
    return rows
