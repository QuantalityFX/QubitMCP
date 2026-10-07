"""Render matching and coordinated folder renames for collection review."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

from nodes.video_player.spec import _IMAGE_EXTS, _natural_sort_key


def resolved_path(raw, base=None):
    path = Path(raw).expanduser()
    if not path.is_absolute() and base is not None:
        path = Path(base) / path
    return path.resolve()


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def ensure_idle(scene):
    if getattr(scene, "_foreach_active_session", None) is not None:
        raise ValueError("Stop the For Each render before changing review assignments or folders.")


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def scan_renders(rows, base, root, cancelled=lambda: False):
    """Match exact recorded BVH paths first; accept only unique name fallbacks."""
    root = resolved_path(root)
    if not root.is_dir():
        raise ValueError("Select an existing render folder.")
    keys = {path_key(resolved_path(row["path"], base)) for row in rows}
    names = {}
    for row in rows:
        source = resolved_path(row["path"], base)
        names.setdefault(source.parent.name.casefold(), set()).add(path_key(source))
    candidates, manifests, skipped = [], {}, 0
    def walk_error(exc):
        raise exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        if cancelled():
            return {"cancelled": True, "assignments": {}, "skipped": 0}
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        folder = Path(directory)
        for name in files:
            if name.endswith("_manifest.json"):
                manifest = folder / name
                try:
                    for record in read_json(manifest).get("takes", []):
                        key = path_key(record["take_folder"])
                        manifests.setdefault(key, []).append(str(manifest))
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
        frames = sorted((folder / name for name in files if Path(name).suffix.lower() in _IMAGE_EXTS), key=_natural_sort_key)
        if not frames:
            continue
        metadata_path = folder / "take.json"
        metadata = {}
        if metadata_path.exists():
            try:
                metadata = read_json(metadata_path)
                if not isinstance(metadata, dict):
                    raise ValueError("Invalid take metadata")
            except (OSError, ValueError):
                skipped += 1
                continue
            if metadata.get("status") not in (None, "completed"):
                skipped += 1
                continue
        source = metadata.get("source_path")
        if source:
            matched = path_key(resolved_path(source, base))
            if matched not in keys:
                skipped += 1
                continue  # A conflicting exact path must never become a name match.
        else:
            label = re.sub(r"^Take_\d+_", "", folder.name, flags=re.IGNORECASE)
            possible = names.get(label.casefold(), set())
            if len(possible) != 1:
                skipped += 1
                continue
            matched = next(iter(possible))
        try:
            fps = float(metadata.get("frame_rate", 30))
        except (TypeError, ValueError):
            fps = 30.0
        assignment = {"folder": str(folder), "first_frame": str(frames[0]),
                      "frame_rate": max(1.0, min(240.0, fps)),
                      "frame_stamp": frames[0].stat().st_mtime_ns}
        # Prefer the latest completed take when a BVH has multiple renders.
        stamp = metadata_path.stat().st_mtime_ns if metadata_path.exists() else frames[0].stat().st_mtime_ns
        candidates.append((stamp, str(folder), matched, assignment))
    assignments = {}
    for _, _, matched, assignment in sorted(candidates):
        assignment["manifests"] = manifests.get(path_key(assignment["folder"]), [])
        assignments[matched] = assignment
    return {"cancelled": False, "assignments": assignments, "skipped": skipped}


def apply_assignments(model, scene, result):
    from . import spec
    ensure_idle(scene)
    if result.get("cancelled"):
        return 0
    base = spec.mocap._workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
    rows = spec.entries(model)
    count = 0
    for row in rows:
        assignment = result["assignments"].get(path_key(resolved_path(row["path"], base)))
        if assignment is not None:
            # Approval belongs to the BVH path, not transient render metadata.
            row["render"] = assignment
            count += 1
    spec.save_state(model, rows, spec.output_number(model), scene)
    return count


def validate_folder_name(name):
    name = name.strip()
    if (not name or len(name) > 120 or name in (".", "..") or name.endswith((".", " "))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.IGNORECASE)):
        raise ValueError("Enter a valid folder name without slashes or Windows reserved characters.")
    return name


def remap_path(raw, moves, base=None):
    if not raw:
        return raw
    path = resolved_path(raw, base)
    for old, new in sorted(moves, key=lambda pair: len(pair[0].parts), reverse=True):
        try:
            return str(new / path.relative_to(old))
        except ValueError:
            continue
    return raw


def rewrite_record(record, moves, base=None):
    result = copy.deepcopy(record)
    for key in ("source_path", "take_folder", "output_template", "first_frame", "folder"):
        if result.get(key):
            result[key] = remap_path(result[key], moves, base)
    return result


def rename_entry(model, scene, number, name):
    """Rename sibling folders without overwriting; roll back if any write fails."""
    from . import spec
    ensure_idle(scene)
    name = validate_folder_name(name)
    base = spec.mocap._workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
    rows = spec.entries(model)
    if not 1 <= number <= len(rows):
        raise ValueError("Select a BVH first.")
    source_file = resolved_path(rows[number - 1]["path"], base)
    source_folder = source_file.parent
    if not source_file.is_file() or source_folder.parent == source_folder:
        raise ValueError("The BVH folder is missing or cannot be renamed.")
    # A folder rename affects every sibling BVH; keep all open collections valid.
    models = {id(model): model}
    for item in getattr(scene, "_node_items", {}).values():
        if item.model.kind == "mocap_collection":
            models[id(item.model)] = item.model
    states = [(current, spec.entries(current), spec.output_number(current)) for current in models.values()]
    moves = [(source_folder, source_folder.with_name(name))]
    related = []
    for _, current_rows, _ in states:
        for row in current_rows:
            path = resolved_path(row["path"], base)
            if path.is_relative_to(source_folder):
                related.append(row)
                folder = row.get("render", {}).get("folder")
                if folder and path.parent == source_folder:
                    old = resolved_path(folder)
                    if old == source_folder:
                        continue
                    prefix = re.match(r"^(Take_\d+)_", old.name, re.IGNORECASE)
                    new = old.with_name(f"{prefix.group(1)}_{name}" if prefix else name)
                    moves.append((old, new))
    moves = list(dict.fromkeys((old, new) for old, new in moves if old != new))
    if not moves:
        return
    destinations = set()
    for old, new in moves:
        if not old.is_dir() or old.is_symlink() or getattr(old.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Folder is missing or is a linked folder: {old}")
        if new.parent != old.parent:
            raise ValueError("Folders must stay inside their existing parent directory.")
        if new.exists() or path_key(new) in destinations:
            raise ValueError(f"A folder already uses that name: {new}")
        destinations.add(path_key(new))
        for other, _ in moves:
            if other != old and (old.is_relative_to(other) or other.is_relative_to(old)):
                raise ValueError("The BVH and render folders must be separate, not nested, to rename together.")

    metadata_paths = set()
    for row in related:
        render = row.get("render", {})
        if render.get("folder"):
            take = resolved_path(render["folder"]) / "take.json"
            if take.exists():
                metadata_paths.add(take)
            metadata_paths.update(resolved_path(path) for path in render.get("manifests", []) if Path(path).is_file())
    # Older takes may share a manifest with the assigned take. Keep their source
    # references valid even though only the assigned render folder is renamed.
    for path in list(metadata_paths):
        if path.name.endswith("_manifest.json"):
            manifest = read_json(path)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("takes", []), list):
                raise ValueError(f"Invalid render manifest: {path}")
            for record in manifest.get("takes", []):
                if not isinstance(record, dict):
                    raise ValueError(f"Invalid take record in {path}")
                source = record.get("source_path", "")
                if source and remap_path(source, moves, base) != source and record.get("take_folder"):
                    take = resolved_path(record["take_folder"]) / "take.json"
                    if take.exists():
                        metadata_paths.add(take)
    documents = []
    for path in metadata_paths:
        original = path.read_bytes()
        data = json.loads(original.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError(f"Invalid render metadata: {path}")
        changed = rewrite_record(data, moves, base)
        if "takes" in changed:
            changed["takes"] = [rewrite_record(record, moves, base) for record in changed["takes"]]
        documents.append((path, original, changed))
    completed, written = [], []
    try:
        for old, new in moves:
            old.rename(new)
            completed.append((old, new))
        for old_path, original, data in documents:
            path = Path(remap_path(str(old_path), moves))
            # Keep original bytes for rollback, including the document being written.
            written.append((path, original))
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        failures = []
        for path, original in reversed(written):
            try:
                path.write_bytes(original)
            except OSError as rollback:
                failures.append(str(rollback))
        for old, new in reversed(completed):
            try:
                new.rename(old)
            except OSError as rollback:
                failures.append(str(rollback))
        if failures:
            raise OSError(f"Rename failed: {exc}. Could not fully restore folders: {'; '.join(failures)}") from exc
        raise
    for current, current_rows, active in states:
        changed = False
        for row in current_rows:
            path = remap_path(row["path"], moves, base)
            if path != row["path"]:
                row["path"] = path
                changed = True
            if row.get("render"):
                updated = rewrite_record(row["render"], moves)
                updated["manifests"] = [remap_path(path, moves) for path in updated.get("manifests", [])]
                changed |= updated != row["render"]
                row["render"] = updated
        if changed:
            spec.save_state(current, current_rows, active, scene)
            spec.schedule_live_preview(current, scene)
