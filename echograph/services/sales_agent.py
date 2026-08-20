from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from echograph.services.skills_library import resolve_library_path, scan_skills_library, skills_root


FRONT_MATTER_RE = re.compile(r"\A---\s*\r?\n(?P<front>.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
SLIDE_HEADING_RE = re.compile(r"(?m)^##\s+Slide\s+(?P<number>\d{1,2})\s*:\s*(?P<title>.+?)\s*$")
H2_HEADING_RE = re.compile(r"(?m)^##\s+")
SLOT_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
MARKDOWN_CODE_RE = re.compile(r"`([^`]+)`")
DECK_MANIFEST_RE = re.compile(
    r"<script\b[^>]*\bid=[\"']deck-manifest[\"'][^>]*>(?P<json>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
SUPPORTING_SECTION_LABEL = "Supporting Notes"
STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "now",
    "the",
    "this",
    "that",
    "what",
    "when",
    "where",
    "why",
    "with",
    "you",
    "your",
}


@dataclass
class SalesTemplateSlide:
    number: int
    title: str
    slide_id: str
    required: bool = True
    question: str = ""
    objective: str = ""
    audience: str = ""
    accepted_point_types: List[str] = field(default_factory=list)
    slots: List[str] = field(default_factory=list)
    readiness_criteria: List[str] = field(default_factory=list)
    fallback_behavior: List[str] = field(default_factory=list)
    copy_constraints: List[str] = field(default_factory=list)
    source_point_id_requirements: List[str] = field(default_factory=list)
    missing_field_behavior: List[str] = field(default_factory=list)
    source_guidance: List[str] = field(default_factory=list)
    validation_rules: List[str] = field(default_factory=list)
    body: str = ""


@dataclass
class SalesDeckResult:
    ok: bool
    message: str
    deck_dir: str = ""
    index_path: str = ""
    css_path: str = ""
    manifest_path: str = ""
    template_path: str = ""
    template_family_id: str = ""
    template_version_id: str = ""
    slide_count: int = 0
    point_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "deck_dir": self.deck_dir,
            "index_path": self.index_path,
            "css_path": self.css_path,
            "manifest_path": self.manifest_path,
            "template_path": self.template_path,
            "template_family_id": self.template_family_id,
            "template_version_id": self.template_version_id,
            "slide_count": self.slide_count,
            "point_count": self.point_count,
            "warnings": list(self.warnings),
        }


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


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _coerce_scalar(value: str) -> Any:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        if text[0] == '"':
            try:
                return json.loads(text)
            except Exception:
                return text[1:-1]
        return text[1:-1]
    return text


def _parse_front_matter(text: str) -> tuple[Dict[str, Any], str]:
    raw = str(text or "")
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    data: Dict[str, Any] = {}
    current_key = ""
    for raw_line in match.group("front").splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            existing = data.get(current_key)
            value = _coerce_scalar(stripped[2:])
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
        data[key] = _coerce_scalar(value) if value else []
    return data, raw[match.end():].lstrip("\r\n")


def _slug(value: Any, fallback: str = "item") -> str:
    text = str(value or "").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", ".", "/"}:
            out.append("_")
    cleaned = re.sub(r"_+", "_", "".join(out)).strip("._-")
    return cleaned or fallback


def _normalize_type(value: Any) -> str:
    return _slug(value, fallback="concept")


def _truncate_text(value: str, *, limit: int, suffix: str = "...") -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    suffix = suffix if len(suffix) < limit else ""
    cut_limit = max(1, limit - len(suffix))
    snippet = text[:cut_limit].rstrip()
    boundary = max(snippet.rfind(" "), snippet.rfind(","), snippet.rfind(";"), snippet.rfind(":"))
    if boundary >= max(24, int(cut_limit * 0.62)):
        snippet = snippet[:boundary].rstrip(" ,;:-")
    else:
        snippet = snippet.rstrip(" ,;:-")
    if not snippet:
        snippet = text[:cut_limit].rstrip(" ,;:-")
    return f"{snippet}{suffix}"


def _clean_markdown_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "")
    text = re.sub(r"\A---\s*\r?\n.*?\r?\n---\s*", " ", text, flags=re.DOTALL)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = MARKDOWN_CODE_RE.sub(r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"[*_]{1,3}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit > 0 and len(text) > limit:
        return _truncate_text(text, limit=limit)
    return text


def _sentence(value: Any, *, limit: int = 180) -> str:
    clean = _clean_markdown_text(value, limit=limit * 2)
    if not clean:
        return ""
    match = re.search(r"^(.{24,}?[.!?])\s+", clean)
    if match:
        clean = match.group(1)
    if len(clean) > limit:
        clean = _truncate_text(clean, limit=limit)
    return clean


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", str(value or "").lower().replace("_", " "))
        if token not in STOP_WORDS
    }


def _parse_list_after(label: str, body: str) -> List[str]:
    lines = str(body or "").splitlines()
    wanted = label.strip().lower()
    values: List[str] = []
    in_list = False
    for raw in lines:
        stripped = raw.strip()
        if not in_list:
            if stripped.lower().rstrip(":") == wanted:
                in_list = True
            continue
        if not stripped:
            if values:
                break
            continue
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            value = value.strip("`")
            value = re.sub(r"^`|`$", "", value).strip()
            if value and value not in values:
                values.append(value)
            continue
        if values:
            break
    return values


def _parse_text_after(label: str, body: str) -> str:
    pattern = rf"(?m)^{re.escape(label.strip())}\s*:\s*(?P<value>.*?)\s*$"
    match = re.search(pattern, body or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return _clean_markdown_text(match.group("value").strip().strip("`"), limit=500)


def _parse_bool_after(label: str, body: str, *, default: bool = False) -> bool:
    value = _parse_text_after(label, body).strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "y", "required"}:
        return True
    if value in {"0", "false", "no", "n", "optional"}:
        return False
    return default


def _parse_id(body: str, fallback: str) -> str:
    match = re.search(r"(?m)^id:\s*`?(?P<id>[^`\r\n]+)`?\s*$", body or "")
    if not match:
        return fallback
    return _slug(match.group("id"), fallback=fallback)


def parse_sales_agent_template(text: str) -> tuple[Dict[str, Any], str, List[SalesTemplateSlide]]:
    metadata, body = _parse_front_matter(text)
    matches = list(SLIDE_HEADING_RE.finditer(body))
    slides: List[SalesTemplateSlide] = []
    for idx, match in enumerate(matches):
        start = match.end()
        next_h2 = H2_HEADING_RE.search(body, start)
        end = next_h2.start() if next_h2 else len(body)
        section = body[start:end].strip()
        number = int(match.group("number"))
        title = _clean_markdown_text(match.group("title"), limit=120) or f"Slide {number:02d}"
        fallback_id = f"slide_{number:02d}_{_slug(title, fallback='slide')}"
        slots: List[str] = []
        for slot in SLOT_RE.findall(section):
            if slot not in slots:
                slots.append(slot)
        slides.append(
            SalesTemplateSlide(
                number=number,
                title=title,
                slide_id=_parse_id(section, fallback_id),
                required=_parse_bool_after("required", section, default=True),
                question=_parse_text_after("question", section),
                objective=_parse_text_after("objective", section),
                audience=_parse_text_after("audience", section),
                accepted_point_types=[_normalize_type(item) for item in _parse_list_after("accepted_point_types", section)],
                slots=slots,
                readiness_criteria=_parse_list_after("readiness_criteria", section),
                fallback_behavior=_parse_list_after("fallback_behavior", section),
                copy_constraints=_parse_list_after("copy_constraints", section),
                source_point_id_requirements=_parse_list_after("source_point_id_requirements", section),
                missing_field_behavior=_parse_list_after("missing_field_behavior", section),
                source_guidance=_parse_list_after("source_guidance", section),
                validation_rules=_parse_list_after("validation_rules", section),
                body=section,
            )
        )
    return metadata, body, slides


