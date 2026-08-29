from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.services.sales_agent import (
    analyze_sales_deck_readiness,
    generate_sales_deck_from_ai_draft,
    parse_sales_agent_template,
    restyle_sales_deck_from_index,
    sales_deck_prompt_context_from_index,
)
from echograph.services.skills_library import resolve_library_path, scan_skills_library
from nodes.core import Spec


TASK_NODE_KIND = "task"
TASK_NODE_ALIASES = {"agent_task", "task_node", "workflow_task"}
TASK_NODE_KINDS = {TASK_NODE_KIND, *TASK_NODE_ALIASES}

SKILLS_INPUT_PORT = "skills"
DATA_NEXUS_INPUT_PORT = "data_nexus"
MEDIATOR_INPUT_PORT = "mediator"
STATUS_OUTPUT_PORT = "status"
QUESTIONS_OUTPUT_PORT = "questions"
ARTIFACT_OUTPUT_PORT = "artifact"

TASK_BODY_W = 540
TASK_BODY_H = 350

TASK_STATUS_PARAM = "__task_status"
TASK_ID_PARAM = "__task_id"
TASK_GUIDE_PATH_PARAM = "__task_guide_path"
TASK_AGENT_TEMPLATE_PATH_PARAM = "__task_agent_template_path"
TASK_LAST_QUESTIONS_PARAM = "__task_last_questions_json"
TASK_LAST_REPORT_PARAM = "__task_last_report_json"
TASK_LAST_ARTIFACT_PARAM = "__task_last_artifact_path"
TASK_SIZE_PARAM = "__task_size"

CURRENT_SLIDE_TYPE = "slide"
CURRENT_SLIDE_FOLDER = "Slides"
PREVIOUS_SLIDE_TYPE = "old_slide"
PREVIOUS_SLIDE_FOLDER = "Previous Slides"

TASK_STATUS_CHOICES = (
    ("created", "Created"),
    ("needs_setup", "Needs Setup"),
    ("needs_answers", "Needs Answers"),
    ("ready_to_generate", "Ready"),
    ("draft_ready", "Draft"),
    ("generating", "Generating"),
    ("approved", "Approved"),
    ("dismissed", "Dismissed"),
    ("failed", "Failed"),
)

