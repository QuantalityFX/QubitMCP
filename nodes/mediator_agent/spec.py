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
MEDIGATOR_MAX_PROMPT_LOG_FILES = 15
MEDIGATOR_MAX_CONSOLE_LOG_LINES = 5000
MEDIGATOR_CODEX_MODEL = "gpt-5.5"
VOICE_ACTOR_KINDS = {"voice_actor", "voice actor", "voiceactor"}
VOICE_ACTOR_SEND_TOKEN_PARAM = "__voice_actor_send_token"
QDECK_CONTROLLER_KINDS = {
    "qubit_deck_controller",
    "qubit deck controller",
    "qubitdeckcontroller",
    "qubitdeck controller",
}
SYSTEM_PROMPT_KINDS = {"llm_prompt", "gpt_prompt", "prompt", "system_prompt"}
QDECK_PROMPT_PROFILE = "qubit_deck_controller"
QDECK_DEFAULT_API_BASE = "http://127.0.0.1:8765"
QDECK_CONTEXT_MAX_ROWS = 220
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
MEDIGATOR_PROMPT_PROFILE_ALIASES = {
    "romantic_dark_assistant": TANYA_PROMPT_PROFILE,
}
SECURITY_AGENT_ICON_FILENAMES = ("ScurityAgent_Icon.png", "SecurityAgent_Icon.png")
OPERATOR_AGENT_ICON_FILENAMES = ("ITOperatorAgent_Icon.png", "OperatorAgent_Icon.png")
TANYA_AGENT_ICON_FILENAMES = ("AssistentTanyaAgent_Icon.png", "AssistantTanyaAgent_Icon.png", "TanyaAI_Icon.png")
MEDIGATOR_PENDING_SECURITY_REQUEST_PARAM = "__pending_security_request"
MEDIGATOR_PENDING_SECURITY_REQUESTER_NODE_PARAM = "__pending_security_requester_node"
MEDIGATOR_PENDING_SECURITY_USER_INPUT_PARAM = "__pending_security_user_input"
MEDIGATOR_PENDING_SECURITY_SIGNATURE_PARAM = "__pending_security_signature"
MEDIGATOR_CODEX_RESPONSE_SOURCES = {
    "auto",
    "manual",
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


def _parse_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _qdeck_fetch_buttons(api_base: str) -> list[dict]:
    base = str(api_base or "").strip() or QDECK_DEFAULT_API_BASE
    request = Request(
        url=urljoin((base.rstrip("/") + "/"), "api/buttons"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
    except HTTPError as ex:
        detail = ex.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {ex.code}: {detail or ex.reason}") from ex
    except URLError as ex:
        raise RuntimeError(f"Connection failed: {ex.reason}") from ex

    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception as ex:
        raise RuntimeError(f"Invalid JSON response: {ex}") from ex

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
    api_base = _param_value_from_item(node_item, "api_base", QDECK_DEFAULT_API_BASE).strip() or QDECK_DEFAULT_API_BASE
    try:
        buttons = _qdeck_fetch_buttons(api_base)
        return _qdeck_summarize_buttons(buttons)
    except Exception:
        pass

    model = getattr(node_item, "model", None)
    info_text = str(getattr(model, "info", "") or "").strip() if model is not None else ""
    if info_text:
        return info_text

    try:
        return str(scene.resolve_text_value(node_item) or "").strip()
    except Exception:
        return ""


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
            "unknown_default_role": None,
        }
    return {
        "prefer_named": True,
        "voice_kinds": None,
        "history_kinds": None,
        "system_kinds": None,
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


def _mediator_widget_from_item(node_item):
    return getattr(node_item, "_mediator_console_widget", None)


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


def _compose_mediator_prompt(
    system_prompt: str,
    chatbot_history: str,
    voice_input: str,
    default_system_prompt: str = "",
) -> tuple[str, str]:
    clean_system = _trim_text(system_prompt, MEDIGATOR_MAX_SYSTEM_CHARS, keep_tail=False)
    clean_history = _trim_text(chatbot_history, MEDIGATOR_MAX_HISTORY_CHARS, keep_tail=True)
    clean_voice = _trim_text(voice_input, MEDIGATOR_MAX_VOICE_CHARS, keep_tail=True)
    clean_default = _trim_text(default_system_prompt, MEDIGATOR_MAX_SYSTEM_CHARS, keep_tail=False)
    effective_system = clean_system or clean_default or MEDIGATOR_DEFAULT_SYSTEM_PROMPT
    payload = "\n\n".join(
        [
            effective_system,
            clean_history,
            clean_voice,
        ]
    )
    signature = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    prompt = (
        f"{effective_system}\n\n"
        "Task:\n"
        "1. Read the conversation history and the latest voice input.\n"
        "2. Produce the best next assistant reply.\n"
        "3. Return only the assistant response text.\n\n"
        "Conversation history:\n"
        f"{clean_history or '(none)'}\n\n"
        "Latest voice input:\n"
        f"{clean_voice or '(none)'}\n"
    )
    return prompt, signature


def build_ports(node_item) -> None:
    if not hasattr(node_item, "ensure_input"):
        return
    for port_name in ("voice_input", "chatbot_history", "system_prompt"):
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
    clean = SECURITY_REQUEST_RE.sub("", clean)
    clean = SECURITY_APPROVAL_RE.sub("", clean)
    clean = re.sub(r"<\s*/?\s*(?:security_request|security_approval)\b[^>]*>", "", clean, flags=re.IGNORECASE | re.DOTALL)
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
        self._security_dialog = None
        self._qdeck_handoff_dialog = None
        self._qdeck_handoff_active = False
        self._tanya_dialog = None
        self._tanya_popup_message = ""

        self.setMinimumSize(MEDIGATOR_BODY_W, MEDIGATOR_BODY_H)
        try:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass

        self._workspace_label = QtWidgets.QLabel(f"Workspace: {self._workspace_dir}")
        self._workspace_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._workspace_label.setStyleSheet("QLabel{color:#93c5fd;}")

        self._command_edit = QtWidgets.QLineEdit()
        self._command_edit.setPlaceholderText("Enter a command (for example: & \".../tools/codex.ps1\")")
        self._command_edit.returnPressed.connect(self._run_command)
        self._command_edit.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:4px 6px;}"
        )

        codex_script = _repo_root() / "tools" / "codex.ps1"
        if codex_script.exists():
            self._command_edit.setText(f'& "{codex_script}" -Sandbox read-only')

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

        self._console_append.connect(self._append_console_line)
        self._command_done.connect(self._on_command_done)
        self._update_controls()

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
        payload = f"[{_format_ts()}] {line.rstrip()}\n"
        path = self._console_log_path()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            _prune_text_log_tail(path, keep_lines=MEDIGATOR_MAX_CONSOLE_LOG_LINES)
        except Exception:
            pass

    def _write_history(self, command: str) -> None:
        payload = f"[{_format_ts()}] {command.strip()}\n"
        try:
            with self._history_path().open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            pass

    @QtCore.Slot(str)
    def _append_console_line(self, line: str) -> None:
        text = str(line or "").rstrip("\r\n")
        if not text:
            return
        self._console.appendPlainText(text)
        self._write_console_log(text)
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
        self._tanya_popup_message = message
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

    def _process_approved_qdeck_decision(
        self,
        *,
        approval_text: str,
        request_text: str,
        original_user_input: str,
        guard_node_name: str,
    ) -> None:
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

        clean_voice_input = _normalize_qdeck_voice_input((original_user_input or voice_input or "").strip())
        if not clean_voice_input:
            self._set_status("Security approval received, but original voice request is unavailable.", error=True)
            return

        self._show_qdeck_handoff_dialog(request_text=request_text, voice_input=clean_voice_input)
        prompt, signature = _compose_mediator_prompt(
            system_prompt,
            chatbot_history,
            clean_voice_input,
            _load_prompt_profile_text(QDECK_PROMPT_PROFILE),
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
        )
        signature = _security_signature("security_approval", approval_text, request_text, clean_voice_input, signature)
        self._run_codex_prompt(prompt, signature, "security_approval")

    def _handle_security_request_output(self, output: str) -> bool:
        request_text, attrs = _first_security_request(output)
        if not request_text:
            return False
        if self._selected_prompt_profile() == SECURITY_GUARD_PROMPT_PROFILE:
            return False

        scene = self._ensure_scene()
        guard_item = _find_security_guard_item(scene, exclude_item=self._node_item)
        guard_widget = _mediator_widget_from_item(guard_item) if guard_item is not None else None

        requester_node_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
        original_input = self._last_prompt_voice_input.strip()
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
        profile = self._selected_prompt_profile()
        clean_voice_input = str(voice_input or "").strip()
        if not clean_voice_input:
            if not force:
                self._last_auto_voice_input = ""
                self._last_auto_voice_token = current_voice_token
            if force:
                self._set_status("No voice_input text available.", error=True)
            return
        self._last_prompt_voice_input = clean_voice_input
        if not force:
            self._last_auto_voice_input = clean_voice_input
            self._last_auto_voice_token = current_voice_token

        prompt_voice_input = clean_voice_input
        if profile == QDECK_PROMPT_PROFILE:
            prompt_voice_input = _normalize_qdeck_voice_input(clean_voice_input)

        prompt, signature = _compose_mediator_prompt(
            system_prompt,
            chatbot_history,
            prompt_voice_input,
            self._selected_profile_prompt(),
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
        mode = "auto" if source == "auto" else "manual"
        self._stop_requested = False
        self._set_running(True)
        self._set_status(f"Running Codex ({mode})...")
        self._write_history(f"tools\\codex.ps1 -Exec <mediator:{mode}>")
        threading.Thread(
            target=self._codex_worker,
            args=(prompt, signature, source),
            daemon=True,
        ).start()

    def _codex_worker(self, prompt: str, signature: str, source: str) -> None:
        exit_code = -1
        error_text = ""
        response_text = ""
        process = None
        output_path = None
        try:
            codex_script = _repo_root() / "tools" / "codex.ps1"
            if not codex_script.exists():
                raise RuntimeError(f"Missing Codex launcher: {codex_script}")

            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            prompt_path = self._workspace_dir / f"mediator_prompt_{stamp}.md"
            output_path = self._workspace_dir / f"mediator_response_{stamp}.txt"
            prompt_path.write_text(prompt or "", encoding="utf-8")
            _prune_mediator_prompt_logs(self._workspace_dir)

            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(codex_script),
                "-Exec",
                "-Model",
                MEDIGATOR_CODEX_MODEL,
                "-Sandbox",
                "read-only",
                "-Cd",
                str(self._workspace_dir),
                "-OutputLastMessage",
                str(output_path),
            ]

            self._console_append.emit(f"$ {subprocess.list2cmdline(cmd)}")
            process = subprocess.Popen(
                cmd,
                cwd=str(self._workspace_dir),
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
                    self._console_append.emit(raw.rstrip("\n"))

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
                    self._console_append.emit(raw.rstrip("\n"))
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
        if self._stop_requested:
            self._clear_pending()
            self._stop_requested = False
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
                _set_node_info(self._node_item, output)
                self._console_append.emit("[mediator] Response published to node output.")
                self._last_processed_signature = str(signature or self._last_processed_signature)
                self._maybe_show_tanya_speech_popup(output, source=clean_source)
                security_handled = self._handle_security_output(output)
                if clean_source == "security_approval":
                    preview = output.splitlines()[0].strip() if output.splitlines() else output
                    self._finish_qdeck_handoff_dialog(message=preview or "Qubit Deck command published.", error=False)
                if not security_handled:
                    if clean_source == "auto":
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
        if pending_prompt:
            if pending_signature and pending_signature == self._last_processed_signature:
                return
            self._run_codex_prompt(pending_prompt, pending_signature, pending_source or "auto")

    def _on_scene_links_changed(self, *_args):
        self._sync_auto_baseline()

    def _on_scene_param_changed(self, name=None, _params=None):
        self._maybe_process_inputs(changed_name=name, force=False, source="auto")

    def _clear_console(self) -> None:
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
