from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from echograph.services.skills_library import resolve_library_path, scan_skills_library, skills_root


SLIDE_MARKER_RE = re.compile(r"(?m)^SLIDE\s+(\d{1,2})\s*$", re.IGNORECASE)
TABLE_ROW_RE = re.compile(r"^\|\s*\*\*(?P<key>[^*|]+)\*\*\s*\|\s*(?P<value>.*?)\s*\|\s*$")
VERSION_SUFFIX_RE = re.compile(r"_v_(\d{3,})$", re.IGNORECASE)
AGENT_SLOT_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
AGENT_SLIDE_HEADING_RE = re.compile(r"(?m)^##\s+Slide\s+\d{1,3}\b", re.IGNORECASE)
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
AGENT_TEMPLATE_TAG_RE = re.compile(
    r"<agent_template_markdown\b[^>]*>(?P<body>.*?)</agent_template_markdown>",
    re.IGNORECASE | re.DOTALL,
)
CONVERSION_REPORT_TAG_RE = re.compile(
    r"<conversion_report_markdown\b[^>]*>(?P<body>.*?)</conversion_report_markdown>",
    re.IGNORECASE | re.DOTALL,
)
TASK_GUIDE_TAG_RE = re.compile(
    r"<task_guide_markdown\b[^>]*>(?P<body>.*?)</task_guide_markdown>",
    re.IGNORECASE | re.DOTALL,
)


def _repo_root() -> Path:
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd().resolve()


def _library_root(root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root else skills_root()
    if not base.is_absolute():
        return (_repo_root() / base).resolve()
    return base.resolve()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root()).as_posix()
    except Exception:
        return str(path)


def _normalize_path_text(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").lower()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _coerce_front_matter_scalar(value: str) -> Any:
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
            value = _coerce_front_matter_scalar(stripped[2:])
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
        data[key] = _coerce_front_matter_scalar(value) if value else []
    return data


def _ascii(value: Any) -> str:
    text = str(value or "")
    text = (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2022", "-")
    )
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _clean_inline(value: Any) -> str:
    text = _ascii(value)
    text = text.replace("\\[", "[").replace("\\]", "]")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _slug(value: str, fallback: str = "item") -> str:
    text = _ascii(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or fallback


def _title_from_intro(text: str, fallback: str) -> str:
    for line in str(text or "").splitlines():
        clean = _clean_inline(line)
        if clean:
            return clean[:120]
    return fallback


def _title_from_source_path(path: Path) -> str:
    stem = VERSION_SUFFIX_RE.sub("", path.stem)
    stem = re.sub(r"(?i)_human_template", "", stem)
    stem = re.sub(r"(?i)_template(_|$)", r"\1", stem)
    title = re.sub(r"[_-]+", " ", stem).strip()
    return _clean_inline(title).title() or "Agent Template"


def _title_from_section(section: str, fallback: str) -> str:
    lines = str(section or "").splitlines()
    for line in lines[1:8]:
        clean = _clean_inline(line)
        if clean and not clean.startswith("#") and not clean.lower().startswith("slide "):
            return clean[:120]
    return fallback


def _question_from_section(section: str) -> str:
    match = re.search(r"\*\*(.+?)\*\*", section or "", re.DOTALL)
    if not match:
        return ""
    return _clean_inline(match.group(1))[:260]


def _table_guidance(section: str) -> Dict[str, str]:
    guidance: Dict[str, str] = {}
    for raw_line in str(section or "").splitlines():
        match = TABLE_ROW_RE.match(raw_line.strip())
        if not match:
            continue
        key = _slug(match.group("key"), fallback="field")
        value = _clean_inline(match.group("value"))
        if value:
            guidance[key] = value
    return guidance


def _slide_slot_prefix(number: int) -> str:
    return f"slide_{number:02d}"


def _placeholder_slots(section: str) -> List[str]:
    slots: List[str] = []
    for match in re.finditer(r"\\?\[(?P<name>[A-Z][A-Z0-9 _/-]{2,80})\s*:", section or ""):
        key = _slug(match.group("name"), fallback="")
        if key and key not in slots:
            slots.append(key)
    return slots


def _semantic_type_hints(title: str, question: str, guidance: Dict[str, str]) -> List[str]:
    text = " ".join([title, question, *guidance.keys(), *guidance.values()])
    words = [word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", _ascii(text).lower())]
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "your",
        "what",
        "when",
        "where",
        "which",
        "does",
        "must",
        "slide",
        "best",
        "core",
        "avoid",
        "proof",
        "note",
        "visual",
        "copy",
        "example",
    }
    hints: List[str] = []
    for word in words:
        if word in stop or len(word) < 4:
            continue
        key = _slug(word, fallback="")
        if key and key not in hints:
            hints.append(key)
        if len(hints) >= 5:
            break
    return hints or ["concept", "evidence", "source_note"]


def _validation_from_guidance(guidance: Dict[str, str]) -> List[str]:
    rules = [
        "Must fill required visible slots from Data Nexus points or report them as missing.",
        "Must attach source point IDs for generated claims.",
    ]
    for key in ("avoid", "proof_to_collect", "core_job"):
        value = guidance.get(key)
        if not value:
            continue
        label = key.replace("_", " ")
        rules.append(f"Respect source guidance for {label}: {value}")
    return rules[:5]


def _slot_names_for_slide(slide: "ConvertedSlide") -> List[str]:
    prefix = slide.slot_prefix
    source_slots = list(getattr(slide, "source_slots", []) or [])
    if source_slots:
        return [f"{prefix}.{slot}" for slot in source_slots]
    return [
        f"{prefix}.headline",
        f"{prefix}.supporting_proof",
        f"{prefix}.visual_brief",
        f"{prefix}.speaker_note",
    ]


def _artifact_slot_prefix(artifact_kind: str) -> str:
    token = _slug(artifact_kind, fallback="artifact")
    if token in {"html_deck", "deck", "pitch_deck"}:
        return "deck"
    return "artifact"


def _artifact_slot_names(artifact_kind: str) -> List[str]:
    prefix = _artifact_slot_prefix(artifact_kind)
    return [
        f"{prefix}.title",
        f"{prefix}.audience",
        f"{prefix}.goal",
        f"{prefix}.review_status",
        f"{prefix}.generated_at",
        f"{prefix}.source_conversation_id",
        f"{prefix}.template_family_id",
        f"{prefix}.template_version_id",
        f"{prefix}.template_version",
    ]


def _total_slot_count(slides: List["ConvertedSlide"], artifact_kind: str) -> int:
    return len(_artifact_slot_names(artifact_kind)) + sum(len(_slot_names_for_slide(slide)) + 2 for slide in slides)


@dataclass
class ConvertedSlide:
    number: int
    title: str
    slide_id: str
    question: str = ""
    guidance: Dict[str, str] = field(default_factory=dict)
    source_slots: List[str] = field(default_factory=list)
    accepted_point_types: List[str] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def slot_prefix(self) -> str:
        return _slide_slot_prefix(self.number)


@dataclass
class TeacherConversionResult:
    ok: bool
    message: str
    agent_template_path: str = ""
    conversion_report_path: str = ""
    template_family_id: str = ""
    template_version_id: str = ""
    slide_count: int = 0
    slot_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "agent_template_path": self.agent_template_path,
            "conversion_report_path": self.conversion_report_path,
            "template_family_id": self.template_family_id,
            "template_version_id": self.template_version_id,
            "slide_count": self.slide_count,
            "slot_count": self.slot_count,
            "warnings": list(self.warnings),
        }


@dataclass
class TeacherGuideResult:
    ok: bool
    message: str
    task_guide_path: str = ""
    guide_family_id: str = ""
    guide_version_id: str = ""
    target_agent: str = ""
    task_kind: str = ""
    artifact_kind: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "task_guide_path": self.task_guide_path,
            "guide_family_id": self.guide_family_id,
            "guide_version_id": self.guide_version_id,
            "target_agent": self.target_agent,
            "task_kind": self.task_kind,
            "artifact_kind": self.artifact_kind,
            "warnings": list(self.warnings),
        }


