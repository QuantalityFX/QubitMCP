from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

VERSION_SUFFIX_RE = re.compile(r"_v_(\d{3,})$", re.IGNORECASE)
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
SLOT_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
SLIDE_HEADING_RE = re.compile(r"^##\s+Slide\s+\d+", re.IGNORECASE | re.MULTILINE)
VALID_TEMPLATE_STATUSES = {"draft", "needs_review", "approved", "deprecated", "archived"}
PACKAGE_REFERENCE_KEYS = (
    "source_human_template",
    "conversion_report",
    "package_files",
    "required_files",
    "required_assets",
    "supporting_files",
    "asset_files",
)


def _repo_root() -> Path:
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd().resolve()


def skills_root() -> Path:
    return _repo_root() / "Skills"


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root()).as_posix()
    except Exception:
        return str(path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _modified_timestamp(path: Path) -> str:
    try:
        return f"{path.stat().st_mtime:.6f}"
    except Exception:
        return ""


def _coerce_scalar(value: str) -> Any:
    text = str(value or "").strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def _parse_front_matter(text: str) -> Dict[str, Any]:
    match = FRONT_MATTER_RE.match(text or "")
    if not match:
        return {}

    data: Dict[str, Any] = {}
    current_key = ""
    for raw_line in match.group(1).splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            value = _coerce_scalar(stripped[2:])
            existing = data.get(current_key)
            if isinstance(existing, list):
                existing.append(value)
            else:
                data[current_key] = [value]
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        current_key = key
        value = value.strip()
        if value:
            data[key] = _coerce_scalar(value)
        else:
            data[key] = []
    return data


def _read_front_matter(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    return _parse_front_matter(text)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _version_from_stem(stem: str) -> str:
    match = VERSION_SUFFIX_RE.search(stem or "")
    if not match:
        return ""
    return f"v_{match.group(1)}"


def _family_from_stem(stem: str) -> str:
    return VERSION_SUFFIX_RE.sub("", stem or "")


def _version_sort_key(version_id: str) -> int:
    match = re.search(r"(\d+)$", str(version_id or ""))
    if not match:
        return -1
    try:
        return int(match.group(1))
    except Exception:
        return -1


def _list_markdown(directory: Path) -> List[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    try:
        return sorted(
            (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".md"),
            key=lambda p: p.name.lower(),
        )
    except Exception:
        return []


def _list_markdown_many(directories: Iterable[Path]) -> List[Path]:
    paths: List[Path] = []
    seen: set[str] = set()
    for directory in directories:
        for path in _list_markdown(directory):
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return sorted(paths, key=lambda p: (_display_path(p).lower(), p.name.lower()))


@dataclass
class SkillAsset:
    kind: str
    path: str
    name: str
    modified_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "kind": self.kind,
            "path": self.path,
            "name": self.name,
            "modified_at": self.modified_at,
            "warnings": list(self.warnings),
        }
        data.update(self.metadata)
        return data


@dataclass
class SkillsLibrarySnapshot:
    root: str
    human_templates: List[SkillAsset] = field(default_factory=list)
    agent_templates: List[SkillAsset] = field(default_factory=list)
    task_guides: List[SkillAsset] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "human_templates": [asset.to_dict() for asset in self.human_templates],
            "agent_templates": [asset.to_dict() for asset in self.agent_templates],
            "task_guides": [asset.to_dict() for asset in self.task_guides],
            "warnings": list(self.warnings),
        }


def _validate_agent_metadata(metadata: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    required = (
        "template_id",
        "template_family_id",
        "template_version_id",
        "target_agent",
        "artifact_kind",
        "delivery_formats",
        "status",
    )
    for key in required:
        value = metadata.get(key)
        if value is None or value == "" or value == []:
            warnings.append(f"Missing `{key}`.")
    formats = metadata.get("delivery_formats")
    if formats is not None and not isinstance(formats, list):
        warnings.append("`delivery_formats` should be a list.")
    status = str(metadata.get("status", "") or "").strip().lower()
    if status and status not in VALID_TEMPLATE_STATUSES:
        warnings.append(f"`status` should be one of: {', '.join(sorted(VALID_TEMPLATE_STATUSES))}.")
    if int(metadata.get("slot_count", 0) or 0) <= 0:
        warnings.append("No named `{{...}}` slots found.")
    if int(metadata.get("slide_count", 0) or 0) <= 0:
        warnings.append("No `## Slide ##` headings found.")
    return warnings


def _validate_task_guide_metadata(metadata: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    required = (
        "guide_id",
        "guide_family_id",
        "guide_version_id",
        "target_agent",
        "task_kind",
        "artifact_kind",
        "status",
    )
    for key in required:
        value = metadata.get(key)
        if value is None or value == "" or value == []:
            warnings.append(f"Missing `{key}`.")
    kind = str(metadata.get("kind", "") or "").strip().lower()
    if kind and kind != "task_guide":
        warnings.append("`kind` should be `task_guide`.")
    for key in ("required_inputs", "default_steps"):
        value = metadata.get(key)
        if value is not None and value != [] and not isinstance(value, list):
            warnings.append(f"`{key}` should be a list.")
    status = str(metadata.get("status", "") or "").strip().lower()
    if status and status not in VALID_TEMPLATE_STATUSES:
        warnings.append(f"`status` should be one of: {', '.join(sorted(VALID_TEMPLATE_STATUSES))}.")
    return warnings


def _coerce_reference_values(value: Any) -> List[str]:
    if value is None or value == "" or value == []:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, tuple):
        raw_values = list(value)
    else:
        raw_values = [value]
    out: List[str] = []
    for raw in raw_values:
        text = str(raw or "").strip().strip("'\"`")
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            pieces = [piece.strip().strip("'\"`") for piece in text[1:-1].split(",")]
        elif "," in text and not re.search(r"://", text):
            pieces = [piece.strip().strip("'\"`") for piece in text.split(",")]
        else:
            pieces = [text]
        for piece in pieces:
            if piece and piece not in out:
                out.append(piece)
    return out


def _resolve_package_reference(raw: str, *, asset_path: Path, root_path: Path) -> Path:
    text = str(raw or "").strip().replace("\\", "/")
    candidate = Path(text).expanduser()
    repo_root = _repo_root()
    if candidate.is_absolute():
        return candidate.resolve()
    if text.lower().startswith("skills/"):
        return (repo_root / candidate).resolve()
    parent_candidate = (asset_path.parent / candidate).resolve()
    if parent_candidate.exists():
        return parent_candidate
    return (root_path / candidate).resolve()


def _package_reference_warnings(metadata: Dict[str, Any], *, asset_path: Path, root_path: Path) -> List[str]:
    warnings: List[str] = []
    root = root_path.resolve()
    checked = 0
    missing: List[str] = []
    for key in PACKAGE_REFERENCE_KEYS:
        for raw in _coerce_reference_values(metadata.get(key)):
            if re.search(r"://", raw):
                warnings.append(f"`{key}` uses a URL; package files must be local Skills paths: {raw}")
                continue
            try:
                resolved = _resolve_package_reference(raw, asset_path=asset_path, root_path=root)
            except Exception as exc:
                warnings.append(f"`{key}` could not be resolved: {raw} ({exc})")
                continue
            if not _is_relative_to(resolved, root):
                warnings.append(f"`{key}` points outside the Skills library: {raw}")
                continue
            checked += 1
            if not resolved.exists():
                missing.append(f"{key}: {raw}")
    if checked:
        metadata["package_file_count"] = checked
    if missing:
        metadata["missing_package_files"] = missing
        for item in missing:
            warnings.append(f"Missing package file `{item}`.")
    return warnings


def _agent_template_asset(path: Path, root: Path | None = None) -> SkillAsset:
    try:
        text = _read_text(path)
    except Exception:
        text = ""
    metadata = _parse_front_matter(text)
    version_from_name = _version_from_stem(path.stem)
    family_from_name = _family_from_stem(path.stem)
    metadata.setdefault("template_version_id", version_from_name)
    metadata.setdefault("template_family_id", family_from_name)
    if not metadata.get("template_id") and metadata.get("template_family_id") and metadata.get("template_version_id"):
        metadata["template_id"] = f"{metadata['template_family_id']}_{metadata['template_version_id']}"
    metadata["version_sort"] = _version_sort_key(str(metadata.get("template_version_id") or ""))
    slots = SLOT_RE.findall(text or "")
    slot_counts: Dict[str, int] = {}
    for slot in slots:
        slot_counts[slot] = slot_counts.get(slot, 0) + 1
    metadata["slot_count"] = len(slot_counts)
    metadata["duplicate_slots"] = sorted(slot for slot, count in slot_counts.items() if count > 1)
    metadata["slide_count"] = len(SLIDE_HEADING_RE.findall(text or ""))
    warnings = _validate_agent_metadata(metadata)
    if not version_from_name:
        warnings.append("Filename is missing `_v_###` version suffix.")
    if version_from_name and metadata.get("template_version_id") != version_from_name:
        warnings.append("Filename version does not match `template_version_id`.")
    if root is not None:
        warnings.extend(_package_reference_warnings(metadata, asset_path=path, root_path=root))
    return SkillAsset(
        kind="agent_template",
        path=_display_path(path),
        name=path.name,
        modified_at=_modified_timestamp(path),
        metadata=metadata,
        warnings=warnings,
    )


def _task_guide_asset(path: Path, root: Path | None = None) -> SkillAsset:
    try:
        text = _read_text(path)
    except Exception:
        text = ""
    metadata = _parse_front_matter(text)
    version_from_name = _version_from_stem(path.stem)
    family_from_name = _family_from_stem(path.stem)
    metadata.setdefault("kind", "task_guide")
    metadata.setdefault("guide_version_id", version_from_name)
    metadata.setdefault("guide_family_id", family_from_name)
    if not metadata.get("guide_id") and metadata.get("guide_family_id") and metadata.get("guide_version_id"):
        metadata["guide_id"] = f"{metadata['guide_family_id']}_{metadata['guide_version_id']}"
    metadata["version_sort"] = _version_sort_key(str(metadata.get("guide_version_id") or ""))
    warnings = _validate_task_guide_metadata(metadata)
    if not version_from_name:
        warnings.append("Filename is missing `_v_###` version suffix.")
    if version_from_name and metadata.get("guide_version_id") != version_from_name:
        warnings.append("Filename version does not match `guide_version_id`.")
    if root is not None:
        warnings.extend(_package_reference_warnings(metadata, asset_path=path, root_path=root))
    return SkillAsset(
        kind="task_guide",
        path=_display_path(path),
        name=path.name,
        modified_at=_modified_timestamp(path),
        metadata=metadata,
        warnings=warnings,
    )


def _human_template_asset(path: Path) -> SkillAsset:
    return SkillAsset(
        kind="human_template",
        path=_display_path(path),
        name=path.name,
        modified_at=_modified_timestamp(path),
        metadata={"template_family_id": _family_from_stem(path.stem)},
    )


def scan_skills_library(root: str | Path | None = None) -> SkillsLibrarySnapshot:
    base = Path(root).expanduser() if root else skills_root()
    if not base.is_absolute():
        base = (_repo_root() / base).resolve()
    else:
        base = base.resolve()

    snapshot = SkillsLibrarySnapshot(root=_display_path(base))
    if not base.exists():
        snapshot.warnings.append(f"Skills root does not exist: {_display_path(base)}")
        return snapshot

    human_dir = base / "human_templates"
    agent_dir = base / "agent_templates"
    task_guide_dirs = [base / "task_guides", base / "agent_guides"]
    if not human_dir.exists():
        snapshot.warnings.append(f"Missing human templates directory: {_display_path(human_dir)}")
    if not agent_dir.exists():
        snapshot.warnings.append(f"Missing agent templates directory: {_display_path(agent_dir)}")

    snapshot.human_templates = [_human_template_asset(path) for path in _list_markdown(human_dir)]
    snapshot.agent_templates = _annotate_agent_versions([_agent_template_asset(path, base) for path in _list_markdown(agent_dir)])
    snapshot.task_guides = _annotate_task_guide_versions([_task_guide_asset(path, base) for path in _list_markdown_many(task_guide_dirs)])
    return snapshot


def _annotate_agent_versions(assets: List[SkillAsset]) -> List[SkillAsset]:
    by_family: Dict[str, List[SkillAsset]] = {}
    for asset in assets:
        family = str(asset.metadata.get("template_family_id", "") or "").strip()
        if not family:
            continue
        by_family.setdefault(family, []).append(asset)

    for family, family_assets in by_family.items():
        ordered = sorted(
            family_assets,
            key=lambda asset: (
                int(asset.metadata.get("version_sort", -1) or -1),
                str(asset.metadata.get("template_version_id", "") or ""),
                asset.name,
            ),
            reverse=True,
        )
        latest = ordered[0] if ordered else None
        approved = [
            asset
            for asset in ordered
            if str(asset.metadata.get("status", "") or "").strip().lower() == "approved"
        ]
        latest_approved = approved[0] if approved else None
        for asset in family_assets:
            asset.metadata["template_family_id"] = family
            asset.metadata["family_version_count"] = len(family_assets)
            if latest is not None:
                asset.metadata["latest_version_id"] = latest.metadata.get("template_version_id", "")
                asset.metadata["latest_template_id"] = latest.metadata.get("template_id", "")
                asset.metadata["latest_template_path"] = latest.path
                asset.metadata["is_latest_version"] = asset.path == latest.path
            else:
                asset.metadata["is_latest_version"] = False
            if latest_approved is not None:
                asset.metadata["latest_approved_version_id"] = latest_approved.metadata.get("template_version_id", "")
                asset.metadata["latest_approved_template_id"] = latest_approved.metadata.get("template_id", "")
                asset.metadata["latest_approved_template_path"] = latest_approved.path
                asset.metadata["is_latest_approved"] = asset.path == latest_approved.path
                asset.metadata["has_approved_version"] = True
            else:
                asset.metadata["latest_approved_version_id"] = ""
                asset.metadata["latest_approved_template_id"] = ""
                asset.metadata["latest_approved_template_path"] = ""
                asset.metadata["is_latest_approved"] = False
                asset.metadata["has_approved_version"] = False

    return sorted(
        assets,
        key=lambda asset: (
            str(asset.metadata.get("template_family_id", "") or "").lower(),
            -int(asset.metadata.get("version_sort", -1) or -1),
            asset.name.lower(),
        ),
    )


def _annotate_task_guide_versions(assets: List[SkillAsset]) -> List[SkillAsset]:
    by_family: Dict[str, List[SkillAsset]] = {}
    for asset in assets:
        family = str(asset.metadata.get("guide_family_id", "") or "").strip()
        if not family:
            continue
        by_family.setdefault(family, []).append(asset)

    for family, family_assets in by_family.items():
        ordered = sorted(
            family_assets,
            key=lambda asset: (
                int(asset.metadata.get("version_sort", -1) or -1),
                str(asset.metadata.get("guide_version_id", "") or ""),
                asset.name,
            ),
            reverse=True,
        )
        latest = ordered[0] if ordered else None
        approved = [
            asset
            for asset in ordered
            if str(asset.metadata.get("status", "") or "").strip().lower() == "approved"
        ]
        latest_approved = approved[0] if approved else None
        for asset in family_assets:
            asset.metadata["guide_family_id"] = family
            asset.metadata["family_version_count"] = len(family_assets)
            if latest is not None:
                asset.metadata["latest_version_id"] = latest.metadata.get("guide_version_id", "")
                asset.metadata["latest_asset_id"] = latest.metadata.get("guide_id", "")
                asset.metadata["latest_asset_path"] = latest.path
                asset.metadata["is_latest_version"] = asset.path == latest.path
            else:
                asset.metadata["is_latest_version"] = False
            if latest_approved is not None:
                asset.metadata["latest_approved_version_id"] = latest_approved.metadata.get("guide_version_id", "")
                asset.metadata["latest_approved_asset_id"] = latest_approved.metadata.get("guide_id", "")
                asset.metadata["latest_approved_asset_path"] = latest_approved.path
                asset.metadata["is_latest_approved"] = asset.path == latest_approved.path
                asset.metadata["has_approved_version"] = True
            else:
                asset.metadata["latest_approved_version_id"] = ""
                asset.metadata["latest_approved_asset_id"] = ""
                asset.metadata["latest_approved_asset_path"] = ""
                asset.metadata["is_latest_approved"] = False
                asset.metadata["has_approved_version"] = False

    return sorted(
        assets,
        key=lambda asset: (
            str(asset.metadata.get("guide_family_id", "") or "").lower(),
            -int(asset.metadata.get("version_sort", -1) or -1),
            asset.name.lower(),
        ),
    )


def latest_by_family(assets: Iterable[SkillAsset], *, status: str | None = None) -> Dict[str, SkillAsset]:
    out: Dict[str, SkillAsset] = {}
    wanted_status = str(status or "").strip().lower()
    for asset in assets:
        data = asset.to_dict()
        if wanted_status and str(data.get("status", "") or "").strip().lower() != wanted_status:
            continue
        family = str(data.get("template_family_id", "") or "").strip()
        if not family:
            continue
        current = out.get(family)
        if current is None:
            out[family] = asset
            continue
        if int(data.get("version_sort", -1) or -1) > int(current.to_dict().get("version_sort", -1) or -1):
            out[family] = asset
    return out


def resolve_library_path(path: str | Path, root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root else skills_root()
    if not base.is_absolute():
        base = (_repo_root() / base).resolve()
    else:
        base = base.resolve()

    raw_path = Path(str(path or "").strip()).expanduser()
    if not raw_path.is_absolute():
        raw_path = (_repo_root() / raw_path).resolve()
    else:
        raw_path = raw_path.resolve()

    if not _is_relative_to(raw_path, base):
        raise ValueError(f"Path is outside the Skills library: {_display_path(raw_path)}")
    return raw_path


def update_skill_asset_status(path: str | Path, status: str, root: str | Path | None = None) -> tuple[bool, str]:
    next_status = str(status or "").strip().lower()
    if next_status not in VALID_TEMPLATE_STATUSES:
        return False, f"Unsupported asset status: {status}"

    try:
        asset_path = resolve_library_path(path, root=root)
    except Exception as exc:
        return False, str(exc)

    base = Path(root).expanduser() if root else skills_root()
    if not base.is_absolute():
        base = (_repo_root() / base).resolve()
    else:
        base = base.resolve()
    agent_dir = base / "agent_templates"
    task_guide_dirs = [base / "task_guides", base / "agent_guides"]
    if _is_relative_to(asset_path, agent_dir):
        asset_label = "template"
    elif any(_is_relative_to(asset_path, directory) for directory in task_guide_dirs):
        asset_label = "task guide"
    else:
        return False, "Only files under `Skills/agent_templates/`, `Skills/task_guides/`, or `Skills/agent_guides/` can be approved from the Skills node."
    if asset_path.suffix.lower() != ".md":
        return False, "Only markdown Skills assets can be updated."
    if not asset_path.exists():
        return False, f"Skills asset not found: {_display_path(asset_path)}"

    try:
        text = asset_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = asset_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, f"Failed to read Skills asset: {exc}"

    match = FRONT_MATTER_RE.match(text or "")
    if not match:
        return False, "Skills asset must have front matter before status can be updated."

    lines = match.group(1).splitlines()
    replaced = False
    for idx, line in enumerate(lines):
        if re.match(r"^\s*status\s*:", line):
            lines[idx] = f"status: {next_status}"
            replaced = True
            break
    if not replaced:
        lines.append(f"status: {next_status}")

    suffix = text[match.end():]
    new_text = "---\n" + "\n".join(lines).rstrip() + "\n---\n" + suffix
    try:
        asset_path.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return False, f"Failed to update Skills asset status: {exc}"
    return True, f"Updated {asset_label} {asset_path.name} to `{next_status}`."


def update_agent_template_status(path: str | Path, status: str, root: str | Path | None = None) -> tuple[bool, str]:
    return update_skill_asset_status(path, status, root=root)
