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
from echograph.qt_compat import QtWidgets, QtCore


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
MEDIGATOR_CODEX_MODEL = "gpt-5.3-codex"
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

    def _collect_inputs(self) -> tuple[str, str, str]:
        scene = self._ensure_scene()
        if scene is None:
            return "", "", ""
        profile = self._selected_prompt_profile()
        policy = _input_policy_for_profile(profile)
        if bool(policy.get("prefer_named", True)) and _has_named_input_edges(scene, self._node_item):
            return _collect_inputs_from_named_ports(scene, self._node_item, profile)
        # Single-pin mode: route by upstream node kind policy.
        return _collect_inputs_from_default_pin(scene, self._node_item, profile)

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
        is_codex_process = clean_source in {"auto", "manual"}
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
            self._set_status(error_text, error=True)
        elif is_codex_process:
            output = str(response_text or "").strip()
            if output:
                _set_node_info(self._node_item, output)
                self._console_append.emit("[mediator] Response published to node output.")
                self._last_processed_signature = str(signature or self._last_processed_signature)
                if clean_source == "auto":
                    self._set_status("Auto-processing complete.")
                else:
                    self._set_status("Processing complete.")
            else:
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

    h = body.sizeHint().height()
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
    return y_cursor + h


MEDIGATOR_SPEC = Spec(
    stripe_color="#0f766e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