def resolve_latest_approved_sales_agent_template(
    *,
    root: str | Path | None = None,
    template_family_id: str = "",
) -> tuple[Path | None, str]:
    snapshot = scan_skills_library(root)
    family_filter = str(template_family_id or "").strip()
    candidates = []
    for asset in snapshot.agent_templates:
        data = asset.to_dict()
        if str(data.get("status", "") or "").strip().lower() != "approved":
            continue
        if str(data.get("target_agent", "") or "").strip().lower() != "sales_agent":
            continue
        if str(data.get("artifact_kind", "") or "").strip().lower() != "html_deck":
            continue
        if family_filter and str(data.get("template_family_id", "") or "") != family_filter:
            continue
        candidates.append(data)

    if not candidates:
        return None, "No approved `sales_agent` / `html_deck` template was found."

    candidates.sort(
        key=lambda item: (
            int(item.get("version_sort", -1) or -1),
            str(item.get("template_version_id", "") or ""),
            str(item.get("name", "") or ""),
        ),
        reverse=True,
    )
    try:
        return resolve_library_path(str(candidates[0].get("path") or ""), root=_library_root(root)), ""
    except Exception as exc:
        return None, str(exc)


def _point_id(point: Dict[str, Any]) -> str:
    return str(point.get("id", "") or "").strip()


def _point_title(point: Dict[str, Any]) -> str:
    return str(point.get("title", "") or point.get("label", "") or _point_id(point) or "Point").strip()


def _point_summary(point: Dict[str, Any]) -> str:
    return str(point.get("summary", "") or "").strip()


def _point_content(point: Dict[str, Any]) -> str:
    return str(point.get("content", "") or point.get("note", "") or "").strip()


def _score_point_for_slide(point: Dict[str, Any], slide: SalesTemplateSlide) -> tuple[int, bool]:
    accepted = {_normalize_type(item) for item in slide.accepted_point_types if str(item or "").strip()}
    point_type = _normalize_type(point.get("type", "concept"))
    text = " ".join(
        [
            point_type,
            _point_id(point),
            _point_title(point),
            _point_summary(point),
            _point_content(point),
        ]
    ).lower()
    score = 0
    exact = bool(accepted and point_type in accepted)
    if exact:
        score += 100
    accepted_tokens = set()
    for item in accepted:
        accepted_tokens.update(_tokens(item))
    point_tokens = _tokens(text)
    slide_tokens = _tokens(slide.title)
    score += len(accepted_tokens & point_tokens) * 8
    score += len(slide_tokens & point_tokens) * 6
    if not accepted and slide_tokens & point_tokens:
        score += 20
    if point_type == "concept":
        score -= 3
    return max(score, 0), exact


def _select_points_for_slide(
    points: List[Dict[str, Any]],
    slide: SalesTemplateSlide,
    *,
    limit: int = 3,
) -> tuple[List[Dict[str, Any]], bool]:
    ranked = []
    for idx, point in enumerate(points):
        score, exact = _score_point_for_slide(point, slide)
        if score <= 0:
            continue
        ranked.append((score, exact, idx, point))
    ranked.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
    selected = [dict(item[3]) for item in ranked[:limit]]
    has_exact = any(bool(item[1]) for item in ranked[:limit])
    return selected, has_exact


QUESTION_HINTS = {
    "company_profile": "the company name, product one-liner, buyer, and concrete value promise",
    "product_one_liner": "the one-sentence product description and who it is for",
    "product_positioning": "the category, buyer, and why this product is different",
    "market_timing": "what changed recently and why the timing matters now",
    "customer_problem": "the customer pain, current workaround, and cost of doing nothing",
    "product_demo": "the input, action, output, and visible workflow result",
    "product_workflow": "the workflow steps and the before/after outcome",
    "workflow_outcome": "the measurable workflow improvement, baseline, and result",
    "technical_moat": "the technical edge, integration depth, reliability, or data advantage",
    "market_icp": "the buyer/user profile, initial use case, budget owner, and market segment",
    "traction_metric": "the metric, timeframe, source, and what it proves about demand",
    "go_to_market": "the acquisition channel, sales motion, conversion mechanism, and expansion loop",
    "pricing_economics": "the pricing model, charge metric, margin/cost structure, and willingness to pay",
    "competitive_positioning": "the alternatives, why buyers switch, and the winning criteria",
    "team_background": "the founder-market fit, relevant execution proof, milestones, and resource needs",
}


def _point_type(point: Dict[str, Any]) -> str:
    return _normalize_type(point.get("type", "concept"))


def _point_is_question_like(point: Dict[str, Any]) -> bool:
    point_type = _point_type(point)
    text = " ".join(
        [
            _point_title(point),
            _point_summary(point),
            _point_content(point),
        ]
    ).strip()
    if point_type in {"question", "prep_question"}:
        return True
    return bool(text and text.endswith("?"))