SKILLS_KINDS = {"skills", "skills_library", "skill_library"}
DATA_NEXUS_KINDS = {"data_nexus", "data nexus", "data_graph", "data graph", "nexus"}
MEDIATOR_KINDS = {"mediator_agent", "medigator_agent", "medigator", "mediator", "mediator agent", "medigator agent"}
SALES_AGENT_QUESTIONS_RE = re.compile(
    r"<sales_agent_questions\b[^>]*>(?P<payload>.*?)</sales_agent_questions>",
    re.IGNORECASE | re.DOTALL,
)
SALES_AGENT_REVIEW_RE = re.compile(
    r"<sales_agent_review\b[^>]*>(?P<payload>.*?)</sales_agent_review>",
    re.IGNORECASE | re.DOTALL,
)
SALES_AGENT_DRAFT_RE = re.compile(
    r"<sales_agent_draft\b[^>]*>(?P<payload>.*?)</sales_agent_draft>",
    re.IGNORECASE | re.DOTALL,
)


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _edge_dst_port(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        if hasattr(edge, attr):
            value = str(getattr(edge, attr) or "").strip().lower()
            if value:
                return value
    return ""


def _connected_input_node(scene, node_item, port_name: str, allowed_kinds: set[str]):
    if scene is None or node_item is None:
        return None
    try:
        edges = list(scene._in_edges(node_item))
    except Exception:
        edges = []
    wanted_port = str(port_name or "").strip().lower()
    for edge in edges:
        src = getattr(edge, "src", None)
        if _edge_dst_port(edge) == wanted_port and _kind_of_item(src) in allowed_kinds:
            return src
    for edge in edges:
        src = getattr(edge, "src", None)
        if _kind_of_item(src) in allowed_kinds:
            return src
    return None


def _param_value_from_model(model, name: str, default: str = "") -> str:
    target = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == target:
            return str(entry.get("value", "") or "")
    return str(default or "")


def _set_param_on_item(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, value, rebuild=False, notify_scene=notify_scene)
            return
        except Exception:
            pass
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    target = str(name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == target:
            entry["value"] = str(value or "")
            break
    else:
        params.append({"name": name, "value": str(value or "")})
    try:
        model.params = params
    except Exception:
        pass
    if notify_scene:
        scene = None
        try:
            scene = node_item.scene()
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "set_node_params"):
            try:
                scene.set_node_params(getattr(model, "name", "") or "", params, rebuild=False)
            except Exception:
                pass


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        params = []
        try:
            model.params = params
        except Exception:
            return
    target = str(name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == target:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(node_item) -> None:
    model = getattr(node_item, "model", None)
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        return
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == "__ui_hidden_params":
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(hidden_entry)
    hidden = {part.strip().lower() for part in str(hidden_entry.get("value", "") or "").split(",") if part.strip()}
    hidden.update(
        {
            TASK_ID_PARAM,
            TASK_STATUS_PARAM,
            TASK_GUIDE_PATH_PARAM,
            TASK_AGENT_TEMPLATE_PATH_PARAM,
            TASK_LAST_QUESTIONS_PARAM,
            TASK_LAST_REPORT_PARAM,
            TASK_LAST_ARTIFACT_PARAM,
            TASK_SIZE_PARAM,
            SKILLS_INPUT_PORT,
            DATA_NEXUS_INPUT_PORT,
            MEDIATOR_INPUT_PORT,
            STATUS_OUTPUT_PORT,
            QUESTIONS_OUTPUT_PORT,
            ARTIFACT_OUTPUT_PORT,
        }
    )
    hidden_entry["value"] = ",".join(sorted(hidden))


def _best_version_asset(assets: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not assets:
        return None
    for asset in assets:
        if asset.get("is_latest_approved"):
            return asset
    return sorted(
        assets,
        key=lambda asset: (
            int(asset.get("version_sort", -1) or -1),
            str(asset.get("guide_version_id") or asset.get("template_version_id") or ""),
            str(asset.get("name") or ""),
        ),
        reverse=True,
    )[0]


def _approved_sales_pitch_guides(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for asset in snapshot.get("task_guides", []) or []:
        if str(asset.get("status") or "").strip().lower() != "approved":
            continue
        if str(asset.get("target_agent") or "").strip().lower() != "sales_agent":
            continue
        if str(asset.get("task_kind") or "").strip().lower() != "pitch_deck":
            continue
        if str(asset.get("artifact_kind") or "").strip().lower() != "html_deck":
            continue
        out.append(asset)
    return out


def _approved_sales_templates(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for asset in snapshot.get("agent_templates", []) or []:
        if str(asset.get("status") or "").strip().lower() != "approved":
            continue
        if str(asset.get("target_agent") or "").strip().lower() != "sales_agent":
            continue
        if str(asset.get("artifact_kind") or "").strip().lower() != "html_deck":
            continue
        out.append(asset)
    return out


def _is_question_like_point(point: Dict[str, Any]) -> bool:
    point_type = str(point.get("type") or "").strip().lower()
    content = str(point.get("content") or point.get("summary") or point.get("title") or "").strip()
    if point_type in {"question", "prep_question"}:
        return True
    return content.endswith("?")


def _is_generated_slide_point(point: Dict[str, Any]) -> bool:
    point_type = str(point.get("type") or "").strip().lower()
    folder = str(point.get("folder") or "").strip().lower()
    return point_type in {
        CURRENT_SLIDE_TYPE,
        "deck_slide",
        "generated_slide",
        PREVIOUS_SLIDE_TYPE,
        "previous_slide",
        "deprecated_slide",
    } or folder in {
        "slides",
        "generated_slides",
        "previous slides",
        "old slides",
        "deprecated slides",
    }


def _slug(value: Any, fallback: str = "item") -> str:
    text = str(value or "").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", ".", "/", ":"}:
            out.append("_")
    clean = re.sub(r"_+", "_", "".join(out)).strip("._-")
    return clean or fallback


def _point_type_counts(points: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for point in points:
        point_type = str(point.get("type") or "concept").strip().lower() or "concept"
        counts[point_type] = counts.get(point_type, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _asset_line(asset: Dict[str, Any] | None, *, version_key: str) -> str:
    if not asset:
        return "missing"
    status = str(asset.get("status") or "").strip() or "unknown"
    version = str(asset.get(version_key) or "").strip() or "unversioned"
    return f"{asset.get('name') or 'asset'} ({status}, {version})"


def _task_id_for_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    existing = _param_value_from_model(model, TASK_ID_PARAM, "").strip()
    if existing:
        return existing
    value = "task_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    _set_param_on_item(node_item, TASK_ID_PARAM, value, notify_scene=False)
    return value


def _scene_for_item(node_item):
    try:
        return node_item.scene()
    except Exception:
        return None


def _connected_task_nodes(node_item):
    scene = _scene_for_item(node_item)
    return (
        scene,
        _connected_input_node(scene, node_item, SKILLS_INPUT_PORT, SKILLS_KINDS),
        _connected_input_node(scene, node_item, DATA_NEXUS_INPUT_PORT, DATA_NEXUS_KINDS),
    )


def _connected_task_mediator_node(node_item):
    scene = _scene_for_item(node_item)
    return _connected_input_node(scene, node_item, MEDIATOR_INPUT_PORT, MEDIATOR_KINDS)


def _data_nexus_bundle_from_node(data_nexus_node) -> tuple[Dict[str, Any], str]:
    if data_nexus_node is None:
        return {}, "Connect a Data Nexus node to the Task data_nexus input."
    try:
        from nodes.data_nexus import spec as data_nexus_spec

        bundle_helper = getattr(data_nexus_spec, "normalized_data_nexus_points_from_item", None)
        if not callable(bundle_helper):
            return {}, "Data Nexus point bundle helper is unavailable."
        bundle = bundle_helper(data_nexus_node)
        if not isinstance(bundle, dict):
            return {}, "Data Nexus point bundle helper returned an invalid bundle."
        return bundle, ""
    except Exception as exc:
        return {}, f"Failed to read Data Nexus bundle: {exc}"


def _data_nexus_bundle_fingerprint(bundle: Dict[str, Any]) -> str:
    points = []
    for point in (bundle or {}).get("points", []) or []:
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("id") or "").strip()
        links = []
        for link in point.get("links", []) or []:
            if not isinstance(link, dict):
                continue
            links.append(
                {
                    "direction": str(link.get("direction") or "").strip(),
                    "source": str(link.get("source") or "").strip(),
                    "target": str(link.get("target") or "").strip(),
                    "label": str(link.get("label") or "").strip(),
                }
            )
        links.sort(key=lambda item: (item["direction"], item["source"], item["target"], item["label"]))
        points.append(
            {
                "id": point_id,
                "type": str(point.get("type") or "").strip(),
                "title": str(point.get("title") or point.get("label") or "").strip(),
                "summary": str(point.get("summary") or "").strip(),
                "content": str(point.get("content") or point.get("note") or "").strip(),
                "folder": str(point.get("folder") or "").strip(),
                "source_point_ids": _coerce_string_list(point.get("source_point_ids") or [], limit=80),
                "answer_status": str(point.get("answer_status") or "").strip(),
                "question_text": str(point.get("question_text") or "").strip(),
                "expected_answer_point_type": str(point.get("expected_answer_point_type") or "").strip(),
                "links": links,
            }
        )
    points.sort(key=lambda item: item["id"])
    edges = []
    for edge in (bundle or {}).get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        edges.append(
            {
                "source": str(edge.get("source") or "").strip(),
                "target": str(edge.get("target") or "").strip(),
                "label": str(edge.get("label") or "").strip(),
            }
        )
    edges.sort(key=lambda item: (item["source"], item["target"], item["label"]))
    payload = {"points": points, "edges": edges, "active_question_id": str((bundle or {}).get("active_question_id") or "")}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8", errors="ignore")).hexdigest()


def _current_data_nexus_fingerprint(node_item) -> str:
    _scene, _skills_node, data_nexus_node = _connected_task_nodes(node_item)
    bundle, _error = _data_nexus_bundle_from_node(data_nexus_node)
    return _data_nexus_bundle_fingerprint(bundle) if bundle else ""


def _generation_result_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    generation = report.get("generation")
    if isinstance(generation, dict):
        return generation
    artifact = report.get("artifact")
    if isinstance(artifact, dict):
        return artifact
    return {}


def _artifact_path_from_report(report: Dict[str, Any]) -> str:
    generation = _generation_result_from_report(report)
    return str(generation.get("index_path") or generation.get("html") or "")


def _deck_artifact_path_for_report(node_item, report: Dict[str, Any]) -> str:
    artifact_path = _artifact_path_from_report(report)
    if not artifact_path:
        artifact_path = _param_value_from_model(getattr(node_item, "model", None), TASK_LAST_ARTIFACT_PARAM, "").strip()
    return artifact_path


def _slide_point_id(task_id: str, slide_number: int) -> str:
    clean_task_id = _slug(task_id, fallback="task")
    try:
        number = int(slide_number or 0)
    except Exception:
        number = 0
    return _slug(f"{clean_task_id}_slide_{number:02d}", fallback=f"{clean_task_id}_slide")


def _slide_point_note(deck_context: Dict[str, Any], slide: Dict[str, Any]) -> str:
    try:
        number = int(slide.get("number") or 0)
    except Exception:
        number = 0
    title = str(slide.get("title") or f"Slide {number:02d}").strip()
    headline = str(slide.get("headline") or "").strip()
    supporting = _coerce_string_list(slide.get("supporting_proof") or [], limit=12)
    speaker_note = str(slide.get("speaker_note") or "").strip()
    source_ids = _coerce_string_list(slide.get("source_point_ids") or [], limit=40)
    review_status = str(slide.get("review_status") or "").strip()
    lines = [
        f"# Slide {number:02d}: {title}" if number else f"# {title or 'Slide'}",
        "",
        f"Deck: {deck_context.get('deck_title') or 'Generated Deck'}",
    ]
    if deck_context.get("index_path"):
        lines.append(f"Artifact: {deck_context.get('index_path')}")
    if deck_context.get("template_path"):
        lines.append(f"Template path: {deck_context.get('template_path')}")
    if deck_context.get("template_family_id"):
        lines.append(f"Template family: {deck_context.get('template_family_id')}")
    if deck_context.get("template_version_id"):
        lines.append(f"Template version: {deck_context.get('template_version_id')}")
    if deck_context.get("generated_at"):
        lines.append(f"Generated: {deck_context.get('generated_at')}")
    if review_status:
        lines.append(f"Review status: {review_status}")
    if headline:
        lines.extend(["", "## Headline", "", headline])
    if supporting:
        lines.extend(["", "## Supporting Notes", ""])
        lines.extend(f"- {item}" for item in supporting)
    if speaker_note:
        lines.extend(["", "## Speaker Note", "", speaker_note])
    if source_ids:
        lines.extend(["", "## Source Point IDs", ""])
        lines.extend(f"- {item}" for item in source_ids)
    return "\n".join(lines).strip()


def _slide_actions_from_deck_context(task_id: str, deck_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    slide_points: List[tuple[int, str]] = []
    slides = deck_context.get("slides") if isinstance(deck_context, dict) else []
    raw_slides = [slide for slide in (slides if isinstance(slides, list) else []) if isinstance(slide, dict)]
    raw_slides.sort(key=lambda slide: _int_or_zero(slide.get("number") or slide.get("slide_number") or slide.get("slide")))
    for raw_slide in raw_slides:
        if not isinstance(raw_slide, dict):
            continue
        try:
            number = int(raw_slide.get("number") or 0)
        except Exception:
            number = 0
        if not number:
            continue
        title = str(raw_slide.get("title") or f"Slide {number:02d}").strip()
        headline = str(raw_slide.get("headline") or "").strip()
        source_ids = _coerce_string_list(raw_slide.get("source_point_ids") or [], limit=40)
        point_id = _slide_point_id(task_id, number)
        slide_points.append((number, point_id))
        actions.append(
            {
                "op": "upsert_point",
                "id": point_id,
                "label": f"Slide {number:02d} - {title}"[:120],
                "type": CURRENT_SLIDE_TYPE,
                "folder": CURRENT_SLIDE_FOLDER,
                "summary": headline or title,
                "note": _slide_point_note(deck_context, raw_slide),
                "note_mode": "replace",
                "source_point_ids": source_ids,
                "replace_source_edges": True,
                "create_source_edges": True,
                "source_edge_label": "uses_source",
            }
        )
    for (_source_number, source_id), (_target_number, target_id) in zip(slide_points, slide_points[1:]):
        actions.append(
            {
                "op": "link",
                "source": source_id,
                "target": target_id,
                "label": "next_slide",
                "create_missing": False,
            }
        )
    return actions


def _existing_current_slide_points(task_id: str, bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    clean_task_id = _slug(task_id, fallback="task")
    prefix = f"{clean_task_id}_slide_"
    out: List[Dict[str, Any]] = []
    for point in (bundle or {}).get("points", []) or []:
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("id") or "").strip()
        point_type = str(point.get("type") or "").strip().lower()
        folder = str(point.get("folder") or "").strip().lower()
        if not point_id.startswith(prefix):
            continue
        if point_type != CURRENT_SLIDE_TYPE and folder != CURRENT_SLIDE_FOLDER.lower():
            continue
        out.append(dict(point))
    return out


def _point_note_text(point: Dict[str, Any]) -> str:
    return str(point.get("content") or point.get("note") or "").strip()


def _archive_slide_point_id(point: Dict[str, Any]) -> str:
    point_id = str(point.get("id") or "").strip()
    payload = "\n".join(
        [
            point_id,
            str(point.get("title") or point.get("label") or ""),
            str(point.get("summary") or ""),
            _point_note_text(point),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return _slug(f"{point_id}_old_{digest}", fallback="old_slide")


def _archive_note_for_slide_point(point: Dict[str, Any], *, replacement_id: str = "", reason: str = "") -> str:
    point_id = str(point.get("id") or "").strip()
    title = str(point.get("title") or point.get("label") or point_id or "Slide").strip()
    summary = str(point.get("summary") or "").strip()
    note = _point_note_text(point)
    clean_reason = str(reason or "a newer generated deck indexed replacement slides for this Task.").strip()
    lines = [
        f"# Old Slide: {title}",
        "",
        f"Archived from current slide point: `{point_id}`",
        f"Reason: {clean_reason}",
    ]
    if replacement_id:
        lines.append(f"Replacement current slide point: `{replacement_id}`")
    if summary:
        lines.extend(["", "Previous summary:", "", summary])
    if note:
        lines.extend(["", "Previous slide content:", "", note])
    return "\n".join(lines).strip()


def _archive_replaced_slide_actions(
    task_id: str,
    existing_points: List[Dict[str, Any]],
    current_slide_actions: List[Dict[str, Any]],
    *,
    reason: str = "",
) -> List[Dict[str, Any]]:
    current_by_id = {
        str(action.get("id") or "").strip(): action
        for action in current_slide_actions
        if action.get("op") == "upsert_point" and str(action.get("type") or "").strip().lower() == CURRENT_SLIDE_TYPE
    }
    actions: List[Dict[str, Any]] = []
    link_actions: List[Dict[str, Any]] = []
    for point in existing_points:
        point_id = str(point.get("id") or "").strip()
        if not point_id:
            continue
        replacement = current_by_id.get(point_id)
        old_note = _point_note_text(point)
        old_summary = str(point.get("summary") or "").strip()
        old_label = str(point.get("title") or point.get("label") or "").strip()
        if replacement is not None:
            new_note = str(replacement.get("note") or "").strip()
            new_summary = str(replacement.get("summary") or "").strip()
            new_label = str(replacement.get("label") or "").strip()
            if old_note == new_note and old_summary == new_summary and old_label == new_label:
                continue
        archive_id = _archive_slide_point_id(point)
        source_ids = _coerce_string_list(point.get("source_point_ids") or point.get("source_ids") or [], limit=40)
        actions.append(
            {
                "op": "upsert_point",
                "id": archive_id,
                "label": f"Old {old_label or point_id}"[:120],
                "type": PREVIOUS_SLIDE_TYPE,
                "folder": PREVIOUS_SLIDE_FOLDER,
                "summary": f"Archived slide output: {old_summary or old_label or point_id}"[:500],
                "note": _archive_note_for_slide_point(
                    point,
                    replacement_id=point_id if replacement is not None else "",
                    reason=reason,
                ),
                "note_mode": "replace",
                "source_point_ids": source_ids,
                "replace_source_edges": True,
                "create_source_edges": True,
                "source_edge_label": "uses_source",
            }
        )
        if replacement is not None:
            link_actions.append(
                {
                    "op": "link",
                    "source": archive_id,
                    "target": point_id,
                    "label": "replaced_by",
                    "create_missing": False,
                }
            )
    return [*actions, *link_actions]


def _delete_current_slide_actions(existing_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for point in existing_points:
        point_id = str(point.get("id") or "").strip()
        if point_id:
            actions.append({"op": "delete_point", "id": point_id})
    return actions


def _compare_token(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").lower()


def _template_identity_changed(previous_report: Dict[str, Any], current_template: Dict[str, Any]) -> bool:
    previous_template = previous_report.get("agent_template") if isinstance(previous_report.get("agent_template"), dict) else {}
    if not previous_template or not current_template:
        return False
    for key in ("path", "template_version_id", "template_family_id", "template_id"):
        old_value = _compare_token(previous_template.get(key))
        new_value = _compare_token(current_template.get(key))
        if old_value and new_value and old_value != new_value:
            return True
    return False


def _note_field_value(note: str, label: str) -> str:
    match = re.search(rf"(?im)^{re.escape(label)}\s*:\s*(?P<value>.+?)\s*$", str(note or ""))
    return str(match.group("value") or "").strip() if match else ""


def _slide_point_template_identity_changed(existing_points: List[Dict[str, Any]], current_template: Dict[str, Any]) -> bool:
    current_values = {
        "Template path": _compare_token(current_template.get("path")),
        "Template family": _compare_token(current_template.get("template_family_id")),
        "Template version": _compare_token(current_template.get("template_version_id")),
    }
    for point in existing_points:
        note = _point_note_text(point)
        for label, current_value in current_values.items():
            old_value = _compare_token(_note_field_value(note, label))
            if old_value and current_value and old_value != current_value:
                return True
    return False


def _slide_number_from_point(point: Dict[str, Any]) -> int:
    point_id = str(point.get("id") or "").strip()
    match = re.search(r"(?:^|_)slide_(?P<number>\d{1,2})(?:_|$)", point_id, re.IGNORECASE)
    if match:
        return _int_or_zero(match.group("number"))
    label = str(point.get("label") or point.get("title") or "").strip()
    match = re.search(r"\bslide\s+(?P<number>\d{1,2})\b", label, re.IGNORECASE)
    if match:
        return _int_or_zero(match.group("number"))
    note = _point_note_text(point)
    match = re.search(r"(?m)^#\s+Slide\s+(?P<number>\d{1,2})\s*:", note, re.IGNORECASE)
    return _int_or_zero(match.group("number")) if match else 0


def _normalize_slide_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    text = re.sub(r"^slide\s+\d{1,2}\s*[-:]\s*", "", text, flags=re.IGNORECASE)
    return text.strip(" .:-_")


def _slide_title_from_point(point: Dict[str, Any]) -> str:
    note = _point_note_text(point)
    match = re.search(r"(?m)^#\s+Slide\s+\d{1,2}\s*:\s*(?P<title>.+?)\s*$", note, re.IGNORECASE)
    if match:
        return str(match.group("title") or "").strip()
    label = str(point.get("label") or point.get("title") or "").strip()
    return re.sub(r"^Slide\s+\d{1,2}\s*[-:]\s*", "", label, flags=re.IGNORECASE).strip()


def _template_slide_titles_from_readiness(readiness: Dict[str, Any]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for slide in (readiness or {}).get("slides", []) or []:
        if not isinstance(slide, dict):
            continue
        number = _int_or_zero(slide.get("number"))
        title = str(slide.get("title") or "").strip()
        if number and title:
            out[number] = title
    return out


def _template_slide_titles_from_asset(template: Dict[str, Any], skills_root: str) -> Dict[int, str]:
    path_text = str((template or {}).get("path") or "").strip()
    if not path_text:
        return {}
    try:
        path = resolve_library_path(path_text, root=skills_root or "Skills")
        text = path.read_text(encoding="utf-8", errors="ignore")
        _metadata, _body, slides = parse_sales_agent_template(text)
    except Exception:
        return {}
    return {slide.number: slide.title for slide in slides if slide.number and slide.title}


def _slide_signature_mismatch(existing_points: List[Dict[str, Any]], template_titles: Dict[int, str]) -> bool:
    if not existing_points or not template_titles:
        return False
    existing_titles: Dict[int, str] = {}
    for point in existing_points:
        number = _slide_number_from_point(point)
        if number:
            existing_titles[number] = _slide_title_from_point(point)
    if not existing_titles:
        return False
    if set(existing_titles) != set(template_titles):
        return True
    for number, old_title in existing_titles.items():
        new_title = template_titles.get(number, "")
        if _normalize_slide_title(old_title) and _normalize_slide_title(new_title) and _normalize_slide_title(old_title) != _normalize_slide_title(new_title):
            return True
    return False


def _deprecate_current_slides_for_template_change(
    node_item,
    task_id: str,
    bundle: Dict[str, Any],
    current_template: Dict[str, Any],
    previous_report: Dict[str, Any],
    skills_root: str,
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    result = {
        "ok": True,
        "deprecated_slide_count": 0,
        "folder": PREVIOUS_SLIDE_FOLDER,
        "point_type": PREVIOUS_SLIDE_TYPE,
        "message": "",
    }
    existing_points = _existing_current_slide_points(task_id, bundle)
    if not existing_points or not current_template:
        return result

    template_changed = _template_identity_changed(previous_report, current_template) or _slide_point_template_identity_changed(
        existing_points,
        current_template,
    )
    template_titles = _template_slide_titles_from_readiness(readiness) or _template_slide_titles_from_asset(current_template, skills_root)
    signature_mismatch = _slide_signature_mismatch(existing_points, template_titles)
    if not template_changed and not signature_mismatch:
        return result

    reason_parts = []
    if template_changed:
        reason_parts.append("the approved agent template changed")
    if signature_mismatch:
        reason_parts.append("the current slide points no longer match the approved template slide structure")
    reason = "; ".join(reason_parts) + "."
    actions = [
        *_archive_replaced_slide_actions(task_id, existing_points, [], reason=reason),
        *_delete_current_slide_actions(existing_points),
    ]
    if not actions:
        return result

    _, _, data_nexus_node = _connected_task_nodes(node_item)
    if data_nexus_node is None:
        result["ok"] = False
        result["message"] = "Connect a Data Nexus node before deprecating old slide points."
        return result

    try:
        from nodes.data_nexus import spec as data_nexus_spec

        handler = getattr(data_nexus_spec, "apply_data_nexus_update_from_item", None)
        if not callable(handler):
            result["ok"] = False
            result["message"] = "Data Nexus update helper is unavailable."
            return result
        ok, message = handler(data_nexus_node, {"actions": actions}, requester="Task")
        already_current = str(message or "").startswith("No Data Nexus changes")
        result["ok"] = bool(ok or already_current)
        result["deprecated_slide_count"] = len(existing_points) if result["ok"] else 0
        result["message"] = str(message or f"Moved {len(existing_points)} old slide point(s) to {PREVIOUS_SLIDE_FOLDER}.")
    except Exception as exc:
        result["ok"] = False
        result["message"] = f"Failed to deprecate old slide points: {exc}"
    return result


def _index_deck_slides_to_data_nexus(node_item, artifact_path: str, report: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "ok": False,
        "slide_count": 0,
        "message": "No generated deck artifact is available yet. Generate a draft first.",
        "index_path": str(artifact_path or "").strip(),
    }
    path = str(artifact_path or "").strip()
    if not path:
        updated = dict(report)
        updated["slide_index"] = result
        return updated

    deck_context = sales_deck_prompt_context_from_index(path)
    if not deck_context.get("ok"):
        result["message"] = str(deck_context.get("message") or "Could not read slide data from the deck HTML.")
        updated = dict(report)
        updated["slide_index"] = result
        return updated

    task_id = str(report.get("task_id") or _task_id_for_item(node_item) or "task").strip()
    actions = _slide_actions_from_deck_context(task_id, deck_context)
    slide_point_count = sum(1 for action in actions if action.get("op") == "upsert_point")
    source_link_count = sum(
        len(action.get("source_point_ids") or [])
        for action in actions
        if action.get("op") == "upsert_point" and action.get("type") == CURRENT_SLIDE_TYPE
    )
    if not slide_point_count:
        result["message"] = "Deck HTML did not contain any slide data to index."
        updated = dict(report)
        updated["slide_index"] = result
        return updated

    _, _, data_nexus_node = _connected_task_nodes(node_item)
    if data_nexus_node is None:
        result["message"] = "Connect a Data Nexus node to the Task data_nexus input before indexing slides."
        updated = dict(report)
        updated["slide_index"] = result
        return updated

    try:
        from nodes.data_nexus import spec as data_nexus_spec

        handler = getattr(data_nexus_spec, "apply_data_nexus_update_from_item", None)
        if not callable(handler):
            result["message"] = "Data Nexus update helper is unavailable."
        else:
            existing_bundle, _bundle_error = _data_nexus_bundle_from_node(data_nexus_node)
            existing_current_slides = _existing_current_slide_points(task_id, existing_bundle)
            archive_actions = _archive_replaced_slide_actions(
                task_id,
                existing_current_slides,
                actions,
                reason="a newer generated deck indexed replacement slides for this Task.",
            )
            current_slide_ids = {
                str(action.get("id") or "").strip()
                for action in actions
                if action.get("op") == "upsert_point" and str(action.get("type") or "").strip().lower() == CURRENT_SLIDE_TYPE
            }
            delete_stale_slide_actions = _delete_current_slide_actions(
                [
                    point
                    for point in existing_current_slides
                    if str(point.get("id") or "").strip() not in current_slide_ids
                ]
            )
            ok, message = handler(data_nexus_node, {"actions": [*archive_actions, *delete_stale_slide_actions, *actions]}, requester="Task")
            already_current = str(message or "").startswith("No Data Nexus changes")
            result = {
                "ok": bool(ok or already_current),
                "slide_count": slide_point_count,
                "old_slide_count": sum(1 for action in archive_actions if action.get("op") == "upsert_point"),
                "message": str(message or "Indexed generated deck slides into Data Nexus."),
                "index_path": path,
                "folder": CURRENT_SLIDE_FOLDER,
                "point_type": CURRENT_SLIDE_TYPE,
                "sequence_link_count": max(0, slide_point_count - 1),
                "source_link_count": source_link_count,
            }
    except Exception as exc:
        result["message"] = f"Failed to index deck slides into Data Nexus: {exc}"

    updated = dict(report)
    updated["slide_index"] = result
    return updated


def index_task_slides_to_data_nexus_from_item(node_item) -> Dict[str, Any]:
    report = _report_from_model(node_item)
    if not report:
        report = {
            "task_id": _task_id_for_item(node_item),
            "status": _param_value_from_model(getattr(node_item, "model", None), TASK_STATUS_PARAM, "created"),
        }
    artifact_path = _deck_artifact_path_for_report(node_item, report)
    report = _index_deck_slides_to_data_nexus(node_item, artifact_path, report)
    _persist_task_report(node_item, report)
    return report


def _persist_task_report(node_item, report: Dict[str, Any], *, notify_scene: bool = True) -> None:
    status = str(report.get("status") or "created").strip() or "created"
    questions = report.get("questions") or []
    if not isinstance(questions, list):
        questions = []
    refinement_questions = report.get("refinement_questions") or []
    if not isinstance(refinement_questions, list):
        refinement_questions = []
    output_questions = questions if questions else refinement_questions
    guide = report.get("guide") if isinstance(report.get("guide"), dict) else {}
    template = report.get("agent_template") if isinstance(report.get("agent_template"), dict) else {}
    artifact_path = _artifact_path_from_report(report)
    if not artifact_path:
        artifact_path = _param_value_from_model(getattr(node_item, "model", None), TASK_LAST_ARTIFACT_PARAM, "")

    _set_param_on_item(node_item, TASK_STATUS_PARAM, status, notify_scene=False)
    _set_param_on_item(node_item, TASK_GUIDE_PATH_PARAM, str((guide or {}).get("path") or ""), notify_scene=False)
    _set_param_on_item(node_item, TASK_AGENT_TEMPLATE_PATH_PARAM, str((template or {}).get("path") or ""), notify_scene=False)
    _set_param_on_item(node_item, TASK_LAST_QUESTIONS_PARAM, json.dumps(questions, ensure_ascii=False), notify_scene=False)
    _set_param_on_item(node_item, TASK_LAST_REPORT_PARAM, json.dumps(report, ensure_ascii=False), notify_scene=False)
    _set_param_on_item(node_item, TASK_LAST_ARTIFACT_PARAM, artifact_path, notify_scene=False)
    _set_param_on_item(node_item, STATUS_OUTPUT_PORT, status, notify_scene=False)
    _set_param_on_item(
        node_item,
        QUESTIONS_OUTPUT_PORT,
        "\n\n".join(str(question.get("text", "") or "") for question in output_questions if isinstance(question, dict)),
        notify_scene=False,
    )
    _set_param_on_item(node_item, ARTIFACT_OUTPUT_PORT, artifact_path, notify_scene=notify_scene)


def _is_html_preview_item(item) -> bool:
    model = getattr(item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower() in {
        "html_preview",
        "html preview",
        "htmlpreview",
    }


def _set_path_on_item(item, path: str) -> None:
    setter = getattr(item, "_set_param_value", None)
    if callable(setter):
        try:
            setter("path", path, rebuild=False, notify_scene=False)
            return
        except Exception:
            pass
    model = getattr(item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    for param in params:
        if isinstance(param, dict) and str(param.get("name", "") or "").strip().lower() == "path":
            param["value"] = path
            break
    else:
        params.append({"name": "path", "value": path})
    try:
        model.params = params
    except Exception:
        pass


def _refresh_connected_html_previews(node_item, artifact_path: str) -> None:
    path = str(artifact_path or "").strip()
    if not path:
        return
    scene = _scene_for_item(node_item)
    if scene is None:
        return
    accepted_src_ports = {"", ARTIFACT_OUTPUT_PORT, "path", "html", "output"}
    accepted_dst_ports = {"", "path", "artifact", "html", "input"}
    try:
        edges = list(getattr(scene, "_edges", []) or [])
    except Exception:
        edges = []
    for edge in edges:
        if getattr(edge, "src", None) is not node_item:
            continue
        src_port = str(getattr(edge, "src_port_name", "") or "").strip().lower()
        if src_port not in accepted_src_ports:
            continue
        dst = getattr(edge, "dst", None)
        if not _is_html_preview_item(dst):
            continue
        dst_port = (
            getattr(edge, "dst_port_name", None)
            or getattr(edge, "dst_label", None)
            or getattr(edge, "dst_name", None)
            or ""
        )
        if str(dst_port or "").strip().lower() not in accepted_dst_ports:
            continue
        _set_path_on_item(dst, path)
        try:
            scene.refresh_node_widget(getattr(getattr(dst, "model", None), "name", "") or "")
        except Exception:
            try:
                dst._recompute_height()
                dst._build_widgets()
            except Exception:
                pass


def _report_from_model(node_item) -> Dict[str, Any]:
    raw = _param_value_from_model(getattr(node_item, "model", None), TASK_LAST_REPORT_PARAM, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _trim_prompt_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _coerce_string_list(value: Any, *, limit: int = 20) -> List[str]:
    raw_values = value if isinstance(value, list) else [value]
    out: List[str] = []
    for raw in raw_values:
        if raw is None:
            continue
        if isinstance(raw, str):
            pieces = re.split(r"[,;\n]+", raw)
        else:
            pieces = [str(raw)]
        for piece in pieces:
            text = str(piece or "").strip().strip("\"'")
            if text and text not in out:
                out.append(text[:180])
            if len(out) >= limit:
                return out
    return out


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        match = re.search(r"\b(\d{1,2})\b", str(value or ""))
        return int(match.group(1)) if match else 0


def _coerce_sales_agent_question(raw: Any, *, default_kind: str = "") -> Dict[str, Any] | None:
    if isinstance(raw, str):
        data: Dict[str, Any] = {"text": raw}
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        return None

    text = str(data.get("text") or data.get("question") or data.get("prompt") or "").strip()
    if len(text) < 8:
        return None

    question_kind = str(
        data.get("question_kind")
        or data.get("kind")
        or data.get("status")
        or default_kind
        or "weak"
    ).strip().lower()
    if question_kind in {"blocking", "missing_answer", "needs_answer"}:
        question_kind = "missing"
    if question_kind not in {"missing", "weak", "refinement"}:
        question_kind = "refinement" if default_kind == "refinement" else "weak"

    slide_number = _int_or_zero(data.get("slide_number") or data.get("slide") or data.get("slide_index"))
    if not slide_number:
        match = re.search(r"\bslide\s+(\d{1,2})\b", text, re.IGNORECASE)
        slide_number = int(match.group(1)) if match else 0

    accepted_type = str(
        data.get("accepted_point_type")
        or data.get("expected_answer_point_type")
        or data.get("answer_point_type")
        or data.get("point_answer_type")
        or ""
    ).strip()
    slide_title = str(data.get("slide_title") or data.get("title") or "").strip()
    question_id = str(data.get("question_id") or data.get("id") or "").strip()
    if not question_id:
        kind_token = "refine" if question_kind == "refinement" else "prep"
        type_token = _slug(accepted_type or slide_title or "answer", fallback="answer")
        question_id = f"slide_{slide_number:02d}_{kind_token}_{type_token}" if slide_number else f"{kind_token}_{type_token}"

    out: Dict[str, Any] = {
        "question_id": _slug(question_id, fallback="prep_question"),
        "point_type": "prep_question",
        "question_kind": question_kind,
        "slide_number": slide_number,
        "slide_title": slide_title,
        "accepted_point_type": accepted_type,
        "matched_point_ids": _coerce_string_list(
            data.get("matched_point_ids")
            if "matched_point_ids" in data
            else data.get("source_point_ids")
            if "source_point_ids" in data
            else data.get("source_ids")
        ),
        "text": text,
    }
    criteria = _coerce_string_list(
        data.get("evaluation_criteria")
        if "evaluation_criteria" in data
        else data.get("criteria")
        if "criteria" in data
        else data.get("checks")
    )
    if criteria:
        out["evaluation_criteria"] = criteria
    return out


def _coerce_sales_agent_question_list(value: Any, *, default_kind: str = "") -> List[Dict[str, Any]]:
    raw_items = value if isinstance(value, list) else [value] if value else []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        question = _coerce_sales_agent_question(raw, default_kind=default_kind)
        if question is None:
            continue
        key = str(question.get("question_id") or question.get("text") or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(question)
    return out


def _coerce_sales_agent_resolved_question(raw: Any) -> Dict[str, Any] | None:
    if isinstance(raw, str):
        question_id = raw.strip()
        answer_ids: List[str] = []
    elif isinstance(raw, dict):
        question_id = str(
            raw.get("question_id")
            or raw.get("id")
            or raw.get("question")
            or raw.get("prep_question")
            or ""
        ).strip()
        answer_ids = _coerce_string_list(
            raw.get("answer_point_ids")
            if "answer_point_ids" in raw
            else raw.get("answer_ids")
            if "answer_ids" in raw
            else raw.get("source_point_ids")
            if "source_point_ids" in raw
            else raw.get("matched_point_ids")
            if "matched_point_ids" in raw
            else raw.get("answer_point_id")
            if "answer_point_id" in raw
            else raw.get("answer_id")
            if "answer_id" in raw
            else raw.get("source_point_id")
            if "source_point_id" in raw
            else raw.get("source")
        )
        answer_type = str(
            raw.get("answer_point_type")
            or raw.get("accepted_point_type")
            or raw.get("expected_answer_point_type")
            or raw.get("type")
            or ""
        ).strip()
    else:
        return None
    if not isinstance(raw, dict):
        answer_type = ""
    question_id = _slug(question_id, fallback="")
    answer_ids = [_slug(item, fallback="") for item in answer_ids if str(item or "").strip()]
    answer_ids = [item for item in answer_ids if item]
    if not question_id or not answer_ids:
        return None
    out = {"question_id": question_id, "answer_point_ids": answer_ids}
    if answer_type:
        out["answer_point_type"] = _slug(answer_type, fallback="")
    return out


def _coerce_sales_agent_resolved_question_list(value: Any) -> List[Dict[str, Any]]:
    raw_items = value if isinstance(value, list) else [value] if value else []
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items:
        resolved = _coerce_sales_agent_resolved_question(raw)
        if resolved is None:
            continue
        clean_answer_ids: List[str] = []
        question_id = str(resolved.get("question_id") or "").strip()
        for answer_id in resolved.get("answer_point_ids") or []:
            key = (question_id, str(answer_id or "").strip())
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            clean_answer_ids.append(key[1])
        if clean_answer_ids:
            item = {"question_id": question_id, "answer_point_ids": clean_answer_ids}
            answer_type = str(resolved.get("answer_point_type") or "").strip()
            if answer_type:
                item["answer_point_type"] = answer_type
            out.append(item)
    return out


def _normalize_sales_agent_slide_status(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ok": "ready",
        "good": "ready",
        "complete": "ready",
        "ready_to_generate": "ready",
        "ready_for_review": "ready",
        "needs_answer": "weak",
        "needs_answers": "weak",
        "needs_review": "weak",
        "needs_work": "weak",
        "incomplete": "weak",
        "thin": "weak",
        "generic": "weak",
        "not_ready": "weak",
        "missing_answer": "missing",
        "no_source": "missing",
        "no_sources": "missing",
        "absent": "missing",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in {"ready", "weak", "missing"} else ""


def _coerce_sales_agent_notes(value: Any, *, limit: int = 5) -> List[str]:
    raw_values = value if isinstance(value, list) else [value]
    notes: List[str] = []
    for raw in raw_values:
        if raw is None:
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        if text and text not in notes:
            notes.append(text[:260])
        if len(notes) >= limit:
            break
    return notes


def _coerce_sales_agent_slide_review(raw: Any) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    slide_number = _int_or_zero(raw.get("slide_number") or raw.get("number") or raw.get("slide") or raw.get("slide_index"))
    if not slide_number:
        return None
    status = _normalize_sales_agent_slide_status(raw.get("status") or raw.get("readiness") or raw.get("verdict"))
    if not status:
        return None

    notes: List[str] = []
    for key in ("notes", "note", "reason", "reasoning", "message", "gaps", "missing_fields", "quality_notes"):
        notes.extend(_coerce_sales_agent_notes(raw.get(key), limit=5))
        if len(notes) >= 5:
            notes = notes[:5]
            break

    out: Dict[str, Any] = {
        "slide_number": slide_number,
        "status": status,
    }
    title = str(raw.get("slide_title") or raw.get("title") or "").strip()
    if title:
        out["slide_title"] = title
    if notes:
        out["notes"] = notes

    source_keys = ("matched_point_ids", "source_point_ids", "source_ids", "kept_point_ids")
    for key in source_keys:
        if key in raw:
            out["matched_point_ids"] = _coerce_string_list(raw.get(key), limit=12)
            break
    return out


def _coerce_sales_agent_slide_review_list(value: Any) -> List[Dict[str, Any]]:
    raw_items = value if isinstance(value, list) else [value] if value else []
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for raw in raw_items:
        review = _coerce_sales_agent_slide_review(raw)
        if review is None:
            continue
        slide_number = int(review.get("slide_number") or 0)
        if slide_number in seen:
            continue
        seen.add(slide_number)
        out.append(review)
    return out


def _merge_sales_agent_notes(existing: Any, additions: List[str]) -> List[str]:
    notes = _coerce_sales_agent_notes(existing, limit=12)
    for note in additions:
        clean = str(note or "").strip()
        if not clean:
            continue
        if not clean.lower().startswith("sales agent:"):
            clean = f"Sales Agent: {clean}"
        if clean not in notes:
            notes.append(clean[:280])
    return notes[:12]


def _apply_sales_agent_slide_reviews(readiness: Dict[str, Any], reviews: List[Dict[str, Any]]) -> tuple[Dict[str, Any], int]:
    if not isinstance(readiness, dict) or not reviews:
        return readiness, 0
    slides = [dict(slide) for slide in (readiness.get("slides") or []) if isinstance(slide, dict)]
    if not slides:
        return readiness, 0
    reviews_by_number = {int(review.get("slide_number") or 0): review for review in reviews}
    changed = 0
    updated_slides: List[Dict[str, Any]] = []
    for slide in slides:
        number = int(slide.get("number") or 0)
        review = reviews_by_number.get(number)
        if review:
            previous_status = str(slide.get("status") or "").strip().lower()
            status = str(review.get("status") or "").strip().lower()
            if status:
                slide["status"] = status
                slide["ai_status"] = status
            if "matched_point_ids" in review:
                slide["matched_point_ids"] = list(review.get("matched_point_ids") or [])
            notes = list(review.get("notes") or [])
            if notes:
                slide["notes"] = _merge_sales_agent_notes(slide.get("notes") or [], notes)
            elif status and status != previous_status:
                slide["notes"] = _merge_sales_agent_notes(slide.get("notes") or [], [f"Marked {status} after semantic review."])
            changed += 1
        updated_slides.append(slide)

    ready_count = sum(1 for slide in updated_slides if str(slide.get("status") or "").strip().lower() == "ready")
    weak_count = sum(1 for slide in updated_slides if str(slide.get("status") or "").strip().lower() == "weak")
    missing_count = sum(1 for slide in updated_slides if str(slide.get("status") or "").strip().lower() == "missing")
    updated = dict(readiness)
    updated["slides"] = updated_slides
    updated["ready_slide_count"] = ready_count
    updated["weak_slide_count"] = weak_count
    updated["missing_slide_count"] = missing_count
    updated["status"] = "ready_to_generate" if weak_count == 0 and missing_count == 0 else "needs_answers"
    return updated, changed


def _sales_agent_payload_from_response(text: str, *, mode: str = "") -> tuple[Dict[str, Any], str]:
    clean = str(text or "").strip()
    regexes = [SALES_AGENT_QUESTIONS_RE, SALES_AGENT_REVIEW_RE, SALES_AGENT_DRAFT_RE]
    if mode == "review":
        regexes = [SALES_AGENT_REVIEW_RE, SALES_AGENT_QUESTIONS_RE]
    elif mode == "draft":
        regexes = [SALES_AGENT_DRAFT_RE, SALES_AGENT_REVIEW_RE, SALES_AGENT_QUESTIONS_RE]
    elif mode == "write_questions":
        regexes = [SALES_AGENT_QUESTIONS_RE, SALES_AGENT_REVIEW_RE]
    for regex in regexes:
        match = regex.search(clean)
        if not match:
            continue
        payload = str(match.groupdict().get("payload", "") or "").strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except Exception as exc:
            return {}, f"Sales Agent returned invalid JSON: {exc}"
        if isinstance(parsed, dict):
            return parsed, ""
        return {}, "Sales Agent payload was not a JSON object."
    if clean.startswith("{") and clean.endswith("}"):
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                return parsed, ""
        except Exception as exc:
            return {}, f"Sales Agent returned invalid JSON: {exc}"
    return {}, "Sales Agent response did not include a sales_agent_review or sales_agent_questions payload."


def _apply_sales_agent_ai_output(report: Dict[str, Any], response_text: str, *, mode: str) -> Dict[str, Any]:
    updated = dict(report)
    payload, error = _sales_agent_payload_from_response(response_text, mode=mode)
    if error:
        updated["sales_agent_ai"] = {
            "ok": False,
            "mode": mode,
            "message": error,
        }
        return updated

    questions = _coerce_sales_agent_question_list(
        payload.get("questions")
        or payload.get("blocking_questions")
        or payload.get("missing_questions"),
        default_kind="missing",
    )
    refinement_questions = _coerce_sales_agent_question_list(
        payload.get("refinement_questions")
        or payload.get("followup_questions")
        or payload.get("improvement_questions"),
        default_kind="refinement",
    )
    slide_reviews = _coerce_sales_agent_slide_review_list(
        payload.get("slide_reviews")
        or payload.get("readiness_reviews")
        or payload.get("slide_readiness")
        or payload.get("slides")
    )
    resolved_questions = _coerce_sales_agent_resolved_question_list(
        payload.get("resolved_questions")
        or payload.get("answered_questions")
        or payload.get("question_answers")
        or payload.get("resolved_prep_questions")
    )

    if "questions" in payload or "blocking_questions" in payload or "missing_questions" in payload:
        updated["questions"] = questions
    if "refinement_questions" in payload or "followup_questions" in payload or "improvement_questions" in payload:
        updated["refinement_questions"] = refinement_questions
    if (
        "resolved_questions" in payload
        or "answered_questions" in payload
        or "question_answers" in payload
        or "resolved_prep_questions" in payload
    ):
        updated["resolved_questions"] = resolved_questions
    slide_review_count = 0
    if slide_reviews and isinstance(updated.get("readiness"), dict):
        readiness, slide_review_count = _apply_sales_agent_slide_reviews(dict(updated.get("readiness") or {}), slide_reviews)
        updated["readiness"] = readiness

    current_status = str(updated.get("status") or "").strip().lower()
    payload_status = str(payload.get("status") or "").strip().lower()
    ai_readiness_status = ""
    if isinstance(updated.get("readiness"), dict):
        ai_readiness_status = str((updated.get("readiness") or {}).get("status") or "").strip().lower()
    allowed_status = {"needs_setup", "needs_answers", "ready_to_generate", "draft_ready", "failed"}
    if questions:
        updated["status"] = "needs_answers"
    elif payload_status == "needs_answers":
        updated["status"] = "needs_answers"
    elif slide_review_count and ai_readiness_status in allowed_status:
        updated["status"] = ai_readiness_status
    elif payload_status in allowed_status:
        updated["status"] = payload_status
    elif current_status:
        updated["status"] = current_status
    final_status = str(updated.get("status") or "").strip().lower()
    if isinstance(updated.get("readiness"), dict) and final_status in {"needs_answers", "ready_to_generate"}:
        readiness = dict(updated.get("readiness") or {})
        readiness["status"] = final_status
        updated["readiness"] = readiness
    if isinstance(updated.get("checks"), dict) and final_status in {"needs_answers", "ready_to_generate"}:
        checks = dict(updated.get("checks") or {})
        checks["readiness_ready"] = final_status == "ready_to_generate"
        updated["checks"] = checks

    next_question_id = str(payload.get("next_question_id") or "").strip()
    next_question_text = str(payload.get("next_question_text") or "").strip()
    if not next_question_text:
        next_question = (questions or refinement_questions or [{}])[0]
        if isinstance(next_question, dict):
            next_question_id = next_question_id or str(next_question.get("question_id") or "").strip()
            next_question_text = str(next_question.get("text") or "").strip()

    updated["sales_agent_ai"] = {
        "ok": True,
        "mode": mode,
        "message": str(payload.get("message") or payload.get("summary") or "Sales Agent AI review completed.").strip(),
        "question_count": len(questions),
        "refinement_question_count": len(refinement_questions),
        "slide_review_count": slide_review_count,
        "resolved_question_count": len(resolved_questions),
        "next_question_id": next_question_id,
        "next_question_text": next_question_text,
    }
    return updated


def _question_point_id(task_id: str, question: Dict[str, Any]) -> str:
    base = str(question.get("question_id") or "").strip()
    if not base:
        slide_number = int(question.get("slide_number", 0) or 0)
        point_type = str(question.get("accepted_point_type") or question.get("point_type") or "prep").strip()
        base = f"slide_{slide_number:02d}_{point_type}"
    clean_task_id = _slug(task_id, fallback="task")
    clean_base = _slug(base, fallback="prep_question")
    if clean_base.startswith(f"{clean_task_id}_"):
        return clean_base
    return _slug(f"{task_id}_{base}", fallback=f"{task_id}_prep_question")


def _question_label(question: Dict[str, Any]) -> str:
    slide_number = int(question.get("slide_number", 0) or 0)
    slide_title = str(question.get("slide_title") or "").strip()
    suffix = "Refinement Question" if str(question.get("question_kind") or "").strip().lower() == "refinement" else "Prep Question"
    if slide_number and slide_title:
        return f"Slide {slide_number:02d} {slide_title} {suffix}"
    if slide_number:
        return f"Slide {slide_number:02d} {suffix}"
    return f"Sales {suffix}"


def _question_matched_point_ids(question: Dict[str, Any], slide: Dict[str, Any] | None) -> List[str]:
    if "matched_point_ids" in question:
        raw = question.get("matched_point_ids")
    elif "source_point_ids" in question:
        raw = question.get("source_point_ids")
    elif "source_ids" in question:
        raw = question.get("source_ids")
    else:
        raw = (slide or {}).get("matched_point_ids") or []
    return _coerce_string_list(raw)


def _question_note(task_id: str, question: Dict[str, Any], slide: Dict[str, Any] | None) -> str:
    slide_number = int(question.get("slide_number", 0) or 0)
    slide_title = str(question.get("slide_title") or (slide or {}).get("title") or "").strip()
    accepted_type = str(question.get("accepted_point_type") or (slide or {}).get("target_point_type") or "").strip()
    status = str((slide or {}).get("status") or "needs_answer").strip()
    lines = [
        "Question:",
        str(question.get("text") or "").strip(),
        "",
        f"Task: {task_id}",
    ]
    if slide_number or slide_title:
        lines.append(f"Slide: {slide_number:02d} - {slide_title}".strip())
    if accepted_type:
        lines.append(f"Expected answer point type: {accepted_type}")
    question_kind = str(question.get("question_kind") or "blocking").strip()
    if question_kind:
        lines.append(f"Question kind: {question_kind}")
    if status:
        lines.append(f"Readiness status: {status}")
    matched_ids = _question_matched_point_ids(question, slide)
    if matched_ids:
        lines.append("Matched source points: " + ", ".join(str(item) for item in matched_ids if str(item or "").strip()))
    notes = list((slide or {}).get("notes") or [])
    if notes:
        lines.extend(["", "Why this is needed:"])
        lines.extend(f"- {note}" for note in notes)
    criteria = _coerce_string_list(question.get("evaluation_criteria"))
    if criteria:
        lines.extend(["", "Evaluation criteria:"])
        lines.extend(f"- {item}" for item in criteria)
    lines.extend(
        [
            "",
            "Answering workflow:",
            f"- Add or update a Data Nexus answer point with type `{accepted_type or 'concept'}`.",
            "- Include the concrete claim, evidence, and source context the slide should use.",
            "- Link the answer back to this prep question when the answer is ready.",
        ]
    )
    return "\n".join(line for line in lines if line is not None).strip()


def _question_actions_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    task_id = str(report.get("task_id") or "").strip() or "task"
    blocking_questions = [question for question in (report.get("questions") or []) if isinstance(question, dict)]
    refinement_questions = [question for question in (report.get("refinement_questions") or []) if isinstance(question, dict)]
    questions = blocking_questions if blocking_questions else refinement_questions
    readiness = report.get("readiness") if isinstance(report.get("readiness"), dict) else {}
    slides = [slide for slide in (readiness.get("slides") or []) if isinstance(slide, dict)]
    slides_by_number = {int(slide.get("number", 0) or 0): slide for slide in slides}
    actions: List[Dict[str, Any]] = []
    for question in questions:
        if str(question.get("point_type") or "").strip().lower() != "prep_question":
            continue
        point_id = _question_point_id(task_id, question)
        slide = slides_by_number.get(int(question.get("slide_number", 0) or 0))
        accepted_type = str(question.get("accepted_point_type") or (slide or {}).get("target_point_type") or "").strip()
        label = _question_label(question)
        question_kind = str(question.get("question_kind") or "").strip().lower()
        summary = f"{'Refinement' if question_kind == 'refinement' else 'Prep'} question for {label.replace(' Prep Question', '').replace(' Refinement Question', '')}"
        if accepted_type:
            summary += f" requiring `{accepted_type}`."
        else:
            summary += "."
        folder = "refinement_questions" if question_kind == "refinement" else "prep_questions"
        link_label = "refines_answer" if question_kind == "refinement" else "needs_stronger_answer"
        matched_ids = _question_matched_point_ids(question, slide)
        source_point_ids = [str(matched_id or "").strip() for matched_id in matched_ids if str(matched_id or "").strip()]
        action = {
            "op": "upsert_point",
            "id": point_id,
            "label": label,
            "type": "prep_question",
            "folder": folder,
            "summary": summary,
            "note": _question_note(task_id, question, slide),
            "note_mode": "replace",
            "source_point_ids": source_point_ids,
            "source_edge_label": link_label,
            "replace_source_edges": True,
            "create_source_edges": False,
        }
        actions.append(action)
    return actions


def _resolved_question_actions_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for resolved in report.get("resolved_questions", []) or []:
        if not isinstance(resolved, dict):
            continue
        question_id = str(resolved.get("question_id") or "").strip()
        if not question_id:
            continue
        answer_type = str(resolved.get("answer_point_type") or "").strip()
        for answer_id in _coerce_string_list(resolved.get("answer_point_ids") or [], limit=80):
            clean_answer_id = str(answer_id or "").strip()
            if not clean_answer_id:
                continue
            action = {
                "op": "answer_question",
                "question": question_id,
                "answer_point_id": clean_answer_id,
            }
            if answer_type:
                action["type"] = answer_type
            actions.append(action)
    return actions


def _apply_resolved_question_actions_to_data_nexus(node_item, report: Dict[str, Any]) -> Dict[str, Any]:
    actions = _resolved_question_actions_from_report(report if isinstance(report, dict) else {})
    if not actions:
        return report
    updated = dict(report)
    write_result = {
        "ok": False,
        "message": "No resolved prep-question links were written.",
        "resolved_question_count": 0,
        "action_count": len(actions),
    }
    _scene, _skills_node, data_nexus_node = _connected_task_nodes(node_item)
    if data_nexus_node is None:
        write_result["message"] = "Connect a Data Nexus node to the Task data_nexus input."
        updated["data_nexus_answer_links"] = write_result
        return updated
    try:
        from nodes.data_nexus import spec as data_nexus_spec

        handler = getattr(data_nexus_spec, "apply_data_nexus_update_from_item", None)
        if not callable(handler):
            write_result["message"] = "Data Nexus update helper is unavailable."
        else:
            ok, message = handler(data_nexus_node, {"actions": actions}, requester="Task")
            already_current = str(message or "").startswith("No Data Nexus changes")
            write_result = {
                "ok": bool(ok or already_current),
                "message": str(message or ("Resolved prep-question links are already current." if already_current else "")),
                "resolved_question_count": len({str(action.get("question") or "") for action in actions}),
                "action_count": len(actions),
            }
    except Exception as exc:
        write_result["message"] = f"Failed to link resolved prep questions in Data Nexus: {exc}"
    updated["data_nexus_answer_links"] = write_result
    fingerprint = _current_data_nexus_fingerprint(node_item)
    if fingerprint:
        data_nexus = dict(updated.get("data_nexus") or {})
        data_nexus["fingerprint"] = fingerprint
        updated["data_nexus"] = data_nexus
    return updated


def _report_has_writable_questions(report: Dict[str, Any]) -> bool:
    return bool(_question_actions_from_report(report if isinstance(report, dict) else {}))


def _question_text_from_nexus_point(point: Dict[str, Any]) -> str:
    for key in ("question_text", "text", "prompt"):
        text = str(point.get(key) or "").strip()
        if text:
            return text
    raw = str(point.get("content") or point.get("note") or point.get("summary") or "").strip()
    match = re.search(r"(?is)\bQuestion:\s*(?P<text>.+?)(?:\n\s*\n|\nTask:|\Z)", raw)
    if match:
        text = re.sub(r"\s+", " ", match.group("text")).strip()
        if text:
            return text
    return raw


def _field_from_question_note(note: str, label: str) -> str:
    pattern = rf"(?im)^\s*{re.escape(label)}\s*:\s*(?P<value>.+?)\s*$"
    match = re.search(pattern, str(note or ""))
    return str(match.group("value") or "").strip() if match else ""


def _slide_from_question_point(point: Dict[str, Any]) -> tuple[int, str]:
    raw = " ".join(
        str(point.get(key) or "")
        for key in ("id", "title", "label", "content", "note", "summary")
    )
    slide_number = 0
    slide_title = ""
    match = re.search(r"\bslide[_\s-]*(?P<number>\d{1,2})\b", raw, re.IGNORECASE)
    if match:
        slide_number = int(match.group("number") or 0)
    note = str(point.get("content") or point.get("note") or "")
    slide_line = _field_from_question_note(note, "Slide")
    if slide_line:
        match = re.search(r"\b(?P<number>\d{1,2})\b\s*-?\s*(?P<title>.*)$", slide_line)
        if match:
            slide_number = slide_number or int(match.group("number") or 0)
            slide_title = str(match.group("title") or "").strip(" -")
    if not slide_title:
        title = str(point.get("title") or point.get("label") or "").strip()
        title = re.sub(r"\b(?:Prep|Refinement)\s+Question\b", "", title, flags=re.IGNORECASE).strip(" -")
        title = re.sub(r"^Slide\s+\d{1,2}\s*", "", title, flags=re.IGNORECASE).strip(" -")
        slide_title = title
    return slide_number, slide_title


def _open_task_questions_from_bundle(task_id: str, bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    clean_task_id = _slug(task_id, fallback="task")
    questions: List[Dict[str, Any]] = []
    for point in (bundle or {}).get("points", []) or []:
        if not isinstance(point, dict):
            continue
        point_type = str(point.get("type") or "").strip().lower()
        if point_type != "prep_question" and not bool(point.get("is_question")):
            continue
        point_id = str(point.get("id") or "").strip()
        if clean_task_id and point_id and not _slug(point_id, fallback="point").startswith(f"{clean_task_id}_"):
            continue
        answer_status = str(point.get("answer_status") or "").strip().lower()
        if answer_status and answer_status not in {"open", "unanswered", "pending", "needs_answer", "needs_answers"}:
            continue
        question_text = _question_text_from_nexus_point(point)
        if len(question_text) < 8:
            continue
        note = str(point.get("content") or point.get("note") or "")
        slide_number, slide_title = _slide_from_question_point(point)
        folder = str(point.get("folder") or "").strip().lower()
        question_kind = _field_from_question_note(note, "Question kind").strip().lower()
        if not question_kind:
            question_kind = "refinement" if "refine" in point_id.lower() or "refinement" in folder else "weak"
        if question_kind not in {"missing", "weak", "refinement"}:
            question_kind = "refinement" if question_kind == "refine" else "weak"
        accepted_type = (
            str(point.get("expected_answer_point_type") or point.get("accepted_point_type") or "").strip()
            or _field_from_question_note(note, "Expected answer point type")
        )
        questions.append(
            {
                "question_id": point_id or f"slide_{slide_number:02d}_{question_kind}",
                "point_type": "prep_question",
                "question_kind": question_kind,
                "slide_number": slide_number,
                "slide_title": slide_title,
                "accepted_point_type": accepted_type,
                "matched_point_ids": _coerce_string_list(point.get("source_point_ids") or []),
                "text": question_text,
            }
        )
    questions.sort(key=lambda item: (int(item.get("slide_number") or 999), str(item.get("question_id") or "")))
    return questions


def _apply_open_data_nexus_questions(node_item, report: Dict[str, Any], *, generation_message: str = "") -> tuple[Dict[str, Any], bool]:
    _, _, data_nexus_node = _connected_task_nodes(node_item)
    bundle, _bundle_error = _data_nexus_bundle_from_node(data_nexus_node)
    open_questions = _open_task_questions_from_bundle(str(report.get("task_id") or _task_id_for_item(node_item)), bundle)
    if not open_questions:
        return report, False
    blocking = [question for question in open_questions if str(question.get("question_kind") or "").strip().lower() != "refinement"]
    refinements = [question for question in open_questions if str(question.get("question_kind") or "").strip().lower() == "refinement"]
    active_id = str((bundle or {}).get("active_question_id") or "").strip()
    next_question = next(
        (question for question in open_questions if active_id and str(question.get("question_id") or "") == active_id),
        open_questions[0],
    )
    updated = dict(report)
    updated["status"] = "needs_answers"
    updated["questions"] = blocking or []
    updated["refinement_questions"] = refinements
    checks = dict(updated.get("checks") or {})
    checks["readiness_ready"] = False
    updated["checks"] = checks
    if isinstance(updated.get("readiness"), dict):
        readiness = dict(updated.get("readiness") or {})
        readiness["status"] = "needs_answers"
        updated["readiness"] = readiness
    message = "Open Data Nexus prep questions already exist; answer them before generating new questions."
    updated["sales_agent_ai"] = {
        "ok": True,
        "pending": False,
        "mode": "open_questions",
        "message": message,
        "open_question_count": len(open_questions),
        "next_question_id": str(next_question.get("question_id") or ""),
        "next_question_text": str(next_question.get("text") or ""),
    }
    if generation_message:
        updated["generation"] = {
            "ok": False,
            "message": generation_message,
        }
        updated["artifact"] = updated["generation"]
    return updated, True


def _asset_prompt_excerpt(asset: Dict[str, Any], *, root: str, limit: int = 5000) -> str:
    path_text = str((asset or {}).get("path") or "").strip()
    if not path_text:
        return ""
    candidates: List[Path] = []
    try:
        raw_path = Path(path_text)
        candidates.append(raw_path)
        if not raw_path.is_absolute():
            if root:
                candidates.append(Path(root) / raw_path)
            candidates.append(Path.cwd() / raw_path)
    except Exception:
        return ""
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.exists() and candidate.is_file():
                return _trim_prompt_text(candidate.read_text(encoding="utf-8", errors="ignore"), limit)
        except Exception:
            continue
    return ""


def _sales_agent_report_for_prompt(report: Dict[str, Any]) -> Dict[str, Any]:
    readiness = report.get("readiness") if isinstance(report.get("readiness"), dict) else {}
    slides = []
    for slide in readiness.get("slides", []) or []:
        if not isinstance(slide, dict):
            continue
        slides.append(
            {
                "number": slide.get("number"),
                "title": slide.get("title"),
                "status": slide.get("status"),
                "accepted_point_types": slide.get("accepted_point_types") or [],
                "target_point_type": slide.get("target_point_type"),
                "matched_point_ids": slide.get("matched_point_ids") or [],
                "has_exact_type_match": bool(slide.get("has_exact_type_match")),
                "quality_schema": slide.get("quality_schema") or {},
                "notes": slide.get("notes") or [],
            }
        )
    questions = []
    for key in ("questions", "refinement_questions"):
        for question in report.get(key, []) or []:
            if not isinstance(question, dict):
                continue
            questions.append(
                {
                    "question_id": question.get("question_id"),
                    "question_kind": question.get("question_kind") or ("refinement" if key == "refinement_questions" else "missing"),
                    "slide_number": question.get("slide_number"),
                    "slide_title": question.get("slide_title"),
                    "accepted_point_type": question.get("accepted_point_type"),
                    "matched_point_ids": question.get("matched_point_ids") or [],
                    "text": question.get("text"),
                }
            )
    return {
        "task_id": report.get("task_id"),
        "status": report.get("status"),
        "guide": {
            "name": (report.get("guide") or {}).get("name"),
            "path": (report.get("guide") or {}).get("path"),
            "version": (report.get("guide") or {}).get("guide_version_id"),
        },
        "template": {
            "name": (report.get("agent_template") or {}).get("name"),
            "path": (report.get("agent_template") or {}).get("path"),
            "version": (report.get("agent_template") or {}).get("template_version_id"),
        },
        "data_nexus": report.get("data_nexus") or {},
        "readiness": {
            "status": readiness.get("status"),
            "ready_slide_count": readiness.get("ready_slide_count"),
            "weak_slide_count": readiness.get("weak_slide_count"),
            "missing_slide_count": readiness.get("missing_slide_count"),
            "slides": slides,
        },
        "current_questions": questions,
    }


def _sales_agent_bundle_for_prompt(bundle: Dict[str, Any]) -> Dict[str, Any]:
    points = []
    for point in (bundle or {}).get("points", []) or []:
        if not isinstance(point, dict):
            continue
        if _is_generated_slide_point(point):
            continue
        points.append(
            {
                "id": point.get("id"),
                "type": point.get("type"),
                "title": point.get("title") or point.get("label"),
                "summary": _trim_prompt_text(point.get("summary"), 420),
                "content": _trim_prompt_text(point.get("content") or point.get("note"), 1200),
                "folder": point.get("folder"),
                "is_question": bool(point.get("is_question")),
                "answer_status": point.get("answer_status"),
                "is_active_question": bool(point.get("is_active_question")),
                "question_text": _trim_prompt_text(point.get("question_text"), 700),
                "expected_answer_point_type": point.get("expected_answer_point_type"),
                "source_point_ids": point.get("source_point_ids") or [],
            }
        )
        if len(points) >= 100:
            break
    return {
        "vault": (bundle or {}).get("vault"),
        "active_question_id": (bundle or {}).get("active_question_id"),
        "points": points,
    }


def _compose_sales_agent_task_prompt(report: Dict[str, Any], bundle: Dict[str, Any], *, mode: str) -> str:
    prompt_report = _sales_agent_report_for_prompt(report)
    root = str(report.get("skills_root") or "Skills")
    guide_excerpt = _asset_prompt_excerpt(report.get("guide") or {}, root=root)
    template_excerpt = _asset_prompt_excerpt(report.get("agent_template") or {}, root=root)
    if mode == "draft":
        existing_deck = report.get("existing_deck") if isinstance(report.get("existing_deck"), dict) else {}
        existing_deck_json = (
            json.dumps(existing_deck, ensure_ascii=False, indent=2)
            if existing_deck and existing_deck.get("ok")
            else "(none)"
        )
        return (
            "You are the Sales Pitch Deck Agent for QubitMCP.\n"
            "Generate the actual pitch deck slide copy with the Mediator LLM.\n"
            "Use only the approved guide, approved HTML deck template, deterministic readiness report, and current Data Nexus facts.\n"
            "The app will render your structured copy into HTML; do not output raw HTML.\n\n"
            "Draft rules:\n"
            "- Write one complete slide object for every slide in the approved template.\n"
            "- Use Data Nexus facts as the source of truth. Do not invent customers, revenue, traction, prices, metrics, competitors, team members, or claims that are not supported.\n"
            "- Each slide must include: number, title, headline, supporting_proof, speaker_note, and source_point_ids.\n"
            "- supporting_proof must be an array of 2-5 concise evidence bullets.\n"
            "- source_point_ids must contain only existing Data Nexus point ids that support that specific slide.\n"
            "- This is a draft-anyway request: do not refuse only because readiness is `needs_answers` or open prep questions exist.\n"
            "- For weak or unvalidated slides, write conservative hypothesis copy, mark the slide review_status `needs_review`, and clearly avoid unsupported claims.\n"
            "- Use careful wording such as `current hypothesis`, `needs validation`, or `initial signal` when facts are not proven.\n"
            "- Only return status `needs_answers` without slides when there are not enough credible facts to create any useful draft at all.\n"
            "- Otherwise return status `draft_ready` and include all slides, even when some slides need review.\n\n"
            "Existing deck edit rules:\n"
            "- If Existing deck draft JSON is provided, treat Generate Draft as a conservative edit of that deck, not a blank rewrite.\n"
            "- Preserve slide order, useful headlines, useful supporting notes, speaker notes, and source ids unless the current Data Nexus facts justify a change.\n"
            "- Prefer small improvements, missing-slide placeholders, and review-status updates over replacing the whole deck voice.\n"
            "- If only style/layout changed, keep the existing slide copy unchanged.\n\n"
            "Question rules when status is needs_answers:\n"
            "- Questions must be specific to the saved Data Nexus facts and the slide they support.\n"
            "- Every question must include: question_id, point_type=\"prep_question\", question_kind, slide_number, slide_title, accepted_point_type, matched_point_ids, text, and evaluation_criteria.\n\n"
            "Return a short user-facing summary, then exactly one hidden JSON tag. Do not emit data_nexus_update tags.\n"
            "The user-facing summary must not expose question_id, point_type, expected answer type, matched_point_ids, source-point plumbing, or hidden JSON details.\n"
            "Use this tag:\n"
            "<sales_agent_draft>{\"status\":\"draft_ready|needs_answers\",\"message\":\"short summary\",\"deck_title\":\"\",\"slide_reviews\":[],\"questions\":[],\"warnings\":[],\"slides\":[]}</sales_agent_draft>\n\n"
            "Slide object example:\n"
            "{\"number\":4,\"title\":\"Product / Demo\",\"headline\":\"QubitMCP turns a chat request into an auditable deck workflow.\",\"supporting_proof\":[\"Tanya reads Data Nexus facts and approved Skills assets.\",\"The Task node writes missing prep questions and generates an HTML draft.\"],\"speaker_note\":\"Show the buyer the end-to-end workflow from chat input to generated deck artifact.\",\"source_point_ids\":[\"task_20260817_071710_slide_04_product_demo_answer\"],\"review_status\":\"ready_for_review\"}\n\n"
            "Approved guide excerpt:\n"
            f"{guide_excerpt or '(unavailable)'}\n\n"
            "Approved template excerpt:\n"
            f"{template_excerpt or '(unavailable)'}\n\n"
            "Task/readiness report JSON:\n"
            f"{json.dumps(prompt_report, ensure_ascii=False, indent=2)}\n\n"
            "Existing deck draft JSON:\n"
            f"{existing_deck_json}\n\n"
            "Data Nexus bundle JSON:\n"
            f"{json.dumps(_sales_agent_bundle_for_prompt(bundle), ensure_ascii=False, indent=2)}\n"
        )
    tag = "sales_agent_questions" if mode == "write_questions" else "sales_agent_review"
    mode_instruction = (
        "Generate the best current prep/refinement questions and choose the next question the user should answer."
        if mode == "write_questions"
        else "Review the deterministic readiness result, judge each slide semantically, and correct the task status/questions if the facts justify it."
    )
    return (
        "You are the Sales Pitch Deck Agent for QubitMCP.\n"
        "Use the approved guide, approved HTML deck template, deterministic readiness report, and Data Nexus facts.\n"
        f"{mode_instruction}\n\n"
        "Readiness review rules:\n"
        "- Treat the deterministic readiness result as candidate source matching, not final proof that the slide is good.\n"
        "- For review mode, return a slide_reviews item for every template slide with slide_number, status, matched_point_ids, and notes.\n"
        "- Mark a slide `ready` only when the matched facts directly satisfy the template objective and would create coherent pitch-deck copy.\n"
        "- Mark a slide `weak` when the sources are related but generic, vague, incomplete, stale, contradictory, or missing an important buyer/investor detail.\n"
        "- Mark a slide `missing` when no credible Data Nexus answer point supports the slide.\n"
        "- matched_point_ids must include only source ids that truly support that slide; use [] when the deterministic matches are not actually relevant.\n\n"
        "Question quality rules:\n"
        "- Existing open prep questions are listed in the Data Nexus bundle with answer_status `open`.\n"
        "- If a newer non-question Data Nexus point already answers an open prep question, do not ask that question again; include it in `resolved_questions` with the existing question_id and answer_point_ids.\n"
        "- resolved_questions may include answer_point_type only when the existing point clearly should be retyped to the prep question's accepted answer type.\n"
        "- The app will link each resolved answer point to the prep question with `answers_question`.\n"
        "- Questions must be specific to the saved Data Nexus facts and the slide they support.\n"
        "- Do not ask generic filler questions when the answer is already present.\n"
        "- Prefer one concrete question per missing or weak slide; use refinement questions only when the deck is ready but a slide can become sharper.\n"
        "- Preserve existing question_id values when they still represent the same slide need so Data Nexus updates the old question instead of creating duplicates.\n"
        "- Every question must include: question_id, point_type=\"prep_question\", question_kind, slide_number, slide_title, accepted_point_type, matched_point_ids, text, and evaluation_criteria.\n"
        "- Evaluate source relevance yourself. matched_point_ids must refer only to existing Data Nexus point ids that directly help answer or refine that exact question.\n"
        "- If the deterministic slide matches are weak, generic, stale, or merely adjacent topics, return matched_point_ids: [] so the question is saved without source references.\n"
        "- If an existing prep question has stale source references, preserve the question_id when appropriate but return the corrected matched_point_ids list, including [] when no source should remain connected.\n"
        "- evaluation_criteria should say how to judge whether the user's answer is coherent and useful enough to become a Data Nexus answer point.\n\n"
        "Return a short user-facing summary, then exactly one hidden JSON tag. Do not emit data_nexus_update tags.\n"
        "The user-facing summary must not expose question_id, point_type, expected answer type, matched_point_ids, source-point plumbing, or hidden JSON details.\n"
        f"Use this tag:\n<{tag}>{{\"status\":\"needs_answers|ready_to_generate\",\"message\":\"short summary\",\"slide_reviews\":[],\"questions\":[],\"refinement_questions\":[],\"resolved_questions\":[],\"next_question_id\":\"\",\"next_question_text\":\"\"}}</{tag}>\n\n"
        "Include resolved_questions when existing points answer open prep questions, for example:\n"
        "\"resolved_questions\":[{\"question_id\":\"task_20260817_071710_slide_08_refine_traction_metric\",\"answer_point_ids\":[\"product_validation_workflow_test\"],\"answer_point_type\":\"traction_metric\"}]\n\n"
        "For review mode, include slide_reviews in the hidden tag, for example:\n"
        "\"slide_reviews\":[{\"slide_number\":4,\"status\":\"weak\",\"matched_point_ids\":[\"example_point_id\"],\"notes\":[\"The source describes the product but not the exact demo workflow.\"]}]\n\n"
        "Approved guide excerpt:\n"
        f"{guide_excerpt or '(unavailable)'}\n\n"
        "Approved template excerpt:\n"
        f"{template_excerpt or '(unavailable)'}\n\n"
        "Task/readiness report JSON:\n"
        f"{json.dumps(prompt_report, ensure_ascii=False, indent=2)}\n\n"
        "Data Nexus bundle JSON:\n"
        f"{json.dumps(_sales_agent_bundle_for_prompt(bundle), ensure_ascii=False, indent=2)}\n"
    )


def _report_can_use_sales_agent_ai(report: Dict[str, Any]) -> bool:
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    return bool(
        checks.get("skills_connected")
        and checks.get("data_nexus_connected")
        and checks.get("guide_found")
        and checks.get("template_found")
    )


def _write_question_actions_to_data_nexus(node_item, report: Dict[str, Any]) -> Dict[str, Any]:
    actions = _question_actions_from_report(report)
    task_id = str(report.get("task_id") or _task_id_for_item(node_item) or "task").strip()
    keep_ids = [str(action.get("id") or "").strip() for action in actions if str(action.get("id") or "").strip()]
    keep_ids.extend(
        str(resolved.get("question_id") or "").strip()
        for resolved in report.get("resolved_questions", []) or []
        if isinstance(resolved, dict) and str(resolved.get("question_id") or "").strip()
    )
    keep_ids = list(dict.fromkeys(keep_ids))
    sync_action = {
        "op": "sync_task_questions",
        "task_id": task_id,
        "keep_ids": keep_ids,
        "folders": ["prep_questions", "refinement_questions"],
    }
    write_result = {
        "ok": False,
        "message": "No pending prep questions to write.",
        "question_count": 0,
    }

    _, _, data_nexus_node = _connected_task_nodes(node_item)
    if data_nexus_node is None:
        write_result["message"] = "Connect a Data Nexus node to the Task data_nexus input."
        report["data_nexus_write"] = write_result
        _persist_task_report(node_item, report)
        return report

    try:
        from nodes.data_nexus import spec as data_nexus_spec

        handler = getattr(data_nexus_spec, "apply_data_nexus_update_from_item", None)
        if not callable(handler):
            write_result["message"] = "Data Nexus update helper is unavailable."
        else:
            ok, message = handler(data_nexus_node, {"actions": [sync_action, *actions]}, requester="Task")
            already_current = str(message or "").startswith("No Data Nexus changes")
            active_question_id = ""
            active_message = ""
            selector = getattr(data_nexus_spec, "set_active_data_nexus_question_from_item", None)
            if callable(selector):
                active_ok, active_message = selector(data_nexus_node, "")
                if active_ok:
                    active_bundle, _active_error = _data_nexus_bundle_from_node(data_nexus_node)
                    active_question_id = str((active_bundle or {}).get("active_question_id") or "").strip()
            if already_current:
                message_text = (
                    "Prep questions already exist in Data Nexus."
                    if actions
                    else "No current prep questions; Data Nexus question set is already synced."
                )
            else:
                message_text = str(message or "")
            write_result = {
                "ok": bool(ok or already_current),
                "message": message_text,
                "question_count": sum(1 for action in actions if action.get("op") == "upsert_point"),
                "action_count": len(actions) + 1,
                "active_question_id": active_question_id,
                "active_message": active_message,
            }
    except Exception as exc:
        write_result["message"] = f"Failed to write prep questions to Data Nexus: {exc}"

    report["data_nexus_write"] = write_result
    fingerprint = _current_data_nexus_fingerprint(node_item)
    if fingerprint:
        data_nexus = dict(report.get("data_nexus") or {})
        data_nexus["fingerprint"] = fingerprint
        report["data_nexus"] = data_nexus
    _persist_task_report(node_item, report)
    return report


def _start_sales_agent_ai_task(node_item, report: Dict[str, Any], *, mode: str, on_done=None) -> tuple[bool, str]:
    mediator_node = _connected_task_mediator_node(node_item)
    if mediator_node is None:
        return False, "Connect a Mediator node to the Task mediator input for Sales Agent AI."
    _, _, data_nexus_node = _connected_task_nodes(node_item)
    bundle, bundle_error = _data_nexus_bundle_from_node(data_nexus_node)
    if bundle_error:
        return False, bundle_error
    try:
        from nodes.mediator_agent import spec as mediator_spec
    except Exception as exc:
        return False, f"Mediator helper is unavailable: {exc}"
    runner = getattr(mediator_spec, "run_sales_agent_task_from_item", None)
    if not callable(runner):
        return False, "Connected Mediator does not support Sales Agent task prompts yet."

    prompt = _compose_sales_agent_task_prompt(report, bundle, mode=mode)
    signature = hashlib.sha1(f"sales_agent:{mode}\n{prompt}".encode("utf-8", errors="ignore")).hexdigest()

    def _done(exit_code: int, error_text: str, response_text: str) -> None:
        final_report = dict(report)
        if int(exit_code) != 0 or str(error_text or "").strip():
            message = str(error_text or f"Sales Agent AI exited with code {exit_code}.").strip()
            if mode == "draft":
                final_report = _draft_generation_failure(final_report, message)
            else:
                final_report["sales_agent_ai"] = {
                    "ok": False,
                    "mode": mode,
                    "message": message,
                }
            _persist_task_report(node_item, final_report)
        else:
            if mode == "write_questions":
                final_report = _apply_sales_agent_ai_output(final_report, response_text, mode=mode)
                final_report = _apply_resolved_question_actions_to_data_nexus(node_item, final_report)
                final_report = _write_question_actions_to_data_nexus(node_item, final_report)
            elif mode == "draft":
                final_report = _apply_sales_agent_draft_output(node_item, final_report, response_text)
                _persist_task_report(node_item, final_report)
                generation = final_report.get("generation") if isinstance(final_report.get("generation"), dict) else {}
                if generation.get("ok") and generation.get("index_path"):
                    _refresh_connected_html_previews(node_item, str(generation.get("index_path") or ""))
            else:
                final_report = _apply_sales_agent_ai_output(final_report, response_text, mode=mode)
                final_report = _apply_resolved_question_actions_to_data_nexus(node_item, final_report)
                _persist_task_report(node_item, final_report)
        if callable(on_done):
            try:
                on_done(final_report)
            except Exception:
                pass
        else:
            widget = getattr(node_item, "_task_widget", None)
            if widget is not None and hasattr(widget, "_finish_async_report"):
                try:
                    widget._finish_async_report(final_report)
                except Exception:
                    pass

    scene = _scene_for_item(node_item)
    started, message = runner(scene, mediator_node, prompt, signature, _done)
    return bool(started), str(message or "")


def run_task_step_with_sales_agent_ai_from_item(node_item, on_done=None) -> Dict[str, Any]:
    report = run_task_step_from_item(node_item)
    if not _report_can_use_sales_agent_ai(report):
        report, reused_open_questions = _apply_open_data_nexus_questions(node_item, report)
        if reused_open_questions:
            _persist_task_report(node_item, report)
        return report
    if _connected_task_mediator_node(node_item) is None:
        report, reused_open_questions = _apply_open_data_nexus_questions(node_item, report)
        if reused_open_questions:
            _persist_task_report(node_item, report)
        return report
    report["sales_agent_ai"] = {
        "ok": None,
        "pending": True,
        "mode": "review",
        "message": "Sales Agent AI is reviewing current Data Nexus readiness and open prep questions.",
    }
    _persist_task_report(node_item, report)
    started, message = _start_sales_agent_ai_task(node_item, report, mode="review", on_done=on_done)
    if not started:
        report["sales_agent_ai"] = {
            "ok": False,
            "mode": "review",
            "message": message,
        }
        _persist_task_report(node_item, report)
    return report


def write_task_questions_to_data_nexus_from_item(node_item, *, use_mediator: bool = True, on_done=None) -> Dict[str, Any]:
    mediator_node = _connected_task_mediator_node(node_item)
    current_report = _report_from_model(node_item)
    current_fingerprint = _current_data_nexus_fingerprint(node_item)
    report_fingerprint = str(((current_report or {}).get("data_nexus") or {}).get("fingerprint") or "").strip()
    if _report_has_writable_questions(current_report) and current_fingerprint and report_fingerprint == current_fingerprint:
        return _write_question_actions_to_data_nexus(node_item, current_report)

    report = run_task_step_from_item(node_item)
    if (
        use_mediator
        and _report_can_use_sales_agent_ai(report)
        and mediator_node is not None
    ):
        report["sales_agent_ai"] = {
            "ok": None,
            "pending": True,
            "mode": "write_questions",
            "message": "Sales Agent AI is reviewing current points before writing prep questions.",
        }
        _persist_task_report(node_item, report)
        started, message = _start_sales_agent_ai_task(node_item, report, mode="write_questions", on_done=on_done)
        if started:
            return report
        report["sales_agent_ai"] = {
            "ok": False,
            "mode": "write_questions",
            "message": message,
        }
    report, reused_open_questions = _apply_open_data_nexus_questions(node_item, report)
    if reused_open_questions:
        report["data_nexus_write"] = {
            "ok": True,
            "message": "Open prep questions already exist in Data Nexus. No new questions were written.",
            "question_count": 0,
            "action_count": 0,
        }
        _persist_task_report(node_item, report)
        return report
    return _write_question_actions_to_data_nexus(node_item, report)


def _draft_generation_failure(report: Dict[str, Any], message: str, *, status: str = "failed") -> Dict[str, Any]:
    updated = dict(report)
    clean_message = str(message or "Mediator AI draft generation failed.").strip()
    updated["status"] = status
    updated["generation"] = {
        "ok": False,
        "message": clean_message,
    }
    updated["sales_agent_ai"] = {
        "ok": False,
        "mode": "draft",
        "message": clean_message,
    }
    updated["questions"] = [
        {
            "question_id": "generation_failed",
            "point_type": "generation",
            "text": clean_message,
        }
    ]
    return updated


def _apply_sales_agent_draft_output(node_item, report: Dict[str, Any], response_text: str) -> Dict[str, Any]:
    payload, error = _sales_agent_payload_from_response(response_text, mode="draft")
    if error:
        return _draft_generation_failure(report, error)
    payload_status = str(payload.get("status") or "").strip().lower()
    if payload_status not in {"draft_ready", "ready_to_generate", "needs_answers"} and not isinstance(payload.get("slides"), list):
        return _draft_generation_failure(
            report,
            "Sales Agent draft response did not include usable slide copy.",
        )

    _, _, data_nexus_node = _connected_task_nodes(node_item)
    bundle, bundle_error = _data_nexus_bundle_from_node(data_nexus_node)
    if bundle_error:
        return _draft_generation_failure(report, bundle_error)

    template = report.get("agent_template") if isinstance(report.get("agent_template"), dict) else {}
    result = generate_sales_deck_from_ai_draft(
        str((template or {}).get("path") or ""),
        bundle,
        payload,
        root=str(report.get("skills_root") or "Skills"),
    )
    result_data = result.to_dict()
    updated = _apply_sales_agent_ai_output(dict(report), response_text, mode="draft")
    updated["generation"] = result_data
    updated["artifact"] = result_data
    if result.ok:
        updated["status"] = "draft_ready"
        updated["sales_agent_ai"] = {
            "ok": True,
            "mode": "draft",
            "message": str(payload.get("message") or result.message or "Mediator AI generated the deck draft.").strip(),
            "draft_slide_count": result.slide_count,
            "warning_count": len(result.warnings),
        }
        updated = _index_deck_slides_to_data_nexus(node_item, result.index_path, updated)
    else:
        updated["status"] = "failed"
        updated["questions"] = [
            {
                "question_id": "generation_failed",
                "point_type": "generation",
                "text": result.message,
            }
        ]
        updated["sales_agent_ai"] = {
            "ok": False,
            "mode": "draft",
            "message": result.message,
        }
    return updated


def run_task_step_from_item(node_item) -> Dict[str, Any]:
    task_id = _task_id_for_item(node_item)
    previous_report = _report_from_model(node_item)
    _, skills_node, data_nexus_node = _connected_task_nodes(node_item)
    mediator_node = _connected_task_mediator_node(node_item)

    checks = {
        "skills_connected": skills_node is not None,
        "data_nexus_connected": data_nexus_node is not None,
        "mediator_connected": mediator_node is not None,
        "guide_found": False,
        "template_found": False,
        "points_found": False,
        "readiness_ready": False,
    }
    questions: List[Dict[str, str]] = []
    refinement_questions: List[Dict[str, Any]] = []
    snapshot: Dict[str, Any] = {}
    guide = None
    template = None
    skills_root = ""

    if skills_node is None:
        questions.append(
            {
                "question_id": "setup_skills",
                "point_type": "setup",
                "text": "Connect a Skills node to the Task skills input.",
            }
        )
    else:
        skills_model = getattr(skills_node, "model", None)
        skills_root = _param_value_from_model(skills_model, "root", "Skills").strip() or "Skills"
        try:
            snapshot = scan_skills_library(skills_root).to_dict()
            guide = _best_version_asset(_approved_sales_pitch_guides(snapshot))
            template = _best_version_asset(_approved_sales_templates(snapshot))
        except Exception as exc:
            snapshot = {"warnings": [str(exc)]}
        checks["guide_found"] = guide is not None
        checks["template_found"] = template is not None
        if guide is None:
            questions.append(
                {
                    "question_id": "setup_task_guide",
                    "point_type": "setup",
                    "text": "Approve a Sales Pitch Deck task guide in Skills/agent_guides or Skills/task_guides.",
                }
            )
        if template is None:
            questions.append(
                {
                    "question_id": "setup_agent_template",
                    "point_type": "setup",
                    "text": "Approve a Sales Agent HTML deck template in Skills/agent_templates.",
                }
            )

    bundle: Dict[str, Any] = {}
    points: List[Dict[str, Any]] = []
    point_counts: Dict[str, int] = {}
    usable_points = 0
    vault_path = ""
    readiness: Dict[str, Any] = {}
    slide_deprecation: Dict[str, Any] = {}

    def apply_bundle_summary(next_bundle: Dict[str, Any]) -> None:
        nonlocal points, point_counts, usable_points, vault_path
        raw_points = next_bundle.get("points", []) if isinstance(next_bundle, dict) else []
        points = [dict(point) for point in raw_points if isinstance(point, dict)]
        point_counts = _point_type_counts(points)
        usable_points = sum(
            1
            for point in points
            if str(point.get("content") or point.get("note") or point.get("summary") or "").strip()
            and not _is_question_like_point(point)
            and not _is_generated_slide_point(point)
        )
        vault_path = str(next_bundle.get("vault") or "") if isinstance(next_bundle, dict) else ""

    if data_nexus_node is None:
        questions.append(
            {
                "question_id": "setup_data_nexus",
                "point_type": "setup",
                "text": "Connect a Data Nexus node to the Task data_nexus input.",
            }
        )
    else:
        bundle, bundle_error = _data_nexus_bundle_from_node(data_nexus_node)
        if bundle_error:
            questions.append(
                {
                    "question_id": "data_nexus_read",
                    "point_type": "setup",
                    "text": bundle_error,
                }
            )
        apply_bundle_summary(bundle)
        checks["points_found"] = bool(points)
        if not points:
            questions.append(
                {
                    "question_id": "data_nexus_empty",
                    "point_type": "prep_answer",
                    "text": "Add sales preparation answers to the connected Data Nexus vault.",
                }
            )
        elif usable_points <= 0:
            questions.append(
                {
                    "question_id": "data_nexus_answers",
                    "point_type": "prep_answer",
                    "text": "Answer the sales preparation questions so Data Nexus contains usable sales facts.",
                }
            )

    can_analyze = (
        checks["skills_connected"]
        and checks["data_nexus_connected"]
        and checks["guide_found"]
        and checks["template_found"]
    )
    if can_analyze:
        readiness = analyze_sales_deck_readiness(
            str((template or {}).get("path") or ""),
            bundle,
            root=skills_root or "Skills",
        )
        slide_deprecation = _deprecate_current_slides_for_template_change(
            node_item,
            task_id,
            bundle,
            template or {},
            previous_report,
            skills_root or "Skills",
            readiness,
        )
        if slide_deprecation.get("ok") and int(slide_deprecation.get("deprecated_slide_count", 0) or 0) > 0:
            refreshed_bundle, refresh_error = _data_nexus_bundle_from_node(data_nexus_node)
            if refresh_error:
                slide_deprecation["message"] = f"{slide_deprecation.get('message') or ''} Refresh failed: {refresh_error}".strip()
            else:
                bundle = refreshed_bundle
                apply_bundle_summary(bundle)
                readiness = analyze_sales_deck_readiness(
                    str((template or {}).get("path") or ""),
                    bundle,
                    root=skills_root or "Skills",
                )
        checks["readiness_ready"] = str(readiness.get("status") or "").strip().lower() == "ready_to_generate"
        if not bool(readiness.get("ok", False)):
            questions.append(
                {
                    "question_id": "readiness_error",
                    "point_type": "setup",
                    "text": str(readiness.get("message") or "Sales Agent readiness analysis failed."),
                }
            )
        readiness_questions = readiness.get("questions") if isinstance(readiness, dict) else []
        if isinstance(readiness_questions, list) and readiness_questions:
            questions.extend(question for question in readiness_questions if isinstance(question, dict))
        raw_refinement_questions = readiness.get("refinement_questions") if isinstance(readiness, dict) else []
        if isinstance(raw_refinement_questions, list):
            refinement_questions = [question for question in raw_refinement_questions if isinstance(question, dict)]

    if not checks["skills_connected"] or not checks["data_nexus_connected"] or not checks["guide_found"] or not checks["template_found"]:
        status = "needs_setup"
    elif not checks["points_found"] or usable_points <= 0:
        status = "needs_answers"
    elif readiness:
        status = str(readiness.get("status") or "needs_answers").strip().lower() or "needs_answers"
    else:
        status = "ready_to_generate"

    report = {
        "task_id": task_id,
        "status": status,
        "skills_root": skills_root,
        "guide": guide or {},
        "agent_template": template or {},
        "data_nexus": {
            "vault": vault_path,
            "point_count": len(points),
            "usable_point_count": usable_points,
            "point_types": point_counts,
            "fingerprint": _data_nexus_bundle_fingerprint(bundle),
        },
        "checks": checks,
        "readiness": readiness,
        "slide_deprecation": slide_deprecation,
        "questions": questions,
        "refinement_questions": refinement_questions,
        "snapshot_warnings": list(snapshot.get("warnings", []) or []) if isinstance(snapshot, dict) else [],
    }

    _persist_task_report(node_item, report)
    return report


def generate_task_draft_from_item(node_item, on_done=None) -> Dict[str, Any]:
    _set_param_on_item(node_item, TASK_STATUS_PARAM, "generating", notify_scene=False)
    _set_param_on_item(node_item, STATUS_OUTPUT_PORT, "generating", notify_scene=True)

    existing_artifact_path = _param_value_from_model(getattr(node_item, "model", None), TASK_LAST_ARTIFACT_PARAM, "").strip()
    report = run_task_step_from_item(node_item)
    if existing_artifact_path:
        existing_deck = sales_deck_prompt_context_from_index(existing_artifact_path)
        if existing_deck.get("ok"):
            report["existing_deck"] = existing_deck
    report, _reused_open_questions = _apply_open_data_nexus_questions(node_item, report)
    if not _report_can_use_sales_agent_ai(report):
        report = _draft_generation_failure(
            report,
            "Connect Skills, Data Nexus, Mediator, and approved Sales Agent guide/template before generating a draft.",
        )
        _persist_task_report(node_item, report)
        return report

    if _connected_task_mediator_node(node_item) is None:
        report = _draft_generation_failure(
            report,
            "Connect a Mediator node to the Task mediator input. Task Generate Draft now uses the Mediator Sales Agent LLM.",
        )
        _persist_task_report(node_item, report)
        return report

    report["status"] = "generating"
    report["sales_agent_ai"] = {
        "ok": None,
        "pending": True,
        "mode": "draft",
        "message": "Sales Agent AI is writing the deck draft.",
    }
    _persist_task_report(node_item, report)
    started, message = _start_sales_agent_ai_task(node_item, report, mode="draft", on_done=on_done)
    if not started:
        report = _draft_generation_failure(report, message)
        _persist_task_report(node_item, report)
    return report


def refresh_task_deck_html_from_item(node_item) -> Dict[str, Any]:
    report = _report_from_model(node_item)
    if not report:
        report = {
            "task_id": _task_id_for_item(node_item),
            "status": _param_value_from_model(getattr(node_item, "model", None), TASK_STATUS_PARAM, "created"),
        }
    artifact_path = _artifact_path_from_report(report)
    if not artifact_path:
        artifact_path = _param_value_from_model(getattr(node_item, "model", None), TASK_LAST_ARTIFACT_PARAM, "").strip()
    if not artifact_path:
        result_data = {
            "ok": False,
            "message": "No generated deck artifact is available yet. Generate a draft first.",
        }
        report["generation"] = result_data
        report["artifact"] = result_data
        _persist_task_report(node_item, report)
        return report

    result = restyle_sales_deck_from_index(artifact_path)
    result_data = result.to_dict()
    report["generation"] = result_data
    report["artifact"] = result_data
    if result.ok:
        report["status"] = "draft_ready"
        report["sales_agent_ai"] = {
            "ok": True,
            "pending": False,
            "mode": "restyle",
            "message": result.message,
        }
        _persist_task_report(node_item, report)
        _refresh_connected_html_previews(node_item, result.index_path)
    else:
        report["sales_agent_ai"] = {
            "ok": False,
            "pending": False,
            "mode": "restyle",
            "message": result.message,
        }
        _persist_task_report(node_item, report)
    return report


def _format_report(report: Dict[str, Any]) -> str:
    if not report:
        return "status: created"
    data_nexus = report.get("data_nexus") or {}
    point_types = data_nexus.get("point_types") or {}
    type_text = ", ".join(f"{key}: {value}" for key, value in point_types.items()) or "none"
    lines = [
        f"status: {report.get('status') or 'unknown'}",
        f"task_id: {report.get('task_id') or ''}",
        "",
        "connections:",
        f"  skills: {'yes' if (report.get('checks') or {}).get('skills_connected') else 'no'}",
        f"  data_nexus: {'yes' if (report.get('checks') or {}).get('data_nexus_connected') else 'no'}",
        f"  mediator: {'yes' if (report.get('checks') or {}).get('mediator_connected') else 'no'}",
        "",
        "skills:",
        f"  guide: {_asset_line(report.get('guide') or None, version_key='guide_version_id')}",
        f"  template: {_asset_line(report.get('agent_template') or None, version_key='template_version_id')}",
        "",
        "data_nexus:",
        f"  vault: {data_nexus.get('vault') or '(workflow/default)'}",
        f"  points: {data_nexus.get('point_count', 0)}",
        f"  usable_points: {data_nexus.get('usable_point_count', 0)}",
        f"  types: {type_text}",
    ]
    readiness = report.get("readiness") if isinstance(report.get("readiness"), dict) else {}
    if readiness:
        lines.extend(
            [
                "",
                "readiness:",
                f"  status: {readiness.get('status') or 'unknown'}",
                f"  slides: {readiness.get('ready_slide_count', 0)} ready / {readiness.get('weak_slide_count', 0)} weak / {readiness.get('missing_slide_count', 0)} missing",
            ]
        )
        slides = readiness.get("slides") or []
        if slides:
            lines.append("  slide_detail:")
            for slide in slides:
                if not isinstance(slide, dict):
                    continue
                number = int(slide.get("number", 0) or 0)
                status = str(slide.get("status") or "unknown")
                title = str(slide.get("title") or "")
                matched = ", ".join(str(item) for item in (slide.get("matched_point_ids") or [])) or "none"
                lines.append(f"    {number:02d} {status}: {title} [{matched}]")
                notes = slide.get("notes") or []
                for note in notes[:2]:
                    lines.append(f"      - {note}")
    sales_agent_ai = report.get("sales_agent_ai") if isinstance(report.get("sales_agent_ai"), dict) else {}
    if sales_agent_ai:
        lines.extend(
            [
                "",
                "sales_agent_ai:",
                f"  ok: {sales_agent_ai.get('ok')}",
                f"  pending: {sales_agent_ai.get('pending', False)}",
                f"  mode: {sales_agent_ai.get('mode') or ''}",
                f"  message: {sales_agent_ai.get('message') or ''}",
            ]
        )
        if sales_agent_ai.get("next_question_text"):
            lines.append(f"  next_question: {sales_agent_ai.get('next_question_text')}")
    generation = _generation_result_from_report(report)
    if generation:
        lines.extend(
            [
                "",
                "artifact:",
                f"  ok: {generation.get('ok')}",
                f"  message: {generation.get('message') or ''}",
                f"  index: {generation.get('index_path') or ''}",
                f"  manifest: {generation.get('manifest_path') or ''}",
                f"  deck_dir: {generation.get('deck_dir') or ''}",
            ]
        )
        generation_warnings = generation.get("warnings") or []
        if generation_warnings:
            lines.append("  warnings:")
            lines.extend(f"    - {warning}" for warning in generation_warnings)
    warnings = report.get("snapshot_warnings") or []
    if warnings:
        lines.extend(["", "warnings:"])
        lines.extend(f"  - {warning}" for warning in warnings)
    questions = report.get("questions") or []
    if questions:
        lines.extend(["", "next:"])
        for idx, question in enumerate(questions):
            if idx:
                lines.append("")
            lines.append(f"  - {question.get('text') or ''}")
    refinement_questions = report.get("refinement_questions") or []
    if refinement_questions:
        lines.extend(["", "refinement_questions:"])
        for idx, question in enumerate(refinement_questions):
            if idx:
                lines.append("")
            lines.append(f"  - {question.get('text') or ''}")
    data_nexus_answer_links = report.get("data_nexus_answer_links") if isinstance(report.get("data_nexus_answer_links"), dict) else {}
    if data_nexus_answer_links:
        lines.extend(
            [
                "",
                "data_nexus_answer_links:",
                f"  ok: {data_nexus_answer_links.get('ok')}",
                f"  resolved_questions: {data_nexus_answer_links.get('resolved_question_count', 0)}",
                f"  message: {data_nexus_answer_links.get('message') or ''}",
            ]
        )
    data_nexus_write = report.get("data_nexus_write") if isinstance(report.get("data_nexus_write"), dict) else {}
    if data_nexus_write:
        lines.extend(
            [
                "",
                "data_nexus_write:",
                f"  ok: {data_nexus_write.get('ok')}",
                f"  questions: {data_nexus_write.get('question_count', 0)}",
                f"  message: {data_nexus_write.get('message') or ''}",
            ]
        )
    slide_deprecation = report.get("slide_deprecation") if isinstance(report.get("slide_deprecation"), dict) else {}
    if slide_deprecation and (slide_deprecation.get("deprecated_slide_count") or slide_deprecation.get("message")):
        lines.extend(
            [
                "",
                "slide_deprecation:",
                f"  ok: {slide_deprecation.get('ok')}",
                f"  previous_slides: {slide_deprecation.get('deprecated_slide_count', 0)}",
                f"  folder: {slide_deprecation.get('folder') or ''}",
                f"  type: {slide_deprecation.get('point_type') or ''}",
                f"  message: {slide_deprecation.get('message') or ''}",
            ]
        )
    slide_index = report.get("slide_index") if isinstance(report.get("slide_index"), dict) else {}
    if slide_index:
        lines.extend(
            [
                "",
                "slide_index:",
                f"  ok: {slide_index.get('ok')}",
                f"  slides: {slide_index.get('slide_count', 0)}",
                f"  previous_slides: {slide_index.get('old_slide_count', 0)}",
                f"  sequence_links: {slide_index.get('sequence_link_count', 0)}",
                f"  source_links: {slide_index.get('source_link_count', 0)}",
                f"  folder: {slide_index.get('folder') or ''}",
                f"  type: {slide_index.get('point_type') or ''}",
                f"  message: {slide_index.get('message') or ''}",
            ]
        )
    return "\n".join(lines)


def _status_css_class(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {
        "ready",
        "ready_to_generate",
        "draft_ready",
        "approved",
        "true",
        "yes",
        "ok",
        "ready_for_review",
    }:
        return "ok"
    if text in {"needs_answers", "needs_review", "weak", "generating", "pending"}:
        return "warn"
    if text in {"needs_setup", "missing", "failed", "false", "no", "error"}:
        return "bad"
    return "value"


def _status_span(value: Any) -> str:
    clean = str(value or "").strip()
    return f"<span class=\"{_status_css_class(clean)}\">{html_lib.escape(clean)}</span>"


def _highlight_count_summary(text: str) -> str:
    escaped = html_lib.escape(text)

    def repl(match) -> str:
        count = match.group(1)
        label = match.group(2)
        return f"{count} <span class=\"{_status_css_class(label)}\">{label}</span>"

    return re.sub(r"\b(\d+)\s+(ready|weak|missing)\b", repl, escaped)


def _format_report_line_html(line: str) -> str:
    raw = str(line or "")
    stripped = raw.strip()
    if not stripped:
        return ""
    if not raw.startswith(" ") and stripped.endswith(":"):
        return f"<span class=\"section\">{html_lib.escape(raw)}</span>"

    match = re.match(r"^(\s*)(status|ok):\s*(.+?)\s*$", raw, flags=re.IGNORECASE)
    if match:
        indent, label, value = match.groups()
        return f"{html_lib.escape(indent)}<span class=\"label\">{html_lib.escape(label)}:</span> {_status_span(value)}"

    match = re.match(r"^(\s*)(skills|data_nexus|mediator):\s*(yes|no)\s*$", raw, flags=re.IGNORECASE)
    if match:
        indent, label, value = match.groups()
        return f"{html_lib.escape(indent)}<span class=\"label\">{html_lib.escape(label)}:</span> {_status_span(value)}"

    match = re.match(r"^(\s*slides:\s*)(.+?)\s*$", raw, flags=re.IGNORECASE)
    if match:
        return f"<span class=\"label\">{html_lib.escape(match.group(1))}</span>{_highlight_count_summary(match.group(2))}"

    match = re.match(r"^(\s*\d{2}\s+)(ready|weak|missing|failed|unknown)(:.*)$", raw, flags=re.IGNORECASE)
    if match:
        prefix, status, suffix = match.groups()
        return f"{html_lib.escape(prefix)}{_status_span(status)}{html_lib.escape(suffix)}"

    match = re.match(r"^(\s*)(message):\s*(.+?)\s*$", raw, flags=re.IGNORECASE)
    if match:
        indent, label, value = match.groups()
        lower = value.lower()
        cls = "bad" if "failed" in lower or "error" in lower else "warn" if "no " in lower or "needs" in lower else "info"
        return (
            f"{html_lib.escape(indent)}<span class=\"label\">{html_lib.escape(label)}:</span> "
            f"<span class=\"{cls}\">{html_lib.escape(value)}</span>"
        )

    if stripped.startswith("- "):
        return f"<span class=\"question\">{html_lib.escape(raw)}</span>"
    return html_lib.escape(raw)


def _format_report_text_html(text: str) -> str:
    body = "\n".join(_format_report_line_html(line) for line in str(text or "").splitlines())
    return (
        "<html><head><style>"
        "body{margin:0;background:#0b1018;color:#dbeafe;}"
        "pre{margin:0;white-space:pre-wrap;font-family:Cascadia Mono,Consolas,monospace;font-size:12px;line-height:1.38;}"
        ".section{color:#93c5fd;font-weight:700;}"
        ".label{color:#94a3b8;font-weight:600;}"
        ".value{color:#dbeafe;}"
        ".ok{color:#86efac;font-weight:800;}"
        ".warn{color:#fbbf24;font-weight:800;}"
        ".bad{color:#fb7185;font-weight:800;}"
        ".info{color:#67e8f9;font-weight:700;}"
        ".question{color:#fef3c7;}"
        "</style></head><body><pre>"
        f"{body}"
        "</pre></body></html>"
    )


def _format_report_html(report: Dict[str, Any]) -> str:
    return _format_report_text_html(_format_report(report))


class TaskWidget(QtWidgets.QFrame):
    def __init__(self, node_item=None, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        try:
            setattr(node_item, "_task_widget", self)
        except Exception:
            pass
        self.setObjectName("TaskWidget")
        self.setStyleSheet(
            """
            QFrame#TaskWidget{background:#0f1216;border:1px solid #334155;border-radius:6px;}
            QLabel{color:#e5e7eb;}
            QLabel#TaskTitle{font-weight:600;color:#f8fafc;}
            QLabel#TaskSubtle{color:#94a3b8;}
            QTextEdit{background:#0b1018;color:#dbeafe;border:1px solid #334155;border-radius:4px;padding:6px;}
            QComboBox{background:#111827;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:3px 8px;font-weight:600;}
            QComboBox:hover{border-color:#94a3b8;background:#1e293b;}
            QComboBox:disabled{background:#111827;border-color:#334155;color:#64748b;}
            QComboBox QAbstractItemView{background:#0f1216;color:#e5e7eb;border:1px solid #475569;selection-background-color:#164e63;outline:0;}
            QPushButton{background:#1e293b;color:#f8fafc;border:1px solid #64748b;border-radius:4px;padding:5px 9px;font-weight:600;}
            QPushButton:hover{background:#334155;}
            QPushButton#TaskRunButton{background:#1d4ed8;border-color:#60a5fa;color:#eff6ff;}
            QPushButton#TaskRunButton:hover{background:#2563eb;}
            QPushButton#TaskGenerateButton{background:#0f766e;border-color:#2dd4bf;color:#ecfeff;}
            QPushButton#TaskGenerateButton:hover{background:#0d9488;}
            QPushButton#TaskRefreshHtmlButton{background:#4338ca;border-color:#a5b4fc;color:#eef2ff;}
            QPushButton#TaskRefreshHtmlButton:hover{background:#4f46e5;}
            QPushButton#TaskIndexSlidesButton{background:#166534;border-color:#39ff14;color:#f0fdf4;}
            QPushButton#TaskIndexSlidesButton:hover{background:#15803d;}
            QPushButton#TaskWriteQuestionsButton{background:#d97706;border-color:#fbbf24;color:#111827;}
            QPushButton#TaskWriteQuestionsButton:hover{background:#f59e0b;}
            QPushButton:disabled{background:#111827;border-color:#334155;color:#64748b;}
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Task")
        title.setObjectName("TaskTitle")
        header.addWidget(title, 0)
        header.addStretch(1)
        self._status_combo = QtWidgets.QComboBox()
        self._status_combo.setToolTip("Set the Task status: Draft, Approved, Dismissed, or another workflow state.")
        self._status_combo.setFixedWidth(148)
        try:
            foreground_role = QtCore.Qt.ItemDataRole.ForegroundRole
        except Exception:
            foreground_role = QtCore.Qt.ForegroundRole
        manual_status_colors = {
            "approved": "#86efac",
            "dismissed": "#fb7185",
        }
        for value, label in TASK_STATUS_CHOICES:
            self._status_combo.addItem(label, value)
            color = manual_status_colors.get(value)
            if color:
                self._status_combo.setItemData(
                    self._status_combo.count() - 1,
                    QtGui.QBrush(QtGui.QColor(color)),
                    foreground_role,
                )
        self._status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        header.addWidget(self._status_combo, 0)
        layout.addLayout(header)

        self._summary = QtWidgets.QLabel("")
        self._summary.setObjectName("TaskSubtle")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._report = QtWidgets.QTextEdit()
        self._report.setReadOnly(True)
        self._report.setMinimumHeight(190)
        layout.addWidget(self._report, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._run_btn = QtWidgets.QPushButton("Run Step")
        self._generate_btn = QtWidgets.QPushButton("Generate Draft")
        self._refresh_html_btn = QtWidgets.QPushButton("Refresh HTML")
        self._index_slides_btn = QtWidgets.QPushButton("Index Slides")
        self._write_questions_btn = QtWidgets.QPushButton("Write Questions")
        self._run_btn.setObjectName("TaskRunButton")
        self._generate_btn.setObjectName("TaskGenerateButton")
        self._refresh_html_btn.setObjectName("TaskRefreshHtmlButton")
        self._index_slides_btn.setObjectName("TaskIndexSlidesButton")
        self._write_questions_btn.setObjectName("TaskWriteQuestionsButton")
        self._run_btn.setToolTip("Inspect connected Skills, Data Nexus, and Mediator readiness for this task.")
        self._generate_btn.setToolTip("Ask the Mediator Sales Agent to generate or update the HTML deck draft, then index the created slides into Data Nexus.")
        self._refresh_html_btn.setToolTip("Rebuild the current generated deck HTML with the latest deck style without changing slide copy.")
        self._index_slides_btn.setToolTip("Read the current generated deck HTML and create or update slide points in Data Nexus.")
        self._write_questions_btn.setToolTip("Write the current prep and refinement questions into Data Nexus without generating a deck.")
        self._run_btn.clicked.connect(self._run_step)
        self._generate_btn.clicked.connect(self._generate_draft)
        self._refresh_html_btn.clicked.connect(self._refresh_html)
        self._index_slides_btn.clicked.connect(self._index_slides)
        self._write_questions_btn.clicked.connect(self._write_questions)
        actions.addWidget(self._run_btn, 0)
        actions.addWidget(self._write_questions_btn, 0)
        actions.addWidget(self._generate_btn, 0)
        actions.addWidget(self._refresh_html_btn, 0)
        actions.addWidget(self._index_slides_btn, 0)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._refresh_from_state()

    def sizeHint(self):
        return QtCore.QSize(TASK_BODY_W, TASK_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(460, 320)

    def _refresh_from_state(self) -> None:
        model = getattr(self._node_item, "model", None)
        status = _param_value_from_model(model, TASK_STATUS_PARAM, "created").strip() or "created"
        guide_path = _param_value_from_model(model, TASK_GUIDE_PATH_PARAM, "").strip()
        template_path = _param_value_from_model(model, TASK_AGENT_TEMPLATE_PATH_PARAM, "").strip()
        report_raw = _param_value_from_model(model, TASK_LAST_REPORT_PARAM, "").strip()
        report = {}
        if report_raw:
            try:
                parsed = json.loads(report_raw)
                if isinstance(parsed, dict):
                    report = parsed
            except Exception:
                report = {}
        if report:
            self._show_report(report)
            return
        self._set_status_combo_value(status)
        self._status_combo.setEnabled(True)
        self._generate_btn.setEnabled(status in {"needs_answers", "ready_to_generate", "draft_ready", "failed"})
        has_artifact = bool(_param_value_from_model(model, TASK_LAST_ARTIFACT_PARAM, "").strip())
        self._refresh_html_btn.setEnabled(has_artifact)
        self._index_slides_btn.setEnabled(has_artifact)
        self._write_questions_btn.setEnabled(self._has_prep_questions(report))
        self._summary.setText(
            f"guide: {guide_path or 'unresolved'}\n"
            f"template: {template_path or 'unresolved'}"
        )
        self._report.setHtml(_format_report_text_html(f"status: {status}\n\nClick Run Step to inspect connected Skills and Data Nexus."))

    def _show_report(self, report: Dict[str, Any]) -> None:
        status = str((report or {}).get("status") or "unknown")
        ai = report.get("sales_agent_ai") if isinstance(report, dict) and isinstance(report.get("sales_agent_ai"), dict) else {}
        pending = bool(ai.get("pending"))
        artifact_path = _artifact_path_from_report(report) or _param_value_from_model(getattr(self._node_item, "model", None), TASK_LAST_ARTIFACT_PARAM, "").strip()
        self._set_status_combo_value(status)
        self._status_combo.setEnabled(not pending)
        self._run_btn.setEnabled(not pending)
        self._generate_btn.setEnabled((status in {"needs_answers", "ready_to_generate", "draft_ready", "failed"}) and not pending)
        self._refresh_html_btn.setEnabled(bool(artifact_path) and not pending)
        self._index_slides_btn.setEnabled(bool(artifact_path) and not pending)
        self._write_questions_btn.setEnabled(self._has_prep_questions(report) and not pending)
        self._summary.setText(
            f"guide: {str((report.get('guide') or {}).get('path') or 'unresolved')}\n"
            f"template: {str((report.get('agent_template') or {}).get('path') or 'unresolved')}"
        )
        self._report.setHtml(_format_report_html(report))

    def _finish_async_report(self, report: Dict[str, Any]) -> None:
        if isinstance(report, dict):
            self._show_report(report)

    def _run_step(self) -> None:
        report = run_task_step_with_sales_agent_ai_from_item(self._node_item, on_done=self._finish_async_report)
        self._show_report(report)

    def _generate_draft(self) -> None:
        report = generate_task_draft_from_item(self._node_item, on_done=self._finish_async_report)
        self._show_report(report)

    def _refresh_html(self) -> None:
        report = refresh_task_deck_html_from_item(self._node_item)
        self._show_report(report)

    def _index_slides(self) -> None:
        report = index_task_slides_to_data_nexus_from_item(self._node_item)
        self._show_report(report)

    def _write_questions(self) -> None:
        report = write_task_questions_to_data_nexus_from_item(
            self._node_item,
            use_mediator=True,
            on_done=self._finish_async_report,
        )
        self._show_report(report)

    @staticmethod
    def _has_prep_questions(report: Dict[str, Any]) -> bool:
        if not isinstance(report, dict):
            return False
        questions = []
        for key in ("questions", "refinement_questions"):
            value = report.get(key)
            if isinstance(value, list):
                questions.extend(value)
        return any(
            isinstance(question, dict)
            and str(question.get("point_type") or "").strip().lower() == "prep_question"
            for question in questions
        )

    def _set_status_combo_value(self, status: str) -> None:
        clean = str(status or "").strip().lower() or "created"
        idx = self._status_combo.findData(clean)
        if idx < 0:
            label = clean.replace("_", " ").title()
            self._status_combo.addItem(label, clean)
            idx = self._status_combo.findData(clean)
        self._status_combo.blockSignals(True)
        try:
            if idx >= 0:
                self._status_combo.setCurrentIndex(idx)
        finally:
            self._status_combo.blockSignals(False)

    def _on_status_combo_changed(self, _index: int) -> None:
        value = str(self._status_combo.currentData() or "").strip().lower()
        if value:
            self._set_status(value)

    def _set_status(self, status: str) -> None:
        clean = str(status or "").strip().lower()
        valid = {value for value, _label in TASK_STATUS_CHOICES}
        if clean not in valid:
            return
        report = _report_from_model(self._node_item)
        if report:
            report = dict(report)
            report["status"] = clean
            report["manual_status"] = clean
            _persist_task_report(self._node_item, report, notify_scene=True)
            self._show_report(report)
            return
        _set_param_on_item(self._node_item, TASK_STATUS_PARAM, clean, notify_scene=False)
        _set_param_on_item(self._node_item, STATUS_OUTPUT_PORT, clean, notify_scene=True)
        self._refresh_from_state()


def build_ports(node_item) -> None:
    try:
        setattr(node_item, "_hide_default_output_with_named", True)
        setattr(node_item, "_default_named_input", SKILLS_INPUT_PORT)
        setattr(node_item, "_show_default_input_with_named", True)
    except Exception:
        pass
    for name, default in (
        (TASK_ID_PARAM, ""),
        (TASK_STATUS_PARAM, "created"),
        (TASK_GUIDE_PATH_PARAM, ""),
        (TASK_AGENT_TEMPLATE_PATH_PARAM, ""),
        (TASK_LAST_QUESTIONS_PARAM, "[]"),
        (TASK_LAST_REPORT_PARAM, ""),
        (TASK_LAST_ARTIFACT_PARAM, ""),
        (TASK_SIZE_PARAM, ""),
        (STATUS_OUTPUT_PORT, "created"),
        (QUESTIONS_OUTPUT_PORT, ""),
        (ARTIFACT_OUTPUT_PORT, ""),
    ):
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(node_item)
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(SKILLS_INPUT_PORT)
        node_item.ensure_input(DATA_NEXUS_INPUT_PORT)
        node_item.ensure_input(MEDIATOR_INPUT_PORT)
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output(STATUS_OUTPUT_PORT)
        node_item.ensure_output(QUESTIONS_OUTPUT_PORT)
        node_item.ensure_output(ARTIFACT_OUTPUT_PORT)


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = TaskWidget(node_item, None)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    pad = float(getattr(node_item, "_PADDING", 0.0) or 0.0)
    current_w = float(getattr(node_item, "width", TASK_BODY_W) or TASK_BODY_W)
    current_h = float(getattr(node_item, "height", TASK_BODY_H) or TASK_BODY_H)
    available_h = int(max(0.0, current_h - float(y_cursor) - pad))
    w = max(TASK_BODY_W, int(hint.width()), int(current_w))
    h = max(TASK_BODY_H, int(hint.height()), available_h)
    try:
        proxy.setMinimumSize(w, h)
        proxy.setPreferredSize(w, h)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    bottom_y = int(y_cursor + h)
    try:
        required_height = float(bottom_y) + pad
        if current_w < float(w) or current_h < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = max(current_w, float(w))
            node_item.height = max(current_h, required_height)
            try:
                node_item.update()
            except Exception:
                pass
    except Exception:
        pass
    return bottom_y


TASK_SPEC = Spec(
    stripe_color="#0f766e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