@dataclass
class TeacherConversionRequest:
    ok: bool
    message: str
    prompt: str = ""
    signature: str = ""
    source_path: str = ""
    agent_template_path: str = ""
    conversion_report_path: str = ""
    template_family_id: str = ""
    template_version_id: str = ""
    target_agent: str = ""
    artifact_kind: str = ""
    source_mode: str = ""
    slide_count: int = 0
    slot_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "prompt": self.prompt,
            "signature": self.signature,
            "source_path": self.source_path,
            "agent_template_path": self.agent_template_path,
            "conversion_report_path": self.conversion_report_path,
            "template_family_id": self.template_family_id,
            "template_version_id": self.template_version_id,
            "target_agent": self.target_agent,
            "artifact_kind": self.artifact_kind,
            "source_mode": self.source_mode,
            "slide_count": self.slide_count,
            "slot_count": self.slot_count,
            "warnings": list(self.warnings),
        }


@dataclass
class TeacherGuideRequest:
    ok: bool
    message: str
    prompt: str = ""
    signature: str = ""
    source_template_path: str = ""
    task_guide_path: str = ""
    guide_family_id: str = ""
    guide_version_id: str = ""
    source_template_id: str = ""
    source_template_family_id: str = ""
    source_template_version_id: str = ""
    target_agent: str = ""
    task_kind: str = ""
    artifact_kind: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "prompt": self.prompt,
            "signature": self.signature,
            "source_template_path": self.source_template_path,
            "task_guide_path": self.task_guide_path,
            "guide_family_id": self.guide_family_id,
            "guide_version_id": self.guide_version_id,
            "source_template_id": self.source_template_id,
            "source_template_family_id": self.source_template_family_id,
            "source_template_version_id": self.source_template_version_id,
            "target_agent": self.target_agent,
            "task_kind": self.task_kind,
            "artifact_kind": self.artifact_kind,
            "warnings": list(self.warnings),
        }


def extract_slides(markdown_text: str) -> tuple[List[ConvertedSlide], List[str]]:
    warnings: List[str] = []
    matches = list(SLIDE_MARKER_RE.finditer(markdown_text or ""))
    slides: List[ConvertedSlide] = []
    if not matches:
        return slides, ["No `SLIDE ##` markers found."]

    seen_ids: set[str] = set()
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        section = markdown_text[match.start():end]
        title = _title_from_section(section, f"Slide {number:02d}")
        slide_id = f"slide_{number:02d}_{_slug(title, fallback='slide')}"
        base_slide_id = slide_id
        suffix = 2
        while slide_id in seen_ids:
            slide_id = f"{base_slide_id}_{suffix}"
            suffix += 1
        seen_ids.add(slide_id)

        slide_warnings: List[str] = []
        question = _question_from_section(section)
        if not question:
            slide_warnings.append("No bold slide question found.")
        guidance = _table_guidance(section)
        if not guidance:
            slide_warnings.append("No guidance table rows found.")
        source_slots = _placeholder_slots(section)
        accepted_point_types = _semantic_type_hints(title, question, guidance)
        validation = _validation_from_guidance(guidance)
        slides.append(
            ConvertedSlide(
                number=number,
                title=title,
                slide_id=slide_id,
                question=question,
                guidance=guidance,
                source_slots=source_slots,
                accepted_point_types=accepted_point_types,
                validation=validation,
                warnings=slide_warnings,
            )
        )
    return slides, warnings