def _usable_sales_points(point_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_points = (point_bundle or {}).get("points", [])
    points = [dict(point) for point in raw_points if isinstance(point, dict)]
    return [point for point in points if not _point_is_question_like(point)]


def _target_point_type_for_slide(slide: SalesTemplateSlide, selected: List[Dict[str, Any]]) -> str:
    accepted = [_normalize_type(item) for item in slide.accepted_point_types if str(item or "").strip()]
    accepted_set = set(accepted)
    for point in selected:
        point_type = _point_type(point)
        if point_type in accepted_set:
            return point_type
    if accepted:
        return accepted[0]
    return _slug(slide.title, fallback="concept")


def _readiness_notes_for_slide(
    slide: SalesTemplateSlide,
    selected: List[Dict[str, Any]],
    has_exact_type: bool,
) -> List[str]:
    notes: List[str] = []
    if not selected:
        notes.append("No Data Nexus point matched this slide.")
        return notes
    if slide.accepted_point_types and not has_exact_type:
        notes.append("No exact accepted point-type match; add a more specific Data Nexus answer.")

    primary = selected[0]
    if not _point_title(primary):
        notes.append("Matched point is missing a title.")
    if not _point_summary(primary):
        notes.append("Matched point is missing a concise summary.")
    content = _clean_markdown_text(_point_content(primary), limit=1200)
    if len(content) < 40:
        notes.append("Matched point needs a more concrete answer body.")
    if _point_is_question_like(primary):
        notes.append("Matched point still looks like an unanswered question.")
    return notes


def _readiness_question_for_slide(
    slide: SalesTemplateSlide,
    *,
    status: str,
    target_point_type: str,
    notes: List[str],
) -> Dict[str, Any]:
    hint = QUESTION_HINTS.get(target_point_type) or "the specific claim, evidence, and source the slide should use"
    prefix = f"Slide {slide.number:02d} ({slide.title})"
    template_question = str(slide.question or "").strip()
    if status == "missing":
        text = (
            f"{template_question} Add a `{target_point_type}` answer with {hint}."
            if template_question
            else f"What should {prefix} say? Add a `{target_point_type}` answer with {hint}."
        )
    elif any("No exact accepted point-type match" in note for note in notes):
        text = (
            f"{template_question} Include a `{target_point_type}` answer with {hint}."
            if template_question
            else f"What `{target_point_type}` answer should support {prefix}? Include {hint}."
        )
    else:
        text = f"Can you strengthen the Data Nexus answer for {prefix}? Include {hint}."
    return {
        "question_id": f"slide_{slide.number:02d}_{_slug(target_point_type, fallback='prep')}",
        "point_type": "prep_question",
        "slide_number": slide.number,
        "slide_title": slide.title,
        "accepted_point_type": target_point_type,
        "text": text,
    }


def _refinement_question_for_slide(
    slide: SalesTemplateSlide,
    *,
    target_point_type: str,
    selected: List[Dict[str, Any]],
) -> Dict[str, Any]:
    hint = QUESTION_HINTS.get(target_point_type) or "the claim, evidence, source, and buyer-relevant detail the slide should use"
    source_ids = _format_source_ids(selected)
    source_text = ", ".join(source_ids[:3]) if source_ids else "the matched Data Nexus answer"
    return {
        "question_id": f"slide_{slide.number:02d}_refine_{_slug(target_point_type, fallback='prep')}",
        "point_type": "prep_question",
        "question_kind": "refinement",
        "slide_number": slide.number,
        "slide_title": slide.title,
        "accepted_point_type": target_point_type,
        "matched_point_ids": source_ids,
        "text": (
            f"What detail would make Slide {slide.number:02d} ({slide.title}) stronger? "
            f"Review {source_text} and add or confirm {hint}."
        ),
    }


def analyze_sales_deck_readiness(
    template_path: str | Path | None,
    point_bundle: Dict[str, Any],
    *,
    root: str | Path | None = None,
) -> Dict[str, Any]:
    root_path = _library_root(root)
    if template_path:
        try:
            resolved_template = resolve_library_path(str(template_path), root=root_path)
        except Exception as exc:
            return {"ok": False, "status": "needs_setup", "message": str(exc), "slides": [], "questions": []}
    else:
        resolved_template, message = resolve_latest_approved_sales_agent_template(root=root_path)
        if resolved_template is None:
            return {"ok": False, "status": "needs_setup", "message": message, "slides": [], "questions": []}

    try:
        template_text = _read_text(resolved_template)
    except Exception as exc:
        return {
            "ok": False,
            "status": "needs_setup",
            "message": f"Failed to read agent template: {exc}",
            "slides": [],
            "questions": [],
        }

    metadata, _template_body, template_slides = parse_sales_agent_template(template_text)
    status = str(metadata.get("status", "") or "").strip().lower()
    target_agent = str(metadata.get("target_agent", "") or "").strip().lower()
    artifact_kind = str(metadata.get("artifact_kind", "") or "").strip().lower()
    if status != "approved":
        return {"ok": False, "status": "needs_setup", "message": "Approve the agent template before readiness analysis.", "slides": [], "questions": []}
    if target_agent != "sales_agent":
        return {"ok": False, "status": "needs_setup", "message": "Selected template is not a `sales_agent` template.", "slides": [], "questions": []}
    if artifact_kind != "html_deck":
        return {"ok": False, "status": "needs_setup", "message": "Selected template does not generate `html_deck` artifacts.", "slides": [], "questions": []}
    if not template_slides:
        return {"ok": False, "status": "needs_setup", "message": "Selected template has no `## Slide NN: ...` sections.", "slides": [], "questions": []}

    points = _usable_sales_points(point_bundle)
    slide_reports: List[Dict[str, Any]] = []
    questions: List[Dict[str, Any]] = []
    refinement_questions: List[Dict[str, Any]] = []
    for slide in template_slides:
        selected, has_exact = _select_points_for_slide(points, slide)
        notes = _readiness_notes_for_slide(slide, selected, has_exact)
        if not selected:
            slide_status = "missing"
        elif notes:
            slide_status = "weak"
        else:
            slide_status = "ready"
        target_point_type = _target_point_type_for_slide(slide, selected)
        if slide_status != "ready":
            questions.append(
                _readiness_question_for_slide(
                    slide,
                    status=slide_status,
                    target_point_type=target_point_type,
                    notes=notes,
                )
            )
        else:
            refinement_questions.append(
                _refinement_question_for_slide(
                    slide,
                    target_point_type=target_point_type,
                    selected=selected,
                )
            )
        slide_reports.append(
            {
                "slide_id": slide.slide_id,
                "number": slide.number,
                "title": slide.title,
                "required": bool(slide.required),
                "status": slide_status,
                "accepted_point_types": list(slide.accepted_point_types),
                "quality_schema": _slide_quality_schema(slide),
                "target_point_type": target_point_type,
                "matched_point_ids": _format_source_ids(selected),
                "has_exact_type_match": has_exact,
                "notes": notes,
            }
        )

    ready_count = sum(1 for slide in slide_reports if slide.get("status") == "ready")
    weak_count = sum(1 for slide in slide_reports if slide.get("status") == "weak")
    missing_count = sum(1 for slide in slide_reports if slide.get("status") == "missing")
    overall_status = "ready_to_generate" if weak_count == 0 and missing_count == 0 else "needs_answers"
    return {
        "ok": True,
        "status": overall_status,
        "message": "All slides have usable Data Nexus support." if overall_status == "ready_to_generate" else "Some slides need stronger Data Nexus answers.",
        "template_path": _display_path(resolved_template),
        "template_family_id": str(metadata.get("template_family_id", "") or ""),
        "template_version_id": str(metadata.get("template_version_id", "") or ""),
        "slide_count": len(slide_reports),
        "ready_slide_count": ready_count,
        "weak_slide_count": weak_count,
        "missing_slide_count": missing_count,
        "point_count": len((point_bundle or {}).get("points", []) or []),
        "usable_point_count": len(points),
        "slides": slide_reports,
        "questions": questions,
        "refinement_questions": refinement_questions,
    }


def _deck_title(metadata: Dict[str, Any], template_body: str, points: List[Dict[str, Any]]) -> str:
    for point in points:
        point_type = _normalize_type(point.get("type", ""))
        if point_type in {"company_profile", "product_one_liner", "product_summary"}:
            title = _point_title(point)
            if title:
                return title
    match = re.search(r"(?m)^#\s+(.+?)\s*$", template_body or "")
    if match:
        title = _clean_markdown_text(match.group(1), limit=120)
        title = re.sub(r"\s+Agent\s+Template\s*$", "", title, flags=re.IGNORECASE).strip()
        if title:
            return title
    family = str(metadata.get("template_family_id", "") or "").replace("_", " ").strip()
    return family.title() or "Sales Deck"


def _format_source_ids(points: List[Dict[str, Any]]) -> List[str]:
    out = []
    for point in points:
        point_id = _point_id(point)
        if point_id and point_id not in out:
            out.append(point_id)
    return out


def _slide_quality_schema(slide: SalesTemplateSlide) -> Dict[str, Any]:
    return {
        "required": bool(slide.required),
        "question": slide.question,
        "objective": slide.objective,
        "audience": slide.audience,
        "accepted_point_types": list(slide.accepted_point_types),
        "readiness_criteria": list(slide.readiness_criteria or slide.validation_rules),
        "fallback_behavior": list(slide.fallback_behavior or slide.missing_field_behavior),
        "copy_constraints": list(slide.copy_constraints),
        "source_point_id_requirements": list(slide.source_point_id_requirements),
        "missing_field_behavior": list(slide.missing_field_behavior),
        "source_guidance": list(slide.source_guidance),
        "validation_rules": list(slide.validation_rules),
    }


def _slide_copy(slide: SalesTemplateSlide, points: List[Dict[str, Any]], has_exact_type: bool) -> Dict[str, Any]:
    source_ids = _format_source_ids(points)
    missing = []
    if not points:
        missing.append("No Data Nexus point matched this slide.")
    elif slide.accepted_point_types and not has_exact_type:
        missing.append("No exact accepted point-type match; review semantic match.")

    if points:
        primary = points[0]
        headline = _sentence(_point_summary(primary) or _point_title(primary), limit=112)
        if not headline:
            headline = _point_title(primary)
        proof_items = []
        for point in points:
            proof = _sentence(_point_content(point) or _point_summary(point) or _point_title(point), limit=190)
            if proof:
                proof_items.append(proof)
        if not proof_items:
            proof_items.append(_point_title(primary))
        note = _sentence(_point_content(primary) or _point_summary(primary), limit=320)
    else:
        headline = "Needs Data Nexus evidence"
        proof_items = ["Connect or add points matching the slide's accepted point types."]
        note = "No source point was available for this slide."

    return {
        "slide_id": slide.slide_id,
        "number": slide.number,
        "title": slide.title,
        "headline": headline,
        "supporting_proof": proof_items,
        "speaker_note": note,
        "source_point_ids": source_ids,
        "missing_fields": missing,
        "review_status": "needs_review" if missing else "ready_for_review",
        "accepted_point_types": list(slide.accepted_point_types),
        "quality_schema": _slide_quality_schema(slide),
    }


def _esc(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _render_sources(source_ids: List[str]) -> str:
    if not source_ids:
        return "<span class=\"source missing\">no source point</span>"
    return "".join(f"<span class=\"source\">{_esc(point_id)}</span>" for point_id in source_ids)


def _render_proof_items(items: List[str]) -> str:
    if not items:
        return "<p class=\"empty\">No proof available.</p>"
    return "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in items) + "</ul>"


def _render_html(
    *,
    title: str,
    metadata: Dict[str, Any],
    slides: List[Dict[str, Any]],
    point_count: int,
    generated_at: str,
    manifest: Dict[str, Any],
) -> str:
    family = str(metadata.get("template_family_id", "") or "")
    version = str(metadata.get("template_version_id", "") or "")
    review_status = (
        "needs_review"
        if any(slide.get("missing_fields") or str(slide.get("review_status") or "").strip().lower() == "needs_review" for slide in slides)
        else "ready_for_review"
    )
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2).replace("</", "<\\/")
    slide_html = []
    slide_count = len(slides)
    for slide in slides:
        number = int(slide.get("number") or 0)
        title_text = str(slide.get("title") or "").strip()
        missing = slide.get("missing_fields", []) or []
        missing_html = ""
        if missing:
            missing_html = (
                "<div class=\"missing-fields\"><strong>Needs review</strong>"
                + "<ul>"
                + "".join(f"<li>{_esc(item)}</li>" for item in missing)
                + "</ul></div>"
            )
        slide_html.append(
            "\n".join(
                [
                    f"<section class=\"slide\" id=\"{_esc(slide['slide_id'])}\" data-review-status=\"{_esc(slide['review_status'])}\">",
                    "  <div class=\"slide-meta\">",
                    f"    <span>Slide {number:02d} / {slide_count:02d}</span>",
                    f"    <span>{_esc(slide['review_status'])}</span>",
                    "  </div>",
                    "  <div class=\"slide-main\">",
                    "    <div class=\"slide-title-row\">",
                    f"      <span class=\"slide-number-badge\">{number:02d}</span>",
                    f"      <p class=\"slide-title\">{_esc(title_text)}</p>",
                    "    </div>",
                    f"    <h2>{_esc(slide['headline'])}</h2>",
                    "  </div>",
                    "  <div class=\"slide-proof\">",
                    f"    <h3>{_esc(SUPPORTING_SECTION_LABEL)}</h3>",
                    f"    {_render_proof_items(list(slide.get('supporting_proof', []) or []))}",
                    f"    {missing_html}",
                    "  </div>",
                    "  <aside class=\"speaker-note\">",
                    "    <h3>Speaker Note</h3>",
                    f"    <p>{_esc(slide.get('speaker_note', ''))}</p>",
                    "  </aside>",
                    f"  <footer class=\"sources\">{_render_sources(list(slide.get('source_point_ids', []) or []))}</footer>",
                    "</section>",
                ]
            )
        )

    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{_esc(title)}</title>\n"
        "  <link rel=\"stylesheet\" href=\"styles.css\">\n"
        "</head>\n"
        "<body>\n"
        "  <header class=\"deck-header\">\n"
        "    <div>\n"
        f"      <p class=\"eyebrow\">{_esc(family)} &middot; { _esc(version) }</p>\n"
        f"      <h1>{_esc(title)}</h1>\n"
        "    </div>\n"
        "    <dl>\n"
        f"      <div><dt>Status</dt><dd>{_esc(review_status)}</dd></div>\n"
        f"      <div><dt>Points</dt><dd>{int(point_count)}</dd></div>\n"
        f"      <div><dt>Generated</dt><dd>{_esc(generated_at)}</dd></div>\n"
        "    </dl>\n"
        "  </header>\n"
        "  <main class=\"deck\">\n"
        f"{''.join(slide_html)}\n"
        "  </main>\n"
        f"  <script type=\"application/json\" id=\"deck-manifest\">{manifest_json}</script>\n"
        "</body>\n"
        "</html>\n"
    )


