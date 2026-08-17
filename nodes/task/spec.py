from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from PySide6 import QtCore, QtWidgets

from echograph.services.sales_agent import generate_sales_deck_from_template
from echograph.services.skills_library import scan_skills_library
from nodes.core import Spec


TASK_NODE_KIND = "task"
TASK_NODE_ALIASES = {"agent_task", "task_node", "workflow_task"}
TASK_NODE_KINDS = {TASK_NODE_KIND, *TASK_NODE_ALIASES}

SKILLS_INPUT_PORT = "skills"
DATA_NEXUS_INPUT_PORT = "data_nexus"
STATUS_OUTPUT_PORT = "status"
QUESTIONS_OUTPUT_PORT = "questions"
ARTIFACT_OUTPUT_PORT = "artifact"

TASK_BODY_W = 600
TASK_BODY_H = 340

TASK_STATUS_PARAM = "__task_status"
TASK_ID_PARAM = "__task_id"
TASK_GUIDE_PATH_PARAM = "__task_guide_path"
TASK_AGENT_TEMPLATE_PATH_PARAM = "__task_agent_template_path"
TASK_LAST_QUESTIONS_PARAM = "__task_last_questions_json"
TASK_LAST_REPORT_PARAM = "__task_last_report_json"
TASK_LAST_ARTIFACT_PARAM = "__task_last_artifact_path"
TASK_SIZE_PARAM = "__task_size"

SKILLS_KINDS = {"skills", "skills_library", "skill_library"}
DATA_NEXUS_KINDS = {"data_nexus", "data nexus", "data_graph", "data graph", "nexus"}


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


def _persist_task_report(node_item, report: Dict[str, Any], *, notify_scene: bool = True) -> None:
    status = str(report.get("status") or "created").strip() or "created"
    questions = report.get("questions") or []
    if not isinstance(questions, list):
        questions = []
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
        "\n".join(str(question.get("text", "") or "") for question in questions if isinstance(question, dict)),
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


def run_task_step_from_item(node_item) -> Dict[str, Any]:
    task_id = _task_id_for_item(node_item)
    _, skills_node, data_nexus_node = _connected_task_nodes(node_item)

    checks = {
        "skills_connected": skills_node is not None,
        "data_nexus_connected": data_nexus_node is not None,
        "guide_found": False,
        "template_found": False,
        "points_found": False,
    }
    questions: List[Dict[str, str]] = []
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
        raw_points = bundle.get("points", []) if isinstance(bundle, dict) else []
        points = [dict(point) for point in raw_points if isinstance(point, dict)]
        point_counts = _point_type_counts(points)
        usable_points = sum(
            1
            for point in points
            if str(point.get("content") or point.get("summary") or "").strip() and not _is_question_like_point(point)
        )
        vault_path = str(bundle.get("vault") or "") if isinstance(bundle, dict) else ""
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

    if not checks["skills_connected"] or not checks["data_nexus_connected"] or not checks["guide_found"] or not checks["template_found"]:
        status = "needs_setup"
    elif not checks["points_found"] or usable_points <= 0:
        status = "needs_answers"
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
        },
        "checks": checks,
        "questions": questions,
        "snapshot_warnings": list(snapshot.get("warnings", []) or []) if isinstance(snapshot, dict) else [],
    }

    _persist_task_report(node_item, report)
    return report