def _template_family_from_source(source_path: Path, root: Path, target_agent: str) -> tuple[str, int]:
    source_display = _display_path(source_path)
    snapshot = scan_skills_library(root)
    source_key = _normalize_path_text(source_display)
    output_base = _agent_output_base_name(source_path).lower()
    best_family = ""
    best_version = 0
    filename_family = ""
    filename_version = 0

    for asset in snapshot.agent_templates:
        data = asset.to_dict()
        family = str(data.get("template_family_id") or "").strip()
        version = int(data.get("version_sort", -1) or -1)
        source = _normalize_path_text(data.get("source_human_template"))
        agent = str(data.get("target_agent") or "").strip().lower()
        if family and source == source_key and (not agent or agent == target_agent.lower()):
            best_family = family
            best_version = max(best_version, version)
        name_base = VERSION_SUFFIX_RE.sub("", asset.name.rsplit(".", 1)[0]).lower()
        if family and name_base == output_base and (not agent or agent == target_agent.lower()):
            filename_family = family
            filename_version = max(filename_version, version)

    if best_family:
        return best_family, best_version
    if filename_family:
        return filename_family, filename_version

    stem = source_path.stem
    family_stem = re.sub(r"(?i)_human_template", "", stem)
    family_stem = re.sub(r"(?i)_template(_|$)", r"\1", family_stem)
    return _slug(family_stem, fallback="agent_template"), 0


def _agent_output_base_name(source_path: Path) -> str:
    stem = source_path.stem
    if re.search(r"(?i)_template_", stem):
        return re.sub(r"(?i)_template_", "_Agent_Template_", stem, count=1)
    if re.search(r"(?i)_template$", stem):
        return re.sub(r"(?i)_template$", "_Agent_Template", stem, count=1)
    return f"{stem}_Agent_Template"


def _next_output_path(source_path: Path, root: Path, next_number: int) -> tuple[Path, str]:
    agent_dir = root / "agent_templates"
    base_name = _agent_output_base_name(source_path)
    version_number = max(1, int(next_number or 1))
    while True:
        version_id = f"v_{version_number:03d}"
        output_path = agent_dir / f"{base_name}_{version_id}.md"
        if not output_path.exists():
            return output_path, version_id
        version_number += 1


def _task_guide_output_base_name(source_path: Path) -> str:
    stem = VERSION_SUFFIX_RE.sub("", source_path.stem)
    if re.search(r"(?i)agent_template", stem):
        return re.sub(r"(?i)agent_template", "Agent_Guide", stem, count=1)
    if re.search(r"(?i)_template(_|$)", stem):
        return re.sub(r"(?i)_template(_|$)", r"_Agent_Guide\1", stem, count=1)
    if re.search(r"(?i)_guide(_|$)", stem):
        return stem
    return f"{stem}_Agent_Guide"


def _guide_family_from_agent_template(
    source_path: Path,
    root: Path,
    metadata: Dict[str, Any],
    target_agent: str,
    task_kind: str,
    artifact_kind: str,
) -> tuple[str, int]:
    source_display = _display_path(source_path)
    source_key = _normalize_path_text(source_display)
    base_name = _task_guide_output_base_name(source_path)
    snapshot = scan_skills_library(root)
    best_family = ""
    best_version = 0
    filename_family = ""
    filename_version = 0
    for asset in snapshot.task_guides:
        data = asset.to_dict()
        family = str(data.get("guide_family_id") or "").strip()
        version = int(data.get("version_sort", -1) or -1)
        source = _normalize_path_text(data.get("source_agent_template"))
        agent = str(data.get("target_agent") or "").strip().lower()
        guide_task = str(data.get("task_kind") or "").strip().lower()
        guide_artifact = str(data.get("artifact_kind") or "").strip().lower()
        if (
            family
            and source == source_key
            and (not agent or agent == target_agent.lower())
            and (not guide_task or guide_task == task_kind.lower())
            and (not guide_artifact or guide_artifact == artifact_kind.lower())
        ):
            best_family = family
            best_version = max(best_version, version)
        name_base = VERSION_SUFFIX_RE.sub("", asset.name.rsplit(".", 1)[0]).lower()
        if (
            family
            and name_base == base_name.lower()
            and (not agent or agent == target_agent.lower())
            and (not guide_task or guide_task == task_kind.lower())
            and (not guide_artifact or guide_artifact == artifact_kind.lower())
        ):
            filename_family = family
            filename_version = max(filename_version, version)
    if best_family:
        return best_family, best_version
    if filename_family:
        return filename_family, filename_version
    template_family = str(metadata.get("template_family_id") or "").strip()
    if template_family:
        return _slug(f"{template_family}_agent_guide", fallback="agent_guide"), 0
    return _slug(base_name, fallback="agent_guide"), 0