def _render_css() -> str:
    return """\
:root {
  color-scheme: dark;
  --bg: #111315;
  --panel: #191d22;
  --text: #f4f7fb;
  --muted: #aeb7c4;
  --line: #303844;
  --accent: #38bdf8;
  --accent-2: #34d399;
  --warn: #f59e0b;
  --danger: #fb7185;
}

* {
  box-sizing: border-box;
}

html {
  scroll-snap-type: y proximity;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  letter-spacing: 0;
}

.deck-header {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 18px 40px;
  background: rgba(17, 19, 21, 0.94);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(10px);
}

.deck-header h1 {
  margin: 2px 0 0;
  font-size: 1.55rem;
  line-height: 1.2;
  font-weight: 650;
}

.deck-header dl {
  display: flex;
  gap: 18px;
  margin: 0;
}

.deck-header div,
.deck-header dt,
.deck-header dd {
  min-width: 0;
}

.deck-header dt {
  color: var(--muted);
  font-size: 0.72rem;
  text-transform: uppercase;
}

.deck-header dd {
  margin: 2px 0 0;
  font-size: 0.88rem;
}

.deck {
  width: 100%;
}

.slide {
  min-height: 100vh;
  scroll-snap-align: start;
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
  grid-template-rows: auto 1fr auto;
  gap: 28px 34px;
  padding: 62px 56px 44px;
  border-bottom: 1px solid var(--line);
}

.slide-meta {
  grid-column: 1 / -1;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted);
  font-size: 0.78rem;
  text-transform: uppercase;
}

.eyebrow {
  margin: 0;
  color: var(--accent);
  font-size: 0.82rem;
  font-weight: 650;
  text-transform: uppercase;
}

.slide-main {
  align-self: center;
  min-width: 0;
}

.slide-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 0 0 18px;
}

.slide-number-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 48px;
  height: 42px;
  padding: 0 10px;
  border: 1px solid #256b55;
  border-radius: 6px;
  color: #bbf7d0;
  background: #0f2d24;
  font-size: 1.05rem;
  font-weight: 750;
}

.slide-title {
  margin: 0;
  color: var(--accent);
  font-size: 1.28rem;
  line-height: 1.2;
  font-weight: 760;
  text-transform: uppercase;
}

.slide-main h2 {
  margin: 0;
  max-width: 980px;
  font-size: 3rem;
  line-height: 1.05;
  font-weight: 720;
}

.slide-proof,
.speaker-note {
  align-self: center;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 22px;
}

.slide-proof h3,
.speaker-note h3 {
  margin: 0 0 12px;
  font-size: 0.86rem;
  color: var(--accent-2);
  text-transform: uppercase;
}

.slide-proof ul {
  margin: 0;
  padding-left: 20px;
}

.slide-proof li {
  margin: 0 0 10px;
  color: #dbe4ef;
  line-height: 1.45;
}

.speaker-note {
  grid-column: 1 / 2;
  align-self: start;
}

.speaker-note p,
.empty {
  margin: 0;
  color: var(--muted);
  line-height: 1.45;
}

.missing-fields {
  margin-top: 18px;
  border-top: 1px solid var(--line);
  padding-top: 14px;
  color: var(--warn);
}

.missing-fields ul {
  margin-top: 8px;
}

.sources {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.source {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 4px 8px;
  border: 1px solid #256b55;
  border-radius: 999px;
  color: #bbf7d0;
  background: #0f2d24;
  font-size: 0.78rem;
}

.source.missing {
  border-color: #7c2d12;
  color: #fed7aa;
  background: #321609;
}

[data-review-status="needs_review"] .slide-meta span:last-child {
  color: var(--danger);
}

@media (max-width: 860px) {
  .deck-header {
    position: static;
    align-items: flex-start;
    flex-direction: column;
    padding: 16px 20px;
  }

  .deck-header dl {
    flex-wrap: wrap;
  }

  .slide {
    min-height: auto;
    grid-template-columns: 1fr;
    padding: 40px 22px 32px;
  }

  .slide-main h2 {
    font-size: 2rem;
  }

  .slide-title-row {
    align-items: flex-start;
    gap: 10px;
  }

  .slide-number-badge {
    min-width: 42px;
    height: 36px;
    font-size: 0.95rem;
  }

  .slide-title {
    font-size: 1rem;
  }

  .speaker-note {
    grid-column: 1;
  }
}

@page {
  size: 11in 8.5in;
  margin: 0;
}

@media print {
  html {
    scroll-snap-type: none;
  }

  html,
  body {
    width: 11in;
    min-height: 8.5in;
    margin: 0;
    background: #111315;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  .deck-header {
    position: relative;
    top: auto;
    z-index: auto;
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(3.1in, 0.8fr);
    align-items: center;
    justify-content: stretch;
    width: 11in;
    height: 8.5in;
    min-height: 8.5in;
    max-height: 8.5in;
    gap: 0.45in;
    padding: 0.78in;
    overflow: hidden;
    background: #111315;
    border-bottom: 0;
    backdrop-filter: none;
    break-after: page;
    page-break-after: always;
    break-inside: avoid;
    page-break-inside: avoid;
  }

  .deck-header h1 {
    font-size: 0.46in;
    line-height: 1.05;
  }

  .deck-header .eyebrow {
    font-size: 0.12in;
  }

  .deck-header dl {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0.14in;
  }

  .deck-header dl > div {
    padding: 0.16in;
    background: #191d22;
    border: 1px solid #303844;
    border-radius: 0.08in;
  }

  .deck-header dt {
    font-size: 0.09in;
  }

  .deck-header dd {
    font-size: 0.14in;
  }

  .deck {
    display: block;
    width: 11in;
    margin: 0;
    padding: 0;
  }

  .slide {
    width: 11in;
    height: 8.5in;
    min-height: 8.5in;
    max-height: 8.5in;
    overflow: hidden;
    break-after: page;
    page-break-after: always;
    break-inside: avoid;
    page-break-inside: avoid;
    scroll-snap-align: none;
    grid-template-columns: minmax(0, 1.2fr) minmax(2.9in, 0.8fr);
    gap: 0.24in 0.32in;
    padding: 0.48in 0.52in 0.4in;
    border-bottom: 0;
  }

  .slide:last-child {
    break-after: auto;
    page-break-after: auto;
  }

  .slide-meta {
    font-size: 0.68rem;
  }

  .slide-main h2 {
    font-size: 2.35rem;
    line-height: 1.05;
  }

  .slide-title {
    font-size: 1.05rem;
  }

  .slide-proof,
  .speaker-note {
    padding: 0.18in;
  }

  .slide-proof li,
  .speaker-note p,
  .empty {
    line-height: 1.35;
  }
}
"""