def generate_task_draft_from_item(node_item) -> Dict[str, Any]:
    _set_param_on_item(node_item, TASK_STATUS_PARAM, "generating", notify_scene=False)
    _set_param_on_item(node_item, STATUS_OUTPUT_PORT, "generating", notify_scene=True)

    report = run_task_step_from_item(node_item)
    if str(report.get("status") or "").strip().lower() != "ready_to_generate":
        report["generation"] = {
            "ok": False,
            "message": "Task is not ready to generate. Resolve the setup or answer questions first.",
        }
        _persist_task_report(node_item, report)
        return report

    _, _, data_nexus_node = _connected_task_nodes(node_item)
    bundle, bundle_error = _data_nexus_bundle_from_node(data_nexus_node)
    if bundle_error:
        report["status"] = "failed"
        report["questions"] = [
            {
                "question_id": "data_nexus_read",
                "point_type": "setup",
                "text": bundle_error,
            }
        ]
        report["generation"] = {"ok": False, "message": bundle_error}
        _persist_task_report(node_item, report)
        return report

    template = report.get("agent_template") if isinstance(report.get("agent_template"), dict) else {}
    result = generate_sales_deck_from_template(
        str((template or {}).get("path") or ""),
        bundle,
        root=str(report.get("skills_root") or "Skills"),
    )
    result_data = result.to_dict()
    report["generation"] = result_data
    report["artifact"] = result_data
    if result.ok:
        report["status"] = "draft_ready"
        report["questions"] = []
    else:
        report["status"] = "failed"
        report["questions"] = [
            {
                "question_id": "generation_failed",
                "point_type": "generation",
                "text": result.message,
            }
        ]
    _persist_task_report(node_item, report)
    if result.ok:
        _refresh_connected_html_previews(node_item, result.index_path)
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
    generation = _generation_result_from_report(report)
    if generation:
        lines.extend(
            [
                "",
                "artifact:",
                f"  ok: {generation.get('ok')}",
                f"  message: {generation.get('message') or ''}",
                f"  index: {generation.get('index_path') or ''}",
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
        lines.extend(f"  - {question.get('text') or ''}" for question in questions)
    return "\n".join(lines)


class TaskWidget(QtWidgets.QFrame):
    def __init__(self, node_item=None, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self.setObjectName("TaskWidget")
        self.setStyleSheet(
            """
            QFrame#TaskWidget{background:#0f1216;border:1px solid #334155;border-radius:6px;}
            QLabel{color:#e5e7eb;}
            QLabel#TaskTitle{font-weight:600;color:#f8fafc;}
            QLabel#TaskSubtle{color:#94a3b8;}
            QPlainTextEdit{background:#0b1018;color:#dbeafe;border:1px solid #334155;border-radius:4px;padding:6px;}
            QPushButton{background:#1e293b;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:4px 8px;}
            QPushButton:hover{background:#334155;}
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Task")
        title.setObjectName("TaskTitle")
        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("TaskSubtle")
        self._status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        header.addWidget(title, 0)
        header.addWidget(self._status, 1)
        layout.addLayout(header)

        self._summary = QtWidgets.QLabel("")
        self._summary.setObjectName("TaskSubtle")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._report = QtWidgets.QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setMinimumHeight(190)
        layout.addWidget(self._report, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._run_btn = QtWidgets.QPushButton("Run Step")
        self._generate_btn = QtWidgets.QPushButton("Generate Draft")
        self._approve_btn = QtWidgets.QPushButton("Approve")
        self._dismiss_btn = QtWidgets.QPushButton("Dismiss")
        self._run_btn.clicked.connect(self._run_step)
        self._generate_btn.clicked.connect(self._generate_draft)
        self._approve_btn.clicked.connect(lambda _=False: self._set_status("approved"))
        self._dismiss_btn.clicked.connect(lambda _=False: self._set_status("dismissed"))
        actions.addWidget(self._run_btn, 0)
        actions.addWidget(self._generate_btn, 0)
        actions.addWidget(self._approve_btn, 0)
        actions.addWidget(self._dismiss_btn, 0)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._refresh_from_state()

    def sizeHint(self):
        return QtCore.QSize(TASK_BODY_W, TASK_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(440, 280)

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
        self._status.setText(status)
        self._generate_btn.setEnabled(status in {"ready_to_generate", "draft_ready"})
        self._summary.setText(
            f"guide: {guide_path or 'unresolved'}\n"
            f"template: {template_path or 'unresolved'}"
        )
        self._report.setPlainText(_format_report(report) if report else f"status: {status}\n\nClick Run Step to inspect connected Skills and Data Nexus.")

    def _run_step(self) -> None:
        report = run_task_step_from_item(self._node_item)
        self._status.setText(str(report.get("status") or "unknown"))
        self._generate_btn.setEnabled(str(report.get("status") or "").strip().lower() in {"ready_to_generate", "draft_ready"})
        self._summary.setText(
            f"guide: {str((report.get('guide') or {}).get('path') or 'unresolved')}\n"
            f"template: {str((report.get('agent_template') or {}).get('path') or 'unresolved')}"
        )
        self._report.setPlainText(_format_report(report))

    def _generate_draft(self) -> None:
        report = generate_task_draft_from_item(self._node_item)
        status = str(report.get("status") or "unknown")
        self._status.setText(status)
        self._generate_btn.setEnabled(status in {"ready_to_generate", "draft_ready"})
        self._summary.setText(
            f"guide: {str((report.get('guide') or {}).get('path') or 'unresolved')}\n"
            f"template: {str((report.get('agent_template') or {}).get('path') or 'unresolved')}"
        )
        self._report.setPlainText(_format_report(report))

    def _set_status(self, status: str) -> None:
        clean = str(status or "").strip().lower()
        if clean not in {"approved", "dismissed"}:
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