def _next_task_guide_output_path(source_path: Path, root: Path, next_number: int) -> tuple[Path, str]:
    guide_dir = root / "agent_guides"
    base_name = _task_guide_output_base_name(source_path)
    version_number = max(1, int(next_number or 1))
    while True:
        version_id = f"v_{version_number:03d}"
        output_path = guide_dir / f"{base_name}_{version_id}.md"
        if not output_path.exists():
            return output_path, version_id
        version_number += 1


def _yaml_list(values: List[str], indent: str = "  ") -> str:
    if not values:
        return f"{indent}- none"
    return "\n".join(f"{indent}- {value}" for value in values)


def _render_slide(slide: ConvertedSlide) -> str:
    prefix = slide.slot_prefix
    slot_names = _slot_names_for_slide(slide)
    lines: List[str] = [
        f"## Slide {slide.number:02d}: {slide.title}",
        "",
        f"id: `{slide.slide_id}`",
        "",
    ]
    if slide.question:
        lines.extend([f"question: {slide.question}", ""])
    lines.extend(
        [
            "required: true",
            "",
            "accepted_point_types:",
            "",
            *[f"- {item}" for item in slide.accepted_point_types],
            "",
            "slots:",
            "",
            *[f"- `{{{{{slot}}}}}`" for slot in slot_names],
            f"- `{{{{{prefix}.source_point_ids}}}}`",
            f"- `{{{{{prefix}.missing_fields}}}}`",
            "",
        ]
    )

    if slide.guidance:
        lines.extend(["source_guidance:", ""])
        for key in ("core_job", "best_visual", "proof_to_collect", "technical_founder_note", "avoid", "optional_variant"):
            value = slide.guidance.get(key)
            if value:
                lines.append(f"- {key.replace('_', ' ').title()}: {value}")
        extra = sorted(key for key in slide.guidance if key not in {"core_job", "best_visual", "proof_to_collect", "technical_founder_note", "avoid", "optional_variant"})
        for key in extra:
            lines.append(f"- {key.replace('_', ' ').title()}: {slide.guidance[key]}")
        lines.append("")

    lines.extend(
        [
            "content_rules:",
            "",
            "- Use Data Nexus points whose `type` matches one of the accepted point type hints, or explain why another type was selected.",
            "- Preserve source meaning while rewriting for concise slide copy.",
            "- Do not invent facts, metrics, dates, names, claims, prices, or commitments.",
            "- Mark the slide `needs_review` when required inputs are missing or only candidate points are available.",
            "",
            "validation:",
            "",
            *[f"- {item}" for item in slide.validation],
            "- Must attach source point IDs for every visible claim.",
            "- Must report missing fields instead of filling unsupported claims.",
        ]
    )
    if slide.warnings:
        lines.extend(["", "teacher_warnings:", "", *[f"- {warning}" for warning in slide.warnings]])
    return "\n".join(lines).rstrip()


def _render_agent_template(
    *,
    source_path: Path,
    source_title: str,
    template_family_id: str,
    template_version_id: str,
    conversion_report_path: str,
    slides: List[ConvertedSlide],
    generated_at: str,
    target_agent: str,
    artifact_kind: str,
) -> str:
    template_id = f"{template_family_id}_{template_version_id}"
    source_display = _display_path(source_path)
    slot_count = _total_slot_count(slides, artifact_kind)
    target_label = target_agent.replace("_", " ").strip().title() or "Target Agent"
    lines: List[str] = [
        "---",
        f"template_id: {template_id}",
        f"template_family_id: {template_family_id}",
        f"template_version_id: {template_version_id}",
        "template_kind: agent_template",
        f"source_human_template: {source_display}",
        f"conversion_report: {conversion_report_path}",
        f"target_agent: {target_agent}",
        f"artifact_kind: {artifact_kind}",
        "delivery_formats:",
        "  - html",
        "  - pdf",
        f"slide_count: {len(slides)}",
        f"slot_count: {slot_count}",
        "version: 0.1.0",
        "status: draft",
        "generated_by: teacher_agent",
        f"generated_at: {generated_at}",
        "---",
        f"# {source_title} Agent Template",
        "",
        "## Purpose",
        "",
        "This agent-formatted template was generated from a human markdown guide by `teacher_agent`.",
        f"The {target_label} should use it to map Data Nexus points into the requested `{artifact_kind}` artifact.",
        "",
        f"The {target_label} must preserve source point IDs for every filled visible claim, metric, proof point, and note.",
        "",
        "## Global Rules",
        "",
        "- Generate the declared artifact format as the editable master.",
        "- Export secondary delivery formats only after the editable artifact has been reviewed or explicitly requested.",
        "- Keep one main idea per generated section.",
        "- Prefer approved Data Nexus points. Candidate points may be used only when the generated section is marked `needs_review`.",
        "- Do not invent facts, metrics, dates, names, claims, prices, or commitments.",
        "- Separate visible artifact copy from private notes.",
        "- Keep visible text short enough for the target artifact format.",
        "- Use source point IDs in the artifact manifest for every filled slot.",
        "- Report missing fields instead of filling unsupported claims with generic language.",
        "",
        "## Required Artifact Slots",
        "",
        *[f"- `{{{{{slot}}}}}`" for slot in _artifact_slot_names(artifact_kind)],
        "",
        "## Point Status Policy",
        "",
        "Preferred point status:",
        "",
        "```text",
        "approved",
        "```",
        "",
        "Allowed draft point status:",
        "",
        "```text",
        "candidate",
        "```",
        "",
        "If any candidate point is used, the affected slide must include:",
        "",
        "```text",
        "review_status: needs_review",
        "```",
        "",
        "## Data Nexus Point Contract",
        "",
        f"The {target_label} should consume normalized Data Nexus point bundles with at least:",
        "",
        "```json",
        "{",
        '  "id": "point_id",',
        '  "type": "point_type",',
        '  "title": "Point title",',
        '  "summary": "Short point summary",',
        '  "content": "Markdown point body",',
        '  "file": "point_file.md"',
        "}",
        "```",
        "",
        "The `type` field must align with each slide's `accepted_point_types` list.",
        "",
        "## Slide Slot Contract",
        "",
        "Every slide below must produce:",
        "",
        "- visible content slots inferred from the human template placeholders.",
        "- optional visual or media instruction slots when the human template asks for them.",
        "- optional presenter note slots when the human template asks for them.",
        "- `source_point_ids`: Data Nexus point IDs used to fill the slide.",
        "- `missing_fields`: required details that could not be filled.",
        "",
        f"Detected slides: {len(slides)}",
        "",
        f"Declared slots: {slot_count}",
        "",
    ]
    lines.extend(_render_slide(slide) + "\n" for slide in slides)
    lines.extend(
        [
            "## Validation Summary",
            "",
            "- Required slots must be present.",
            "- Slot IDs must be unique.",
            "- Slide IDs must be unique.",
            "- Every slide must have at least one accepted Data Nexus point type.",
            "- Generated artifacts must record this exact template family and version.",
        ]
    )
    return "\n\n".join(part.rstrip() for part in lines if part is not None).rstrip() + "\n"