def _unique_deck_dir(output_root: Path, family_id: str, version_id: str, generated_at: str) -> Path:
    stamp = generated_at.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
    base = output_root / _slug(family_id, "sales_deck") / _slug(version_id, "version") / stamp / "deck"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = base.parent / f"deck_{suffix}"
        suffix += 1
    return candidate


def _output_base_for_deck(root_path: Path, output_root: str | Path | None) -> Path:
    if output_root:
        output_base = Path(output_root).expanduser()
        if not output_base.is_absolute():
            return (root_path / output_base).resolve()
        return output_base.resolve()
    return root_path / "generated_decks"


def _write_deck_artifact(
    *,
    title: str,
    metadata: Dict[str, Any],
    slides: List[Dict[str, Any]],
    point_count: int,
    point_bundle: Dict[str, Any],
    resolved_template: Path,
    family_id: str,
    version_id: str,
    output_root: str | Path | None,
    root_path: Path,
    generated_by: str,
    warnings: List[str] | None = None,
) -> SalesDeckResult:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output_base = _output_base_for_deck(root_path, output_root)
    deck_dir = _unique_deck_dir(output_base, family_id, version_id, generated_at)
    index_path = deck_dir / "index.html"
    css_path = deck_dir / "styles.css"
    manifest_path = deck_dir / "manifest.json"
    clean_warnings = list(warnings or [])
    manifest = {
        "generated_by": generated_by,
        "generated_at": generated_at,
        "deck_title": title,
        "template_path": _display_path(resolved_template),
        "template_family_id": family_id,
        "template_version_id": version_id,
        "point_bundle_version": (point_bundle or {}).get("version", ""),
        "point_count": int(point_count),
        "review_status": (
            "needs_review"
            if clean_warnings
            or any(str(slide.get("review_status") or "").strip().lower() == "needs_review" for slide in slides)
            else "ready_for_review"
        ),
        "artifacts": {
            "deck_dir": str(deck_dir),
            "index_path": str(index_path),
            "css_path": str(css_path),
            "manifest_path": str(manifest_path),
        },
        "slides": slides,
    }
    html_text = _render_html(
        title=title,
        metadata=metadata,
        slides=slides,
        point_count=int(point_count),
        generated_at=generated_at,
        manifest=manifest,
    )

    try:
        deck_dir.mkdir(parents=True, exist_ok=False)
        index_path.write_text(html_text, encoding="utf-8")
        css_path.write_text(_render_css(), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        return SalesDeckResult(False, f"Failed to write Sales Agent deck: {exc}", warnings=clean_warnings)

    source_label = "Mediator AI" if generated_by == "sales_agent_mediator_ai" else "Sales Agent"
    return SalesDeckResult(
        True,
        f"Generated {source_label} HTML deck with {len(slides)} slides.",
        deck_dir=str(deck_dir),
        index_path=str(index_path),
        css_path=str(css_path),
        manifest_path=str(manifest_path),
        template_path=_display_path(resolved_template),
        template_family_id=family_id,
        template_version_id=version_id,
        slide_count=len(slides),
        point_count=int(point_count),
        warnings=clean_warnings,
    )


def _coerce_slide_text(value: Any, *, limit: int = 240) -> str:
    text = _clean_markdown_text(str(value or ""), limit=limit)
    text = re.sub(r"\s+", " ", text).strip()
    return _truncate_text(text, limit=limit)


def _coerce_slide_text_list(value: Any, *, limit: int = 5, item_limit: int = 220) -> List[str]:
    raw_items = value if isinstance(value, list) else [value]
    out: List[str] = []
    for raw in raw_items:
        if raw is None:
            continue
        text = _coerce_slide_text(raw, limit=item_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _coerce_source_id_list(value: Any, *, limit: int = 12) -> List[str]:
    raw_items = value if isinstance(value, list) else re.split(r"[,;\n]+", str(value or ""))
    out: List[str] = []
    for raw in raw_items:
        text = str(raw or "").strip().strip("\"'")
        if text and text not in out:
            out.append(text[:180])
        if len(out) >= limit:
            break
    return out


def _strip_html_text(value: str) -> str:
    text = re.sub(r"(?is)<script\b.*?</script>", " ", str(value or ""))
    text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _deck_title_from_html(html_text: str) -> str:
    for pattern in (r"(?is)<h1\b[^>]*>(?P<value>.*?)</h1>", r"(?is)<title\b[^>]*>(?P<value>.*?)</title>"):
        match = re.search(pattern, str(html_text or ""))
        if match:
            title = _coerce_slide_text(_strip_html_text(match.group("value")), limit=140)
            if title:
                return title
    return ""


def _path_from_artifact(raw: Any, *, base_dir: Path, default_name: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        return base_dir / default_name
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_deck_manifest(index_path: str | Path) -> tuple[Path, str, Dict[str, Any], str]:
    path = Path(index_path).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists() or not path.is_file():
        return path, "", {}, f"Deck index file does not exist: {path}"
    try:
        html_text = _read_text(path)
    except Exception as exc:
        return path, "", {}, f"Failed to read deck index: {exc}"

    title = _deck_title_from_html(html_text)
    manifest: Dict[str, Any] = {}
    match = DECK_MANIFEST_RE.search(html_text)
    if match:
        raw_json = html_lib.unescape(str(match.group("json") or "").strip())
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                manifest = parsed
        except Exception:
            manifest = {}
    if not manifest:
        manifest_path = path.parent / "manifest.json"
        if manifest_path.exists():
            try:
                parsed = json.loads(_read_text(manifest_path))
                if isinstance(parsed, dict):
                    manifest = parsed
            except Exception:
                manifest = {}
    if not manifest:
        return path, title, {}, "Deck manifest was not found in index.html or manifest.json."
    if not title:
        title = _coerce_slide_text(manifest.get("deck_title") or manifest.get("title"), limit=140)
    return path, title, manifest, ""


def _coerce_manifest_slide(raw: Dict[str, Any], *, fallback_number: int) -> Dict[str, Any]:
    try:
        number = int(raw.get("number") or raw.get("slide_number") or raw.get("slide") or fallback_number)
    except Exception:
        number = fallback_number
    title = _coerce_slide_text(raw.get("title"), limit=120) or f"Slide {number:02d}"
    headline = _coerce_slide_text(raw.get("headline") or raw.get("title_line") or raw.get("main_claim"), limit=132)
    if not headline:
        headline = f"{title} needs more information"
    proof = _coerce_slide_text_list(
        raw.get("supporting_proof")
        if "supporting_proof" in raw
        else raw.get("supporting_points")
        if "supporting_points" in raw
        else raw.get("proof")
        if "proof" in raw
        else raw.get("evidence")
        if "evidence" in raw
        else raw.get("bullets"),
        limit=5,
        item_limit=230,
    )
    if not proof:
        proof = ["No specific Data Nexus fact was available for this slide yet."]
    speaker_note = _coerce_slide_text(
        raw.get("speaker_note") or raw.get("speaker_notes") or raw.get("talk_track") or raw.get("notes"),
        limit=520,
    )
    if not speaker_note:
        speaker_note = "This slide is included as a draft placeholder and needs a stronger Data Nexus answer before final use."
    review_status = str(raw.get("review_status") or "ready_for_review").strip().lower().replace(" ", "_")
    if review_status not in {"ready_for_review", "needs_review"}:
        review_status = "ready_for_review"
    missing = _coerce_slide_text_list(raw.get("missing_fields") or [], limit=8, item_limit=260)
    if missing:
        review_status = "needs_review"
    return {
        "slide_id": _coerce_slide_text(raw.get("slide_id") or raw.get("id"), limit=120) or f"slide_{number:02d}_{_slug(title, 'slide')}",
        "number": number,
        "title": title,
        "headline": headline,
        "supporting_proof": proof,
        "speaker_note": speaker_note,
        "source_point_ids": _coerce_source_id_list(raw.get("source_point_ids") or raw.get("matched_point_ids") or raw.get("source_ids") or []),
        "missing_fields": missing,
        "review_status": review_status,
        "accepted_point_types": _coerce_source_id_list(raw.get("accepted_point_types") or []),
        "quality_schema": raw.get("quality_schema") if isinstance(raw.get("quality_schema"), dict) else {},
        "generated_copy_source": str(raw.get("generated_copy_source") or "existing_deck").strip(),
    }


def _manifest_slides(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    slides = []
    raw_slides = manifest.get("slides") if isinstance(manifest, dict) else []
    for idx, raw in enumerate(raw_slides if isinstance(raw_slides, list) else [], start=1):
        if isinstance(raw, dict):
            slides.append(_coerce_manifest_slide(raw, fallback_number=idx))
    slides.sort(key=lambda item: int(item.get("number") or 0))
    return slides


def sales_deck_prompt_context_from_index(index_path: str | Path) -> Dict[str, Any]:
    path, title, manifest, error = _load_deck_manifest(index_path)
    if error:
        return {"ok": False, "index_path": str(path), "message": error}
    slides = _manifest_slides(manifest)
    return {
        "ok": True,
        "index_path": str(path),
        "deck_title": title or _coerce_slide_text(manifest.get("deck_title") or "Sales Deck", limit=140),
        "generated_at": manifest.get("generated_at") or "",
        "review_status": manifest.get("review_status") or "",
        "slides": [
            {
                "number": slide.get("number"),
                "title": slide.get("title"),
                "headline": slide.get("headline"),
                "supporting_proof": slide.get("supporting_proof") or [],
                "speaker_note": slide.get("speaker_note"),
                "source_point_ids": slide.get("source_point_ids") or [],
                "review_status": slide.get("review_status"),
            }
            for slide in slides
        ],
    }


def restyle_sales_deck_from_index(index_path: str | Path) -> SalesDeckResult:
    path, title, manifest, error = _load_deck_manifest(index_path)
    if error:
        return SalesDeckResult(False, error)
    slides = _manifest_slides(manifest)
    if not slides:
        return SalesDeckResult(False, "Deck manifest does not contain slide data.")

    deck_dir = path.parent
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    css_path = _path_from_artifact(artifacts.get("css_path"), base_dir=deck_dir, default_name="styles.css")
    manifest_path = _path_from_artifact(artifacts.get("manifest_path"), base_dir=deck_dir, default_name="manifest.json")
    family_id = str(manifest.get("template_family_id") or "").strip()
    version_id = str(manifest.get("template_version_id") or "").strip()
    metadata = {
        "template_family_id": family_id,
        "template_version_id": version_id,
    }
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated_manifest = dict(manifest)
    updated_manifest["deck_title"] = title or _coerce_slide_text(manifest.get("deck_title") or "Sales Deck", limit=140)
    updated_manifest["restyled_at"] = now
    updated_manifest["supporting_section_label"] = SUPPORTING_SECTION_LABEL
    updated_manifest["artifacts"] = {
        "deck_dir": str(deck_dir),
        "index_path": str(path),
        "css_path": str(css_path),
        "manifest_path": str(manifest_path),
    }
    updated_manifest["slides"] = slides
    point_count = int(updated_manifest.get("point_count") or 0)
    generated_at = str(updated_manifest.get("generated_at") or now)
    html_text = _render_html(
        title=updated_manifest["deck_title"],
        metadata=metadata,
        slides=slides,
        point_count=point_count,
        generated_at=generated_at,
        manifest=updated_manifest,
    )
    try:
        path.write_text(html_text, encoding="utf-8")
        css_path.write_text(_render_css(), encoding="utf-8")
        manifest_path.write_text(json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        return SalesDeckResult(False, f"Failed to restyle Sales Agent deck: {exc}")
    return SalesDeckResult(
        True,
        f"Refreshed HTML deck style for {len(slides)} slides.",
        deck_dir=str(deck_dir),
        index_path=str(path),
        css_path=str(css_path),
        manifest_path=str(manifest_path),
        template_path=str(updated_manifest.get("template_path") or ""),
        template_family_id=family_id,
        template_version_id=version_id,
        slide_count=len(slides),
        point_count=point_count,
    )


def _ai_draft_slide_by_number(draft: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    raw_slides = draft.get("slides") if isinstance(draft, dict) else []
    out: Dict[int, Dict[str, Any]] = {}
    for raw in raw_slides if isinstance(raw_slides, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            number = int(raw.get("number") or raw.get("slide_number") or raw.get("slide") or 0)
        except Exception:
            number = 0
        if number and number not in out:
            out[number] = raw
    return out


def _coerce_ai_draft_slide(
    raw: Dict[str, Any],
    slide: SalesTemplateSlide,
    *,
    valid_source_ids: set[str],
) -> tuple[Dict[str, Any] | None, List[str]]:
    warnings: List[str] = []
    headline = _coerce_slide_text(
        raw.get("headline")
        or raw.get("title_line")
        or raw.get("main_claim")
        or raw.get("claim"),
        limit=132,
    )
    proof = _coerce_slide_text_list(
        raw.get("supporting_proof")
        if "supporting_proof" in raw
        else raw.get("proof")
        if "proof" in raw
        else raw.get("evidence")
        if "evidence" in raw
        else raw.get("bullets"),
        limit=5,
        item_limit=230,
    )
    speaker_note = _coerce_slide_text(
        raw.get("speaker_note")
        or raw.get("speaker_notes")
        or raw.get("talk_track")
        or raw.get("notes"),
        limit=520,
    )
    raw_source_ids = (
        raw.get("source_point_ids")
        if "source_point_ids" in raw
        else raw.get("matched_point_ids")
        if "matched_point_ids" in raw
        else raw.get("source_ids")
        if "source_ids" in raw
        else []
    )
    source_ids = _coerce_source_id_list(raw_source_ids)
    if valid_source_ids:
        unknown = [item for item in source_ids if item not in valid_source_ids]
        source_ids = [item for item in source_ids if item in valid_source_ids]
        if unknown:
            warnings.append(f"Slide {slide.number:02d}: ignored unknown source ids: {', '.join(unknown[:4])}.")

    missing = []
    if not headline:
        missing.append("Mediator AI draft did not include a headline.")
    if not proof:
        missing.append("Mediator AI draft did not include supporting proof bullets.")
    if not speaker_note:
        missing.append("Mediator AI draft did not include a speaker note.")
    if missing:
        warnings.extend(f"Slide {slide.number:02d}: {item}" for item in missing)
    if not headline:
        headline = f"{slide.title} needs more information"
    if not proof:
        proof = ["No specific Data Nexus fact was available for this slide yet."]
    if not speaker_note:
        speaker_note = "This slide is included as a draft placeholder and needs a stronger Data Nexus answer before final use."

    review_status = str(raw.get("review_status") or "ready_for_review").strip().lower().replace(" ", "_")
    if review_status not in {"ready_for_review", "needs_review"}:
        review_status = "ready_for_review"
    if missing:
        review_status = "needs_review"
    output = {
        "slide_id": slide.slide_id,
        "number": slide.number,
        "title": _coerce_slide_text(raw.get("title") or slide.title, limit=120) or slide.title,
        "headline": headline,
        "supporting_proof": proof,
        "speaker_note": speaker_note,
        "source_point_ids": source_ids,
        "missing_fields": missing,
        "review_status": review_status,
        "accepted_point_types": list(slide.accepted_point_types),
        "quality_schema": _slide_quality_schema(slide),
        "generated_copy_source": "mediator_ai",
    }
    return output, warnings


def generate_sales_deck_from_ai_draft(
    template_path: str | Path | None,
    point_bundle: Dict[str, Any],
    draft: Dict[str, Any],
    *,
    root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> SalesDeckResult:
    root_path = _library_root(root)
    if template_path:
        try:
            resolved_template = resolve_library_path(str(template_path), root=root_path)
        except Exception as exc:
            return SalesDeckResult(False, str(exc))
    else:
        resolved_template, message = resolve_latest_approved_sales_agent_template(root=root_path)
        if resolved_template is None:
            return SalesDeckResult(False, message)

    try:
        template_text = _read_text(resolved_template)
    except Exception as exc:
        return SalesDeckResult(False, f"Failed to read agent template: {exc}")

    metadata, template_body, template_slides = parse_sales_agent_template(template_text)
    status = str(metadata.get("status", "") or "").strip().lower()
    target_agent = str(metadata.get("target_agent", "") or "").strip().lower()
    artifact_kind = str(metadata.get("artifact_kind", "") or "").strip().lower()
    family_id = str(metadata.get("template_family_id", "") or resolved_template.stem).strip()
    version_id = str(metadata.get("template_version_id", "") or "").strip()

    if status != "approved":
        return SalesDeckResult(False, "Approve the agent template before generating a Sales Agent deck.")
    if target_agent != "sales_agent":
        return SalesDeckResult(False, "Selected template is not a `sales_agent` template.")
    if artifact_kind != "html_deck":
        return SalesDeckResult(False, "Selected template does not generate `html_deck` artifacts.")
    if not template_slides:
        return SalesDeckResult(False, "Selected template has no `## Slide NN: ...` sections.")

    points = _usable_sales_points(point_bundle)
    if not isinstance(draft, dict):
        return SalesDeckResult(False, "Mediator AI draft payload was not a JSON object.")

    valid_source_ids = {_point_id(point) for point in points if _point_id(point)}
    raw_by_number = _ai_draft_slide_by_number(draft)
    slide_outputs: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for slide in template_slides:
        raw_slide = raw_by_number.get(slide.number)
        if not raw_slide:
            warnings.append(f"Slide {slide.number:02d}: Mediator AI draft omitted this slide.")
            raw_slide = {
                "number": slide.number,
                "title": slide.title,
                "headline": f"{slide.title} needs more information",
                "supporting_proof": ["No specific Data Nexus fact was available for this slide yet."],
                "speaker_note": "This slide is included as a draft placeholder and needs a stronger Data Nexus answer before final use.",
                "source_point_ids": [],
                "review_status": "needs_review",
            }
        output, slide_warnings = _coerce_ai_draft_slide(raw_slide, slide, valid_source_ids=valid_source_ids)
        warnings.extend(slide_warnings)
        if output is not None:
            slide_outputs.append(output)

    if len(slide_outputs) != len(template_slides):
        return SalesDeckResult(False, "Mediator AI draft was incomplete; no deck was written.", warnings=warnings)

    title = _coerce_slide_text(draft.get("deck_title") or draft.get("title"), limit=140) or _deck_title(metadata, template_body, points)
    return _write_deck_artifact(
        title=title,
        metadata=metadata,
        slides=slide_outputs,
        point_count=len(points),
        point_bundle=point_bundle,
        resolved_template=resolved_template,
        family_id=family_id,
        version_id=version_id,
        output_root=output_root,
        root_path=root_path,
        generated_by="sales_agent_mediator_ai",
        warnings=warnings,
    )


def generate_sales_deck_from_template(
    template_path: str | Path | None,
    point_bundle: Dict[str, Any],
    *,
    root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> SalesDeckResult:
    root_path = _library_root(root)
    if template_path:
        try:
            resolved_template = resolve_library_path(str(template_path), root=root_path)
        except Exception as exc:
            return SalesDeckResult(False, str(exc))
    else:
        resolved_template, message = resolve_latest_approved_sales_agent_template(root=root_path)
        if resolved_template is None:
            return SalesDeckResult(False, message)

    try:
        template_text = _read_text(resolved_template)
    except Exception as exc:
        return SalesDeckResult(False, f"Failed to read agent template: {exc}")

    metadata, template_body, template_slides = parse_sales_agent_template(template_text)
    status = str(metadata.get("status", "") or "").strip().lower()
    target_agent = str(metadata.get("target_agent", "") or "").strip().lower()
    artifact_kind = str(metadata.get("artifact_kind", "") or "").strip().lower()
    family_id = str(metadata.get("template_family_id", "") or resolved_template.stem).strip()
    version_id = str(metadata.get("template_version_id", "") or "").strip()

    if status != "approved":
        return SalesDeckResult(False, "Approve the agent template before generating a Sales Agent deck.")
    if target_agent != "sales_agent":
        return SalesDeckResult(False, "Selected template is not a `sales_agent` template.")
    if artifact_kind != "html_deck":
        return SalesDeckResult(False, "Selected template does not generate `html_deck` artifacts.")
    if not template_slides:
        return SalesDeckResult(False, "Selected template has no `## Slide NN: ...` sections.")

    points = _usable_sales_points(point_bundle)
    if not points:
        return SalesDeckResult(False, "The normalized Data Nexus point bundle has no usable answer points.")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    slide_outputs = []
    warnings = []
    for slide in template_slides:
        selected, has_exact = _select_points_for_slide(points, slide)
        output = _slide_copy(slide, selected, has_exact)
        if output["missing_fields"]:
            warnings.extend(f"Slide {slide.number:02d}: {item}" for item in output["missing_fields"])
        slide_outputs.append(output)

    title = _deck_title(metadata, template_body, points)
    if output_root:
        output_base = Path(output_root).expanduser()
        if not output_base.is_absolute():
            output_base = (root_path / output_base).resolve()
        else:
            output_base = output_base.resolve()
    else:
        output_base = root_path / "generated_decks"
    deck_dir = _unique_deck_dir(output_base, family_id, version_id, generated_at)
    index_path = deck_dir / "index.html"
    css_path = deck_dir / "styles.css"
    manifest_path = deck_dir / "manifest.json"
    manifest = {
        "generated_by": "sales_agent",
        "generated_at": generated_at,
        "deck_title": title,
        "template_path": _display_path(resolved_template),
        "template_family_id": family_id,
        "template_version_id": version_id,
        "point_bundle_version": (point_bundle or {}).get("version", ""),
        "point_count": len(points),
        "review_status": "needs_review" if warnings else "ready_for_review",
        "artifacts": {
            "deck_dir": str(deck_dir),
            "index_path": str(index_path),
            "css_path": str(css_path),
            "manifest_path": str(manifest_path),
        },
        "slides": slide_outputs,
    }
    html_text = _render_html(
        title=title,
        metadata=metadata,
        slides=slide_outputs,
        point_count=len(points),
        generated_at=generated_at,
        manifest=manifest,
    )

    try:
        deck_dir.mkdir(parents=True, exist_ok=False)
        index_path.write_text(html_text, encoding="utf-8")
        css_path.write_text(_render_css(), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        return SalesDeckResult(False, f"Failed to write Sales Agent deck: {exc}", warnings=warnings)

    return SalesDeckResult(
        True,
        f"Generated Sales Agent HTML deck with {len(slide_outputs)} slides.",
        deck_dir=str(deck_dir),
        index_path=str(index_path),
        css_path=str(css_path),
        manifest_path=str(manifest_path),
        template_path=_display_path(resolved_template),
        template_family_id=family_id,
        template_version_id=version_id,
        slide_count=len(slide_outputs),
        point_count=len(points),
        warnings=warnings,
    )
