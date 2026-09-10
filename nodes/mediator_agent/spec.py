from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore, QtGui
from echograph.services import codex_cli_runner


MEDIGATOR_NODE_KIND = "mediator_agent"
MEDIGATOR_NODE_ALIASES = [
    "medigator_agent",
    "medigator",
    "medigator agent",
    "mediator",
    "mediator agent",
]
MEDIGATOR_NODE_KINDS = {MEDIGATOR_NODE_KIND, *MEDIGATOR_NODE_ALIASES}

MEDIGATOR_BODY_W = 560
MEDIGATOR_BODY_H = 340
MEDIGATOR_MEMORY_ROOT = "mediator_agents"
MEDIGATOR_HIDDEN_PARAM_KEY = "__ui_hidden_params"
MEDIGATOR_PROMPT_PROFILE_PARAM = "__prompt_profile"
MEDIGATOR_OUTPUT_TOKEN_PARAM = "__medigator_output_token"
MEDIGATOR_SPEECH_TEXT_PARAM = "__medigator_speech_text"
MEDIGATOR_SPEECH_TOKEN_PARAM = "__medigator_speech_token"
MEDIGATOR_SPEECH_SESSION_TOKEN_PARAM = "__medigator_speech_session_token"
MEDIGATOR_DEFAULT_PROMPT_PROFILE = "default_mediator"
MEDIGATOR_DEFAULT_SYSTEM_PROMPT = (
    # Fallback used only when prompt profile files are missing or empty.
    "You are Mediator, a conversation mediator. "
    "Use the provided history and latest voice input to craft the next assistant reply. "
    "Return only the assistant response text."
)
MEDIGATOR_MAX_SYSTEM_CHARS = 4000
MEDIGATOR_MAX_HISTORY_CHARS = 24000
MEDIGATOR_MAX_VOICE_CHARS = 8000
MEDIGATOR_MAX_OUTPUT_CONTEXT_CHARS = 2000
MEDIGATOR_MAX_PROMPT_LOG_FILES = 15
MEDIGATOR_MAX_CONSOLE_LOG_LINES = 5000
MEDIGATOR_CONSOLE_FLUSH_INTERVAL_MS = 100
MEDIGATOR_CONSOLE_MAX_LINES_PER_FLUSH = 200
MEDIGATOR_CONSOLE_LOG_PRUNE_INTERVAL_MS = 5000
MEDIGATOR_CODEX_MODEL = "gpt-5.5"
VOICE_ACTOR_KINDS = {"voice_actor", "voice actor", "voiceactor"}
VOICE_ACTOR_LANGUAGE_PARAM = "__voice_actor_stt_language"
VOICE_ACTOR_SEND_TOKEN_PARAM = "__voice_actor_send_token"
VOICE_ACTOR_LANGUAGE_LABELS = {
    "en": "English",
    "es": "Spanish",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
}
TRANSLATOR_DOWNSTREAM_PASS_THROUGH_KINDS = {"python", "output", "wire", "switch"}
QDECK_CONTROLLER_KINDS = {
    "qubit_deck_controller",
    "qubit deck controller",
    "qubitdeckcontroller",
    "qubitdeck controller",
}
SYSTEM_PROMPT_KINDS = {"llm_prompt", "gpt_prompt", "prompt", "system_prompt"}
CODEX_SANDBOX_KINDS = {"codex_sandbox", "codex sandbox", "sandbox"}
DATA_NEXUS_KINDS = {"data_nexus", "data nexus", "data_graph", "data graph", "nexus"}
CHATBOT_KINDS = {"chatbot", "chat bot", "chat_bot"}
QDECK_PROMPT_PROFILE = "qubit_deck_controller"
QDECK_DEFAULT_API_BASE = "http://127.0.0.1:8765"
QDECK_CONTEXT_MAX_ROWS = 220
QDECK_CONNECTION_RECOVERY_HINT = (
    "Refresh the Qubit Deck app to reestablish the connection, then try again."
)
QDECK_CONNECTION_USER_MESSAGE = f"Qubit Deck app API is not reachable. {QDECK_CONNECTION_RECOVERY_HINT}"
AGENT_POPUP_MIN_WIDTH = 520
AGENT_POPUP_RADIUS = 8
AGENT_POPUP_BORDER_WIDTH = 2
AGENT_POPUP_TITLE_RADIUS = max(0, AGENT_POPUP_RADIUS - AGENT_POPUP_BORDER_WIDTH)
AGENT_POPUP_CONTENT_MARGIN_X = 14
AGENT_POPUP_DETAIL_TEXT_WIDTH = (
    AGENT_POPUP_MIN_WIDTH
    - (AGENT_POPUP_BORDER_WIDTH * 2)
    - (AGENT_POPUP_CONTENT_MARGIN_X * 2)
)
AGENT_POPUP_CLOSE_ICON_COLOR = "#d1d5db"
SECURITY_GUARD_PROMPT_PROFILE = "security_guard"
SECURITY_GUARD_POPUP_NAME = "Security Guard popup"
TANYA_PROMPT_PROFILE = "assistant_tanya"
TEACHER_AGENT_PROMPT_PROFILE = "teacher_agent"
SALES_AGENT_PROMPT_PROFILE = "sales_agent"
WEB_DESIGNER_PROMPT_PROFILE = "web_designer"
TRANSLATOR_PROMPT_PROFILE = "translator"
MEDIATOR_PLANNER_PROMPT_PROFILE = "mediator_planner"
JAPANESE_READER_PROMPT_PROFILE = "japanese_reader"
KOREAN_READER_PROMPT_PROFILE = "korean_reader"
CHINESE_READER_PROMPT_PROFILE = "chinese_reader"
WEB_DESIGNER_ADVISORY_RE = re.compile(
    r"\b("
    r"what do you suggest|what would you suggest|what do you recommend|what would you recommend|"
    r"what would you change|what should we use|what can we use|give me options|"
    r"does this look|what do you think|why\b|how would you|review this|feedback|opinion"
    r")\b",
    re.IGNORECASE,
)
WEB_DESIGNER_IMPLEMENTATION_RE = re.compile(
    r"\b(make|fix|update|replace|add|remove|redesign|implement|apply|create|build|edit)\b",
    re.IGNORECASE,
)
WEB_DESIGNER_APPROVAL_RE = re.compile(
    r"\b(go ahead|do that|yes do|yes, do|apply that|implement that|make that change|use option)\b",
    re.IGNORECASE,
)
MEDIGATOR_PROMPT_PROFILE_ALIASES = {
    "romantic_dark_assistant": TANYA_PROMPT_PROFILE,
    "teacher": TEACHER_AGENT_PROMPT_PROFILE,
    "teacher_agent": TEACHER_AGENT_PROMPT_PROFILE,
    "template_teacher": TEACHER_AGENT_PROMPT_PROFILE,
    "template_compiler": TEACHER_AGENT_PROMPT_PROFILE,
    "sales": SALES_AGENT_PROMPT_PROFILE,
    "sales_agent": SALES_AGENT_PROMPT_PROFILE,
    "pitch_deck_sales_agent": SALES_AGENT_PROMPT_PROFILE,
    "sales_pitch_deck_agent": SALES_AGENT_PROMPT_PROFILE,
    "web": WEB_DESIGNER_PROMPT_PROFILE,
    "website": WEB_DESIGNER_PROMPT_PROFILE,
    "web_design": WEB_DESIGNER_PROMPT_PROFILE,
    "web_designer": WEB_DESIGNER_PROMPT_PROFILE,
    "website_design": WEB_DESIGNER_PROMPT_PROFILE,
    "website_designer": WEB_DESIGNER_PROMPT_PROFILE,
    "website_editor": WEB_DESIGNER_PROMPT_PROFILE,
    "web_editor": WEB_DESIGNER_PROMPT_PROFILE,
    "frontend": WEB_DESIGNER_PROMPT_PROFILE,
    "frontend_agent": WEB_DESIGNER_PROMPT_PROFILE,
    "frontend_designer": WEB_DESIGNER_PROMPT_PROFILE,
    "html_editor": WEB_DESIGNER_PROMPT_PROFILE,
    "translator_agent": TRANSLATOR_PROMPT_PROFILE,
    "translation_agent": TRANSLATOR_PROMPT_PROFILE,
    "japanese_agent": JAPANESE_READER_PROMPT_PROFILE,
    "japanese_speaker": JAPANESE_READER_PROMPT_PROFILE,
    "japanese_interpreter": JAPANESE_READER_PROMPT_PROFILE,
    "japanese_translator": JAPANESE_READER_PROMPT_PROFILE,
    "japanese_translation": JAPANESE_READER_PROMPT_PROFILE,
    "english_to_japanese": JAPANESE_READER_PROMPT_PROFILE,
    "japanese_to_english": JAPANESE_READER_PROMPT_PROFILE,
    "jp_reader": JAPANESE_READER_PROMPT_PROFILE,
    "jp_translator": JAPANESE_READER_PROMPT_PROFILE,
    "korean_agent": KOREAN_READER_PROMPT_PROFILE,
    "korean_speaker": KOREAN_READER_PROMPT_PROFILE,
    "korean_interpreter": KOREAN_READER_PROMPT_PROFILE,
    "korean_translator": KOREAN_READER_PROMPT_PROFILE,
    "korean_translation": KOREAN_READER_PROMPT_PROFILE,
    "english_to_korean": KOREAN_READER_PROMPT_PROFILE,
    "kr_reader": KOREAN_READER_PROMPT_PROFILE,
    "ko_reader": KOREAN_READER_PROMPT_PROFILE,
    "ko_translator": KOREAN_READER_PROMPT_PROFILE,
    "chinese_agent": CHINESE_READER_PROMPT_PROFILE,
    "chinese_speaker": CHINESE_READER_PROMPT_PROFILE,
    "chinese_interpreter": CHINESE_READER_PROMPT_PROFILE,
    "chinese_translator": CHINESE_READER_PROMPT_PROFILE,
    "chinese_translation": CHINESE_READER_PROMPT_PROFILE,
    "english_to_chinese": CHINESE_READER_PROMPT_PROFILE,
    "cn_reader": CHINESE_READER_PROMPT_PROFILE,
    "zh_reader": CHINESE_READER_PROMPT_PROFILE,
    "zh_translator": CHINESE_READER_PROMPT_PROFILE,
    "data_nexus": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "data_nexus_agent": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "nexus_agent": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "mediator_planner_agent": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "planner_agent": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "judge": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "judge_agent": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "medic": MEDIATOR_PLANNER_PROMPT_PROFILE,
    "medic_agent": MEDIATOR_PLANNER_PROMPT_PROFILE,
}
SECURITY_AGENT_ICON_FILENAMES = ("ScurityAgent_Icon.png", "SecurityAgent_Icon.png")
OPERATOR_AGENT_ICON_FILENAMES = ("ITOperatorAgent_Icon.png", "OperatorAgent_Icon.png")
TANYA_AGENT_ICON_FILENAMES = ("AssistentTanyaAgent_Icon.png", "AssistantTanyaAgent_Icon.png", "TanyaAI_Icon.png")
WEB_DESIGNER_AGENT_ICON_FILENAMES = ("WebDesignerAgent_Icon.png",)
MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM = "__pending_security_request"
MEDIGATOR_PENDING_SECURITY_REQUESTER_NODE_PARAM = "__pending_security_requester_node"
MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM = "__pending_security_user_input"
MEDIGATOR_PENDING_SECURITY_SIGNATURE_PARAM = "__pending_security_signature"
MEDIGATOR_CODEX_RESPONSE_SOURCES = {
    "auto",
    "manual",
    "data_nexus_planning",
    "teacher_agent",
    "sales_agent",
    "security_request",
    "security_approval",
}
SECURITY_HIDDEN_PARAMS = [
    MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM,
    MEDIGATOR_PENDING_SECURITY_REQUESTER_NODE_PARAM,
    MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM,
    MEDIGATOR_PENDING_SECURITY_SIGNATURE_PARAM,
]
SECURITY_REQUEST_RE = re.compile(
    r"<security_request\b(?P<attrs>[^>]*)>.*?</security_request>",
    re.IGNORECASE | re.DOTALL,
)
SECURITY_APPROVAL_RE = re.compile(
    r"<security_approval\b(?P<attrs>[^>]*)>.*?</security_approval>",
    re.IGNORECASE | re.DOTALL,
)
DATA_NEXUS_UPDATE_RE = re.compile(
    r"<data_nexus_update\b[^>]*>(?P<payload>.*?)</data_nexus_update>",
    re.IGNORECASE | re.DOTALL,
)
SALES_AGENT_TASK_RE = re.compile(
    r"<sales_agent_(?:review|questions|draft)\b[^>]*>.*?</sales_agent_(?:review|questions|draft)>",
    re.IGNORECASE | re.DOTALL,
)
GENERIC_PERMISSION_RESPONSE_RE = re.compile(
    r"^\s*i\s+need\s+(?:your\s+)?permission\s+(?:to\s+proceed|before\s+i\s+proceed)\.?\s*$",
    re.IGNORECASE,
)
SECURITY_ATTR_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(['\"])(.*?)\2",
    re.DOTALL,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _prompts_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def _normalize_prompt_profile(value: str) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    cleaned = []
    for ch in raw:
        if ch.isalnum() or ch in ("_", "-"):
            cleaned.append(ch)
    token = "".join(cleaned).strip("._-")
    token = MEDIGATOR_PROMPT_PROFILE_ALIASES.get(token, token)
    return token or MEDIGATOR_DEFAULT_PROMPT_PROFILE


def _prompt_profile_path(profile: str) -> Path:
    token = _normalize_prompt_profile(profile)
    return _prompts_dir() / f"{token}.md"


def _available_prompt_profiles() -> list[str]:
    out: list[str] = []
    prompts_dir = _prompts_dir()
    if prompts_dir.exists():
        try:
            for path in sorted(prompts_dir.glob("*.md")):
                token = _normalize_prompt_profile(path.stem)
                if token and token not in out:
                    out.append(token)
        except Exception:
            pass
    if MEDIGATOR_DEFAULT_PROMPT_PROFILE not in out:
        out.insert(0, MEDIGATOR_DEFAULT_PROMPT_PROFILE)
    elif out and out[0] != MEDIGATOR_DEFAULT_PROMPT_PROFILE:
        out = [MEDIGATOR_DEFAULT_PROMPT_PROFILE, *[p for p in out if p != MEDIGATOR_DEFAULT_PROMPT_PROFILE]]
    return out


def _resolve_prompt_profile(value: str, known: list[str] | None = None) -> str:
    token = _normalize_prompt_profile(value)
    if known:
        if token in known:
            return token
        if MEDIGATOR_DEFAULT_PROMPT_PROFILE in known:
            return MEDIGATOR_DEFAULT_PROMPT_PROFILE
        return known[0]
    return token or MEDIGATOR_DEFAULT_PROMPT_PROFILE


def _read_prompt_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _load_prompt_profile_text(profile: str) -> str:
    chosen = _read_prompt_file(_prompt_profile_path(profile))
    if chosen:
        return chosen
    fallback = _read_prompt_file(_prompt_profile_path(MEDIGATOR_DEFAULT_PROMPT_PROFILE))
    if fallback:
        return fallback
    return MEDIGATOR_DEFAULT_SYSTEM_PROMPT


def _hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    kwargs = {}
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        try:
            creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW"))
        except Exception:
            creationflags = creationflags
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        try:
            startupinfo = subprocess.STARTUPINFO()
            if hasattr(subprocess, "STARTF_USESHOWWINDOW"):
                startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW"))
            if hasattr(subprocess, "SW_HIDE"):
                startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE"))
        except Exception:
            startupinfo = None
    if creationflags:
        kwargs["creationflags"] = creationflags
    if startupinfo is not None:
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _sanitize_folder_name(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        text = "mediator"
    text = text.replace("medigator", "mediator")
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "mediator"


def _workspace_dir_for_node(node_item) -> Path:
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    safe_name = _sanitize_folder_name(node_name)
    workspace = _repo_root() / "logs" / MEDIGATOR_MEMORY_ROOT / safe_name
    workspace.mkdir(parents=True, exist_ok=True)
    memory_file = workspace / "memory.md"
    if not memory_file.exists():
        memory_file.write_text(
            "# Mediator Memory\n\n"
            "This folder is dedicated to this node.\n"
            "Store notes, summaries, and agent context here.\n",
            encoding="utf-8",
        )
    return workspace


def _prune_mediator_prompt_logs(workspace: Path, *, keep: int = MEDIGATOR_MAX_PROMPT_LOG_FILES) -> None:
    try:
        root = Path(workspace).resolve()
    except Exception:
        return
    if not root.exists() or not root.is_dir():
        return
    try:
        files = [
            path
            for pattern in ("mediator_prompt_*.md", "medigator_prompt_*.md")
            for path in root.glob(pattern)
            if path.is_file()
        ]
    except Exception:
        return
    if len(files) <= max(0, int(keep)):
        return

    def _sort_key(path: Path) -> tuple[float, str]:
        try:
            stamp = float(path.stat().st_mtime)
        except Exception:
            stamp = 0.0
        return stamp, path.name

    for path in sorted(files, key=_sort_key, reverse=True)[max(0, int(keep)):]:
        try:
            if path.resolve().parent != root:
                continue
            path.unlink()
        except Exception:
            pass


def _prune_text_log_tail(path: Path, *, keep_lines: int) -> None:
    limit = max(0, int(keep_lines))
    if limit <= 0:
        return
    try:
        log_path = Path(path)
        if not log_path.exists() or not log_path.is_file():
            return
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return
    if len(lines) <= limit:
        return
    try:
        log_path.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _format_ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ordered_in_edges(scene, node_item) -> list:
    if not scene or not node_item:
        return []
    try:
        ordered = list(scene._ordered_in_edges(node_item))
        if ordered:
            return ordered
    except Exception:
        pass
    try:
        return list(scene._in_edges(node_item))
    except Exception:
        return []


def _ordered_out_edges(scene, node_item) -> list:
    if not scene or not node_item:
        return []
    try:
        edges = list(getattr(scene, "_edges", []) or [])
    except Exception:
        return []
    return [edge for edge in edges if getattr(edge, "src", None) is node_item]


def _edge_port_name(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        if hasattr(edge, attr):
            raw = getattr(edge, attr)
            if raw is not None:
                text = str(raw).strip()
                if text:
                    return text
    return ""


def _input_edges(scene, node_item, port_name: str) -> list:
    edges = _ordered_in_edges(scene, node_item)
    if not edges:
        return []
    target = (port_name or "").strip().lower()
    if not target:
        return edges
    out = []
    for edge in edges:
        if _edge_port_name(edge).strip().lower() == target:
            out.append(edge)
    return out


def _text_from_edges(scene, edges: list) -> str:
    if not scene or not edges:
        return ""

    parts = []
    for edge in edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        src_kind = _kind_of_item(src)
        if src_kind in VOICE_ACTOR_KINDS:
            text = _voice_actor_transcript(src)
        elif src_kind in QDECK_CONTROLLER_KINDS:
            text = _qdeck_context_text(scene, src)
        elif src_kind in CHATBOT_KINDS:
            try:
                from nodes.chatbot import spec as chatbot_spec
                helper = getattr(chatbot_spec, "chatbot_history_context_from_item", None)
                if callable(helper):
                    text = str(helper(scene, src) or "").strip()
                else:
                    text = ""
            except Exception:
                text = ""
            if not text:
                try:
                    text = scene.resolve_text_value(src)
                except Exception:
                    text = ""
        else:
            try:
                text = scene.resolve_text_value(src)
            except Exception:
                text = ""
        if text:
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _param_value_from_item(node_item, name: str, default: str = "") -> str:
    model = getattr(node_item, "model", None)
    target = str(name or "").strip().lower()
    if not target or model is None:
        return str(default or "")
    for entry in (getattr(model, "params", None) or []):
        key = str(entry.get("name", "") or "").strip().lower()
        if key == target:
            return str(entry.get("value", "") or "")
    return str(default or "")


def _normalize_voice_actor_language(value: str) -> str:
    key = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "english": "en",
        "en-us": "en",
        "en-gb": "en",
        "spanish": "es",
        "es-es": "es",
        "es-us": "es",
        "es-mx": "es",
        "japanese": "ja",
        "jp": "ja",
        "ja-jp": "ja",
        "korean": "ko",
        "kr": "ko",
        "ko-kr": "ko",
        "chinese": "zh",
        "mandarin": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-hans-cn": "zh",
        "cmn-hans-cn": "zh",
    }
    key = aliases.get(key, key)
    if key in VOICE_ACTOR_LANGUAGE_LABELS:
        return key
    return "en"


def _voice_actor_language_from_item(node_item) -> tuple[str, str]:
    raw = _param_value_from_item(node_item, VOICE_ACTOR_LANGUAGE_PARAM, "en")
    code = _normalize_voice_actor_language(raw)
    return code, VOICE_ACTOR_LANGUAGE_LABELS.get(code, "English")


def _parse_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _qdeck_api_base_from_item(node_item) -> str:
    return _param_value_from_item(node_item, "api_base", QDECK_DEFAULT_API_BASE).strip() or QDECK_DEFAULT_API_BASE


def _qdeck_connection_failure_message(exc: Exception | str = "") -> str:
    detail = str(exc or "").strip()
    if detail:
        return f"{QDECK_CONNECTION_USER_MESSAGE} Detail: {detail}"
    return QDECK_CONNECTION_USER_MESSAGE


def _qdeck_request_json(api_base: str, path: str, *, timeout: float = 2.5) -> dict:
    base = str(api_base or "").strip() or QDECK_DEFAULT_API_BASE
    request = Request(
        url=urljoin((base.rstrip("/") + "/"), path.lstrip("/")),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
    except HTTPError as ex:
        detail = ex.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {ex.code}: {detail or ex.reason}") from ex
    except URLError as ex:
        raise RuntimeError(f"Connection failed: {ex.reason}") from ex

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as ex:
        raise RuntimeError(f"Invalid JSON response: {ex}") from ex

    return parsed if isinstance(parsed, dict) else {"buttons": parsed}


def _qdeck_health_issue_for_item(node_item) -> str:
    api_base = _qdeck_api_base_from_item(node_item)
    try:
        _qdeck_request_json(api_base, "api/health", timeout=1.75)
        return ""
    except Exception as exc:
        return _qdeck_connection_failure_message(exc)


def _qdeck_fetch_buttons(api_base: str) -> list[dict]:
    payload = _qdeck_request_json(api_base, "api/buttons", timeout=2.5)

    if isinstance(payload, dict):
        buttons = payload.get("buttons", [])
    elif isinstance(payload, list):
        buttons = payload
    else:
        buttons = []
    out: list[dict] = []
    for row in buttons:
        if isinstance(row, dict):
            out.append(row)
    return out


def _qdeck_button_label(button: dict) -> str:
    slot_idx = _parse_int(button.get("index"))
    slot_text = "slot n/a" if slot_idx is None else f"slot {slot_idx + 1}"
    name = str(button.get("name", "") or "").strip() or "<unnamed>"
    display_name = str(button.get("displayName", "") or "").strip()
    if display_name and display_name != name:
        return f"{slot_text}: {name} ({display_name})"
    return f"{slot_text}: {name}"


def _qdeck_summarize_buttons(buttons: list[dict], *, max_rows: int = QDECK_CONTEXT_MAX_ROWS) -> str:
    if not buttons:
        return "Buttons: 0"
    lines = [f"Buttons: {len(buttons)}"]
    for idx, button in enumerate(buttons[:max_rows], 1):
        lines.append(f"{idx}. {_qdeck_button_label(button)}")
    if len(buttons) > max_rows:
        lines.append(f"... {len(buttons) - max_rows} more")
    return "\n".join(lines)


def _qdeck_context_text(scene, node_item) -> str:
    api_base = _qdeck_api_base_from_item(node_item)
    try:
        buttons = _qdeck_fetch_buttons(api_base)
        return _qdeck_summarize_buttons(buttons)
    except Exception as exc:
        failure_text = _qdeck_connection_failure_message(exc)

    model = getattr(node_item, "model", None)
    info_text = str(getattr(model, "info", "") or "").strip() if model is not None else ""
    if info_text:
        return "\n".join([failure_text, "Previous Qubit Deck status:", info_text]).strip()

    return failure_text


def _connected_qdeck_controller_item(scene, node_item):
    if scene is None or node_item is None:
        return None
    for edge in _ordered_in_edges(scene, node_item):
        src = getattr(edge, "src", None)
        if src is not None and _kind_of_item(src) in QDECK_CONTROLLER_KINDS:
            return src
    return None


def _qdeck_context_issue(scene, node_item) -> str:
    qdeck_item = _connected_qdeck_controller_item(scene, node_item)
    if qdeck_item is None:
        return "Connect a Qubit Deck Controller node to provide deck context."
    api_base = _param_value_from_item(qdeck_item, "api_base", "").strip()
    if not api_base:
        return "Set the API URL on the connected Qubit Deck Controller node."
    if not (api_base.lower().startswith("http://") or api_base.lower().startswith("https://")):
        return "Set a valid http:// or https:// API URL on the connected Qubit Deck Controller node."
    return ""


def _connected_codex_sandbox_item(scene, node_item):
    if scene is None or node_item is None:
        return None
    for edge in _ordered_in_edges(scene, node_item):
        src = getattr(edge, "src", None)
        if src is None or _kind_of_item(src) not in CODEX_SANDBOX_KINDS:
            continue
        port = _edge_port_name(edge).strip().lower()
        if not port or port == "sandbox":
            return src
    return None


def _codex_sandbox_context_text(scene, node_item) -> str:
    sandbox_item = _connected_codex_sandbox_item(scene, node_item)
    if sandbox_item is None:
        return ""
    try:
        from nodes.codex_sandbox import spec as sandbox_spec
        summary = getattr(sandbox_spec, "sandbox_summary_text", None)
        if callable(summary):
            return str(summary(sandbox_item) or "").strip()
    except Exception:
        pass
    try:
        return str(scene.resolve_text_value(sandbox_item) or "").strip()
    except Exception:
        return ""


def _codex_sandbox_config(scene, node_item) -> dict:
    sandbox_item = _connected_codex_sandbox_item(scene, node_item)
    if sandbox_item is None:
        return {}
    try:
        from nodes.codex_sandbox import spec as sandbox_spec
        config_from_item = getattr(sandbox_spec, "sandbox_config_from_item", None)
        if callable(config_from_item):
            return dict(config_from_item(sandbox_item) or {})
    except Exception:
        return {}
    return {}


def _resolve_configured_path(value: str, *, base: Path | None = None) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    if not path.is_absolute():
        path = (base or _repo_root()) / path
    try:
        return path.resolve(strict=False)
    except Exception:
        return path.absolute()


def _is_same_or_child_path(path: Path, parent: Path) -> bool:
    try:
        child = str(path.resolve(strict=False)).lower().rstrip("\\/")
        root = str(parent.resolve(strict=False)).lower().rstrip("\\/")
    except Exception:
        child = str(path).lower().rstrip("\\/")
        root = str(parent).lower().rstrip("\\/")
    return child == root or child.startswith(root + os.sep.lower()) or child.startswith(root + "/")


def _resolve_codex_runtime_config(scene, node_item, workspace_dir: Path) -> dict:
    sandbox_config = _codex_sandbox_config(scene, node_item)
    project_root = _resolve_configured_path(str(sandbox_config.get("project_root") or ""))
    working_dir = _resolve_configured_path(str(sandbox_config.get("working_directory") or "")) or project_root
    if working_dir is None:
        working_dir = Path(workspace_dir).resolve(strict=False)

    if not working_dir.exists():
        logs_root = (_repo_root() / "logs").resolve(strict=False)
        if _is_same_or_child_path(working_dir, logs_root):
            working_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise RuntimeError(f"Codex working directory does not exist: {working_dir}")
    if not working_dir.is_dir():
        raise RuntimeError(f"Codex working directory is not a folder: {working_dir}")

    return {
        "codex_executable": str(sandbox_config.get("codex_executable") or ""),
        "model": str(sandbox_config.get("model") or MEDIGATOR_CODEX_MODEL),
        "model_reasoning_effort": codex_cli_runner.normalize_reasoning_effort(
            str(sandbox_config.get("model_reasoning_effort") or "")
        ),
        "sandbox_mode": codex_cli_runner.normalize_sandbox_mode(str(sandbox_config.get("sandbox_mode") or "")),
        "approval_policy": codex_cli_runner.normalize_approval_policy(
            str(sandbox_config.get("approval_policy") or "")
        ),
        "codex_home": str(sandbox_config.get("codex_home") or ""),
        "network_access": codex_cli_runner.normalize_bool(sandbox_config.get("network_access")),
        "extra_args": str(sandbox_config.get("extra_args") or ""),
        "writable_roots": str(sandbox_config.get("writable_roots") or ""),
        "working_directory": working_dir,
        "log_directory": Path(workspace_dir).resolve(strict=False),
        "has_sandbox_node": bool(sandbox_config),
    }


def _data_nexus_context_text(scene, node_item) -> str:
    parts = []
    try:
        edges = _ordered_in_edges(scene, node_item)
    except Exception:
        edges = []
    for edge in edges:
        src = getattr(edge, "src", None)
        if src is None or _kind_of_item(src) not in DATA_NEXUS_KINDS:
            continue
        try:
            from nodes.data_nexus import spec as data_nexus_spec
            helper = getattr(data_nexus_spec, "data_nexus_context_from_item", None)
            if callable(helper):
                text = str(helper(src) or "").strip()
            else:
                text = ""
        except Exception:
            text = ""
        if not text:
            try:
                text = str(scene.resolve_text_value(src) or "").strip()
            except Exception:
                text = ""
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _connected_data_nexus_item(scene, node_item):
    if scene is None or node_item is None:
        return None
    named_edges = _input_edges(scene, node_item, "data_nexus")
    for edge in named_edges:
        src = getattr(edge, "src", None)
        if src is not None and _kind_of_item(src) in DATA_NEXUS_KINDS:
            return src
    for edge in _ordered_in_edges(scene, node_item):
        src = getattr(edge, "src", None)
        if src is not None and _kind_of_item(src) in DATA_NEXUS_KINDS:
            return src
    return None


def _text_from_input(scene, node_item, port_name: str, *, allowed_kinds=None) -> str:
    named_edges = _input_edges(scene, node_item, port_name)
    if allowed_kinds is not None:
        allowed = {str(k or "").strip().lower() for k in allowed_kinds if str(k or "").strip()}
        named_edges = [
            edge
            for edge in named_edges
            if _kind_of_item(getattr(edge, "src", None)) in allowed
        ]
    return _text_from_edges(scene, named_edges)


def _default_input_role_for_kind(kind: str, *, unknown_role: str | None = "chatbot_history") -> str | None:
    key = str(kind or "").strip().lower()
    if key in VOICE_ACTOR_KINDS:
        return "voice_input"
    if key in QDECK_CONTROLLER_KINDS:
        return "chatbot_history"
    if key in DATA_NEXUS_KINDS:
        return "chatbot_history"
    if key in SYSTEM_PROMPT_KINDS:
        return "system_prompt"
    return unknown_role


def _kind_allowed(kind: str, allowed_kinds) -> bool:
    if allowed_kinds is None:
        return True
    allowed = {str(k or "").strip().lower() for k in allowed_kinds if str(k or "").strip()}
    return str(kind or "").strip().lower() in allowed


def _input_policy_for_profile(profile: str) -> dict:
    token = _normalize_prompt_profile(profile)
    if token == QDECK_PROMPT_PROFILE:
        # Qubit Deck profile should only ingest voice input + deck context + optional prompt nodes.
        return {
            "prefer_named": False,
            "voice_kinds": set(VOICE_ACTOR_KINDS),
            "history_kinds": set(QDECK_CONTROLLER_KINDS),
            "system_kinds": set(SYSTEM_PROMPT_KINDS),
            "data_nexus_kinds": set(),
            "unknown_default_role": None,
        }
    return {
        "prefer_named": True,
        "voice_kinds": None,
        "history_kinds": None,
        "system_kinds": None,
        "data_nexus_kinds": None,
        "unknown_default_role": "chatbot_history",
    }


def _collect_inputs_from_named_ports(scene, node_item, profile: str) -> tuple[str, str, str]:
    policy = _input_policy_for_profile(profile)
    system_prompt = _text_from_input(
        scene, node_item, "system_prompt", allowed_kinds=policy.get("system_kinds")
    )
    chatbot_history = _text_from_input(
        scene, node_item, "chatbot_history", allowed_kinds=policy.get("history_kinds")
    )
    data_nexus_context = _text_from_input(
        scene, node_item, "data_nexus", allowed_kinds=policy.get("data_nexus_kinds")
    )
    if data_nexus_context:
        chatbot_history = "\n\n".join(
            part for part in (chatbot_history, f"Data Nexus context:\n{data_nexus_context}") if part
        ).strip()
    voice_input = _text_from_input(
        scene, node_item, "voice_input", allowed_kinds=policy.get("voice_kinds")
    )
    return system_prompt, chatbot_history, voice_input


def _collect_inputs_from_default_pin(scene, node_item, profile: str) -> tuple[str, str, str]:
    policy = _input_policy_for_profile(profile)
    edges = [e for e in _ordered_in_edges(scene, node_item) if not _edge_port_name(e)]
    buckets: dict[str, list[str]] = {
        "system_prompt": [],
        "chatbot_history": [],
        "voice_input": [],
    }
    for edge in edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        kind = _kind_of_item(src)
        role = _default_input_role_for_kind(kind, unknown_role=policy.get("unknown_default_role"))
        if not role:
            continue
        if role == "voice_input" and not _kind_allowed(kind, policy.get("voice_kinds")):
            continue
        if role == "chatbot_history" and not _kind_allowed(kind, policy.get("history_kinds")):
            continue
        if role == "system_prompt" and not _kind_allowed(kind, policy.get("system_kinds")):
            continue
        text = _text_from_edges(scene, [edge])
        if not text:
            continue
        buckets[role].append(text.strip())
    return (
        "\n\n".join(buckets["system_prompt"]).strip(),
        "\n\n".join(buckets["chatbot_history"]).strip(),
        "\n\n".join(buckets["voice_input"]).strip(),
    )


def _has_named_input_edges(scene, node_item) -> bool:
    for edge in _ordered_in_edges(scene, node_item):
        if _edge_port_name(edge):
            return True
    return False


def _collectable_in_edges(scene, node_item, profile: str) -> list:
    policy = _input_policy_for_profile(profile)
    edges = _ordered_in_edges(scene, node_item)
    named_edges = [e for e in edges if _edge_port_name(e)]
    if bool(policy.get("prefer_named", True)) and named_edges:
        out = []
        for edge in named_edges:
            src = getattr(edge, "src", None)
            if src is None:
                continue
            kind = _kind_of_item(src)
            port = _edge_port_name(edge).strip().lower()
            if port == "voice_input" and _kind_allowed(kind, policy.get("voice_kinds")):
                out.append(edge)
            elif port == "chatbot_history" and _kind_allowed(kind, policy.get("history_kinds")):
                out.append(edge)
            elif port == "data_nexus" and _kind_allowed(kind, policy.get("data_nexus_kinds")):
                out.append(edge)
            elif port == "system_prompt" and _kind_allowed(kind, policy.get("system_kinds")):
                out.append(edge)
        return out

    out = []
    for edge in edges:
        if _edge_port_name(edge):
            continue
        src = getattr(edge, "src", None)
        if src is None:
            continue
        kind = _kind_of_item(src)
        role = _default_input_role_for_kind(kind, unknown_role=policy.get("unknown_default_role"))
        if not role:
            continue
        if role == "voice_input" and not _kind_allowed(kind, policy.get("voice_kinds")):
            continue
        if role == "chatbot_history" and not _kind_allowed(kind, policy.get("history_kinds")):
            continue
        if role == "system_prompt" and not _kind_allowed(kind, policy.get("system_kinds")):
            continue
        out.append(edge)
    return out


def _node_name(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "name", "") or "").strip().lower()


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _voice_actor_transcript(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "info", "") or "").strip()


def _voice_actor_mode_from_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    for entry in (getattr(model, "params", None) or []):
        key = str(entry.get("name", "") or "").strip().lower()
        if key != "__voice_actor_mode":
            continue
        value = str(entry.get("value", "") or "").strip().lower()
        return value or "voice_to_text"
    return "voice_to_text"


def _voice_actor_send_token(node_item) -> str:
    model = getattr(node_item, "model", None)
    return _param_value(model, VOICE_ACTOR_SEND_TOKEN_PARAM, "").strip()


def _find_downstream_voice_actor(scene, node_item, max_depth: int = 6):
    if scene is None or node_item is None:
        return None
    queue = [(node_item, 0)]
    visited = set()
    while queue:
        current, depth = queue.pop(0)
        if current is None:
            continue
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)

        kind = _kind_of_item(current)
        if depth > 0 and kind in VOICE_ACTOR_KINDS:
            return current
        if depth >= max(1, int(max_depth)):
            continue
        if depth > 0 and kind not in TRANSLATOR_DOWNSTREAM_PASS_THROUGH_KINDS:
            continue

        for edge in _ordered_out_edges(scene, current):
            dst = getattr(edge, "dst", None)
            if dst is not None and id(dst) not in visited:
                queue.append((dst, depth + 1))
    return None


def _translator_output_context(scene, node_item) -> str:
    target = _find_downstream_voice_actor(scene, node_item)
    if target is None:
        return ""
    model = getattr(target, "model", None)
    name = str(getattr(model, "name", "") or "Voice Actor").strip() or "Voice Actor"
    language_code, language_name = _voice_actor_language_from_item(target)
    mode = _voice_actor_mode_from_item(target)
    return (
        "Target output voice actor:\n"
        f"Name: {name}\n"
        f"Language: {language_name} ({language_code})\n"
        f"Mode: {mode or 'unknown'}\n"
        f"Instruction: Translate the latest voice input into {language_name} for this Voice Actor."
    )


def _set_node_info(node_item, text: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    value = text or ""
    changed = (getattr(model, "info", "") or "") != value
    model.info = value
    params = list(getattr(model, "params", None) or [])
    token_key = MEDIGATOR_OUTPUT_TOKEN_PARAM.strip().lower()
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    token_value = f"{stamp}-{hashlib.sha1(value.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
    token_found = False
    for entry in params:
        if str(entry.get("name", "") or "").strip().lower() == token_key:
            if str(entry.get("value", "") or "") != token_value:
                entry["value"] = token_value
                changed = True
            token_found = True
            break
    if not token_found:
        params.append({"name": MEDIGATOR_OUTPUT_TOKEN_PARAM, "value": token_value})
        changed = True
    model.params = params
    _ensure_hidden_params(model, [MEDIGATOR_OUTPUT_TOKEN_PARAM])
    if not changed:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _publish_mediator_speech_text(node_item, text: str, *, session_token: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    value = str(text or "").strip()
    if not value:
        return
    clean_session_token = str(session_token or "").strip()
    params = list(getattr(model, "params", None) or [])
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
    token_value = f"{stamp}-{digest}"
    wanted = {
        MEDIGATOR_SPEECH_TEXT_PARAM.strip().lower(): (MEDIGATOR_SPEECH_TEXT_PARAM, value),
        MEDIGATOR_SPEECH_TOKEN_PARAM.strip().lower(): (MEDIGATOR_SPEECH_TOKEN_PARAM, token_value),
        MEDIGATOR_SPEECH_SESSION_TOKEN_PARAM.strip().lower(): (
            MEDIGATOR_SPEECH_SESSION_TOKEN_PARAM,
            clean_session_token or token_value,
        ),
    }
    found: set[str] = set()
    changed = False
    for entry in params:
        key = str(entry.get("name", "") or "").strip().lower()
        if key not in wanted:
            continue
        _name, next_value = wanted[key]
        found.add(key)
        if str(entry.get("value", "") or "") != next_value:
            entry["value"] = next_value
            changed = True
    for key, (name, next_value) in wanted.items():
        if key in found:
            continue
        params.append({"name": name, "value": next_value})
        changed = True
    model.params = params
    if _ensure_hidden_params(
        model,
        [MEDIGATOR_SPEECH_TEXT_PARAM, MEDIGATOR_SPEECH_TOKEN_PARAM, MEDIGATOR_SPEECH_SESSION_TOKEN_PARAM],
    ):
        changed = True
    if not changed:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for entry in params:
        key = str(entry.get("name", "") or "").strip().lower()
        if key == MEDIGATOR_HIDDEN_PARAM_KEY:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": MEDIGATOR_HIDDEN_PARAM_KEY, "value": ""}
        params.append(hidden_entry)
    hidden = {
        part.strip().lower()
        for part in str(hidden_entry.get("value", "")).split(",")
        if part.strip()
    }
    for name in names or []:
        token = str(name or "").strip().lower()
        if token:
            hidden.add(token)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _param_value(model, name: str, default: str = "") -> str:
    target = str(name or "").strip().lower()
    if not target:
        return str(default or "")
    for entry in (getattr(model, "params", None) or []):
        key = str(entry.get("name", "") or "").strip().lower()
        if key == target:
            return str(entry.get("value", "") or "")
    return str(default or "")


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    key = str(name or "").strip().lower()
    if not key:
        return
    params = list(getattr(model, "params", None) or [])
    updated = False
    for entry in params:
        entry_key = str(entry.get("name", "") or "").strip().lower()
        if entry_key != key:
            continue
        if str(entry.get("value", "") or "") == str(value or ""):
            return
        entry["value"] = str(value or "")
        updated = True
        break
    if not updated:
        params.append({"name": name, "value": str(value or "")})
    model.params = params
    if not notify_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    try:
        scene.paramChanged.emit(model.name, list(params))
    except Exception:
        pass


def _security_attrs_from_match(match) -> dict[str, str]:
    if not match:
        return {}
    attrs_text = match.groupdict().get("attrs", "") or ""
    attrs: dict[str, str] = {}
    for attr_match in SECURITY_ATTR_RE.finditer(attrs_text):
        attrs[attr_match.group(1).strip().lower()] = attr_match.group(3).strip()
    return attrs


def _first_security_request(text: str) -> tuple[str, dict[str, str]]:
    match = SECURITY_REQUEST_RE.search(str(text or ""))
    if not match:
        return "", {}
    return match.group(0).strip(), _security_attrs_from_match(match)


def _first_security_approval(text: str) -> tuple[str, dict[str, str]]:
    match = SECURITY_APPROVAL_RE.search(str(text or ""))
    if not match:
        return "", {}
    return match.group(0).strip(), _security_attrs_from_match(match)


def _data_nexus_update_payloads(text: str) -> list[str]:
    return [
        str(match.groupdict().get("payload", "") or "").strip()
        for match in DATA_NEXUS_UPDATE_RE.finditer(str(text or ""))
        if str(match.groupdict().get("payload", "") or "").strip()
    ]


def _claims_data_nexus_write_without_update(text: str) -> bool:
    clean = str(text or "").strip()
    return bool(clean and not _data_nexus_update_payloads(clean) and DATA_NEXUS_CLAIMED_WRITE_RE.search(clean))


def _strip_data_nexus_update_tags(text: str) -> str:
    return DATA_NEXUS_UPDATE_RE.sub("", str(text or "")).strip()


def _strip_sales_agent_task_tags(text: str) -> str:
    return SALES_AGENT_TASK_RE.sub("", str(text or "")).strip()


def _data_nexus_action_op(action: dict) -> str:
    return str(
        action.get("op")
        or action.get("operation")
        or action.get("action")
        or ""
    ).strip().lower().replace("-", "_")


def _data_nexus_payloads_have_op(payloads: list[str], ops: set[str]) -> bool:
    wanted = {str(op or "").strip().lower().replace("-", "_") for op in (ops or set())}
    if not wanted:
        return False
    for payload in payloads:
        try:
            data = json.loads(str(payload or ""))
        except Exception:
            continue
        if isinstance(data, list):
            actions = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            raw_actions = data.get("actions")
            if isinstance(raw_actions, list):
                actions = [item for item in raw_actions if isinstance(item, dict)]
            else:
                actions = [data]
        else:
            actions = []
        for action in actions:
            if _data_nexus_action_op(action) in wanted:
                return True
    return False


def _data_nexus_update_payload_with_note_replace(payload: str) -> str:
    try:
        data = json.loads(str(payload or ""))
    except Exception:
        return str(payload or "")

    if isinstance(data, list):
        actions = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        raw_actions = data.get("actions")
        if isinstance(raw_actions, list):
            actions = [item for item in raw_actions if isinstance(item, dict)]
        else:
            actions = []
            raw_points = data.get("points")
            if isinstance(raw_points, list):
                for item in raw_points:
                    if not isinstance(item, dict):
                        continue
                    item.setdefault("op", "upsert_point")
                    actions.append(item)
            if not actions:
                actions = [data]
    else:
        return str(payload or "")

    changed = False
    for action in actions:
        op = _data_nexus_action_op(action)
        if op not in {
            "upsert",
            "upsert_point",
            "update",
            "update_point",
            "set_point",
            "append",
            "append_note",
            "append_point_note",
            "note",
        }:
            continue
        note_alias = str(action.get("description") or action.get("comment") or "").strip()
        if note_alias and not str(action.get("note") or "").strip():
            action["note"] = note_alias
            changed = True
        has_note_body = any(
            str(action.get(key) or "").strip()
            for key in ("note", "description", "comment", "text", "memory", "summary")
        )
        if not has_note_body:
            continue
        mode = str(action.get("note_mode") or action.get("mode") or "").strip().lower()
        if mode in {"append", "add", "additive"}:
            continue
        if op in {"append", "append_note", "append_point_note", "note"}:
            action["op"] = "upsert_point"
            changed = True
        if action.get("note_mode") != "replace":
            action["note_mode"] = "replace"
            changed = True

    if not changed:
        return str(payload or "")
    try:
        return json.dumps(data, separators=(",", ":"))
    except Exception:
        return str(payload or "")


def _xml_attr(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _security_approval_marker(requester: str, tool: str, decision: str) -> str:
    clean_decision = str(decision or "").strip().lower()
    if clean_decision not in {"approved", "denied"}:
        clean_decision = "denied"
    clean_requester = str(requester or "Tanya").strip() or "Tanya"
    clean_tool = str(tool or "qubit_deck_controller").strip() or "qubit_deck_controller"
    return (
        f'<security_approval requester="{_xml_attr(clean_requester)}" '
        f'tool="{_xml_attr(clean_tool)}" scope="single_action" '
        f'decision="{clean_decision}">{clean_decision}</security_approval>'
    )


def _is_qdeck_security_tool(value: str) -> bool:
    return _normalize_prompt_profile(value) == QDECK_PROMPT_PROFILE


DATA_NEXUS_INTENT_RE = re.compile(
    r"\b(data\s+nexus|nexus|vault|graph|point|points|prep[\s_]*question|prep[\s_]*questions|task[\s_]*question|task[\s_]*questions|sales[\s_]*question|sales[\s_]*questions|memory\s+map|project\s+memory|mediator[\s_-]+planner|judge)\b"
    r"|"
    r"\b(add|create|make|save|remember|track|update|answer|respond|resolve|satisfy|delete|forget|connect|link)\b.{0,80}\b(point|note|memory|nexus|graph|vault|prep[\s_]*question|task[\s_]*question|sales[\s_]*question)\b",
    re.IGNORECASE | re.DOTALL,
)
DATA_NEXUS_CLAIMED_WRITE_RE = re.compile(
    r"\b(?:i(?:'ve| have)?|we(?:'ve| have)?|sure[,!\s]*)\s*(?:added|created|saved|remembered|updated|made|tracked|deleted|removed)\b.{0,180}\b(?:data\s+nexus|nexus|point|points|note|memory|graph)\b"
    r"|"
    r"\b(?:added|created|saved|remembered|updated|tracked|deleted|removed)\b.{0,120}\b(?:to|in|from)\s+(?:the\s+)?(?:data\s+nexus|nexus|vault|graph)\b",
    re.IGNORECASE | re.DOTALL,
)
QDECK_INTENT_RE = re.compile(
    r"\b(qubit\s*deck|deck\s+controller|stream\s+deck|deck\s+button|button\s+slot|slot\s+\d+|"
    r"highlight|invoke|open\s+debugger|deck\s+health|ping\s+health|list\s+buttons)\b",
    re.IGNORECASE,
)
QDECK_APP_LAUNCH_RE = re.compile(
    r"\b(open|launch|run|start|press|click)\b",
    re.IGNORECASE,
)
QDECK_APP_TARGET_RE = re.compile(
    r"\b(?:open|launch|run|start|press|click)\s+(?:up\s+)?(?:the\s+)?(?P<target>.+?)(?:\s+(?:app|application|program))?(?:\s+(?:for\s+me|please))?(?:[.!?;]|$)",
    re.IGNORECASE | re.DOTALL,
)
DATA_NEXUS_READ_INTENT_RE = re.compile(
    r"\b(read|show|tell|fetch|look\s*up|what\s+(?:does|do|is)|description|contents?|says?|contains?)\b",
    re.IGNORECASE,
)
DATA_NEXUS_TITLE_READ_INTENT_RE = re.compile(
    r"\bwhat\s+(?:does|do)\s+.+?\s+says?\b"
    r"|"
    r"\bread\s+(?:me\s+)?(?:the\s+)?(?:description|note|text|contents?)\b"
    r"|"
    r"\b(?:description|note|text|contents?)\s+(?:of|for|from)\b",
    re.IGNORECASE | re.DOTALL,
)
DATA_NEXUS_DELETE_INTENT_RE = re.compile(
    r"\b(remove|delete|forget|prune|clear|wipe)\b",
    re.IGNORECASE,
)
DATA_NEXUS_DELETE_ALL_RE = re.compile(
    r"\b(remove|delete|forget|clear|wipe)\b.{0,80}\b(all|every|everything|entire|whole)\b"
    r"|"
    r"\b(all|every|everything|entire|whole)\b.{0,80}\b(points?|notes?|data\s+nexus|nexus|graph|vault)\b",
    re.IGNORECASE | re.DOTALL,
)
DATA_NEXUS_DELETE_POINT_PATTERNS = [
    re.compile(
        r"\b(?:remove|delete|forget)\s+(?:the\s+)?(?:data\s+nexus\s+|nexus\s+)?(?:point|note)\s+(?:called|named|titled)?\s*(?P<query>.+?)(?:\s+from\s+(?:the\s+)?(?:data\s+nexus|nexus|graph|vault)|[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:remove|delete|forget)\s+(?P<query>.+?)\s+from\s+(?:the\s+)?(?:data\s+nexus|nexus|graph|vault)(?:[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
]
DATA_NEXUS_POINT_QUERY_PATTERNS = [
    re.compile(
        r"\b(?:data\s+nexus\s+|nexus\s+)?(?:point|note)\s*[:#-]\s*(?P<query>.+?)(?:[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\bwhat\s+(?:does|do)\s+(?P<query>.+?)\s+says?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\bread\s+(?:me\s+)?(?:the\s+)?(?:description|note|text|contents?)\s+(?:of|for|from)\s+(?P<query>.+?)(?:[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:description|note|text|contents?)\s+(?:of|for|from)\s+(?P<query>.+?)(?:[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\bpoint\s+(?:called|named|titled)\s+(?P<query>.+?)(?:\s+(?:says?|contains?|is)\b|[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:called|named|titled)\s+(?P<query>.+?)(?:\s+(?:says?|contains?|is)\b|[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:point|note)\s+(?P<query>.+?)(?:\s+(?:says?|contains?)\b|[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
]
DATA_NEXUS_ADD_INTENT_RE = re.compile(
    r"\b(add|create|make|readd|re-add|restore|recreate|remember|save|track|capture|log|update)\b",
    re.IGNORECASE,
)
DATA_NEXUS_LINK_INTENT_RE = re.compile(
    r"\b(connect|link|relate|associate|join)\b",
    re.IGNORECASE,
)
DATA_NEXUS_NOTE_REPLACE_INTENT_RE = re.compile(
    r"\b(clean(?:\s+out|\s+up)?|edit|rewrite|revise|replace|remove|delete|strip)\b.{0,140}\b(descriptions?|notes?|comments?|note\s+body|text|prefix|phrase|boilerplate|irrelevant)\b"
    r"|"
    r"\b(descriptions?|notes?|comments?|note\s+body|text|prefix|phrase|boilerplate|irrelevant)\b.{0,140}\b(clean(?:\s+out|\s+up)?|edit|rewrite|revise|replace|remove|delete|strip)\b",
    re.IGNORECASE | re.DOTALL,
)
DATA_NEXUS_NOTE_APPEND_INTENT_RE = re.compile(
    r"\b(append|add\s+(?:a\s+)?(?:note|comment)|add\s+to\s+(?:the\s+)?(?:description|note|comment)|keep\s+existing)\b",
    re.IGNORECASE,
)
DATA_NEXUS_LINK_PATTERNS = [
    re.compile(
        r"\b(?:connect|link|relate|associate|join)\s+[\"'](?P<source>[^\"']+)[\"']\s+(?:to|with|and)\s+[\"'](?P<target>[^\"']+)[\"'](?:\s+(?:as|label(?:ed)?|relationship|relation)\s+(?P<label>.+?))?(?:[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:connect|link|relate|associate|join)\s+(?:the\s+)?(?:data\s+nexus\s+|nexus\s+)?(?:points?\s+)?(?:called\s+|named\s+|titled\s+)?(?P<source>.+?)\s+(?:to|with|and)\s+(?:the\s+)?(?:data\s+nexus\s+|nexus\s+)?(?:points?\s+)?(?:called\s+|named\s+|titled\s+)?(?P<target>.+?)(?:\s+(?:as|label(?:ed)?|relationship|relation)\s+(?P<label>.+?))?(?:\s+(?:in|on)\s+(?:the\s+)?(?:data\s+nexus|nexus|graph)|[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
]
DATA_NEXUS_ANSWER_QUESTION_PATTERNS = [
    re.compile(
        r"\b(?:answer|respond\s+to|resolve|satisfy)\s+(?:the\s+)?(?:data\s+nexus\s+|nexus\s+)?(?:prep[\s_]+|task[\s_]+|sales[\s_]+)?question\s+(?:called\s+|named\s+|titled\s+|id\s+)?[\"']?(?P<question>.+?)[\"']?\s*(?:\bwith\b|\bas\b|:|-)\s+(?P<answer>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:for|to)\s+(?:the\s+)?(?P<question>(?:slide\s+\d+|[^:.;!?]{3,120})(?:\s+(?:prep|refinement|task|sales))?\s+question)\s*[:,-]\s*(?P<answer>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?P<answer>.+?)\s+\b(?:answers|resolves|satisfies)\s+(?:the\s+)?(?P<question>.+?(?:prep|refinement|task|sales)?\s+question)(?:[.!?;]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
]
QDECK_COMMAND_OUTPUT_RE = re.compile(
    r"\{[^{}]*\"action\"\s*:\s*\"(?:invoke|highlight_on|highlight_off|list_buttons|ping_health|open_debugger)\""
    r"[^{}]*(?:\"button_name\"|\"button_slot\")[^{}]*\}",
    re.IGNORECASE | re.DOTALL,
)
USER_FEEDBACK_RE = re.compile(
    r"<user_feedback\b[^>]*>.*?</user_feedback>",
    re.IGNORECASE | re.DOTALL,
)

DATA_NEXUS_FALLBACK_STOPWORDS = {
    "about",
    "action",
    "added",
    "add",
    "again",
    "all",
    "alot",
    "also",
    "and",
    "assistant",
    "because",
    "been",
    "bot",
    "chat",
    "chatbot",
    "clear",
    "cleared",
    "connect",
    "contains",
    "context",
    "could",
    "create",
    "created",
    "data",
    "did",
    "discuss",
    "discussed",
    "discused",
    "do",
    "does",
    "for",
    "from",
    "graph",
    "had",
    "has",
    "have",
    "history",
    "involve",
    "involved",
    "involves",
    "involving",
    "into",
    "keep",
    "lot",
    "make",
    "made",
    "memory",
    "new",
    "nexus",
    "node",
    "not",
    "note",
    "notes",
    "point",
    "points",
    "question",
    "related",
    "remember",
    "remove",
    "removed",
    "save",
    "still",
    "that",
    "the",
    "them",
    "they",
    "this",
    "to",
    "track",
    "update",
    "vault",
    "want",
    "where",
    "we",
    "who",
    "with",
    "you",
}


def _looks_like_data_nexus_request(text: str) -> bool:
    return bool(DATA_NEXUS_INTENT_RE.search(str(text or "")))


def _looks_like_qdeck_request(text: str) -> bool:
    return bool(QDECK_INTENT_RE.search(str(text or "")))


def _qdeck_launch_target_from_request(text: str) -> str:
    raw = str(text or "").strip()
    if not raw or _looks_like_data_nexus_request(raw):
        return ""
    match = QDECK_APP_TARGET_RE.search(raw)
    if not match:
        return ""
    target = str(match.group("target") or "").strip()
    target = re.split(
        r"\s+\b(?:and|then|after|with|using|through|from)\b\s+",
        target,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    target = re.sub(r"^(?:an?|the)\s+", "", target, flags=re.IGNORECASE)
    target = re.sub(r"\b(?:app|application|program)\b$", "", target, flags=re.IGNORECASE).strip()
    target = re.sub(r"\s+", " ", target).strip(" .,:;!?\"'")
    if not target or target.lower() in {"it", "that", "this"}:
        return ""
    return target[:120]


def _looks_like_qdeck_app_launch_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or _looks_like_data_nexus_request(raw):
        return False
    return bool(QDECK_APP_LAUNCH_RE.search(raw) and _qdeck_launch_target_from_request(raw))


def _qdeck_security_request_marker(requester: str, target: str) -> str:
    clean_requester = str(requester or "Tanya").strip() or "Tanya"
    clean_target = str(target or "").strip()
    reason = f"User asked to open {clean_target} through the connected Qubit Deck."
    return (
        f'<security_request requester="{_xml_attr(clean_requester)}" '
        f'tool="qubit_deck_controller" requested_action="invoke" '
        f'target="{_xml_attr(clean_target)}" reason="{_xml_attr(reason)}"></security_request>'
    )


def _clean_data_nexus_read_query(value: str) -> str:
    query = str(value or "").strip(" .,:;!?\"'")
    query = re.sub(r"^(?:the\s+)?(?:data\s+nexus\s+)?", "", query, flags=re.IGNORECASE).strip()
    query = re.sub(
        r"\s+\b(?:say|says|contain|contains|read|show|tell)\b.*$",
        "",
        query,
        flags=re.IGNORECASE | re.DOTALL,
    )
    query = re.sub(
        r"\s+\b(?:description|note|text|content|contents)\b\s*$",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = _clean_data_nexus_ref(query)
    if query.lower() in {"it", "that", "this", "they", "them", "those", "these"}:
        return ""
    if len(query) < 3:
        return ""
    return query[:160]


def _data_nexus_read_query_from_request(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if re.search(r"\b(add|create|make|save|remember|track|update|delete|remove|forget|prune|clear|connect|link)\b", lowered):
        return ""
    has_nexus_intent = _looks_like_data_nexus_request(raw)
    has_read_intent = bool(
        DATA_NEXUS_READ_INTENT_RE.search(raw)
        or DATA_NEXUS_TITLE_READ_INTENT_RE.search(raw)
    )
    has_point_reference = bool(
        re.search(r"\b(?:data\s+nexus\s+|nexus\s+)?(?:point|note)\b", raw, re.IGNORECASE)
    )
    if not (has_nexus_intent or has_read_intent):
        return ""
    if not has_read_intent and not has_point_reference:
        return ""
    for pattern in DATA_NEXUS_POINT_QUERY_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        query = _clean_data_nexus_read_query(match.group("query"))
        if query:
            return query
    return ""


def data_nexus_read_response_from_item(scene, node_item, user_input: str) -> str:
    query = _data_nexus_read_query_from_request(user_input)
    if not query:
        return ""
    nexus_item = _connected_data_nexus_item(scene, node_item)
    if nexus_item is None:
        return ""
    try:
        from nodes.data_nexus import spec as data_nexus_spec
        reader = getattr(data_nexus_spec, "read_data_nexus_point_from_item", None)
        if not callable(reader):
            return ""
        ok, message = reader(nexus_item, query)
        clean_message = str(message or "").strip()
        if clean_message:
            _set_node_info(node_item, f"Data Nexus read: {query}\n\n{clean_message}")
        if ok:
            return clean_message
        return clean_message if _looks_like_data_nexus_request(user_input) else ""
    except Exception:
        return ""


SALES_AGENT_CONTINUE_RE = re.compile(
    r"\b(?:continue|resume|keep\s+going|next\s+question|work(?:ing)?\s+on|keep\s+working\s+on)\b.{0,120}\b(?:sales\s+agent|pitch\s+deck|sales\s+deck|presentation|pitch)\b"
    r"|"
    r"\b(?:sales\s+agent|pitch\s+deck|sales\s+deck|presentation|pitch)\b.{0,120}\b(?:continue|resume|keep\s+going|next\s+question|work(?:ing)?\s+on)\b",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_sales_agent_continue_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if re.search(r"\b(save|add|create|remember|update|answer|respond|resolve|satisfy|delete|remove|forget|prune|clear|connect|link)\b", raw, re.IGNORECASE):
        return False
    return bool(SALES_AGENT_CONTINUE_RE.search(raw))


def _question_display_text(point: dict[str, Any]) -> str:
    text = str(point.get("question_text") or point.get("content") or point.get("summary") or "").strip()
    if text:
        return text
    return str(point.get("title") or point.get("id") or "Sales Agent prep question").strip()


def sales_agent_continue_response_from_item(scene, node_item, user_input: str) -> str:
    if not _looks_like_sales_agent_continue_request(user_input):
        return ""
    nexus_item = _connected_data_nexus_item(scene, node_item)
    if nexus_item is None:
        return ""
    try:
        from nodes.data_nexus import spec as data_nexus_spec
        bundle_helper = getattr(data_nexus_spec, "normalized_data_nexus_points_from_item", None)
        set_active = getattr(data_nexus_spec, "set_active_data_nexus_question_from_item", None)
        if not callable(bundle_helper):
            return ""
        bundle = bundle_helper(nexus_item)
        points = [point for point in (bundle.get("points") or []) if isinstance(point, dict)]
        open_questions = [
            point
            for point in points
            if bool(point.get("is_question")) and str(point.get("answer_status") or "").strip().lower() == "open"
        ]
        if not open_questions:
            message = "There are no open Sales Agent prep questions."
            _set_node_info(node_item, f"Sales Agent route: {message}")
            return message
        active_id = str(bundle.get("active_question_id") or "").strip()
        active_question = next(
            (
                point
                for point in open_questions
                if active_id and str(point.get("id") or "").strip() == active_id
            ),
            None,
        )
        if active_question is None:
            if callable(set_active):
                try:
                    set_active(nexus_item, "")
                    bundle = bundle_helper(nexus_item)
                    points = [point for point in (bundle.get("points") or []) if isinstance(point, dict)]
                    open_questions = [
                        point
                        for point in points
                        if bool(point.get("is_question")) and str(point.get("answer_status") or "").strip().lower() == "open"
                    ]
                    active_id = str(bundle.get("active_question_id") or "").strip()
                except Exception:
                    active_id = ""
            active_question = next(
                (
                    point
                    for point in open_questions
                    if active_id and str(point.get("id") or "").strip() == active_id
                ),
                open_questions[0],
            )
        question = _question_display_text(active_question)
        lines = [
            "Here is the next thing I need:",
            "",
            question,
        ]
        lines.append("")
        lines.append("Answer naturally and I will save it to the active prep question.")
        message = "\n".join(lines).strip()
        _set_node_info(node_item, f"Sales Agent route: active question\n\n{message}")
        return message
    except Exception:
        return ""


def _looks_like_data_nexus_note_replace_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or not _looks_like_data_nexus_request(raw):
        return False
    if DATA_NEXUS_NOTE_APPEND_INTENT_RE.search(raw):
        return False
    return bool(DATA_NEXUS_NOTE_REPLACE_INTENT_RE.search(raw))


def _looks_like_data_nexus_delete_request(text: str) -> bool:
    raw = str(text or "").strip()
    return bool(raw and _looks_like_data_nexus_request(raw) and DATA_NEXUS_DELETE_INTENT_RE.search(raw))


def _looks_like_data_nexus_write_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or not _looks_like_data_nexus_request(raw):
        return False
    if _data_nexus_read_query_from_request(raw):
        return False
    return bool(
        re.search(
            r"\b(add|create|make|save|remember|track|capture|log|update|change|answer|respond|resolve|satisfy|delete|remove|forget|prune|clear|wipe|connect|link|relate|associate|join)\b",
            raw,
            re.IGNORECASE,
        )
    )


def _clean_data_nexus_ref(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(?:the\s+)?(?:data\s+nexus\s+|nexus\s+)?(?:points?|notes?)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:called|named|titled)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:in|on|from)\s+(?:the\s+)?(?:data\s+nexus|nexus|graph|vault)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:points?|notes?)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?\"'")
    return text[:160]


def _data_nexus_delete_all_fallback_payload(user_request: str) -> str:
    text = str(user_request or "").strip()
    if not text or not _looks_like_data_nexus_delete_request(text):
        return ""
    if not DATA_NEXUS_DELETE_ALL_RE.search(text):
        return ""
    return json.dumps({"actions": [{"op": "delete_all_points"}]}, separators=(",", ":"))


def _data_nexus_delete_point_fallback_payload(user_request: str) -> str:
    text = str(user_request or "").strip()
    if not text or not _looks_like_data_nexus_delete_request(text):
        return ""
    if DATA_NEXUS_DELETE_ALL_RE.search(text):
        return ""
    for pattern in DATA_NEXUS_DELETE_POINT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        query = _clean_data_nexus_ref(match.group("query"))
        if query:
            return json.dumps(
                {"actions": [{"op": "delete_point", "label": query}]},
                separators=(",", ":"),
            )
    return ""


def _data_nexus_link_fallback_payload(user_request: str) -> str:
    text = str(user_request or "").strip()
    if not text or not _looks_like_data_nexus_request(text):
        return ""
    lowered = text.lower()
    if re.search(r"\b(add|create|make|save|remember|track|update|delete|remove|forget|prune|clear|read|show|fetch)\b", lowered):
        return ""
    if not DATA_NEXUS_LINK_INTENT_RE.search(text):
        return ""
    for pattern in DATA_NEXUS_LINK_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        source = _clean_data_nexus_ref(match.group("source"))
        target = _clean_data_nexus_ref(match.group("target"))
        label = _clean_data_nexus_ref(match.groupdict().get("label", ""))
        if not source or not target or source.lower() == target.lower():
            continue
        action = {
            "op": "link",
            "source": source,
            "target": target,
            "create_missing": False,
        }
        if label:
            action["label"] = label[:120]
        return json.dumps({"actions": [action]}, separators=(",", ":"))
    return ""


def _data_nexus_answer_question_fallback_payload(user_request: str) -> str:
    text = str(user_request or "").strip()
    if not text or not _looks_like_data_nexus_request(text):
        return ""
    if not re.search(r"\b(answer|answers|respond|resolve|resolves|satisfy|satisfies)\b", text, re.IGNORECASE):
        return ""
    for pattern in DATA_NEXUS_ANSWER_QUESTION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        question = _clean_data_nexus_ref(match.group("question"))
        answer = str(match.group("answer") or "").strip()
        answer = re.sub(r"\s+", " ", answer).strip(" .,:;\"'")
        if not question or len(answer) < 8:
            continue
        action = {
            "op": "answer_question",
            "question": question,
            "answer": answer[:1800],
        }
        return json.dumps({"actions": [action]}, separators=(",", ":"))
    return ""


def _data_nexus_topic_keywords(user_request: str) -> list[str]:
    text = str(user_request or "").strip()
    if not text:
        return []
    lowered = text.lower()
    topic = ""
    patterns = [
        r"(?:relate(?:d)?\s+to|about|for|involv(?:e|es|ed|ing))\s+(?:the\s+)?(?P<topic>.*?)(?:\s+(?:as\s+we|from\s+the|from\s+our|that\s+we|we\s+discuss|we\s+discused|remove|delete|forget|prune|clear|add|create|make|save|track|update)\b|[.!?;]|$)",
        r"\b(?:pitch|presentation|deck|investor|demo|problem|solution|ask|audience|story|narrative|prep|preparation)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        if "topic" in match.groupdict():
            topic = str(match.group("topic") or "").strip(" .,:;!?")
        else:
            topic = str(match.group(0) or "").strip(" .,:;!?")
        if topic:
            break
    source = topic or text
    keywords = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", source)
        if len(token) > 2 and token.lower() not in DATA_NEXUS_FALLBACK_STOPWORDS
    ]
    if "pitch" in lowered and "pitch" not in keywords:
        keywords.insert(0, "pitch")
    if "pitch" in keywords:
        for extra in ("preparation", "prep", "deck", "presentation", "investor", "demo", "story", "ask"):
            if extra in lowered and extra not in keywords:
                keywords.append(extra)
    deduped: list[str] = []
    for keyword in keywords:
        if keyword not in deduped:
            deduped.append(keyword)
    return deduped[:8]


def _history_for_data_nexus_fallback(history_context: str) -> str:
    text = str(history_context or "").strip()
    if not text:
        return ""
    text = re.split(r"\n\s*Data Nexus context\s*:\s*\n", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = DATA_NEXUS_UPDATE_RE.sub("", text)
    text = SECURITY_REQUEST_RE.sub("", text)
    text = SECURITY_APPROVAL_RE.sub("", text)
    text = USER_FEEDBACK_RE.sub("", text)
    text = re.sub(r"^\s*\{[^{}]*\"action\"\s*:\s*\"(?:invoke|highlight_on|highlight_off|list_buttons|ping_health|open_debugger)\"[^{}]*\}\s*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_history_segment_for_point(segment: str) -> str:
    text = str(segment or "").strip()
    text = re.sub(r"^\s*(?:Turn\s+\d+\s+)?(?:User|Assistant)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -:;,.")
    return text


def _is_meta_data_nexus_segment(segment: str) -> bool:
    lowered = str(segment or "").lower()
    if "data nexus" not in lowered and "nexus" not in lowered:
        return False
    return bool(re.search(r"\b(add|create|make|update|remove|delete|clear|cleared|save|track|remember)\b", lowered))


def _history_segments_for_keywords(history_context: str, keywords: list[str], *, limit: int = 8) -> list[str]:
    source = _history_for_data_nexus_fallback(history_context)
    if not source or not keywords:
        return []
    chunks = re.split(
        r"(?=\n?\s*(?:Turn\s+\d+\s+)?(?:User|Assistant)\s*:)",
        source,
        flags=re.IGNORECASE,
    )
    if len(chunks) <= 1:
        chunks = re.split(r"\n{2,}|(?<=[.!?])\s+", source)

    candidates: list[str] = []
    seen: set[str] = set()
    for raw in reversed(chunks):
        clean = _clean_history_segment_for_point(raw)
        if len(clean) < 24:
            continue
        lowered = clean.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        if _is_meta_data_nexus_segment(clean):
            continue
        if "here's the update to data nexus" in lowered or "updating data nexus" in lowered:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(clean[:700])
        if len(candidates) >= limit:
            break
    candidates.reverse()
    return candidates


def _fallback_point_label(segment: str, keywords: list[str]) -> str:
    words = re.findall(r"[A-Za-z0-9_]+", str(segment or ""))
    if not words:
        return "Pitch Note" if "pitch" in keywords else "Recovered Note"
    lowered_words = [word.lower() for word in words]
    start = 0
    for idx, word in enumerate(lowered_words):
        if word in keywords:
            start = max(0, idx - 1)
            break
    label_words = [
        word
        for word in words[start : start + 7]
        if word.lower() not in DATA_NEXUS_FALLBACK_STOPWORDS
    ]
    if not label_words and "pitch" in keywords:
        label_words = ["Pitch", "Discussion"]
    elif not label_words:
        label_words = words[:4]
    label = " ".join(label_words[:6]).strip()
    return label.title()[:80] or "Recovered Note"


def _fallback_point_id(label: str, index: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", str(label or "").strip().lower()).strip("_")
    while "__" in base:
        base = base.replace("__", "_")
    return base or f"recovered_point_{index}"


def _clean_direct_point_note(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?\"'")
    text = re.sub(
        r"^(?:a\s+|an\s+|the\s+)?(?:question|description|note|content|body)\s*(?:about|called|named|titled)?\s*[:=-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" .,:;!?\"'")
    if not text:
        return ""
    if re.match(
        r"^(who|what|when|where|why|how|which|is|are|do|does|can|should|could|would|will|did|has|have)\b",
        text,
        re.IGNORECASE,
    ):
        text = text[:1].upper() + text[1:]
        if not text.endswith("?"):
            text = f"{text}?"
    return text[:1000]


def _direct_point_note_from_request(text: str) -> str:
    patterns = [
        r"\bwhere\s+it\s+(?:contains?|says?|asks?)\s+(?P<note>.+?)(?:[.!?;]|$)",
        r"\b(?:that|which)\s+(?:contains?|says?|asks?)\s+(?P<note>.+?)(?:[.!?;]|$)",
        r"\b(?:with|containing)\s+(?P<note>.+?)(?:[.!?;]|$)",
        r"\b(?:question|description|note|content|body)\s*[:=-]?\s*(?P<note>.+?)(?:[.!?;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        note = _clean_direct_point_note(match.group("note"))
        if note:
            return note
    return ""


def _direct_point_label_from_request(text: str, note: str) -> str:
    patterns = [
        r"\b(?:called|named|titled|label(?:ed)?)\s+[\"']?(?P<label>.+?)[\"']?(?:\s+(?:with|where|that|which|containing|contains?|says?|asks?|description|note|content|body)\b|[.!?;]|$)",
        r"\b(?:page\s+name|point\s+name|title)\s*[:=-]\s*(?P<label>.+?)(?:\s+(?:description|note|content|body)\b|[.!?;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        label = _clean_data_nexus_ref(match.group("label"))
        if label:
            return label[:80]

    words = re.findall(r"[A-Za-z0-9_]+", note)
    ignore = DATA_NEXUS_FALLBACK_STOPWORDS | {
        "are",
        "ask",
        "asks",
        "how",
        "what",
        "when",
        "which",
        "why",
    }
    label_words = [word for word in words if word.lower() not in ignore]
    if label_words:
        label = " ".join(label_words[:5]).title()
        if note.strip().endswith("?") and "question" not in label.lower():
            label = f"{label} Question"
        return label[:80]
    return "Data Nexus Question" if note.strip().endswith("?") else "Data Nexus Point"


def _direct_data_nexus_add_action_from_request(text: str) -> dict[str, str]:
    note = _direct_point_note_from_request(text)
    if not note:
        return {}
    label = _direct_point_label_from_request(text, note)
    return {
        "op": "upsert_point",
        "id": _fallback_point_id(label, 1),
        "label": label,
        "note": note,
    }


def _data_nexus_add_fallback_payload(user_request: str, history_context: str = "") -> str:
    text = str(user_request or "").strip()
    if not text or not _looks_like_data_nexus_request(text):
        return ""
    if not DATA_NEXUS_ADD_INTENT_RE.search(text):
        return ""
    lowered = text.lower()
    if re.search(r"\b(remove|delete|forget|prune)\b", lowered) and not re.search(r"\b(add|create|make|restore|recreate|remember|save|track)\b", lowered):
        return ""
    direct_action = _direct_data_nexus_add_action_from_request(text)
    if direct_action:
        return json.dumps({"actions": [direct_action]}, separators=(",", ":"))
    keywords = _data_nexus_topic_keywords(text)
    if not keywords:
        return ""
    segments = _history_segments_for_keywords(history_context, keywords)
    actions: list[dict[str, str]] = []
    for idx, segment in enumerate(segments, 1):
        label = _fallback_point_label(segment, keywords)
        actions.append(
            {
                "op": "upsert_point",
                "id": _fallback_point_id(label, idx),
                "label": label,
                "note": f"Recovered from chatbot history: {segment}",
            }
        )
    if not actions:
        topic = " ".join(keywords[:3]).strip() or "Data Nexus"
        label = f"{topic.title()} Follow Up"
        actions.append(
            {
                "op": "upsert_point",
                "id": _fallback_point_id(label, 1),
                "label": label,
                "note": "User asked to restore/add related discussion points, but no matching Chatbot history was available to the Mediator fallback.",
            }
        )
    return json.dumps({"actions": actions[:8]}, separators=(",", ":"))


def _data_nexus_fallback_update_payload(user_request: str, history_context: str = "") -> str:
    text = str(user_request or "").strip()
    if not text or not _looks_like_data_nexus_request(text):
        return ""
    lowered = text.lower()
    delete_all_payload = _data_nexus_delete_all_fallback_payload(text)
    if delete_all_payload:
        return delete_all_payload
    delete_point_payload = _data_nexus_delete_point_fallback_payload(text)
    if delete_point_payload:
        return delete_point_payload
    answer_payload = _data_nexus_answer_question_fallback_payload(text)
    if answer_payload:
        return answer_payload
    link_payload = _data_nexus_link_fallback_payload(text)
    if link_payload:
        return link_payload
    wants_cleanup = bool(re.search(r"\b(remove|delete|forget|prune|clear)\b", lowered))
    wants_scope = bool(re.search(r"\b(only|except|rest|other|unrelated)\b", lowered))
    if not (wants_cleanup and wants_scope):
        return _data_nexus_add_fallback_payload(text, history_context)

    topic = ""
    patterns = [
        r"(?:relate(?:d)?\s+to|about|for)\s+(?:the\s+)?(?P<topic>.*?)(?:\s+(?:remove|delete|forget|prune|clear)\b|[.!?;]|$)",
        r"(?:only\s+(?:want|keep)|keep\s+only)\s+(?:the\s+)?(?P<topic>.*?)(?:\s+(?:points?|notes?)\b|\s+(?:remove|delete|forget|prune|clear)\b|[.!?;]|$)",
        r"\bexcept\s+(?:the\s+)?(?P<topic>.*?)(?:\s+(?:points?|notes?)\b|[.!?;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            topic = str(match.group("topic") or "").strip(" .,:;!?")
            if topic:
                break
    if not topic:
        return ""
    stopwords = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "only",
        "keep",
        "want",
        "points",
        "point",
        "notes",
        "note",
        "data",
        "nexus",
        "graph",
        "vault",
        "relate",
        "related",
    }
    keywords = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", topic)
        if len(token) > 2 and token.lower() not in stopwords
    ]
    if not keywords:
        return ""
    return json.dumps(
        {
            "actions": [
                {
                    "op": "prune_points",
                    "topic": topic,
                    "keep_keywords": keywords,
                }
            ]
        },
        separators=(",", ":"),
    )


def _compose_data_nexus_planning_prompt(
    *,
    data_nexus_context: str,
    chatbot_history: str,
    user_input: str,
    chatbot_output: str,
) -> tuple[str, str]:
    planner_prompt = _load_prompt_profile_text(MEDIATOR_PLANNER_PROMPT_PROFILE).strip()
    clean_nexus = _trim_text(data_nexus_context, MEDIGATOR_MAX_HISTORY_CHARS, keep_tail=False)
    clean_history = _trim_text(chatbot_history, MEDIGATOR_MAX_HISTORY_CHARS, keep_tail=True)
    clean_user = _trim_text(user_input, MEDIGATOR_MAX_VOICE_CHARS, keep_tail=True)
    clean_output = _trim_text(chatbot_output, MEDIGATOR_MAX_OUTPUT_CONTEXT_CHARS, keep_tail=False)
    instructions = (
        f"{planner_prompt}\n\n"
        "Data Nexus planning handoff:\n"
        "- You are planning a graph edit for the connected Data Nexus.\n"
        "- Think semantically from the user's request, current graph, and conversation history.\n"
        "- Return a short user-facing answer followed by exactly one hidden <data_nexus_update> tag when a graph change is requested, including add/create/save/update/answer/delete/connect/link requests.\n"
        "- Never claim a Data Nexus point/note/link was added, saved, updated, or deleted unless the same response includes the required hidden data_nexus_update tag.\n"
        "- Do not output Qubit Deck JSON or security_request tags.\n"
        "- Supported actions: upsert_point, append_note, answer_question, delete_point, delete_all_points, prune_points, link, unlink.\n"
        "- For cleanup/edit/rewrite/remove-text requests against point descriptions, notes, or comments, preserve the existing id/label and include \"note_mode\":\"replace\" on every note update. Do not append the cleaned text.\n"
        "- Treat Data Nexus `prep_question` points as task questions, not answer facts. When the user answers one, prefer an `answer_question` action with `question` and `answer`; the app will create/update a normal answer point and link answer -> question with `answers_question`.\n"
        "- If a prep question is marked ACTIVE and the latest user request looks like an answer, use that active question id; the user does not need to repeat the id.\n"
        "- If the user asks to activate, continue, resume, or work with the Sales Agent/pitch deck/presentation and open prep questions exist, ask exactly one open prep question, preferring the ACTIVE one.\n"
        "- If creating answer points directly, use the prep question's expected answer point type when available and link the answer point to the question with label `answers_question`.\n"
        "- For delete/remove/forget/prune/clear requests, emit the destructive action; the app will show the deletion approval popup before applying it.\n"
        "- For 'remove all points' or 'clear the nexus', use {\"op\":\"delete_all_points\"}.\n"
        "- For connect/link requests, prefer existing point ids or labels and set create_missing=false unless the user clearly asked to create missing points.\n"
        "- If the request is ambiguous, emit no update tag and ask one concise clarification question.\n"
    )
    prompt = (
        f"{instructions}\n\n"
        "Current Data Nexus context:\n"
        f"{clean_nexus or '(none)'}\n\n"
        "Conversation history:\n"
        f"{clean_history or '(none)'}\n\n"
        "Latest user request:\n"
        f"{clean_user or '(none)'}\n\n"
        "Tanya/Chatbot output that failed or lacked a valid Data Nexus update:\n"
        f"{clean_output or '(none)'}\n"
    )
    signature = _security_signature("data_nexus_planning", clean_nexus, clean_history, clean_user, clean_output)
    return prompt, signature


def _security_signature(*parts: str) -> str:
    payload = "\n".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


def _mediator_items(scene) -> list:
    if scene is None or not hasattr(scene, "_node_items"):
        return []
    try:
        items = list(scene._node_items.values())
    except Exception:
        return []
    return [item for item in items if _kind_of_item(item) in MEDIGATOR_NODE_KINDS]


def _mediator_profile_from_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return _normalize_prompt_profile(
        _param_value(model, MEDIGATOR_PROMPT_PROFILE_PARAM, MEDIGATOR_DEFAULT_PROMPT_PROFILE)
    )


def chatbot_agent_context_from_item(scene, node_item) -> str:
    """Return Mediator agent context for Chatbot without invoking the Mediator runtime."""
    if node_item is None:
        return ""
    profile = _mediator_profile_from_item(node_item)
    profile_prompt = _load_prompt_profile_text(profile).strip()
    label = profile.replace("_", " ").strip().title() or "Mediator"
    parts = [
        "Mediator agent layer:",
        f"Profile: {label} ({profile})",
        "Apply this profile to the current Chatbot turn. Do not run a separate Mediator model call.",
    ]
    if profile_prompt:
        parts.append(f"Profile instructions:\n{profile_prompt}")
    sandbox_context = _codex_sandbox_context_text(scene, node_item)
    if sandbox_context:
        parts.append(f"Sandbox policy:\n{sandbox_context}")
    data_nexus_context = _data_nexus_context_text(scene, node_item)
    if data_nexus_context:
        parts.append(f"Data Nexus context:\n{data_nexus_context}")
        parts.append(
            "Data Nexus access route:\n"
            "- Read-only Data Nexus requests should be answered from the Data Nexus context when possible. Do not emit a data_nexus_update tag for read/show/fetch/what-does-this-point-say requests.\n"
            "- Data Nexus write requests to add, create, save, update, answer, delete, connect, or link graph, point, note, vault, memory, Mediator Planner, task question, prep question, and nexus changes are handled with a hidden data_nexus_update tag.\n"
            "- Treat `prep_question` points as task questions, not answer facts. When the user answers one, emit an `answer_question` action with `question` and `answer`; the app will create/update a normal answer point and link answer -> question with `answers_question`.\n"
            "- Never claim a Data Nexus point/note/link was added, saved, updated, or deleted unless your same response includes the required hidden data_nexus_update tag.\n"
            "- If a prep question is marked ACTIVE and the user appears to answer it, use the active question id; the user does not need to repeat the id.\n"
            "- If the user asks to activate, continue, resume, or work with the Sales Agent/pitch deck/presentation and open prep questions exist, ask exactly one open prep question, preferring the ACTIVE one.\n"
            "- If creating answer points directly, use the prep question's expected answer point type when available and link the answer point to the question with label `answers_question`.\n"
            "- For delete/remove/forget/prune point requests, emit the tag and let the app show the Data Nexus deletion approval popup.\n"
            "- Do not emit security_request for Data Nexus requests.\n"
            "- Do not output Qubit Deck command JSON for Data Nexus requests.\n"
            "- Qubit Deck security is only for explicit Qubit Deck/deck button/deck slot/deck debugger/deck health or external app launch requests."
        )
    qdeck_item = _connected_qdeck_controller_item(scene, node_item)
    if profile != QDECK_PROMPT_PROFILE and qdeck_item is not None:
        parts.append(
            "Qubit Deck access route:\n"
            "- A Qubit Deck Controller is connected.\n"
            "- For user requests like \"open Houdini\", \"launch OBS\", or \"run Unreal\", emit a security_request for qubit_deck_controller.\n"
            "- Do not output Qubit Deck command JSON in this profile. After approval, the app routes to the qubit_deck_controller profile."
        )
    if profile == QDECK_PROMPT_PROFILE:
        issue = _qdeck_context_issue(scene, node_item)
        if issue:
            parts.append(f"Tool context status:\n{issue}")
        else:
            if qdeck_item is not None:
                parts.append(f"Qubit Deck context:\n{_qdeck_context_text(scene, qdeck_item)}")
    return "\n\n".join(part for part in parts if str(part or "").strip()).strip()


def handle_chatbot_model_output_from_item(
    scene,
    node_item,
    output: str,
    user_input: str,
    history_context: str = "",
    *,
    session_token: str = "",
) -> bool:
    """Let a connected Mediator handle Chatbot LLM output tags/popups on the UI thread."""
    widget = _mediator_widget_from_item(node_item)
    if widget is None:
        return False
    handler = getattr(widget, "_handle_chatbot_model_output", None)
    if not callable(handler):
        return False
    try:
        return bool(handler(output, user_input, history_context, session_token=session_token))
    except Exception:
        return False


def _mediator_widget_from_item(node_item):
    return getattr(node_item, "_mediator_console_widget", None)


def run_teacher_agent_conversion_from_item(
    scene,
    node_item,
    prompt: str,
    signature: str,
    on_done=None,
) -> tuple[bool, str]:
    """Run a Teacher Agent conversion prompt through a connected Mediator widget."""
    widget = _mediator_widget_from_item(node_item)
    if widget is None:
        return False, "Connected Mediator node is not initialized."
    runner = getattr(widget, "_run_codex_prompt", None)
    if not callable(runner):
        return False, "Connected Mediator node cannot run AI prompts."

    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        return False, "Teacher Agent prompt is empty."
    clean_signature = str(signature or "").strip() or _security_signature("teacher_agent", clean_prompt)

    callback_ref = None
    if callable(on_done):
        def _done(exit_code: int, error_text: str, response_text: str, done_signature: str, source: str) -> None:
            if str(source or "").strip().lower() != "teacher_agent":
                return
            if str(done_signature or "").strip() != clean_signature:
                return
            try:
                widget._command_done.disconnect(_done)
            except Exception:
                pass
            try:
                refs = getattr(widget, "_teacher_agent_callbacks", None)
                if isinstance(refs, list) and callback_ref in refs:
                    refs.remove(callback_ref)
            except Exception:
                pass
            try:
                on_done(int(exit_code), str(error_text or ""), str(response_text or ""))
            except Exception:
                pass

        callback_ref = _done
        try:
            refs = getattr(widget, "_teacher_agent_callbacks", None)
            if not isinstance(refs, list):
                refs = []
                setattr(widget, "_teacher_agent_callbacks", refs)
            refs.append(callback_ref)
            widget._command_done.connect(_done)
        except Exception as exc:
            return False, f"Failed to attach Teacher Agent callback: {exc}"

    try:
        widget._last_prompt_voice_input = "Teacher Agent template conversion"
        widget._last_prompt_chatbot_history = ""
    except Exception:
        pass
    try:
        runner(clean_prompt, clean_signature, "teacher_agent")
    except Exception as exc:
        if callback_ref is not None:
            try:
                widget._command_done.disconnect(callback_ref)
            except Exception:
                pass
            try:
                refs = getattr(widget, "_teacher_agent_callbacks", None)
                if isinstance(refs, list) and callback_ref in refs:
                    refs.remove(callback_ref)
            except Exception:
                pass
        return False, f"Failed to start Teacher Agent through Mediator: {exc}"
    return True, "Teacher Agent conversion sent to Mediator AI."


def run_sales_agent_task_from_item(
    scene,
    node_item,
    prompt: str,
    signature: str,
    on_done=None,
) -> tuple[bool, str]:
    """Run a Sales Agent task prompt through a connected Mediator widget."""
    widget = _mediator_widget_from_item(node_item)
    if widget is None:
        return False, "Connected Mediator node is not initialized."
    runner = getattr(widget, "_run_codex_prompt", None)
    if not callable(runner):
        return False, "Connected Mediator node cannot run AI prompts."

    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        return False, "Sales Agent prompt is empty."
    clean_signature = str(signature or "").strip() or _security_signature("sales_agent", clean_prompt)

    callback_ref = None
    if callable(on_done):
        def _done(exit_code: int, error_text: str, response_text: str, done_signature: str, source: str) -> None:
            if str(source or "").strip().lower() != "sales_agent":
                return
            if str(done_signature or "").strip() != clean_signature:
                return
            try:
                widget._command_done.disconnect(_done)
            except Exception:
                pass
            try:
                refs = getattr(widget, "_sales_agent_callbacks", None)
                if isinstance(refs, list) and callback_ref in refs:
                    refs.remove(callback_ref)
            except Exception:
                pass
            try:
                on_done(int(exit_code), str(error_text or ""), str(response_text or ""))
            except Exception:
                pass

        callback_ref = _done
        try:
            refs = getattr(widget, "_sales_agent_callbacks", None)
            if not isinstance(refs, list):
                refs = []
                setattr(widget, "_sales_agent_callbacks", refs)
            refs.append(callback_ref)
            widget._command_done.connect(_done)
        except Exception as exc:
            return False, f"Failed to attach Sales Agent callback: {exc}"

    try:
        widget._last_prompt_voice_input = "Sales Agent task review"
        widget._last_prompt_chatbot_history = ""
    except Exception:
        pass
    try:
        runner(clean_prompt, clean_signature, "sales_agent")
    except Exception as exc:
        if callback_ref is not None:
            try:
                widget._command_done.disconnect(callback_ref)
            except Exception:
                pass
            try:
                refs = getattr(widget, "_sales_agent_callbacks", None)
                if isinstance(refs, list) and callback_ref in refs:
                    refs.remove(callback_ref)
            except Exception:
                pass
        return False, f"Failed to start Sales Agent through Mediator: {exc}"
    return True, "Sales Agent task sent to Mediator AI."


def _find_security_guard_item(scene, *, exclude_item=None):
    for item in _mediator_items(scene):
        if item is exclude_item:
            continue
        if _mediator_profile_from_item(item) == SECURITY_GUARD_PROMPT_PROFILE:
            return item
    for item in _mediator_items(scene):
        if item is exclude_item:
            continue
        name = _node_name(item).replace("_", " ")
        if "security guard" in name:
            return item
    return None


def _find_mediator_item_by_name(scene, name: str):
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for item in _mediator_items(scene):
        if _node_name(item) == wanted:
            return item
    return None


def _trim_text(text: str, limit: int, *, keep_tail: bool = False) -> str:
    clean = str(text or "").strip()
    if limit <= 0 or len(clean) <= limit:
        return clean
    if keep_tail:
        return clean[-limit:]
    return clean[:limit]


def _normalize_qdeck_voice_input(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    out = raw
    out = re.sub(r"\bo[\s\._-]*b[\s\._-]*s\b", "obs", out, flags=re.IGNORECASE)
    out = re.sub(r"\bovs\b", "obs", out, flags=re.IGNORECASE)
    out = re.sub(r"\bobs\s+stand\b", "obs", out, flags=re.IGNORECASE)
    out = re.sub(r"\bovs\s+stand\b", "obs", out, flags=re.IGNORECASE)
    out = re.sub(r"\bobs\s+studio\b", "obs", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()
    return out or raw


def _web_designer_intent_hint(voice_input: str) -> str:
    text = str(voice_input or "").strip()
    if not text:
        return "unclear"
    if WEB_DESIGNER_APPROVAL_RE.search(text):
        return "likely implementation: the user appears to be approving or applying a prior recommendation."
    if WEB_DESIGNER_ADVISORY_RE.search(text):
        return "likely advisory: answer with recommendations and do not edit files unless the user confirms implementation."
    if WEB_DESIGNER_IMPLEMENTATION_RE.search(text):
        return "likely implementation: inspect files, edit when writable, and verify the change."
    if "?" in text:
        return "likely advisory: answer the question and ask before editing files."
    return "unclear: default to advisory behavior unless the conversation clearly asks for implementation."


def _mediator_task_block_for_profile(profile: str) -> str:
    token = _normalize_prompt_profile(profile)
    if token == WEB_DESIGNER_PROMPT_PROFILE:
        return (
            "Task:\n"
            "1. First classify the latest voice input as advisory or implementation.\n"
            "2. If the user asks for suggestions, opinions, feasibility, explanation, review, options, or uses wording such as 'what do you suggest', do not edit files. Inspect website files only if useful, then answer with concise recommendations and ask before making changes.\n"
            "3. If the user clearly asks to make, fix, add, remove, update, replace, redesign, implement, or apply a change, inspect the actual website files in the current working directory or user-specified path before editing.\n"
            "4. For explicit implementation requests, make the necessary HTML, CSS, JavaScript, and asset-reference changes directly when the sandbox allows writes.\n"
            "5. If the target website path is missing, files are unavailable, or the sandbox is read-only, state the exact blocker instead of pretending changes were made.\n"
            "6. Run lightweight verification that fits any change, such as syntax checks, local file existence checks, or browser/dev-server checks when available.\n"
            "7. Return recommendations for advisory requests, or a concise implementation summary with changed files and verification for implemented requests.\n"
        )
    return (
        "Task:\n"
        "1. Read the conversation history, latest voice input, and output context.\n"
        "2. Produce the best next assistant reply.\n"
        "3. Return only the assistant response text.\n"
    )


def _compose_mediator_prompt(
    system_prompt: str,
    chatbot_history: str,
    voice_input: str,
    default_system_prompt: str = "",
    output_context: str = "",
    profile: str = "",
) -> tuple[str, str]:
    clean_system = _trim_text(system_prompt, MEDIGATOR_MAX_SYSTEM_CHARS, keep_tail=False)
    clean_history = _trim_text(chatbot_history, MEDIGATOR_MAX_HISTORY_CHARS, keep_tail=True)
    clean_voice = _trim_text(voice_input, MEDIGATOR_MAX_VOICE_CHARS, keep_tail=True)
    clean_default = _trim_text(default_system_prompt, MEDIGATOR_MAX_SYSTEM_CHARS, keep_tail=False)
    clean_output_context = _trim_text(output_context, MEDIGATOR_MAX_OUTPUT_CONTEXT_CHARS, keep_tail=False)
    effective_system = clean_system or clean_default or MEDIGATOR_DEFAULT_SYSTEM_PROMPT
    profile_token = _normalize_prompt_profile(profile)
    intent_block = ""
    if profile_token == WEB_DESIGNER_PROMPT_PROFILE:
        intent_block = f"Web Designer request intent hint: {_web_designer_intent_hint(clean_voice)}\n\n"
    payload = "\n\n".join(
        [
            effective_system,
            profile_token,
            intent_block,
            clean_history,
            clean_voice,
            clean_output_context,
        ]
    )
    signature = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    output_block = (
        "Output context:\n"
        f"{clean_output_context}\n\n"
        if clean_output_context
        else ""
    )
    prompt = (
        f"{effective_system}\n\n"
        f"{_mediator_task_block_for_profile(profile)}\n"
        f"{intent_block}"
        f"{output_block}"
        "Conversation history:\n"
        f"{clean_history or '(none)'}\n\n"
        "Latest voice input:\n"
        f"{clean_voice or '(none)'}\n"
    )
    return prompt, signature


def build_ports(node_item) -> None:
    if not hasattr(node_item, "ensure_input"):
        return
    for port_name in ("voice_input", "chatbot_history", "system_prompt", "data_nexus", "sandbox"):
        node_item.ensure_input(port_name)


def _style_icon(widget, standard_pixmap: str):
    try:
        style = widget.style()
        return style.standardIcon(getattr(QtWidgets.QStyle, standard_pixmap))
    except Exception:
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                return app.style().standardIcon(getattr(QtWidgets.QStyle, standard_pixmap))
        except Exception:
            pass
    return None


def _tinted_style_icon(widget, standard_pixmap: str, color: str, size: int = 18):
    icon = _style_icon(widget, standard_pixmap)
    if icon is None:
        return None
    try:
        pixmap = icon.pixmap(int(size), int(size))
        if pixmap.isNull():
            return icon
        tinted = QtGui.QPixmap(pixmap.size())
        tinted.fill(QtGui.QColor(0, 0, 0, 0))
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, pixmap)
        try:
            mode = QtGui.QPainter.CompositionMode_SourceIn
        except Exception:
            mode = QtGui.QPainter.CompositionMode.CompositionMode_SourceIn
        painter.setCompositionMode(mode)
        painter.fillRect(tinted.rect(), QtGui.QColor(str(color or AGENT_POPUP_CLOSE_ICON_COLOR)))
        painter.end()
        return QtGui.QIcon(tinted)
    except Exception:
        return icon


def _popup_close_icon(widget):
    return _tinted_style_icon(widget, "SP_DialogCloseButton", AGENT_POPUP_CLOSE_ICON_COLOR, 18)


def _icon_path_from_filenames(filenames) -> Path | None:
    icon_dir = _repo_root() / "icons"
    for filename in filenames or ():
        path = icon_dir / filename
        try:
            if path.exists() and path.is_file():
                return path
        except Exception:
            pass
    return None


def _security_agent_icon_path() -> Path | None:
    return _icon_path_from_filenames(SECURITY_AGENT_ICON_FILENAMES)


def _operator_agent_icon_path() -> Path | None:
    return _icon_path_from_filenames(OPERATOR_AGENT_ICON_FILENAMES)


def _tanya_agent_icon_path() -> Path | None:
    return _icon_path_from_filenames(TANYA_AGENT_ICON_FILENAMES)


def _web_designer_agent_icon_path() -> Path | None:
    return _icon_path_from_filenames(WEB_DESIGNER_AGENT_ICON_FILENAMES)


def _scaled_pixmap(path: Path, size: int):
    try:
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            return None
        aspect_mode = getattr(QtCore.Qt, "KeepAspectRatio", None)
        if aspect_mode is None:
            aspect_mode = QtCore.Qt.AspectRatioMode.KeepAspectRatio
        transform_mode = getattr(QtCore.Qt, "SmoothTransformation", None)
        if transform_mode is None:
            transform_mode = QtCore.Qt.TransformationMode.SmoothTransformation
        return pixmap.scaled(
            int(size),
            int(size),
            aspect_mode,
            transform_mode,
        )
    except Exception:
        return None


def _qt_enum(group_name: str, member_name: str):
    group = getattr(QtCore.Qt, group_name, None)
    if group is not None:
        value = getattr(group, member_name, None)
        if value is not None:
            return value
    return getattr(QtCore.Qt, member_name, None)


def _qt_window_flags(*member_names: str):
    combined = None
    for member_name in member_names:
        value = _qt_enum("WindowType", member_name)
        if value is None:
            continue
        combined = value if combined is None else combined | value
    return combined


def _set_agent_popup_chrome(dialog: QtWidgets.QDialog) -> None:
    flags = _qt_window_flags("Window", "FramelessWindowHint", "WindowStaysOnTopHint")
    if flags is not None:
        try:
            dialog.setWindowFlags(flags)
        except Exception:
            pass
    for member_name, enabled in (
        ("FramelessWindowHint", True),
        ("WindowStaysOnTopHint", True),
        ("WindowTitleHint", False),
        ("WindowSystemMenuHint", False),
        ("WindowMinimizeButtonHint", False),
        ("WindowMaximizeButtonHint", False),
        ("WindowCloseButtonHint", False),
    ):
        flag = _qt_enum("WindowType", member_name)
        if flag is None:
            continue
        try:
            dialog.setWindowFlag(flag, enabled)
        except Exception:
            pass
    try:
        modality = _qt_enum("WindowModality", "NonModal")
        if modality is not None:
            dialog.setWindowModality(modality)
        dialog.setModal(False)
    except Exception:
        pass
    try:
        delete_on_close = _qt_enum("WidgetAttribute", "WA_DeleteOnClose")
        if delete_on_close is not None:
            dialog.setAttribute(delete_on_close, False)
    except Exception:
        pass
    for member_name in ("WA_TranslucentBackground", "WA_NoSystemBackground"):
        try:
            attribute = _qt_enum("WidgetAttribute", member_name)
            if attribute is not None:
                dialog.setAttribute(attribute, True)
        except Exception:
            pass
    try:
        dialog.setAutoFillBackground(False)
    except Exception:
        pass


def _graph_view_for_anchor(anchor_widget):
    if anchor_widget is None:
        return None
    scene = None
    try:
        proxy = anchor_widget.graphicsProxyWidget()
        if proxy is not None:
            scene = proxy.scene()
    except Exception:
        scene = None
    if scene is None:
        try:
            node_item = getattr(anchor_widget, "_node_item", None)
            if node_item is not None:
                scene = node_item.scene()
        except Exception:
            scene = None
    if scene is not None:
        try:
            views = list(scene.views())
        except Exception:
            views = []
        for view in views:
            try:
                if view is not None and view.isVisible() and view.viewport() is not None:
                    return view
            except Exception:
                pass
        for view in views:
            if view is not None:
                return view
    return None


def _agent_popup_target_center(anchor_widget):
    view = _graph_view_for_anchor(anchor_widget)
    if view is not None:
        try:
            viewport = view.viewport()
            return viewport.mapToGlobal(viewport.rect().center())
        except Exception:
            pass
    if anchor_widget is not None:
        try:
            return anchor_widget.mapToGlobal(anchor_widget.rect().center())
        except Exception:
            pass
    return None


def _agent_popup_size(dialog: QtWidgets.QDialog) -> QtCore.QSize:
    try:
        dialog.adjustSize()
    except Exception:
        pass
    try:
        hint = dialog.sizeHint().expandedTo(dialog.minimumSizeHint()).expandedTo(dialog.minimumSize())
    except Exception:
        hint = QtCore.QSize(AGENT_POPUP_MIN_WIDTH, 1)
    try:
        size = dialog.size().expandedTo(hint)
    except Exception:
        size = hint
    width = max(AGENT_POPUP_MIN_WIDTH, int(size.width()))
    height = max(1, int(size.height()))
    try:
        dialog.resize(width, height)
    except Exception:
        pass
    return QtCore.QSize(width, height)


def _position_agent_popup(dialog: QtWidgets.QDialog, anchor_widget) -> None:
    if anchor_widget is None:
        return
    try:
        target_center = _agent_popup_target_center(anchor_widget)
        if target_center is None:
            return
        screen = None
        try:
            screen = QtGui.QGuiApplication.screenAt(target_center)
        except Exception:
            screen = None
        if screen is None:
            try:
                screen = QtWidgets.QApplication.primaryScreen()
            except Exception:
                screen = None
        size = _agent_popup_size(dialog)
        x = int(target_center.x() - (size.width() / 2))
        y = int(target_center.y() - (size.height() / 2))
        if screen is None:
            dialog.move(x, y)
            return
        rect = screen.availableGeometry()
        margin = 12
        x = min(max(x, rect.left() + margin), rect.right() - size.width() - margin)
        y = min(max(y, rect.top() + margin), rect.bottom() - size.height() - margin)
        dialog.move(x, y)
    except Exception:
        pass


def _show_agent_popup(dialog: QtWidgets.QDialog, anchor_widget=None) -> None:
    if anchor_widget is None:
        anchor_widget = getattr(dialog, "_anchor_widget", None)
    _set_agent_popup_chrome(dialog)
    _position_agent_popup(dialog, anchor_widget)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def _event_global_pos(event):
    try:
        return event.globalPosition().toPoint()
    except Exception:
        pass
    try:
        return event.globalPos()
    except Exception:
        return None


def _breakable_popup_text(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, (dict, list)):
            return json.dumps(parsed, ensure_ascii=False, separators=(", ", ": "))
    except Exception:
        pass
    return re.sub(r"([,;:{}\[\]\(\)=])(?=\S)", r"\1 ", clean)


class AgentPopupTextBlock(QtWidgets.QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setMinimumWidth(AGENT_POPUP_DETAIL_TEXT_WIDTH)
        self.setWordWrap(True)
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.setContentsMargins(0, 0, 0, 0)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        except Exception:
            pass
        self.setStyleSheet("QLabel{background:transparent;color:#cbd5e1;padding:0px;margin:0px;}")
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.setText(_breakable_popup_text(text))
        self.fit_to_text()

    def fit_to_text(self, width: int | None = None) -> None:
        try:
            width = max(320, int(width or self.width() or AGENT_POPUP_DETAIL_TEXT_WIDTH))
            self.setMinimumWidth(width)
            self.setMaximumWidth(width)
            height = int(self.heightForWidth(width))
            if height > 0:
                self.setMinimumHeight(max(24, height))
                self.setMaximumHeight(max(24, height))
        except Exception:
            pass
        try:
            self.updateGeometry()
        except Exception:
            pass


class AgentPopupTitleBar(QtWidgets.QFrame):
    def __init__(
        self,
        dialog: QtWidgets.QDialog,
        title: str,
        *,
        accent: str,
        hover_accent: str,
        closable: bool = False,
    ):
        super().__init__(dialog)
        self._dialog = dialog
        self._drag_offset = None
        self.setObjectName("AgentPopupTitleBar")
        self.setFixedHeight(34)
        try:
            hover_attribute = _qt_enum("WidgetAttribute", "WA_Hover")
            if hover_attribute is not None:
                self.setAttribute(hover_attribute, True)
        except Exception:
            pass
        self.setStyleSheet(
            "QFrame#AgentPopupTitleBar{"
            f"background:{accent};"
            f"border-top-left-radius:{AGENT_POPUP_TITLE_RADIUS}px;"
            f"border-top-right-radius:{AGENT_POPUP_TITLE_RADIUS}px;"
            "}"
            "QFrame#AgentPopupTitleBar:hover{"
            f"background:{hover_accent};"
            "}"
            "QLabel{color:#f8fafc;font-size:13px;font-weight:600;}"
            "QPushButton{background:transparent;border:0;border-radius:4px;}"
            "QPushButton:hover{background:rgba(255,255,255,36);}"
        )

        label = QtWidgets.QLabel(str(title or "").strip())
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(6)
        layout.addWidget(label, 1)

        if closable:
            close_btn = QtWidgets.QPushButton()
            close_icon = _popup_close_icon(self)
            if close_icon is not None:
                close_btn.setIcon(close_icon)
            close_btn.setToolTip("Close")
            close_btn.setAccessibleName("Close")
            close_btn.setFixedSize(26, 24)
            close_btn.clicked.connect(dialog.accept)
            layout.addWidget(close_btn, 0)

    def mousePressEvent(self, event):
        try:
            left_button = _qt_enum("MouseButton", "LeftButton") or QtCore.Qt.LeftButton
            if event.button() != left_button:
                return super().mousePressEvent(event)
        except Exception:
            pass
        pos = _event_global_pos(event)
        if pos is not None:
            self._drag_offset = pos - self._dialog.frameGeometry().topLeft()
            try:
                event.accept()
            except Exception:
                pass
            return
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is None:
            return super().mouseMoveEvent(event)
        pos = _event_global_pos(event)
        if pos is not None:
            self._dialog.move(pos - self._drag_offset)
            try:
                event.accept()
            except Exception:
                pass
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        try:
            event.accept()
        except Exception:
            pass


class AgentPopupDialog(QtWidgets.QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(None)
        self._anchor_widget = parent
        self.setWindowTitle(str(title or "").strip())
        self.setMinimumWidth(AGENT_POPUP_MIN_WIDTH)
        self.setObjectName("AgentPopupDialog")
        self.setStyleSheet("QDialog#AgentPopupDialog{background:transparent;border:0px;}")
        _set_agent_popup_chrome(self)

    def _create_popup_surface(self, *, background: str, border: str) -> QtWidgets.QVBoxLayout:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        surface = QtWidgets.QFrame(self)
        surface.setObjectName("AgentPopupSurface")
        try:
            styled_background = _qt_enum("WidgetAttribute", "WA_StyledBackground")
            if styled_background is not None:
                surface.setAttribute(styled_background, True)
        except Exception:
            pass
        surface.setStyleSheet(
            "QFrame#AgentPopupSurface{"
            f"background:{background};"
            f"border:{AGENT_POPUP_BORDER_WIDTH}px solid {border};"
            f"border-radius:{AGENT_POPUP_RADIUS}px;"
            "}"
        )
        root_layout.addWidget(surface, 1)

        surface_layout = QtWidgets.QVBoxLayout(surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        return surface_layout


def _clean_tanya_popup_text(text: str) -> str:
    clean = str(text or "").strip()
    feedback_match = re.search(
        r"<\s*(?:user[\s_-]*feedback|user[\s_-]*feed[\s_-]*back|feedback)\s*>"
        r"(.*?)"
        r"<\s*/\s*(?:user[\s_-]*feedback|user[\s_-]*feed[\s_-]*back|feedback)\s*>",
        clean,
        re.IGNORECASE | re.DOTALL,
    )
    if feedback_match:
        clean = str(feedback_match.group(1) or "").strip()
    clean = _strip_data_nexus_update_tags(clean)
    clean = SECURITY_REQUEST_RE.sub("", clean)
    clean = SECURITY_APPROVAL_RE.sub("", clean)
    clean = re.sub(
        r"<\s*/?\s*(?:security_request|security_approval|data_nexus_update|sales_agent_review|sales_agent_questions|sales_agent_draft)\b[^>]*>",
        "",
        clean,
        flags=re.IGNORECASE | re.DOTALL,
    )
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip()


def _speech_popup_delay_ms(text: str) -> int:
    words = re.findall(r"\S+", str(text or ""))
    return max(2500, min(8000, 1200 + (len(words) * 320)))


class TanyaSpeechDialog(AgentPopupDialog):
    def __init__(self, *, message: str, parent=None):
        super().__init__("Tanya", parent=parent)

        tanya_icon_path = _tanya_agent_icon_path()
        if tanya_icon_path is not None:
            try:
                self.setWindowIcon(QtGui.QIcon(str(tanya_icon_path)))
            except Exception:
                pass

        icon_label = QtWidgets.QLabel()
        icon_pixmap = _scaled_pixmap(tanya_icon_path, 58) if tanya_icon_path is not None else None
        if icon_pixmap is not None:
            icon_label.setPixmap(icon_pixmap)
        else:
            icon = _style_icon(self, "SP_MessageBoxInformation")
            if icon is not None:
                icon_label.setPixmap(icon.pixmap(42, 42))
        icon_label.setFixedSize(64, 64)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        title = QtWidgets.QLabel("Tanya")
        title.setStyleSheet("QLabel{color:#f8fafc;font-size:16px;font-weight:600;}")
        detail = QtWidgets.QLabel(str(message or "").strip())
        detail.setWordWrap(True)
        detail.setStyleSheet("QLabel{color:#e5e7eb;}")
        detail.setMinimumWidth(380)

        close_btn = QtWidgets.QPushButton()
        close_icon = _popup_close_icon(self)
        if close_icon is not None:
            close_btn.setIcon(close_icon)
        close_btn.setToolTip("Close")
        close_btn.setAccessibleName("Close")
        close_btn.setFixedSize(36, 32)
        close_btn.clicked.connect(self.accept)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        text_col.addWidget(title, 0)
        text_col.addWidget(detail, 1)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(icon_label, 0, QtCore.Qt.AlignTop)
        row.addLayout(text_col, 1)

        close_row = QtWidgets.QHBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        close_row.addStretch(1)
        close_row.addWidget(close_btn, 0)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        content_layout.addLayout(row, 1)
        content_layout.addLayout(close_row, 0)

        layout = self._create_popup_surface(background="#111018", border="#5b314f")
        layout.addWidget(AgentPopupTitleBar(self, "Tanya", accent="#5b314f", hover_accent="#744062"), 0)
        layout.addWidget(content, 1)
        self.resize(AGENT_POPUP_MIN_WIDTH, 190)


class WebDesignerResponseDialog(AgentPopupDialog):
    def __init__(self, *, message: str, parent=None):
        super().__init__("Web Designer", parent=parent)

        agent_icon_path = _web_designer_agent_icon_path()
        if agent_icon_path is not None:
            try:
                self.setWindowIcon(QtGui.QIcon(str(agent_icon_path)))
            except Exception:
                pass

        icon_label = QtWidgets.QLabel()
        icon_pixmap = _scaled_pixmap(agent_icon_path, 62) if agent_icon_path is not None else None
        if icon_pixmap is not None:
            icon_label.setPixmap(icon_pixmap)
        else:
            icon = _style_icon(self, "SP_DesktopIcon")
            if icon is not None:
                icon_label.setPixmap(icon.pixmap(44, 44))
        icon_label.setFixedSize(68, 68)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        title = QtWidgets.QLabel("Web Designer")
        title.setStyleSheet("QLabel{color:#f8fafc;font-size:16px;font-weight:700;}")
        subtitle = QtWidgets.QLabel("Website implementation response")
        subtitle.setStyleSheet("QLabel{color:#93c5fd;font-size:12px;}")

        detail = AgentPopupTextBlock(str(message or "").strip())
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setMinimumHeight(120)
        scroll.setMaximumHeight(360)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:0;}"
            "QScrollBar:vertical{background:#0f172a;width:10px;margin:0;}"
            "QScrollBar::handle:vertical{background:#2563eb;border-radius:4px;min-height:24px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
        )
        scroll.setWidget(detail)

        close_btn = QtWidgets.QPushButton()
        close_icon = _popup_close_icon(self)
        if close_icon is not None:
            close_btn.setIcon(close_icon)
        close_btn.setToolTip("Close")
        close_btn.setAccessibleName("Close")
        close_btn.setFixedSize(36, 32)
        close_btn.clicked.connect(self.accept)

        header_text = QtWidgets.QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(4)
        header_text.addWidget(title, 0)
        header_text.addWidget(subtitle, 0)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)
        header_row.addWidget(icon_label, 0, QtCore.Qt.AlignTop)
        header_row.addLayout(header_text, 1)

        close_row = QtWidgets.QHBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        close_row.addStretch(1)
        close_row.addWidget(close_btn, 0)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        content_layout.addLayout(header_row, 0)
        content_layout.addWidget(scroll, 1)
        content_layout.addLayout(close_row, 0)

        layout = self._create_popup_surface(background="#07111f", border="#2563eb")
        layout.addWidget(AgentPopupTitleBar(self, "Web Designer", accent="#1d4ed8", hover_accent="#2563eb"), 0)
        layout.addWidget(content, 1)
        self.resize(AGENT_POPUP_MIN_WIDTH, 330)


class SecurityApprovalDialog(AgentPopupDialog):
    def __init__(self, *, request_text: str, attrs: dict[str, str], parent=None):
        super().__init__("Security Guard", parent=parent)

        requester = str(attrs.get("requester", "") or "Tanya").strip() or "Tanya"
        tool = str(attrs.get("tool", "") or "qubit_deck_controller").strip() or "qubit_deck_controller"
        action = str(attrs.get("requested_action", "") or "invoke").strip() or "invoke"
        target = str(attrs.get("target", "") or "").strip()
        reason = str(attrs.get("reason", "") or "").strip()
        agent_icon_path = _security_agent_icon_path()
        if agent_icon_path is not None:
            try:
                self.setWindowIcon(QtGui.QIcon(str(agent_icon_path)))
            except Exception:
                pass

        icon_label = QtWidgets.QLabel()
        icon_pixmap = _scaled_pixmap(agent_icon_path, 52) if agent_icon_path is not None else None
        if icon_pixmap is not None:
            icon_label.setPixmap(icon_pixmap)
        else:
            icon = _style_icon(self, "SP_MessageBoxWarning")
            if icon is not None:
                icon_label.setPixmap(icon.pixmap(42, 42))
        icon_label.setFixedSize(58, 58)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        title = QtWidgets.QLabel("Qubit Deck access")
        title.setStyleSheet("QLabel{color:#f8fafc;font-size:15px;font-weight:600;}")
        route = QtWidgets.QLabel(f"{requester} -> {tool}")
        route.setStyleSheet("QLabel{color:#93c5fd;}")
        summary = QtWidgets.QLabel(f"{action}: {target or '(unspecified target)'}")
        summary.setWordWrap(True)
        summary.setStyleSheet("QLabel{color:#e5e7eb;}")
        detail = QtWidgets.QLabel(reason or request_text)
        detail.setWordWrap(True)
        detail.setStyleSheet("QLabel{color:#cbd5e1;}")
        detail.setMinimumWidth(380)

        approve_btn = QtWidgets.QPushButton()
        approve_icon = _style_icon(self, "SP_DialogApplyButton")
        if approve_icon is not None:
            approve_btn.setIcon(approve_icon)
        approve_btn.setToolTip("Grant access")
        approve_btn.setAccessibleName("Grant access")
        approve_btn.setFixedSize(44, 36)
        approve_btn.setStyleSheet(
            "QPushButton{background:#0f766e;border:1px solid #115e59;border-radius:6px;}"
            "QPushButton:hover{background:#0d9488;}"
        )

        deny_btn = QtWidgets.QPushButton()
        deny_icon = _style_icon(self, "SP_DialogCancelButton")
        if deny_icon is not None:
            deny_btn.setIcon(deny_icon)
        deny_btn.setToolTip("Deny access")
        deny_btn.setAccessibleName("Deny access")
        deny_btn.setFixedSize(44, 36)
        deny_btn.setStyleSheet(
            "QPushButton{background:#7f1d1d;border:1px solid #991b1b;border-radius:6px;}"
            "QPushButton:hover{background:#991b1b;}"
        )
        approve_btn.clicked.connect(self.accept)
        deny_btn.clicked.connect(self.reject)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        btn_row.addWidget(deny_btn, 0)
        btn_row.addWidget(approve_btn, 0)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        text_col.addWidget(title, 0)
        text_col.addWidget(route, 0)
        text_col.addWidget(summary, 0)
        text_col.addWidget(detail, 0)
        text_col.addLayout(btn_row, 0)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(12)
        top_row.addWidget(icon_label, 0, QtCore.Qt.AlignTop)
        top_row.addLayout(text_col, 1)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        content_layout.addLayout(top_row, 1)

        layout = self._create_popup_surface(background="#0b1220", border="#334155")
        layout.addWidget(
            AgentPopupTitleBar(self, "Security Guard", accent="#334155", hover_accent="#475569"),
            0,
        )
        layout.addWidget(content, 1)
        self.resize(AGENT_POPUP_MIN_WIDTH, 190)


class DataNexusDeletionApprovalDialog(AgentPopupDialog):
    def __init__(self, *, targets: list[dict[str, str]], request_text: str, parent=None):
        super().__init__("Data Nexus Approval", parent=parent)

        count = len(targets or [])
        icon_label = QtWidgets.QLabel()
        icon = _style_icon(self, "SP_MessageBoxWarning")
        if icon is not None:
            icon_label.setPixmap(icon.pixmap(42, 42))
        icon_label.setFixedSize(58, 58)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        title = QtWidgets.QLabel("Approve Data Nexus deletion")
        title.setStyleSheet("QLabel{color:#f8fafc;font-size:15px;font-weight:600;}")
        summary = QtWidgets.QLabel(f"{count} point{'s' if count != 1 else ''} will be removed from the graph and vault.")
        summary.setWordWrap(True)
        summary.setStyleSheet("QLabel{color:#fecaca;}")
        request = QtWidgets.QLabel(str(request_text or "").strip()[:400])
        request.setWordWrap(True)
        request.setStyleSheet("QLabel{color:#cbd5e1;}")
        request.setMinimumWidth(380)

        target_list = QtWidgets.QListWidget()
        target_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        target_list.setFocusPolicy(QtCore.Qt.NoFocus)
        target_list.setMinimumWidth(380)
        target_list.setMinimumHeight(96)
        target_list.setMaximumHeight(190)
        target_list.setStyleSheet(
            "QListWidget{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:4px;}"
            "QListWidget::item{padding:5px 4px;border-bottom:1px solid #1f2937;}"
        )
        for target in targets or []:
            label = str(target.get("label", "") or target.get("id", "") or "Point").strip()
            point_id = str(target.get("id", "") or "").strip()
            file_name = str(target.get("file", "") or "").strip()
            note = str(target.get("note", "") or "").strip()
            parts = [label]
            meta = " / ".join(part for part in (point_id, file_name) if part)
            if meta:
                parts.append(meta)
            if note:
                parts.append(note)
            item = QtWidgets.QListWidgetItem("\n".join(parts))
            item.setToolTip("\n".join(parts))
            target_list.addItem(item)

        approve_btn = QtWidgets.QPushButton()
        approve_icon = _style_icon(self, "SP_DialogApplyButton")
        if approve_icon is not None:
            approve_btn.setIcon(approve_icon)
        approve_btn.setToolTip("Approve deletion")
        approve_btn.setAccessibleName("Approve deletion")
        approve_btn.setFixedSize(44, 36)
        approve_btn.setStyleSheet(
            "QPushButton{background:#b91c1c;border:1px solid #991b1b;border-radius:6px;}"
            "QPushButton:hover{background:#dc2626;}"
        )

        deny_btn = QtWidgets.QPushButton()
        deny_icon = _style_icon(self, "SP_DialogCancelButton")
        if deny_icon is not None:
            deny_btn.setIcon(deny_icon)
        deny_btn.setToolTip("Cancel deletion")
        deny_btn.setAccessibleName("Cancel deletion")
        deny_btn.setFixedSize(44, 36)
        deny_btn.setStyleSheet(
            "QPushButton{background:#334155;border:1px solid #475569;border-radius:6px;}"
            "QPushButton:hover{background:#475569;}"
        )
        approve_btn.clicked.connect(self.accept)
        deny_btn.clicked.connect(self.reject)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        btn_row.addWidget(deny_btn, 0)
        btn_row.addWidget(approve_btn, 0)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(7)
        text_col.addWidget(title, 0)
        text_col.addWidget(summary, 0)
        if request.text().strip():
            text_col.addWidget(request, 0)
        text_col.addWidget(target_list, 1)
        text_col.addLayout(btn_row, 0)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(12)
        top_row.addWidget(icon_label, 0, QtCore.Qt.AlignTop)
        top_row.addLayout(text_col, 1)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        content_layout.addLayout(top_row, 1)

        layout = self._create_popup_surface(background="#1f1115", border="#7f1d1d")
        layout.addWidget(
            AgentPopupTitleBar(self, "Data Nexus", accent="#7f1d1d", hover_accent="#991b1b"),
            0,
        )
        layout.addWidget(content, 1)
        self.resize(AGENT_POPUP_MIN_WIDTH, 330)


class QDeckHandoffDialog(AgentPopupDialog):
    def __init__(self, *, request_text: str, voice_input: str, parent=None):
        super().__init__("Qubit Deck Operator", parent=parent)
        operator_icon_path = _operator_agent_icon_path()
        if operator_icon_path is not None:
            try:
                self.setWindowIcon(QtGui.QIcon(str(operator_icon_path)))
            except Exception:
                pass

        icon_label = QtWidgets.QLabel()
        icon_pixmap = _scaled_pixmap(operator_icon_path, 52) if operator_icon_path is not None else None
        if icon_pixmap is not None:
            icon_label.setPixmap(icon_pixmap)
        else:
            icon = _style_icon(self, "SP_ComputerIcon")
            if icon is not None:
                icon_label.setPixmap(icon.pixmap(42, 42))
        icon_label.setFixedSize(58, 58)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        self._title = QtWidgets.QLabel("Qubit Deck controller")
        self._title.setStyleSheet("QLabel{color:#f8fafc;font-size:15px;font-weight:600;}")
        self._phase = QtWidgets.QLabel("Running qubit_deck_controller.md")
        self._phase.setStyleSheet("QLabel{color:#93c5fd;}")
        self._detail = AgentPopupTextBlock(str(voice_input or request_text or "").strip())

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        self._progress.setStyleSheet(
            "QProgressBar{background:#111827;border:1px solid #334155;border-radius:4px;}"
            "QProgressBar::chunk{background:#22c55e;border-radius:4px;}"
        )

        self._close_btn = QtWidgets.QPushButton()
        close_icon = _popup_close_icon(self)
        if close_icon is not None:
            self._close_btn.setIcon(close_icon)
        self._close_btn.setToolTip("Close")
        self._close_btn.setAccessibleName("Close")
        self._close_btn.setFixedSize(36, 32)
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        text_col.addWidget(self._title, 0)
        text_col.addWidget(self._phase, 0)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(icon_label, 0, QtCore.Qt.AlignTop)
        row.addLayout(text_col, 1)

        close_row = QtWidgets.QHBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        close_row.addStretch(1)
        close_row.addWidget(self._close_btn, 0)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        content_layout.addLayout(row, 0)
        content_layout.addWidget(self._detail, 0)
        content_layout.addWidget(self._progress, 0)
        content_layout.addLayout(close_row, 0)

        layout = self._create_popup_surface(background="#0b1220", border="#334155")
        layout.addWidget(
            AgentPopupTitleBar(self, "Qubit Deck Operator", accent="#334155", hover_accent="#475569"),
            0,
        )
        layout.addWidget(content, 1)
        self.resize(AGENT_POPUP_MIN_WIDTH, 180)
        self._resize_to_detail_text()

    def _detail_text_width(self) -> int:
        try:
            width = int(self.width() or AGENT_POPUP_MIN_WIDTH)
        except Exception:
            width = AGENT_POPUP_MIN_WIDTH
        return max(
            320,
            width - (AGENT_POPUP_BORDER_WIDTH * 2) - (AGENT_POPUP_CONTENT_MARGIN_X * 2),
        )

    def _resize_to_detail_text(self) -> None:
        try:
            self._detail.fit_to_text(self._detail_text_width())
        except Exception:
            pass
        try:
            layout = self.layout()
            if layout is not None:
                layout.activate()
        except Exception:
            pass
        try:
            hint = self.sizeHint().expandedTo(self.minimumSizeHint()).expandedTo(self.minimumSize())
            self.resize(max(AGENT_POPUP_MIN_WIDTH, int(hint.width())), max(1, int(hint.height())))
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        try:
            super().resizeEvent(event)
        except Exception:
            pass
        try:
            self._detail.fit_to_text(self._detail_text_width())
        except Exception:
            pass

    def finish(self, *, message: str, error: bool = False) -> None:
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        if error:
            self._phase.setText("Handoff failed")
            self._phase.setStyleSheet("QLabel{color:#fca5a5;}")
        else:
            self._phase.setText("Command published to Qubit Deck executor")
            self._phase.setStyleSheet("QLabel{color:#86efac;}")
        if message:
            self._detail.set_text(str(message or "").strip()[:500])
        self._resize_to_detail_text()
        self._close_btn.setEnabled(True)


class MediatorConsoleWidget(QtWidgets.QWidget):
    _console_append = QtCore.Signal(str)
    _console_flush_requested = QtCore.Signal()
    _command_done = QtCore.Signal(int, str, str, str, str)

    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._workspace_dir = _workspace_dir_for_node(node_item)
        self._process = None
        self._process_lock = threading.Lock()
        self._running = False
        self._scene = None
        self._scene_connected = False
        self._last_processed_signature = ""
        self._last_voice_mode = ""
        self._last_auto_voice_input = ""
        self._last_auto_voice_token = ""
        self._auto_baseline_ready = False
        self._pending_prompt = ""
        self._pending_signature = ""
        self._pending_source = ""
        self._stop_requested = False
        self._last_prompt_voice_input = ""
        self._last_prompt_chatbot_history = ""
        self._current_chatbot_prompt_session_token = ""
        self._security_dialog = None
        self._data_nexus_delete_dialog = None
        self._qdeck_handoff_dialog = None
        self._qdeck_handoff_active = False
        self._chatbot_handoff_active = False
        self._tanya_dialog = None
        self._tanya_popup_message = ""
        self._last_tanya_speech_key = ""
        self._last_tanya_speech_ms = 0
        self._web_designer_dialog = None
        self._last_web_designer_popup_key = ""
        self._last_web_designer_popup_ms = 0
        self._console_pending_lines: list[str] = []
        self._console_pending_lock = threading.Lock()
        self._console_flush_scheduled = False
        self._last_console_log_prune_ms = 0

        self.setMinimumSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        self._workspace_label = QtWidgets.QLabel(f"Workspace: {self._workspace_dir}")
        self._workspace_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._workspace_label.setStyleSheet("QLabel{color:#93c5fd;}")

        self._command_edit = QtWidgets.QLineEdit()
        self._command_edit.setPlaceholderText("Enter a command (for example: codex login status)")
        self._command_edit.returnPressed.connect(self._run_command)
        self._command_edit.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:4px 6px;}"
        )

        detected_codex = codex_cli_runner.detect_codex_executable()
        if detected_codex:
            self._command_edit.setText(codex_cli_runner.format_command([detected_codex, "login", "status"]))

        self._console = QtWidgets.QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setStyleSheet(
            "QPlainTextEdit{background:#0b1220;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px;}"
        )
        try:
            self._console.setMaximumBlockCount(MEDIGATOR_MAX_CONSOLE_LOG_LINES)
        except Exception:
            pass

        self._status = QtWidgets.QLabel("Ready.")
        self._status.setStyleSheet("QLabel{color:#94a3b8;}")

        self._prompt_profiles = _available_prompt_profiles()
        self._profile_label = QtWidgets.QLabel("Prompt Profile")
        self._profile_label.setStyleSheet("QLabel{color:#cbd5e1;}")
        self._profile_combo = QtWidgets.QComboBox()
        self._profile_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox:disabled{background:#1f2937;color:#94a3b8;border-color:#334155;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        for profile in self._prompt_profiles:
            self._profile_combo.addItem(profile, profile)
        model = getattr(self._node_item, "model", None)
        saved_profile = _resolve_prompt_profile(
            _param_value(model, MEDIGATOR_PROMPT_PROFILE_PARAM, MEDIGATOR_DEFAULT_PROMPT_PROFILE),
            self._prompt_profiles,
        )
        self._profile_combo.blockSignals(True)
        try:
            profile_idx = int(self._profile_combo.findData(saved_profile))
        except Exception:
            profile_idx = -1
        if profile_idx < 0:
            try:
                profile_idx = int(self._profile_combo.findText(saved_profile))
            except Exception:
                profile_idx = -1
        if profile_idx < 0:
            profile_idx = 0
        self._profile_combo.setCurrentIndex(profile_idx)
        self._profile_combo.blockSignals(False)
        _set_param_value(
            self._node_item,
            MEDIGATOR_PROMPT_PROFILE_PARAM,
            self._selected_prompt_profile(),
            notify_scene=False,
        )
        _ensure_hidden_params(model, [MEDIGATOR_PROMPT_PROFILE_PARAM])

        self._run_btn = QtWidgets.QPushButton("Run Cmd")
        self._process_btn = QtWidgets.QPushButton("Process Inputs")
        self._auto_chk = QtWidgets.QCheckBox("Auto")
        self._auto_chk.setChecked(True)
        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._clear_btn = QtWidgets.QPushButton("Clear")
        self._open_btn = QtWidgets.QPushButton("Open Folder")

        self._run_btn.clicked.connect(self._run_command)
        self._process_btn.clicked.connect(self._process_inputs_manual)
        self._stop_btn.clicked.connect(self._stop_command)
        self._clear_btn.clicked.connect(self._clear_console)
        self._open_btn.clicked.connect(self._open_workspace_folder)
        self._profile_combo.currentIndexChanged.connect(self._on_prompt_profile_changed)

        self._run_btn.setStyleSheet(
            "QPushButton{background:#1d4ed8;color:#e2e8f0;border:1px solid #1e3a8a;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#1e40af;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
        )
        self._process_btn.setStyleSheet(
            "QPushButton{background:#0f766e;color:#e2e8f0;border:1px solid #115e59;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#0d9488;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
        )
        self._stop_btn.setStyleSheet(
            "QPushButton{background:#991b1b;color:#fee2e2;border:1px solid #7f1d1d;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#b91c1c;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#334155;}"
        )
        self._clear_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )
        self._open_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )
        self._auto_chk.setStyleSheet("QCheckBox{color:#cbd5e1;}")

        profile_row = QtWidgets.QHBoxLayout()
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.setSpacing(6)
        profile_row.addWidget(self._profile_label, 0)
        profile_row.addWidget(self._profile_combo, 1)

        command_row = QtWidgets.QHBoxLayout()
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.setSpacing(6)
        command_row.addWidget(self._command_edit, 1)
        command_row.addWidget(self._run_btn, 0)
        command_row.addWidget(self._process_btn, 0)
        command_row.addWidget(self._auto_chk, 0)
        command_row.addWidget(self._stop_btn, 0)
        command_row.addWidget(self._clear_btn, 0)
        command_row.addWidget(self._open_btn, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._workspace_label, 0)
        layout.addLayout(profile_row, 0)
        layout.addLayout(command_row, 0)
        layout.addWidget(self._console, 1)
        layout.addWidget(self._status, 0)

        self._console_append.connect(self._queue_console_line)
        self._console_flush_requested.connect(self._schedule_console_flush)
        self._command_done.connect(self._on_command_done)
        self._update_controls()

        self._console_flush_timer = QtCore.QTimer(self)
        self._console_flush_timer.setSingleShot(True)
        self._console_flush_timer.setInterval(MEDIGATOR_CONSOLE_FLUSH_INTERVAL_MS)
        self._console_flush_timer.timeout.connect(self._flush_console_lines)

        self._scene_timer = QtCore.QTimer(self)
        self._scene_timer.setInterval(250)
        self._scene_timer.timeout.connect(self._ensure_scene)
        self._scene_timer.start()
        QtCore.QTimer.singleShot(650, self._sync_auto_baseline)

    def sizeHint(self):
        return QtCore.QSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)

    def _console_log_path(self) -> Path:
        return self._workspace_dir / "console.log"

    def _history_path(self) -> Path:
        return self._workspace_dir / "command_history.log"

    def _write_console_log(self, line: str) -> None:
        self._write_console_log_lines([line])

    def _write_console_log_lines(self, lines: list[str]) -> None:
        clean_lines = [str(line or "").rstrip() for line in (lines or []) if str(line or "").rstrip()]
        if not clean_lines:
            return
        stamp = _format_ts()
        payload = "".join(f"[{stamp}] {line}\n" for line in clean_lines)
        path = self._console_log_path()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            return
        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        last_ms = int(getattr(self, "_last_console_log_prune_ms", 0) or 0)
        if now_ms - last_ms < MEDIGATOR_CONSOLE_LOG_PRUNE_INTERVAL_MS:
            return
        self._last_console_log_prune_ms = now_ms
        _prune_text_log_tail(path, keep_lines=MEDIGATOR_MAX_CONSOLE_LOG_LINES)

    def _write_history(self, command: str) -> None:
        payload = f"[{_format_ts()}] {command.strip()}\n"
        try:
            with self._history_path().open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            pass

    def _queue_console_line_threadsafe(self, line: str) -> None:
        text = str(line or "").rstrip("\r\n")
        if not text:
            return
        request_flush = False
        with self._console_pending_lock:
            self._console_pending_lines.append(text)
            if not self._console_flush_scheduled:
                self._console_flush_scheduled = True
                request_flush = True
        if request_flush:
            self._console_flush_requested.emit()

    @QtCore.Slot(str)
    def _queue_console_line(self, line: str) -> None:
        self._queue_console_line_threadsafe(line)

    @QtCore.Slot()
    def _schedule_console_flush(self) -> None:
        timer = getattr(self, "_console_flush_timer", None)
        if timer is None:
            self._flush_console_lines()
            return
        if not timer.isActive():
            timer.start()

    @QtCore.Slot()
    def _flush_console_lines(self) -> None:
        max_lines = max(1, int(MEDIGATOR_CONSOLE_MAX_LINES_PER_FLUSH))
        request_next_flush = False
        with self._console_pending_lock:
            if len(self._console_pending_lines) > max_lines:
                lines = self._console_pending_lines[:max_lines]
                del self._console_pending_lines[:max_lines]
                self._console_flush_scheduled = True
                request_next_flush = True
            else:
                lines = list(self._console_pending_lines)
                self._console_pending_lines.clear()
                self._console_flush_scheduled = False
        if not lines:
            return
        self._append_console_lines(lines)
        if request_next_flush:
            self._console_flush_requested.emit()

    def _append_console_lines(self, lines: list[str]) -> None:
        clean_lines = [str(line or "").rstrip("\r\n") for line in (lines or []) if str(line or "").rstrip("\r\n")]
        if not clean_lines:
            return
        self._console.appendPlainText("\n".join(clean_lines))
        self._write_console_log_lines(clean_lines)
        try:
            bar = self._console.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status.setText(message or "")
        if error:
            self._status.setStyleSheet("QLabel{color:#fca5a5;}")
        else:
            self._status.setStyleSheet("QLabel{color:#94a3b8;}")

    def _set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._update_controls()
        try:
            self._node_item.setBusyState(self._running, "mediator-running" if self._running else "")
        except Exception:
            pass

    def _update_controls(self) -> None:
        self._run_btn.setEnabled(not self._running)
        self._process_btn.setEnabled(not self._running)
        self._stop_btn.setEnabled(self._running)
        self._command_edit.setEnabled(not self._running)
        self._auto_chk.setEnabled(not self._running)
        self._profile_combo.setEnabled(not self._running)

    def _selected_prompt_profile(self) -> str:
        known = list(getattr(self, "_prompt_profiles", None) or [])
        raw = ""
        try:
            data = self._profile_combo.currentData()
        except Exception:
            data = ""
        if data is not None:
            raw = str(data or "").strip()
        if not raw:
            try:
                raw = str(self._profile_combo.currentText() or "").strip()
            except Exception:
                raw = ""
        return _resolve_prompt_profile(raw, known)

    def _selected_profile_prompt(self) -> str:
        return _load_prompt_profile_text(self._selected_prompt_profile())

    @QtCore.Slot(int)
    def _on_prompt_profile_changed(self, _index: int) -> None:
        profile = self._selected_prompt_profile()
        _ensure_hidden_params(getattr(self._node_item, "model", None), [MEDIGATOR_PROMPT_PROFILE_PARAM])
        _set_param_value(self._node_item, MEDIGATOR_PROMPT_PROFILE_PARAM, profile, notify_scene=True)
        self._last_processed_signature = ""
        if profile == QDECK_PROMPT_PROFILE:
            self._set_status(
                "Prompt profile: qubit_deck_controller (reads VoiceActor + QubitDeckController context only)."
            )
        else:
            self._set_status(f"Prompt profile: {profile}")

    def _auto_enabled(self) -> bool:
        try:
            return bool(self._auto_chk.isChecked())
        except Exception:
            return True

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is not None and not self._scene_connected:
            if hasattr(self._scene, "linksChanged"):
                try:
                    self._scene.linksChanged.connect(self._on_scene_links_changed)
                except Exception:
                    pass
            if hasattr(self._scene, "paramChanged"):
                try:
                    self._scene.paramChanged.connect(self._on_scene_param_changed)
                except Exception:
                    pass
            self._scene_connected = True
            try:
                if self._scene_timer is not None:
                    self._scene_timer.stop()
            except Exception:
                pass
            self._sync_auto_baseline()
        return self._scene

    def _sync_auto_baseline(self) -> None:
        scene = self._ensure_scene()
        if scene is None:
            return
        _system_prompt, _chatbot_history, voice_input = self._collect_inputs()
        self._last_auto_voice_input = str(voice_input or "").strip()
        voice_item = self._connected_voice_actor_item()
        if voice_item is None:
            self._last_voice_mode = ""
            self._last_auto_voice_token = ""
        else:
            self._last_voice_mode = _voice_actor_mode_from_item(voice_item)
            self._last_auto_voice_token = _voice_actor_send_token(voice_item)
        self._auto_baseline_ready = True

    def _connected_source_names(self) -> set[str]:
        scene = self._ensure_scene()
        if scene is None:
            return set()
        profile = self._selected_prompt_profile()
        names = set()
        for edge in _collectable_in_edges(scene, self._node_item, profile):
            src = getattr(edge, "src", None)
            if src is None:
                continue
            name = _node_name(src)
            if name:
                names.add(name)
        return names

    def _connected_voice_actor_item(self):
        scene = self._ensure_scene()
        if scene is None:
            return None
        profile = self._selected_prompt_profile()
        edges = _collectable_in_edges(scene, self._node_item, profile)
        for edge in edges:
            src = getattr(edge, "src", None)
            if src is None:
                continue
            if _kind_of_item(src) in VOICE_ACTOR_KINDS:
                return src
        return None

    def _collect_inputs_for_profile(self, profile: str, *, include_pending_security: bool = True) -> tuple[str, str, str]:
        scene = self._ensure_scene()
        if scene is None:
            return "", "", ""
        profile = _normalize_prompt_profile(profile)
        policy = _input_policy_for_profile(profile)
        if bool(policy.get("prefer_named", True)) and _has_named_input_edges(scene, self._node_item):
            system_prompt, chatbot_history, voice_input = _collect_inputs_from_named_ports(scene, self._node_item, profile)
        else:
            # Single-pin mode: route by upstream node kind policy.
            system_prompt, chatbot_history, voice_input = _collect_inputs_from_default_pin(scene, self._node_item, profile)

        if include_pending_security and profile == SECURITY_GUARD_PROMPT_PROFILE:
            pending_context = self._pending_security_context()
            if pending_context:
                chatbot_history = "\n\n".join(
                    part for part in (chatbot_history, pending_context) if part
                ).strip()
        return system_prompt, chatbot_history, voice_input

    def _collect_inputs(self) -> tuple[str, str, str]:
        return self._collect_inputs_for_profile(self._selected_prompt_profile())

    def _pending_security_context(self) -> str:
        model = getattr(self._node_item, "model", None)
        request_text = _param_value(model, MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM, "").strip()
        if not request_text:
            return ""
        requester_node = _param_value(model, MEDIGATOR_PENDING_SECURITY_REQUESTER_NODE_PARAM, "").strip()
        original_input = _param_value(model, MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM, "").strip()
        lines = ["Pending controlled-tool access request:"]
        if requester_node:
            lines.append(f"Requester node: {requester_node}")
        lines.append(request_text)
        if original_input:
            lines.append("")
            lines.append("Original user request:")
            lines.append(original_input)
        return "\n".join(lines).strip()

    def _store_security_request_state(
        self,
        *,
        request_text: str,
        requester_node_name: str,
        original_input: str,
        request_signature: str,
        guard_item=None,
    ) -> None:
        _set_param_value(self._node_item, MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM, request_text, notify_scene=False)
        _set_param_value(self._node_item, MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM, original_input, notify_scene=False)
        _set_param_value(self._node_item, MEDIGATOR_PENDING_SECURITY_SIGNATURE_PARAM, request_signature, notify_scene=False)
        _ensure_hidden_params(getattr(self._node_item, "model", None), SECURITY_HIDDEN_PARAMS)

        if guard_item is None:
            return
        _set_param_value(guard_item, MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM, request_text, notify_scene=False)
        _set_param_value(guard_item, MEDIGATOR_PENDING_SECURITY_REQUESTER_NODE_PARAM, requester_node_name, notify_scene=False)
        _set_param_value(guard_item, MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM, original_input, notify_scene=False)
        _set_param_value(guard_item, MEDIGATOR_PENDING_SECURITY_SIGNATURE_PARAM, request_signature, notify_scene=False)
        _ensure_hidden_params(getattr(guard_item, "model", None), SECURITY_HIDDEN_PARAMS)

    def _clear_security_request_state(self, *items) -> None:
        for item in items or ():
            if item is None:
                continue
            for param_name in SECURITY_HIDDEN_PARAMS:
                _set_param_value(item, param_name, "", notify_scene=False)

    def _publish_security_popup_decision_to_guard(
        self,
        *,
        guard_item,
        guard_widget,
        approval_text: str,
        decision: str,
    ) -> None:
        if guard_item is None:
            return
        detail = "Approved by user through Security Guard popup." if decision == "approved" else "Denied by user through Security Guard popup."
        _set_node_info(guard_item, "\n".join([approval_text, detail]).strip())
        self._clear_security_request_state(guard_item)
        if guard_widget is not None:
            try:
                guard_widget._console_append.emit(f"[security] {detail}")
                guard_widget._set_status(detail)
            except Exception:
                pass

    def _show_security_request_dialog(
        self,
        *,
        request_text: str,
        attrs: dict[str, str],
        requester_node_name: str,
        original_input: str,
        guard_item=None,
        guard_widget=None,
        delay_for_tanya: bool = True,
    ) -> None:
        if delay_for_tanya:
            tanya_dialog = getattr(self, "_tanya_dialog", None)
            try:
                tanya_visible = bool(tanya_dialog is not None and tanya_dialog.isVisible())
            except Exception:
                tanya_visible = tanya_dialog is not None
            if tanya_visible:
                delay_ms = _speech_popup_delay_ms(getattr(self, "_tanya_popup_message", "") or request_text)
                self._set_status("Tanya is speaking; Security Guard popup will open next.")

                def _open_after_tanya() -> None:
                    self._show_security_request_dialog(
                        request_text=request_text,
                        attrs=attrs,
                        requester_node_name=requester_node_name,
                        original_input=original_input,
                        guard_item=guard_item,
                        guard_widget=guard_widget,
                        delay_for_tanya=False,
                    )

                QtCore.QTimer.singleShot(delay_ms, self, _open_after_tanya)
                return
        self._close_tanya_popup()
        old_dialog = getattr(self, "_security_dialog", None)
        try:
            if old_dialog is not None and old_dialog.isVisible():
                old_dialog.raise_()
                old_dialog.activateWindow()
                self._set_status("Security Guard popup is already waiting for user approval.")
                return
        except Exception:
            pass

        dialog = SecurityApprovalDialog(request_text=request_text, attrs=attrs, parent=self)
        self._security_dialog = dialog

        def _finish(decision: str) -> None:
            if getattr(self, "_security_dialog", None) is dialog:
                self._security_dialog = None
            requester = str(attrs.get("requester", "") or "Tanya").strip() or "Tanya"
            tool = str(attrs.get("tool", "") or "qubit_deck_controller").strip() or "qubit_deck_controller"
            approval_text = _security_approval_marker(requester, tool, decision)
            self._publish_security_popup_decision_to_guard(
                guard_item=guard_item,
                guard_widget=guard_widget,
                approval_text=approval_text,
                decision=decision,
            )
            self._process_security_decision_from_guard(
                approval_text=approval_text,
                request_text=request_text,
                original_user_input=original_input,
                guard_node_name=SECURITY_GUARD_POPUP_NAME,
            )

        dialog.accepted.connect(lambda: _finish("approved"))
        dialog.rejected.connect(lambda: _finish("denied"))
        dialog.finished.connect(
            lambda _result=0, _dialog=dialog: self._clear_security_dialog_reference(_dialog)
        )
        dialog.destroyed.connect(
            lambda _obj=None, _dialog=dialog: self._clear_security_dialog_reference(_dialog)
        )
        self._set_status("Security Guard popup is waiting for user approval.")
        try:
            _show_agent_popup(dialog, self)
        except Exception as exc:
            if getattr(self, "_security_dialog", None) is dialog:
                self._security_dialog = None
            self._set_status(f"Security Guard popup failed: {exc}", error=True)

    def _clear_security_dialog_reference(self, dialog) -> None:
        if getattr(self, "_security_dialog", None) is dialog:
            self._security_dialog = None

    def _close_tanya_popup(self) -> None:
        dialog = getattr(self, "_tanya_dialog", None)
        self._tanya_dialog = None
        self._tanya_popup_message = ""
        if dialog is None:
            return
        try:
            dialog.close()
        except Exception:
            pass

    def _show_qdeck_handoff_dialog(self, *, request_text: str, voice_input: str) -> None:
        old_dialog = getattr(self, "_qdeck_handoff_dialog", None)
        try:
            if old_dialog is not None:
                old_dialog.close()
        except Exception:
            pass
        dialog = QDeckHandoffDialog(request_text=request_text, voice_input=voice_input, parent=self)
        self._qdeck_handoff_dialog = dialog
        self._qdeck_handoff_active = True
        dialog.finished.connect(
            lambda _result=0, _dialog=dialog: self._clear_qdeck_handoff_dialog_reference(_dialog)
        )
        dialog.destroyed.connect(
            lambda _obj=None, _dialog=dialog: self._clear_qdeck_handoff_dialog_reference(_dialog)
        )
        try:
            _show_agent_popup(dialog, self)
        except Exception as exc:
            self._clear_qdeck_handoff_dialog_reference(dialog)
            self._set_status(f"Qubit Deck Operator popup failed: {exc}", error=True)

    def _finish_qdeck_handoff_dialog(self, *, message: str, error: bool = False) -> None:
        if not getattr(self, "_qdeck_handoff_active", False):
            return
        self._qdeck_handoff_active = False
        dialog = getattr(self, "_qdeck_handoff_dialog", None)
        if dialog is None:
            return
        try:
            dialog.finish(message=message, error=error)
        except Exception:
            pass

    def _clear_qdeck_handoff_dialog_reference(self, dialog) -> None:
        if getattr(self, "_qdeck_handoff_dialog", None) is dialog:
            self._qdeck_handoff_dialog = None
            self._qdeck_handoff_active = False

    def _maybe_show_tanya_speech_popup(self, output: str, *, source: str) -> None:
        if self._selected_prompt_profile() != TANYA_PROMPT_PROFILE:
            return
        clean_source = str(source or "").strip().lower()
        if clean_source == "security_approval" and getattr(self, "_qdeck_handoff_active", False):
            return
        message = _clean_tanya_popup_text(output)
        if not message:
            return
        lowered = message.lower()
        if "qubit deck app api is not reachable" in lowered:
            dedupe_key = "qdeck_connection_unreachable"
        elif "security guard" in lowered and any(
            token in lowered for token in ("approval", "approve", "permission", "access")
        ):
            target = _qdeck_launch_target_from_request(getattr(self, "_last_prompt_voice_input", "")).lower()
            dedupe_key = f"security_guard_request:{target or 'generic'}"
        else:
            dedupe_key = hashlib.sha1(message.encode("utf-8", errors="ignore")).hexdigest()
        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        last_key = str(getattr(self, "_last_tanya_speech_key", "") or "")
        last_ms = int(getattr(self, "_last_tanya_speech_ms", 0) or 0)
        dedupe_window_ms = max(3500, _speech_popup_delay_ms(message) + 1000)
        if dedupe_key and dedupe_key == last_key and (now_ms - last_ms) < dedupe_window_ms:
            return
        self._last_tanya_speech_key = dedupe_key
        self._last_tanya_speech_ms = now_ms
        self._tanya_popup_message = message
        session_token = str(getattr(self, "_current_chatbot_prompt_session_token", "") or "").strip()
        _publish_mediator_speech_text(self._node_item, message, session_token=session_token)
        old_dialog = getattr(self, "_tanya_dialog", None)
        try:
            if old_dialog is not None:
                old_dialog.close()
        except Exception:
            pass
        dialog = TanyaSpeechDialog(message=message, parent=self)
        self._tanya_dialog = dialog
        dialog.finished.connect(
            lambda _result=0, _dialog=dialog: self._clear_tanya_dialog_reference(_dialog)
        )
        dialog.destroyed.connect(
            lambda _obj=None, _dialog=dialog: self._clear_tanya_dialog_reference(_dialog)
        )
        try:
            _show_agent_popup(dialog, self)
        except Exception as exc:
            self._clear_tanya_dialog_reference(dialog)
            self._set_status(f"Tanya popup failed: {exc}", error=True)

    def _clear_tanya_dialog_reference(self, dialog) -> None:
        if getattr(self, "_tanya_dialog", None) is dialog:
            self._tanya_dialog = None
            self._tanya_popup_message = ""

    def _maybe_show_web_designer_response_popup(self, output: str, *, source: str) -> None:
        if self._selected_prompt_profile() != WEB_DESIGNER_PROMPT_PROFILE:
            return
        clean_source = str(source or "").strip().lower()
        if clean_source == "security_approval":
            return
        message = _clean_tanya_popup_text(output)
        if not message:
            return
        dedupe_key = hashlib.sha1(message.encode("utf-8", errors="ignore")).hexdigest()
        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        last_key = str(getattr(self, "_last_web_designer_popup_key", "") or "")
        last_ms = int(getattr(self, "_last_web_designer_popup_ms", 0) or 0)
        if dedupe_key == last_key and (now_ms - last_ms) < 3500:
            return
        self._last_web_designer_popup_key = dedupe_key
        self._last_web_designer_popup_ms = now_ms
        old_dialog = getattr(self, "_web_designer_dialog", None)
        try:
            if old_dialog is not None:
                old_dialog.close()
        except Exception:
            pass
        dialog = WebDesignerResponseDialog(message=message, parent=self)
        self._web_designer_dialog = dialog
        dialog.finished.connect(
            lambda _result=0, _dialog=dialog: self._clear_web_designer_dialog_reference(_dialog)
        )
        dialog.destroyed.connect(
            lambda _obj=None, _dialog=dialog: self._clear_web_designer_dialog_reference(_dialog)
        )
        try:
            _show_agent_popup(dialog, self)
        except Exception as exc:
            self._clear_web_designer_dialog_reference(dialog)
            self._set_status(f"Web Designer popup failed: {exc}", error=True)

    def _clear_web_designer_dialog_reference(self, dialog) -> None:
        if getattr(self, "_web_designer_dialog", None) is dialog:
            self._web_designer_dialog = None

    def _process_approved_qdeck_decision(
        self,
        *,
        approval_text: str,
        request_text: str,
        original_user_input: str,
        guard_node_name: str,
    ) -> None:
        scene = self._ensure_scene()
        issue = _qdeck_context_issue(scene, self._node_item)
        if issue:
            self._set_status(issue, error=True)
            _set_node_info(self._node_item, issue)
            self._maybe_show_tanya_speech_popup(issue, source="qdeck_health")
            return

        qdeck_item = _connected_qdeck_controller_item(scene, self._node_item)
        clean_voice_input = _normalize_qdeck_voice_input((original_user_input or "").strip())
        if not clean_voice_input:
            _system_prompt, _chatbot_history, voice_input = self._collect_inputs_for_profile(
                QDECK_PROMPT_PROFILE,
                include_pending_security=False,
            )
            clean_voice_input = _normalize_qdeck_voice_input((voice_input or "").strip())
        if not clean_voice_input:
            self._set_status("Security approval received, but original voice request is unavailable.", error=True)
            return

        self._show_qdeck_handoff_dialog(request_text=request_text, voice_input=clean_voice_input)
        health_issue = _qdeck_health_issue_for_item(qdeck_item)
        if health_issue:
            self._finish_qdeck_handoff_dialog(message=health_issue, error=True)
            self._set_status(health_issue, error=True)
            _set_node_info(self._node_item, health_issue)
            try:
                self._console_append.emit(f"[qdeck] {health_issue}")
            except Exception:
                pass
            self._maybe_show_tanya_speech_popup(QDECK_CONNECTION_USER_MESSAGE, source="qdeck_health")
            return

        system_prompt, chatbot_history, voice_input = self._collect_inputs_for_profile(
            QDECK_PROMPT_PROFILE,
            include_pending_security=False,
        )
        history_parts = [chatbot_history]
        if request_text:
            history_parts.extend(["Approved controlled-tool request:", request_text])
        if approval_text:
            history_parts.extend(["Security Guard decision:", approval_text])
        if guard_node_name:
            history_parts.append(f"Decision source node: {guard_node_name}")
        chatbot_history = "\n\n".join(part for part in history_parts if part).strip()

        prompt, signature = _compose_mediator_prompt(
            system_prompt,
            chatbot_history,
            clean_voice_input,
            _load_prompt_profile_text(QDECK_PROMPT_PROFILE),
            profile=QDECK_PROMPT_PROFILE,
        )
        signature = _security_signature("qdeck_handoff", approval_text, request_text, clean_voice_input, signature)
        self._run_codex_prompt(prompt, signature, "security_approval")

    def _process_security_request_from_agent(
        self,
        *,
        request_text: str,
        requester_node_name: str,
        original_user_input: str,
        request_signature: str,
    ) -> None:
        prompt_history = "\n\n".join(
            part
            for part in (
                "Controlled-tool access request received:",
                request_text,
                f"Requester node: {requester_node_name}" if requester_node_name else "",
                f"Original user request:\n{original_user_input}" if original_user_input else "",
            )
            if part
        )
        prompt, signature = _compose_mediator_prompt(
            "",
            prompt_history,
            "Ask the user for approval for this controlled-tool access request.",
            self._selected_profile_prompt(),
            profile=self._selected_prompt_profile(),
        )
        signature = _security_signature("security_request", request_signature, signature)
        self._run_codex_prompt(prompt, signature, "security_request")

    def _process_security_decision_from_guard(
        self,
        *,
        approval_text: str,
        request_text: str,
        original_user_input: str,
        guard_node_name: str,
    ) -> None:
        _marker, approval_attrs = _first_security_approval(approval_text)
        _request_marker, request_attrs = _first_security_request(request_text)
        decision = str(approval_attrs.get("decision", "") or "").strip().lower()
        tool = str(approval_attrs.get("tool", "") or request_attrs.get("tool", "") or "").strip()
        self._clear_security_request_state(self._node_item)
        if decision == "approved" and _is_qdeck_security_tool(tool):
            self._set_status("Security approved. Running Qubit Deck controller handoff.")
            self._process_approved_qdeck_decision(
                approval_text=approval_text,
                request_text=request_text,
                original_user_input=original_user_input,
                guard_node_name=guard_node_name,
            )
            return

        system_prompt, chatbot_history, voice_input = self._collect_inputs()
        history_parts = [chatbot_history]
        if request_text:
            history_parts.extend(["Recent security request:", request_text])
        if approval_text:
            history_parts.extend(["Security Guard decision:", approval_text])
        if guard_node_name:
            history_parts.append(f"Decision source node: {guard_node_name}")
        chatbot_history = "\n\n".join(part for part in history_parts if part).strip()

        clean_voice_input = (original_user_input or voice_input or "").strip()
        profile = self._selected_prompt_profile()
        if profile == QDECK_PROMPT_PROFILE:
            clean_voice_input = _normalize_qdeck_voice_input(clean_voice_input)
        if not clean_voice_input:
            self._set_status("Security approval received, but original voice request is unavailable.", error=True)
            return

        prompt, signature = _compose_mediator_prompt(
            system_prompt,
            chatbot_history,
            clean_voice_input,
            self._selected_profile_prompt(),
            profile=profile,
        )
        signature = _security_signature("security_approval", approval_text, request_text, clean_voice_input, signature)
        self._run_codex_prompt(prompt, signature, "security_approval")

    def _handle_security_request_output(self, output: str) -> bool:
        request_text, attrs = _first_security_request(output)
        if not request_text:
            return False
        if self._selected_prompt_profile() == SECURITY_GUARD_PROMPT_PROFILE:
            return False

        original_input = self._last_prompt_voice_input.strip()
        tool = str(attrs.get("tool", "") or "").strip()
        target = str(attrs.get("target", "") or "").strip()
        if _is_qdeck_security_tool(tool):
            combined = "\n\n".join(part for part in (original_input, request_text, target) if part)
            if _looks_like_data_nexus_request(combined):
                try:
                    self._console_append.emit("[security] Ignored Qubit Deck security request for Data Nexus intent.")
                except Exception:
                    pass
                self._set_status("Ignored incorrect Qubit Deck request for Data Nexus prompt.", error=True)
                return False
            if not target and not _looks_like_qdeck_request(combined):
                try:
                    self._console_append.emit("[security] Ignored Qubit Deck security request without a clear deck target.")
                except Exception:
                    pass
                self._set_status("Ignored unclear Qubit Deck security request.", error=True)
                return False

        scene = self._ensure_scene()
        guard_item = _find_security_guard_item(scene, exclude_item=self._node_item)
        guard_widget = _mediator_widget_from_item(guard_item) if guard_item is not None else None

        requester_node_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
        request_signature = _security_signature(request_text, original_input)

        self._store_security_request_state(
            request_text=request_text,
            requester_node_name=requester_node_name,
            original_input=original_input,
            request_signature=request_signature,
            guard_item=guard_item,
        )
        if guard_widget is not None:
            try:
                guard_widget._console_append.emit("[security] Pending Qubit Deck access request shown in popup.")
                guard_widget._set_status("Security Guard popup is waiting for user approval.")
            except Exception:
                pass
        self._show_security_request_dialog(
            request_text=request_text,
            attrs=attrs,
            requester_node_name=requester_node_name,
            original_input=original_input,
            guard_item=guard_item,
            guard_widget=guard_widget,
        )
        return True

    def _handle_security_approval_output(self, output: str) -> bool:
        approval_text, attrs = _first_security_approval(output)
        if not approval_text:
            return False
        if self._selected_prompt_profile() != SECURITY_GUARD_PROMPT_PROFILE:
            return False

        scene = self._ensure_scene()
        model = getattr(self._node_item, "model", None)
        requester_node_name = _param_value(model, MEDIGATOR_PENDING_SECURITY_REQUESTER_NODE_PARAM, "").strip()
        requester_item = _find_mediator_item_by_name(scene, requester_node_name)
        requester_widget = _mediator_widget_from_item(requester_item) if requester_item is not None else None
        if requester_item is None or requester_widget is None:
            self._set_status("Security decision published, but requester mediator was not found.", error=True)
            return True

        request_text = _param_value(model, MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM, "").strip()
        original_input = _param_value(model, MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM, "").strip()
        self._clear_security_request_state(self._node_item)

        try:
            requester_widget._process_security_decision_from_guard(
                approval_text=approval_text,
                request_text=request_text,
                original_user_input=original_input,
                guard_node_name=str(getattr(model, "name", "") or "").strip(),
            )
            decision = str(attrs.get("decision", "") or "").strip().lower()
            if decision == "approved":
                self._set_status("Approval sent back to requester mediator.")
            elif decision == "denied":
                self._set_status("Denial sent back to requester mediator.")
            else:
                self._set_status("Security decision sent back to requester mediator.")
        except Exception as exc:
            self._set_status(f"Requester handoff failed: {exc}", error=True)
        return True

    def _handle_security_output(self, output: str) -> bool:
        if self._handle_security_request_output(output):
            return True
        return self._handle_security_approval_output(output)

    def _qdeck_security_fallback_request_text(self, user_input: str) -> str:
        if self._selected_prompt_profile() in {SECURITY_GUARD_PROMPT_PROFILE, QDECK_PROMPT_PROFILE}:
            return ""
        if not _looks_like_qdeck_app_launch_request(user_input):
            return ""
        scene = self._ensure_scene()
        if _connected_qdeck_controller_item(scene, self._node_item) is None:
            return ""
        target = _qdeck_launch_target_from_request(user_input)
        if not target:
            return ""
        requester = "Tanya" if self._selected_prompt_profile() == TANYA_PROMPT_PROFILE else "Mediator"
        marker = _qdeck_security_request_marker(requester, target)
        return f"{marker}\nI need the Security Guard to approve Qubit Deck access before I open {target}."

    def _start_data_nexus_planning(
        self,
        *,
        user_input: str,
        chatbot_output: str,
        history_context: str,
    ) -> bool:
        clean_user = str(user_input or "").strip()
        if not _looks_like_data_nexus_write_request(clean_user):
            return False
        scene = self._ensure_scene()
        if _connected_data_nexus_item(scene, self._node_item) is None:
            return False
        data_nexus_context = _data_nexus_context_text(scene, self._node_item)
        prompt, signature = _compose_data_nexus_planning_prompt(
            data_nexus_context=data_nexus_context,
            chatbot_history=history_context or self._last_prompt_chatbot_history,
            user_input=clean_user,
            chatbot_output=chatbot_output,
        )
        self._last_prompt_voice_input = clean_user
        self._last_prompt_chatbot_history = str(history_context or self._last_prompt_chatbot_history or "").strip()
        _set_node_info(self._node_item, "Data Nexus route: Mediator Planner is reviewing the request.")
        try:
            self._console_append.emit("[data_nexus] Mediator Planner started for Data Nexus edit request.")
        except Exception:
            pass
        self._run_codex_prompt(prompt, signature, "data_nexus_planning")
        return True

    def _data_nexus_delete_targets_for_payloads(self, nexus_item, payloads: list[str]) -> list[dict[str, str]]:
        from nodes.data_nexus import spec as data_nexus_spec

        previewer = getattr(data_nexus_spec, "preview_data_nexus_deletions_from_item", None)
        if not callable(previewer):
            raise RuntimeError("Data Nexus deletion preview handler is unavailable.")
        targets: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for payload in payloads:
            for target in previewer(nexus_item, payload):
                point_id = str(target.get("id", "") or "").strip().lower()
                if not point_id or point_id in seen_ids:
                    continue
                seen_ids.add(point_id)
                targets.append(dict(target))
        return targets

    def _apply_data_nexus_update_payloads(self, nexus_item, payloads: list[str]) -> bool:
        applied_any = False
        messages: list[str] = []
        try:
            self._console_append.emit("[data_nexus] Mediator received Data Nexus update request.")
        except Exception:
            pass
        _set_node_info(self._node_item, "Data Nexus route: Mediator received update request.")
        for payload in payloads:
            try:
                from nodes.data_nexus import spec as data_nexus_spec
                handler = getattr(data_nexus_spec, "apply_data_nexus_update_from_item", None)
                if not callable(handler):
                    raise RuntimeError("Data Nexus update handler is unavailable.")
                changed, message = handler(nexus_item, payload, requester=_node_name(self._node_item) or "Mediator")
                applied_any = applied_any or bool(changed)
                clean_message = str(message or "").strip()
                if clean_message:
                    messages.append(clean_message)
                try:
                    self._console_append.emit(f"[data_nexus] {message}")
                except Exception:
                    pass
            except Exception as exc:
                self._set_status(f"Data Nexus update failed: {exc}", error=True)
                try:
                    self._console_append.emit(f"[data_nexus] Update failed: {exc}")
                except Exception:
                    pass
                return True
        if applied_any:
            self._set_status("Data Nexus updated.")
            detail = "; ".join(messages) if messages else "Applied update."
            _set_node_info(self._node_item, f"Data Nexus route: {detail}")
        else:
            self._set_status("Data Nexus update contained no changes.")
            _set_node_info(self._node_item, "Data Nexus route: update reviewed, no graph changes.")
        return True

    def _show_data_nexus_deletion_dialog(
        self,
        *,
        payloads: list[str],
        targets: list[dict[str, str]],
        nexus_item,
        original_input: str,
        delay_for_tanya: bool = True,
    ) -> None:
        if delay_for_tanya:
            tanya_dialog = getattr(self, "_tanya_dialog", None)
            try:
                tanya_visible = bool(tanya_dialog is not None and tanya_dialog.isVisible())
            except Exception:
                tanya_visible = tanya_dialog is not None
            if tanya_visible:
                delay_ms = _speech_popup_delay_ms(getattr(self, "_tanya_popup_message", "") or original_input)
                self._set_status("Tanya is speaking; Data Nexus approval will open next.")

                def _open_after_tanya() -> None:
                    self._show_data_nexus_deletion_dialog(
                        payloads=payloads,
                        targets=targets,
                        nexus_item=nexus_item,
                        original_input=original_input,
                        delay_for_tanya=False,
                    )

                QtCore.QTimer.singleShot(delay_ms, self, _open_after_tanya)
                return

        self._close_tanya_popup()
        old_dialog = getattr(self, "_data_nexus_delete_dialog", None)
        try:
            if old_dialog is not None and old_dialog.isVisible():
                old_dialog.raise_()
                old_dialog.activateWindow()
                self._set_status("Data Nexus approval popup is already waiting.")
                return
        except Exception:
            pass

        dialog = DataNexusDeletionApprovalDialog(
            targets=targets,
            request_text=original_input,
            parent=self,
        )
        self._data_nexus_delete_dialog = dialog

        def _approve() -> None:
            if getattr(self, "_data_nexus_delete_dialog", None) is dialog:
                self._data_nexus_delete_dialog = None
            try:
                self._console_append.emit(f"[data_nexus] Deletion approved for {len(targets)} point(s).")
            except Exception:
                pass
            self._apply_data_nexus_update_payloads(nexus_item, payloads)

        def _deny() -> None:
            if getattr(self, "_data_nexus_delete_dialog", None) is dialog:
                self._data_nexus_delete_dialog = None
            message = "Data Nexus deletion canceled. No points were removed."
            self._set_status(message)
            try:
                self._console_append.emit(f"[data_nexus] {message}")
            except Exception:
                pass

        dialog.accepted.connect(_approve)
        dialog.rejected.connect(_deny)
        dialog.finished.connect(
            lambda _result=0, _dialog=dialog: self._clear_data_nexus_delete_dialog_reference(_dialog)
        )
        dialog.destroyed.connect(
            lambda _obj=None, _dialog=dialog: self._clear_data_nexus_delete_dialog_reference(_dialog)
        )
        self._set_status("Data Nexus approval popup is waiting.")
        try:
            _show_agent_popup(dialog, self)
        except Exception as exc:
            if getattr(self, "_data_nexus_delete_dialog", None) is dialog:
                self._data_nexus_delete_dialog = None
            self._set_status(f"Data Nexus approval popup failed: {exc}", error=True)

    def _clear_data_nexus_delete_dialog_reference(self, dialog) -> None:
        if getattr(self, "_data_nexus_delete_dialog", None) is dialog:
            self._data_nexus_delete_dialog = None

    def _handle_data_nexus_update_output(
        self,
        output: str,
        *,
        fallback_request: str = "",
        history_context: str = "",
        require_delete_approval: bool = True,
        delay_delete_approval_for_tanya: bool = True,
    ) -> bool:
        payloads = _data_nexus_update_payloads(output)
        explicit_payloads = bool(payloads)
        fallback_is_write = _looks_like_data_nexus_write_request(fallback_request)
        explicit_answer_question = explicit_payloads and _data_nexus_payloads_have_op(
            payloads,
            {"answer_question", "answer_prep_question", "answer"},
        )
        if fallback_request and not fallback_is_write and not explicit_answer_question:
            if explicit_payloads and _data_nexus_read_query_from_request(fallback_request):
                self._set_status("Data Nexus read-only request; ignored update tag.")
                try:
                    self._console_append.emit("[data_nexus] Ignored update tag for read-only Data Nexus request.")
                except Exception:
                    pass
            return False
        if not payloads and fallback_request and fallback_is_write:
            fallback_payload = _data_nexus_fallback_update_payload(fallback_request, history_context)
            if fallback_payload:
                payloads = [fallback_payload]
        if not payloads:
            return False
        if _looks_like_data_nexus_note_replace_request(fallback_request or output):
            payloads = [
                _data_nexus_update_payload_with_note_replace(payload)
                for payload in payloads
            ]
        scene = self._ensure_scene()
        nexus_item = _connected_data_nexus_item(scene, self._node_item)
        if nexus_item is None:
            self._set_status("Data Nexus update requested, but no Data Nexus is connected.", error=True)
            try:
                self._console_append.emit("[data_nexus] Update requested, but no Data Nexus is connected.")
            except Exception:
                pass
            return True
        if require_delete_approval:
            try:
                delete_targets = self._data_nexus_delete_targets_for_payloads(nexus_item, payloads)
            except Exception as exc:
                self._set_status(f"Data Nexus deletion preview failed: {exc}", error=True)
                try:
                    self._console_append.emit(f"[data_nexus] Deletion preview failed: {exc}")
                except Exception:
                    pass
                return True
            if delete_targets:
                self._show_data_nexus_deletion_dialog(
                    payloads=payloads,
                    targets=delete_targets,
                    nexus_item=nexus_item,
                    original_input=fallback_request or _clean_tanya_popup_text(output),
                    delay_for_tanya=delay_delete_approval_for_tanya,
                )
                return True
        return self._apply_data_nexus_update_payloads(nexus_item, payloads)

    def _queue_pending(self, prompt: str, signature: str, source: str) -> None:
        if self._stop_requested:
            return
        self._pending_prompt = str(prompt or "")
        self._pending_signature = str(signature or "")
        self._pending_source = str(source or "auto")

    def _dequeue_pending(self) -> tuple[str, str, str]:
        prompt = self._pending_prompt
        signature = self._pending_signature
        source = self._pending_source
        self._pending_prompt = ""
        self._pending_signature = ""
        self._pending_source = ""
        return prompt, signature, source

    def _clear_pending(self) -> None:
        self._pending_prompt = ""
        self._pending_signature = ""
        self._pending_source = ""

    def _handle_chatbot_model_output(
        self,
        output: str,
        user_input: str,
        history_context: str = "",
        *,
        session_token: str = "",
    ) -> bool:
        clean_output = str(output or "").strip()
        if not clean_output:
            return False
        self._last_prompt_voice_input = str(user_input or "").strip()
        self._current_chatbot_prompt_session_token = str(session_token or "").strip()
        try:
            self._console_append.emit("[chatbot] Response received from Chatbot LLM runtime.")
        except Exception:
            pass
        if (
            _looks_like_data_nexus_write_request(self._last_prompt_voice_input)
            and self._start_data_nexus_planning(
                user_input=self._last_prompt_voice_input,
                chatbot_output=clean_output,
                history_context=history_context,
            )
        ):
            self._maybe_show_tanya_speech_popup(
                "Mediator Planner is reviewing the Data Nexus request.",
                source="chatbot",
            )
            self._chatbot_handoff_active = False
            self._set_status("Data Nexus Mediator Planner is running.")
            return True
        qdeck_fallback_request = ""
        if not _first_security_request(clean_output)[0]:
            qdeck_fallback_request = self._qdeck_security_fallback_request_text(self._last_prompt_voice_input)
        popup_output = clean_output
        popup_text = _clean_tanya_popup_text(clean_output)
        claimed_write_without_update = _claims_data_nexus_write_without_update(clean_output)
        if (
            _looks_like_data_nexus_delete_request(self._last_prompt_voice_input)
            and (
                GENERIC_PERMISSION_RESPONSE_RE.match(popup_text)
                or QDECK_COMMAND_OUTPUT_RE.search(clean_output)
                or USER_FEEDBACK_RE.search(clean_output)
            )
        ):
            popup_output = "Review the Data Nexus deletion approval."
        elif (
            _looks_like_data_nexus_write_request(self._last_prompt_voice_input)
            and (QDECK_COMMAND_OUTPUT_RE.search(clean_output) or USER_FEEDBACK_RE.search(clean_output))
        ):
            popup_output = "Updating Data Nexus from the chat history."
        elif claimed_write_without_update:
            popup_output = "No Data Nexus change was applied because Tanya did not send a valid update request."
        elif qdeck_fallback_request:
            target = _qdeck_launch_target_from_request(self._last_prompt_voice_input)
            popup_output = f"I need Security Guard approval before I open {target}."
        self._maybe_show_tanya_speech_popup(popup_output, source="chatbot")
        nexus_handled = self._handle_data_nexus_update_output(
            clean_output,
            fallback_request=self._last_prompt_voice_input,
            history_context=history_context,
        )
        security_handled = self._handle_security_output(clean_output)
        if not security_handled and qdeck_fallback_request:
            security_handled = self._handle_security_request_output(qdeck_fallback_request)
        if security_handled:
            self._chatbot_handoff_active = True
            self._set_status("Chatbot response handed to Mediator security flow.")
        elif nexus_handled:
            self._chatbot_handoff_active = False
        elif claimed_write_without_update:
            self._chatbot_handoff_active = False
            self._set_status("Chatbot claimed a Data Nexus write without a valid update tag.", error=True)
            _set_node_info(
                self._node_item,
                "No Data Nexus change was applied. Tanya did not send a valid data_nexus_update request.",
            )
            return True
        else:
            self._chatbot_handoff_active = False
            self._set_status("Chatbot response observed.")
        return bool(security_handled or nexus_handled)

    def _process_inputs_if_available(self) -> None:
        self._maybe_process_inputs(force=False, source="auto")

    def _process_inputs_manual(self) -> None:
        self._maybe_process_inputs(force=True, source="manual")

    def _maybe_process_inputs(self, changed_name=None, *, force: bool, source: str) -> None:
        if not force and not self._auto_enabled():
            return
        if not force and not self._auto_baseline_ready:
            self._sync_auto_baseline()
        scene = self._ensure_scene()
        if scene is None:
            return
        profile = self._selected_prompt_profile()
        if profile == QDECK_PROMPT_PROFILE:
            issue = _qdeck_context_issue(scene, self._node_item)
            if issue:
                self._set_status(issue, error=True)
                if force:
                    try:
                        QtWidgets.QMessageBox.warning(self, "Mediator", issue)
                    except Exception:
                        pass
                return
        changed_key = str(changed_name or "").strip().lower()
        if changed_key:
            source_names = self._connected_source_names()
            if source_names and changed_key not in source_names:
                return

        voice_item = self._connected_voice_actor_item()
        current_voice_token = ""
        if voice_item is None:
            self._last_voice_mode = ""
            if not force:
                self._last_auto_voice_token = ""
                return
        else:
            current_voice_mode = _voice_actor_mode_from_item(voice_item)
            current_voice_token = _voice_actor_send_token(voice_item)
            if (
                not force
                and bool(self._last_voice_mode)
                and current_voice_mode != self._last_voice_mode
            ):
                # Mode toggles are control events and should not dispatch downstream calls.
                self._last_voice_mode = current_voice_mode
                self._last_auto_voice_token = current_voice_token
                return
            self._last_voice_mode = current_voice_mode
            if not force:
                if not current_voice_token:
                    self._last_auto_voice_input = ""
                    return
                if current_voice_token == self._last_auto_voice_token:
                    return

        system_prompt, chatbot_history, voice_input = self._collect_inputs()
        clean_voice_input = str(voice_input or "").strip()
        if not clean_voice_input:
            if not force:
                self._last_auto_voice_input = ""
                self._last_auto_voice_token = current_voice_token
            if force:
                self._set_status("No voice_input text available.", error=True)
            return
        self._last_prompt_voice_input = clean_voice_input
        self._last_prompt_chatbot_history = str(chatbot_history or "").strip()
        if not force:
            self._last_auto_voice_input = clean_voice_input
            self._last_auto_voice_token = current_voice_token

        prompt_voice_input = clean_voice_input
        if profile == QDECK_PROMPT_PROFILE:
            prompt_voice_input = _normalize_qdeck_voice_input(clean_voice_input)

        output_context = ""
        if profile == TRANSLATOR_PROMPT_PROFILE:
            output_context = _translator_output_context(scene, self._node_item)

        prompt, signature = _compose_mediator_prompt(
            system_prompt,
            chatbot_history,
            prompt_voice_input,
            self._selected_profile_prompt(),
            output_context,
            profile=profile,
        )
        if not force and current_voice_token:
            signature = hashlib.sha1(f"{signature}\nvoice:{current_voice_token}".encode("utf-8")).hexdigest()
        if not force and signature == self._last_processed_signature:
            return
        if self._running:
            self._queue_pending(prompt, signature, source)
            return
        self._run_codex_prompt(prompt, signature, source)

    def _run_codex_prompt(self, prompt: str, signature: str, source: str) -> None:
        if self._running:
            self._queue_pending(prompt, signature, source)
            return
        if source == "auto":
            mode = "auto"
        elif source == "data_nexus_planning":
            mode = "data nexus"
        elif source == "teacher_agent":
            mode = "teacher agent"
        elif source == "sales_agent":
            mode = "sales agent"
        else:
            mode = "manual"
        self._stop_requested = False
        self._set_running(True)
        self._set_status(f"Running Codex ({mode})...")
        try:
            runtime = _resolve_codex_runtime_config(self._ensure_scene(), self._node_item, self._workspace_dir)
        except Exception as exc:
            error_text = f"Codex setup error: {exc}"
            self._set_running(False)
            self._set_status(error_text, error=True)
            self._console_append.emit(f"[codex] Setup error: {exc}")
            self._command_done.emit(-1, error_text, "", signature, source)
            return
        self._write_history(f"codex exec - <mediator:{mode}>")
        threading.Thread(
            target=self._codex_worker,
            args=(prompt, signature, source, runtime),
            daemon=True,
        ).start()

    def _codex_worker(self, prompt: str, signature: str, source: str, runtime: dict) -> None:
        exit_code = -1
        error_text = ""
        response_text = ""
        process = None
        output_path = None
        try:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            prompt_path = self._workspace_dir / f"mediator_prompt_{stamp}.md"
            output_path = self._workspace_dir / f"mediator_response_{stamp}.txt"
            prompt_path.write_text(prompt or "", encoding="utf-8")
            _prune_mediator_prompt_logs(self._workspace_dir)

            if runtime.get("codex_home"):
                self._queue_console_line_threadsafe(f"[codex] CODEX_HOME={runtime['codex_home']}")
            self._queue_console_line_threadsafe(f"[codex] Working directory: {runtime['working_directory']}")
            if runtime.get("writable_roots"):
                self._queue_console_line_threadsafe(f"[codex] Writable roots: {runtime['writable_roots']}")
            self._queue_console_line_threadsafe(f"[codex] Mediator logs: {runtime['log_directory']}")

            request = codex_cli_runner.CodexExecRequest(
                codex_executable=str(runtime.get("codex_executable") or ""),
                model=str(runtime.get("model") or MEDIGATOR_CODEX_MODEL),
                model_reasoning_effort=str(
                    runtime.get("model_reasoning_effort") or codex_cli_runner.DEFAULT_MODEL_REASONING_EFFORT
                ),
                sandbox_mode=str(runtime.get("sandbox_mode") or codex_cli_runner.DEFAULT_SANDBOX_MODE),
                approval_policy=str(runtime.get("approval_policy") or codex_cli_runner.DEFAULT_APPROVAL_POLICY),
                working_directory=runtime["working_directory"],
                output_last_message_path=output_path,
                codex_home=str(runtime.get("codex_home") or ""),
                network_access=bool(runtime.get("network_access")),
                writable_roots=str(runtime.get("writable_roots") or ""),
                extra_args=str(runtime.get("extra_args") or ""),
            )
            cmd = codex_cli_runner.build_codex_exec_command(request)
            env = codex_cli_runner.build_codex_env(str(runtime.get("codex_home") or ""))

            self._queue_console_line_threadsafe(f"$ {codex_cli_runner.format_command(cmd)}")
            process = subprocess.Popen(
                cmd,
                cwd=str(runtime["working_directory"]),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_hidden_subprocess_kwargs(),
            )
            with self._process_lock:
                self._process = process

            if process.stdin is not None:
                process.stdin.write(prompt or "")
                if not str(prompt or "").endswith("\n"):
                    process.stdin.write("\n")
                process.stdin.flush()
                process.stdin.close()

            if process.stdout is not None:
                for raw in process.stdout:
                    self._queue_console_line_threadsafe(raw.rstrip("\n"))

            exit_code = int(process.wait())
            if output_path.exists():
                try:
                    response_text = output_path.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception:
                    response_text = ""

            if exit_code != 0 and not error_text:
                error_text = f"Codex exited with code {exit_code}."
            if exit_code == 0 and not response_text:
                error_text = "Codex completed but produced no output text."
        except Exception as exc:
            error_text = f"Failed to run Codex: {exc}"
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
            self._command_done.emit(exit_code, error_text, response_text, signature, source)

    def _run_command(self) -> None:
        if self._running:
            self._set_status("A command is already running.", error=True)
            return
        command = str(self._command_edit.text() or "").strip()
        if not command:
            self._set_status("Enter a command first.", error=True)
            return

        self._stop_requested = False
        self._set_running(True)
        self._set_status("Running command...")
        self._write_history(command)
        self._console_append.emit(f"$ {command}")
        threading.Thread(target=self._command_worker, args=(command,), daemon=True).start()

    def _command_worker(self, command: str) -> None:
        exit_code = -1
        error_text = ""
        process = None
        try:
            process = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=str(self._workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_hidden_subprocess_kwargs(),
            )
            with self._process_lock:
                self._process = process
            if process.stdout is not None:
                for raw in process.stdout:
                    self._queue_console_line_threadsafe(raw.rstrip("\n"))
            exit_code = int(process.wait())
        except Exception as exc:
            error_text = f"Failed to run command: {exc}"
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
            self._command_done.emit(exit_code, error_text, "", "", "manual_command")

    def _stop_command(self) -> None:
        proc = None
        with self._process_lock:
            proc = self._process
        self._stop_requested = True
        self._clear_pending()
        if proc is None:
            self._set_status("No running command.")
            return
        pid = 0
        try:
            pid = int(getattr(proc, "pid", 0) or 0)
        except Exception:
            pid = 0
        stopped = False
        if os.name == "nt" and pid > 0:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_hidden_subprocess_kwargs(),
                )
                stopped = True
            except Exception:
                stopped = False
        try:
            if not stopped:
                proc.terminate()
                stopped = True
        except Exception:
            pass
        try:
            if not stopped:
                proc.kill()
                stopped = True
        except Exception:
            pass
        try:
            if stopped:
                self._console_append.emit("Stop requested. Terminating active process...")
            else:
                self._console_append.emit("Stop requested but process termination could not be confirmed.")
            self._set_status("Stopping command...")
        except Exception as exc:
            self._set_status(f"Stop failed: {exc}", error=True)

    @QtCore.Slot(int, str, str, str, str)
    def _on_command_done(
        self,
        exit_code: int,
        error_text: str,
        response_text: str,
        signature: str,
        source: str,
    ) -> None:
        self._set_running(False)

        clean_source = (source or "").strip().lower()
        is_codex_process = clean_source in MEDIGATOR_CODEX_RESPONSE_SOURCES
        chatbot_handoff = bool(self._chatbot_handoff_active and clean_source == "security_approval")
        if self._stop_requested:
            self._clear_pending()
            self._stop_requested = False
            if chatbot_handoff:
                self._chatbot_handoff_active = False
            if is_codex_process:
                self._set_status("Processing stopped.")
            else:
                self._set_status("Command stopped.")
            return

        if error_text:
            self._console_append.emit(error_text)
            if clean_source == "security_approval":
                self._finish_qdeck_handoff_dialog(message=error_text, error=True)
            self._set_status(error_text, error=True)
        elif is_codex_process:
            output = str(response_text or "").strip()
            if output:
                qdeck_fallback_request = ""
                if clean_source not in {"security_request", "security_approval"} and not _first_security_request(output)[0]:
                    qdeck_fallback_request = self._qdeck_security_fallback_request_text(self._last_prompt_voice_input)
                visible_output = _strip_sales_agent_task_tags(_strip_data_nexus_update_tags(output))
                if clean_source == "teacher_agent":
                    visible_output = "Teacher Agent conversion response received."
                elif clean_source == "sales_agent":
                    visible_output = "Sales Agent response received."
                if (
                    _looks_like_data_nexus_write_request(self._last_prompt_voice_input)
                    and (QDECK_COMMAND_OUTPUT_RE.search(output) or USER_FEEDBACK_RE.search(output))
                ):
                    visible_output = "Data Nexus update sent to Mediator."
                elif qdeck_fallback_request:
                    visible_output = "Security Guard approval requested."
                if chatbot_handoff:
                    self._console_append.emit("[mediator] Chatbot-origin execution complete; output kept off Mediator node output.")
                else:
                    _set_node_info(self._node_item, visible_output or "Data Nexus update requested.")
                    self._console_append.emit("[mediator] Response published to node output.")
                self._last_processed_signature = str(signature or self._last_processed_signature)
                security_handled = False
                if not chatbot_handoff:
                    popup_output = output
                    popup_text = _clean_tanya_popup_text(output)
                    if clean_source == "teacher_agent":
                        popup_output = "Teacher Agent conversion response received."
                    elif clean_source == "sales_agent":
                        popup_output = "Sales Agent response received."
                    elif (
                        GENERIC_PERMISSION_RESPONSE_RE.match(popup_text)
                        and _looks_like_data_nexus_delete_request(self._last_prompt_voice_input)
                    ):
                        popup_output = "Review the Data Nexus deletion approval."
                    elif (
                        _looks_like_data_nexus_write_request(self._last_prompt_voice_input)
                        and (QDECK_COMMAND_OUTPUT_RE.search(output) or USER_FEEDBACK_RE.search(output))
                    ):
                        popup_output = "Updating Data Nexus from the chat history."
                    elif qdeck_fallback_request:
                        target = _qdeck_launch_target_from_request(self._last_prompt_voice_input)
                        popup_output = f"I need Security Guard approval before I open {target}."
                    self._maybe_show_tanya_speech_popup(popup_output, source=clean_source)
                    self._maybe_show_web_designer_response_popup(popup_output, source=clean_source)
                nexus_handled = self._handle_data_nexus_update_output(
                    output,
                    fallback_request=self._last_prompt_voice_input,
                    history_context=self._last_prompt_chatbot_history,
                )
                if not chatbot_handoff:
                    security_handled = self._handle_security_output(output)
                    if not security_handled and qdeck_fallback_request:
                        security_handled = self._handle_security_request_output(qdeck_fallback_request)
                if clean_source == "security_approval":
                    preview = output.splitlines()[0].strip() if output.splitlines() else output
                    self._finish_qdeck_handoff_dialog(message=preview or "Qubit Deck command published.", error=False)
                if not security_handled and not nexus_handled:
                    if chatbot_handoff:
                        self._set_status("Chatbot-origin execution complete.")
                    elif clean_source == "auto":
                        self._set_status("Auto-processing complete.")
                    elif clean_source == "security_request":
                        self._set_status("Security request processing complete.")
                    elif clean_source == "security_approval":
                        self._set_status("Security approval processing complete.")
                    else:
                        self._set_status("Processing complete.")
            else:
                if clean_source == "security_approval":
                    self._finish_qdeck_handoff_dialog(message="Codex returned empty output.", error=True)
                self._set_status("Codex returned empty output.", error=True)
        else:
            if int(exit_code) == 0:
                self._set_status("Command finished.")
            else:
                self._set_status(f"Command exited with code {exit_code}.", error=True)

        pending_prompt, pending_signature, pending_source = self._dequeue_pending()
        if chatbot_handoff:
            self._chatbot_handoff_active = False
        if pending_prompt:
            if pending_signature and pending_signature == self._last_processed_signature:
                return
            self._run_codex_prompt(pending_prompt, pending_signature, pending_source or "auto")

    def _on_scene_links_changed(self, *_args):
        self._sync_auto_baseline()

    def _on_scene_param_changed(self, name=None, _params=None):
        self._maybe_process_inputs(changed_name=name, force=False, source="auto")

    def _clear_console(self) -> None:
        try:
            self._console_flush_timer.stop()
        except Exception:
            pass
        with self._console_pending_lock:
            self._console_pending_lines.clear()
            self._console_flush_scheduled = False
        self._console.clear()
        self._set_status("Console cleared.")

    def _open_workspace_folder(self) -> None:
        try:
            subprocess.Popen(["explorer", str(self._workspace_dir)])
        except Exception as exc:
            self._set_status(f"Failed to open folder: {exc}", error=True)


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = MediatorConsoleWidget(node_item, None)
        setattr(node_item, "_mediator_console_widget", body)
    except Exception as exc:
        print("[EchoGraph] Mediator UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        msg = QtWidgets.QLabel("Mediator UI failed to load. Check console output for details.")
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel{color:#e5e7eb;}")
        lay.addWidget(msg, 1)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    h = max(36, int(hint.height()))
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        available = float(node_item.height) - float(y_cursor) - pad
        if available > h:
            h = int(available)
    except Exception:
        pass
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    bottom_y = int(y_cursor + h)
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        required_height = float(bottom_y) + pad
        if float(getattr(node_item, "height", 0.0) or 0.0) < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = required_height
            try:
                node_item.update()
            except Exception:
                pass
    except Exception:
        pass
    return bottom_y


MEDIGATOR_SPEC = Spec(
    stripe_color="#0f766e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