def _conversion_paths_and_context(
    source_path: str | Path,
    *,
    root: str | Path | None = None,
    target_agent: str = "sales_agent",
    artifact_kind: str = "html_deck",
    allow_unstructured: bool = False,
) -> tuple[bool, str, Path | None, Path | None, Path | None, str, str, List[ConvertedSlide], List[str], str]:
    root_path = _library_root(root)
    try:
        source = resolve_library_path(source_path, root=root_path)
    except Exception as exc:
        return False, str(exc), None, None, None, "", "", [], [], ""

    human_dir = root_path / "human_templates"
    try:
        source.relative_to(human_dir)
    except Exception:
        return False, "Teacher Agent can only convert files under `Skills/human_templates/`.", None, None, None, "", "", [], [], ""

    if not source.exists() or source.suffix.lower() != ".md":
        return False, f"Human template not found or not markdown: {_display_path(source)}", None, None, None, "", "", [], [], ""

    try:
        text = _read_text(source)
    except Exception as exc:
        return False, f"Failed to read human template: {exc}", None, None, None, "", "", [], [], ""
    if not str(text or "").strip():
        return False, "Human template is empty.", source, None, None, "", "", [], [], text

    slides, warnings = extract_slides(text)
    if not slides and not allow_unstructured:
        return False, "No slide sections found to convert.", source, None, None, "", "", [], warnings, text

    family_id, latest_version = _template_family_from_source(source, root_path, target_agent)
    output_path, version_id = _next_output_path(source, root_path, latest_version + 1)
    report_dir = root_path / "conversion_reports"
    report_path = report_dir / f"{VERSION_SUFFIX_RE.sub('', output_path.stem)}_Conversion_Report_{version_id}.md"
    return True, "", source, output_path, report_path, family_id, version_id, slides, warnings, text


def _guide_paths_and_context(
    source_template_path: str | Path,
    *,
    root: str | Path | None = None,
    target_agent: str = "sales_agent",
    task_kind: str = "pitch_deck",
    artifact_kind: str = "html_deck",
) -> tuple[bool, str, Path | None, Path | None, str, str, Dict[str, Any], str]:
    root_path = _library_root(root)
    try:
        source = resolve_library_path(source_template_path, root=root_path)
    except Exception as exc:
        return False, str(exc), None, None, "", "", {}, ""

    agent_dir = root_path / "agent_templates"
    try:
        source.relative_to(agent_dir)
    except Exception:
        return False, "Teacher Agent can only create guides from files under `Skills/agent_templates/`.", None, None, "", "", {}, ""

    if not source.exists() or source.suffix.lower() != ".md":
        return False, f"Agent template not found or not markdown: {_display_path(source)}", source, None, "", "", {}, ""

    try:
        text = _read_text(source)
    except Exception as exc:
        return False, f"Failed to read agent template: {exc}", source, None, "", "", {}, ""
    if not str(text or "").strip():
        return False, "Agent template is empty.", source, None, "", "", {}, text

    metadata = _parse_front_matter(text)
    if str(metadata.get("template_kind") or "").strip().lower() != "agent_template":
        return False, "Selected file is not an agent template.", source, None, "", "", metadata, text
    if target_agent and str(metadata.get("target_agent") or "").strip().lower() not in {"", target_agent.lower()}:
        return False, f"Agent template target_agent is not `{target_agent}`.", source, None, "", "", metadata, text
    if artifact_kind and str(metadata.get("artifact_kind") or "").strip().lower() not in {"", artifact_kind.lower()}:
        return False, f"Agent template artifact_kind is not `{artifact_kind}`.", source, None, "", "", metadata, text

    family_id, latest_version = _guide_family_from_agent_template(
        source,
        root_path,
        metadata,
        target_agent,
        task_kind,
        artifact_kind,
    )
    output_path, version_id = _next_task_guide_output_path(source, root_path, latest_version + 1)
    return True, "", source, output_path, family_id, version_id, metadata, text


def prepare_teacher_agent_conversion_request(
    source_path: str | Path,
    *,
    root: str | Path | None = None,
    target_agent: str = "sales_agent",
    artifact_kind: str = "html_deck",
) -> TeacherConversionRequest:
    ok, message, source, output_path, report_path, family_id, version_id, slides, warnings, text = _conversion_paths_and_context(
        source_path,
        root=root,
        target_agent=target_agent,
        artifact_kind=artifact_kind,
        allow_unstructured=True,
    )
    if not ok or source is None or output_path is None or report_path is None:
        return TeacherConversionRequest(False, message, warnings=warnings)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_title = _title_from_source_path(source) or _title_from_intro(text, "Agent Template")
    source_mode = "structured_template" if slides else "unstructured_source"
    parsed_slot_count = _total_slot_count(slides, artifact_kind) if slides else 0
    fallback_scaffold = _render_agent_template(
        source_path=source,
        source_title=source_title,
        template_family_id=family_id,
        template_version_id=version_id,
        conversion_report_path=_display_path(report_path),
        slides=slides,
        generated_at=generated_at,
        target_agent=target_agent,
        artifact_kind=artifact_kind,
    )
    if slides:
        source_mode_instruction = (
            "Source mode: structured human template.\n"
            "Use the detected slide sections as the base structure, but still improve weak or generic mappings with semantic reasoning. "
            "Also read the non-slide sections, worksheets, QA checklists, appendix notes, research notes, and usage guidance. "
            "Integrate that material into global pitch specifications, validation rules, optional appendix guidance, and review warnings.\n\n"
        )
        slide_count_requirement = f"- slide_count: {len(slides)}"
        slot_count_requirement = f"- slot_count: {parsed_slot_count}"
        fallback_note = (
            "A deterministic fallback scaffold is provided only as a structural hint. Improve it with semantic reasoning; "
            "do not copy weak generic point-type hints when the source supports better specific requirements."
        )
    else:
        source_mode_instruction = (
            "Source mode: unstructured source document, raw transcript, or AI-formatted markdown guide.\n"
            "No reliable `SLIDE ##` markers were detected. Do not reject the source and do not ask for manual formatting. "
            "Study the whole document as expert guidance. Infer the reusable deck template from explicit slide mentions, "
            "journey points, lesson sequence, examples, comparisons, warnings, and repeated advice. "
            "Create slide sections for the explicit or implied pitch components, and mark uncertain inferences as `needs_review`.\n\n"
            "For unstructured sources, also create global pitch specifications from the non-slide guidance: overall focus, "
            "target audience, narrative arc, positioning, tone, readability constraints, evidence standards, market/timing/competition "
            "rules, ask/use-of-funds rules, visual/copy guidance, and things to avoid.\n\n"
        )
        slide_count_requirement = (
            "- slide_count: concrete integer inferred from the source document; do not use 0 unless no deck structure can be inferred"
        )
        slot_count_requirement = "- slot_count: concrete integer after defining all global and slide slots"
        fallback_note = (
            "A metadata scaffold is provided only to show the required file shape. Because the source is unstructured, "
            "replace the zero-slide scaffold with an inferred slide template and concrete counts."
        )
    prompt = (
        "You are the Teacher Agent running through the Mediator AI layer.\n\n"
        "Convert the provided human-authored source document into an agent-formatted template. "
        "Do not rely on hardcoded examples or slide-number assumptions. Infer the reusable structure, slots, "
        "Data Nexus point requirements, validation rules, and review warnings from the source document itself.\n\n"
        f"{source_mode_instruction}"
        "Return exactly two tagged markdown blocks and no extra prose outside the tags:\n\n"
        "<agent_template_markdown>\n"
        "FULL_AGENT_TEMPLATE_MARKDOWN_HERE\n"
        "</agent_template_markdown>\n\n"
        "<conversion_report_markdown>\n"
        "FULL_CONVERSION_REPORT_MARKDOWN_HERE\n"
        "</conversion_report_markdown>\n\n"
        "Required front matter for the agent template:\n"
        f"- template_id: {family_id}_{version_id}\n"
        f"- template_family_id: {family_id}\n"
        f"- template_version_id: {version_id}\n"
        "- template_kind: agent_template\n"
        f"- source_human_template: {_display_path(source)}\n"
        f"- conversion_report: {_display_path(report_path)}\n"
        f"- target_agent: {target_agent}\n"
        f"- artifact_kind: {artifact_kind}\n"
        "- delivery_formats: html, pdf when relevant to the artifact, otherwise infer from artifact_kind\n"
        f"{slide_count_requirement}\n"
        f"{slot_count_requirement}\n"
        "- version: 0.1.0\n"
        "- status: draft\n"
        "- generated_by: teacher_agent\n"
        f"- generated_at: {generated_at}\n\n"
        "Every output section must include stable IDs, named slots, accepted Data Nexus point-type requirements, "
        "source point ID requirements, missing-field behavior, and validation rules. "
        "Write every fillable value as a literal double-brace slot such as `{{slide_01.headline}}`. "
        "For deck or slide artifacts, use scanner-friendly headings like `## Slide 01: Title`. "
        "Preserve source guidance that matters. If a section cannot be confidently converted, mark it for review.\n\n"
        "Add an `## Overall Pitch Specification` section before slide sections. Capture the presentation's strategic focus, "
        "story model, audience assumptions, evidence standard, copy/design constraints, and review checklist there.\n\n"
        f"{fallback_note}\n\n"
        "Fallback scaffold:\n"
        "```md\n"
        f"{fallback_scaffold}\n"
        "```\n\n"
        "Human source document:\n"
        "```md\n"
        f"{text}\n"
        "```\n"
    )
    signature = hashlib.sha1(
        "\n".join([_display_path(source), family_id, version_id, target_agent, artifact_kind, text]).encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()
    return TeacherConversionRequest(
        True,
        f"Prepared Teacher Agent AI conversion request for `{version_id}`.",
        prompt=prompt,
        signature=signature,
        source_path=_display_path(source),
        agent_template_path=_display_path(output_path),
        conversion_report_path=_display_path(report_path),
        template_family_id=family_id,
        template_version_id=version_id,
        target_agent=target_agent,
        artifact_kind=artifact_kind,
        source_mode=source_mode,
        slide_count=len(slides),
        slot_count=parsed_slot_count,
        warnings=warnings + [f"Slide {slide.number:02d}: {warning}" for slide in slides for warning in slide.warnings],
    )


def prepare_teacher_agent_guide_request(
    source_template_path: str | Path,
    *,
    root: str | Path | None = None,
    target_agent: str = "sales_agent",
    task_kind: str = "pitch_deck",
    artifact_kind: str = "html_deck",
) -> TeacherGuideRequest:
    ok, message, source, output_path, family_id, version_id, metadata, text = _guide_paths_and_context(
        source_template_path,
        root=root,
        target_agent=target_agent,
        task_kind=task_kind,
        artifact_kind=artifact_kind,
    )
    if not ok or source is None or output_path is None:
        return TeacherGuideRequest(False, message)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_display = _display_path(source)
    output_display = _display_path(output_path)
    guide_id = f"{family_id}_{version_id}"
    source_template_id = str(metadata.get("template_id") or "").strip()
    source_template_family_id = str(metadata.get("template_family_id") or "").strip()
    source_template_version_id = str(metadata.get("template_version_id") or "").strip()
    source_slide_count = str(metadata.get("slide_count") or "").strip()
    source_slot_count = str(metadata.get("slot_count") or "").strip()
    prompt = (
        "You are the Teacher Agent running through the Mediator AI layer.\n\n"
        "Create a task guide from the selected agent template. The task guide is not the deck template itself. "
        "It tells the Task node and Sales Agent how to use the template, inspect Data Nexus, ask prep questions, "
        "generate drafts, and keep the user interaction natural.\n\n"
        "Base the guide on what the agent template actually says. If the template emphasizes story, story-first must become "
        "a guide priority. If it emphasizes product workflow, proof quality, 3-minute readability, market timing, competition, "
        "ask/use-of-funds, clarity over design, or technical-founder translation, carry those priorities into the guide. "
        "Do not produce generic workflow boilerplate that ignores the template-specific instructions.\n\n"
        "Return exactly one tagged markdown block and no extra prose outside the tag:\n\n"
        "<task_guide_markdown>\n"
        "FULL_TASK_GUIDE_MARKDOWN_HERE\n"
        "</task_guide_markdown>\n\n"
        "Required YAML front matter for the task guide:\n"
        "```yaml\n"
        "---\n"
        "kind: task_guide\n"
        f"guide_id: {guide_id}\n"
        f"guide_family_id: {family_id}\n"
        f"guide_version_id: {version_id}\n"
        f"target_agent: {target_agent}\n"
        f"task_kind: {task_kind}\n"
        f"artifact_kind: {artifact_kind}\n"
        "status: draft\n"
        f"source_agent_template: {source_display}\n"
        f"source_template_id: {source_template_id}\n"
        f"source_template_family_id: {source_template_family_id}\n"
        f"source_template_version_id: {source_template_version_id}\n"
        "required_inputs:\n"
        "  - agent_template\n"
        "  - data_nexus\n"
        "default_steps:\n"
        "  - readiness_report\n"
        "  - ask_missing_questions\n"
        "  - generate_draft\n"
        "  - review\n"
        "approval_required: true\n"
        "generated_by: teacher_agent\n"
        f"generated_at: {generated_at}\n"
        "---\n"
        "```\n\n"
        "Guide body requirements:\n"
        "- Include `# Sales Pitch Deck Agent Guide` or a similarly specific title.\n"
        "- Include a `## Template-Specific Priorities` section derived from the selected agent template.\n"
        "- Include readiness rules that tell the Sales Agent how to judge whether Data Nexus points satisfy each slide's intent.\n"
        "- Include question rules that ask the smallest useful set of missing business questions.\n"
        "- User-facing questions must be natural and concise. Do not expose internal `question_id`, expected answer point type, "
        "hidden JSON tags, source-point plumbing, or Data Nexus implementation details in chat copy.\n"
        "- Internal prep questions may still define stable IDs, accepted point types, matched point IDs, and evaluation criteria; "
        "those belong in Task/Data Nexus state and hidden JSON, not in the human-facing message.\n"
        "- Tell the Sales Agent to ask one question at a time when continuing through the chatbot, using the active prep question "
        "tracked by Data Nexus/Task state.\n"
        "- Include generation rules for draft decks, review rules, approval/dismissal rules, and warnings for unsupported claims.\n"
        "- Preserve the agent template's validation rules and missing-field behavior at the workflow level.\n\n"
        "Source template metadata:\n"
        f"- template_id: {source_template_id}\n"
        f"- template_family_id: {source_template_family_id}\n"
        f"- template_version_id: {source_template_version_id}\n"
        f"- slide_count: {source_slide_count}\n"
        f"- slot_count: {source_slot_count}\n"
        f"- output_path: {output_display}\n\n"
        "Selected agent template:\n"
        "```md\n"
        f"{text}\n"
        "```\n"
    )
    signature = hashlib.sha1(
        "\n".join(
            [
                source_display,
                output_display,
                family_id,
                version_id,
                source_template_id,
                source_template_version_id,
                target_agent,
                task_kind,
                artifact_kind,
                text,
            ]
        ).encode("utf-8", errors="ignore")
    ).hexdigest()
    return TeacherGuideRequest(
        True,
        f"Prepared Teacher Agent guide request for `{version_id}`.",
        prompt=prompt,
        signature=signature,
        source_template_path=source_display,
        task_guide_path=output_display,
        guide_family_id=family_id,
        guide_version_id=version_id,
        source_template_id=source_template_id,
        source_template_family_id=source_template_family_id,
        source_template_version_id=source_template_version_id,
        target_agent=target_agent,
        task_kind=task_kind,
        artifact_kind=artifact_kind,
    )


def _extract_tagged_markdown(response_text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(response_text or "")
    if not match:
        return ""
    return str(match.group("body") or "").strip()


def write_teacher_agent_guide_response(
    response_text: str,
    request: TeacherGuideRequest | Dict[str, Any],
    *,
    root: str | Path | None = None,
) -> TeacherGuideResult:
    data = request.to_dict() if isinstance(request, TeacherGuideRequest) else dict(request or {})
    if not bool(data.get("ok", False)):
        return TeacherGuideResult(False, str(data.get("message") or "Teacher Agent guide request was not valid."))

    guide_body = _extract_tagged_markdown(response_text, TASK_GUIDE_TAG_RE)
    if not guide_body:
        return TeacherGuideResult(
            False,
            "Mediator AI response did not include `<task_guide_markdown>`.",
            warnings=["Teacher Agent guide response could not be written."],
        )

    try:
        output_path = resolve_library_path(str(data.get("task_guide_path") or ""), root=_library_root(root))
    except Exception as exc:
        return TeacherGuideResult(False, str(exc))

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(guide_body.rstrip() + "\n", encoding="utf-8")
    except Exception as exc:
        return TeacherGuideResult(False, f"Failed to write Teacher Agent guide output: {exc}")

    return TeacherGuideResult(
        True,
        f"Created {output_path.name} from Mediator AI response.",
        task_guide_path=_display_path(output_path),
        guide_family_id=str(data.get("guide_family_id") or ""),
        guide_version_id=str(data.get("guide_version_id") or ""),
        target_agent=str(data.get("target_agent") or ""),
        task_kind=str(data.get("task_kind") or ""),
        artifact_kind=str(data.get("artifact_kind") or ""),
        warnings=list(data.get("warnings", []) or []),
    )


def write_teacher_agent_ai_response(
    response_text: str,
    request: TeacherConversionRequest | Dict[str, Any],
    *,
    root: str | Path | None = None,
) -> TeacherConversionResult:
    data = request.to_dict() if isinstance(request, TeacherConversionRequest) else dict(request or {})
    if not bool(data.get("ok", False)):
        return TeacherConversionResult(False, str(data.get("message") or "Teacher Agent request was not valid."))

    template_body = _extract_tagged_markdown(response_text, AGENT_TEMPLATE_TAG_RE)
    report_body = _extract_tagged_markdown(response_text, CONVERSION_REPORT_TAG_RE)
    if not template_body:
        return TeacherConversionResult(
            False,
            "Mediator AI response did not include `<agent_template_markdown>`.",
            warnings=["Teacher Agent AI response could not be written."],
        )

    try:
        output_path = resolve_library_path(str(data.get("agent_template_path") or ""), root=_library_root(root))
    except Exception as exc:
        return TeacherConversionResult(False, str(exc))
    try:
        report_path = resolve_library_path(str(data.get("conversion_report_path") or ""), root=_library_root(root))
    except Exception:
        report_path = _library_root(root) / "conversion_reports" / f"{output_path.stem}_Conversion_Report.md"

    if not report_body:
        report_body = (
            "# Teacher Agent Conversion Report\n\n"
            "Mediator AI returned an agent template but no tagged conversion report.\n\n"
            f"- Agent template: `{_display_path(output_path)}`\n"
            f"- Template family: `{data.get('template_family_id', '')}`\n"
            f"- Template version: `{data.get('template_version_id', '')}`\n"
            "- Warning: conversion report was generated as a fallback.\n"
        )

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(template_body.rstrip() + "\n", encoding="utf-8")
        report_path.write_text(report_body.rstrip() + "\n", encoding="utf-8")
    except Exception as exc:
        return TeacherConversionResult(False, f"Failed to write Teacher Agent AI output: {exc}")

    response_slide_count = len(AGENT_SLIDE_HEADING_RE.findall(template_body or ""))
    response_slot_count = len({slot.strip() for slot in AGENT_SLOT_RE.findall(template_body or "") if slot.strip()})

    return TeacherConversionResult(
        True,
        f"Created {output_path.name} from Mediator AI response.",
        agent_template_path=_display_path(output_path),
        conversion_report_path=_display_path(report_path),
        template_family_id=str(data.get("template_family_id") or ""),
        template_version_id=str(data.get("template_version_id") or ""),
        slide_count=response_slide_count or int(data.get("slide_count", 0) or 0),
        slot_count=response_slot_count or int(data.get("slot_count", 0) or 0),
        warnings=list(data.get("warnings", []) or []),
    )
