from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List, Sequence, Tuple

try:
    from pymongo import MongoClient  # type: ignore
except Exception:
    MongoClient = None  # optional; DB feature disabled if missing

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore, QtGui
from echograph.constants import APP_TITLE, script_dir

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_PROVIDER = "openai"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "deepseek-r1:14b"
MODEL_PRESETS = [
    ("OpenAI: gpt-5.1", "openai", "gpt-5.1"),
    ("OpenAI: gpt-4.1", "openai", "gpt-4.1"),
    ("OpenAI: gpt-4.1-mini", "openai", "gpt-4.1-mini"),
    ("Ollama: deepseek-r1:14b", "ollama", "deepseek-r1:14b"),
    ("Ollama: deepseek-r1:1.5b", "ollama", "deepseek-r1:1.5b"),
]
DEFAULT_TEMPERATURE = 0.2
MAX_FILE_CHARS = 12000
MAX_TOTAL_CHARS = 60000
PROMPT_NODE_KIND = "llm_prompt"
PROMPT_NODE_ALIASES = [
    "llm prompt",
    "gpt_prompt",
    "gpt prompt",
    "gpt",
]
PROMPT_NODE_KINDS = {PROMPT_NODE_KIND, *PROMPT_NODE_ALIASES}
DB_NAME = "my_database"
# The user’s Mongo layout: database = my_database, collection = EchoGragh
COLLECTION = "EchoGragh"
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH_RE = re.compile(r"^[\\/]{2}[^\\/]+[\\/]+[^\\/]+")

def _text_from_input(card, node_item, port_name: str) -> str:
    sc = getattr(card, "_graph_scene", None)
    if not sc or not node_item:
        return ""
    try:
        in_edges = list(sc._in_edges(node_item))
    except Exception:
        in_edges = []

    def _edge_matches_name(edge) -> bool:
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(edge, attr) and getattr(edge, attr) == port_name:
                return True
        return False

    named_edges = [e for e in in_edges if _edge_matches_name(e)]
    if not named_edges:
        return ""
    try:
        ordered = [
            e for e in sc._ordered_in_edges(node_item)
            if e in named_edges
        ]
        if ordered:
            named_edges = ordered
    except Exception:
        pass

    parts: List[str] = []
    for edge in named_edges:
        try:
            txt = sc.resolve_text_value(edge.src)
        except Exception:
            txt = ""
        if txt:
            parts.append(txt.strip())
    return "\n\n".join(parts).strip()

def _ensure_input(node_item, name: str) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(name)
    elif hasattr(node_item, "add_input_port"):
        node_item.add_input_port(name)
    elif hasattr(node_item, "add_input"):
        node_item.add_input(name)

def _ensure_param(node_item, name: str, default: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
        setattr(model, "params", params)
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})

def build_ports(node_item) -> None:
    try:
        print(f"[llm_prompt] build_ports on node '{getattr(node_item.model, 'name', '?')}' with kind {getattr(node_item.model, 'kind', '?')}")
    except Exception:
        pass
    _ensure_param(node_item, "prompt", "")
    _ensure_param(node_item, "files", "")
    _ensure_param(node_item, "output_path", "")
    _ensure_param(node_item, "api_key", "")
    _ensure_param(node_item, "provider", DEFAULT_PROVIDER)
    _ensure_param(node_item, "ollama_url", DEFAULT_OLLAMA_URL)
    _ensure_param(node_item, "model", DEFAULT_MODEL)
    _ensure_param(node_item, "temperature", str(DEFAULT_TEMPERATURE))
    for port in ("prompt", "files", "output_path", "api_key", "provider", "ollama_url", "model", "temperature"):
        _ensure_input(node_item, port)

def _normalize_provider(raw: str, model: str) -> str:
    val = (raw or "").strip().lower()
    if val in {"openai", "oa", "gpt"}:
        return "openai"
    if val in {"ollama", "local", "deepseek"}:
        return "ollama"
    if not val:
        model_lower = (model or "").strip().lower()
        if "deepseek" in model_lower or "ollama" in model_lower:
            return "ollama"
    return "openai"

def _normalize_ollama_url(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        val = (os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_URL") or "").strip()
    if not val:
        val = DEFAULT_OLLAMA_URL
    if not re.match(r"^https?://", val):
        val = f"http://{val}"
    return val.rstrip("/")

def _looks_like_windows_path(raw: str) -> bool:
    return bool(_WINDOWS_DRIVE_RE.match(raw)) or bool(_UNC_PATH_RE.match(raw))

def _normalize_windows_path(raw: str) -> Path:
    win_path = PureWindowsPath(raw)
    if os.name == "nt":
        return Path(win_path)

    parts = win_path.parts
    drive = win_path.drive
    if drive and len(drive) == 2 and drive[1] == ":":
        drive_letter = drive[0].lower()
        mnt_root = Path("/mnt") / drive_letter
        if mnt_root.exists():
            return mnt_root.joinpath(*parts[1:])
        if len(parts) >= 3 and parts[1].lower() == "users":
            return Path.home().joinpath(*parts[3:])
        return (Path("/") / drive_letter).joinpath(*parts[1:])

    if win_path.anchor.startswith("\\\\"):
        unc_root = Path("/mnt/unc") if Path("/mnt/unc").exists() else Path("/unc")
        anchor = win_path.anchor.strip("\\")
        unc_parts = [part for part in anchor.split("\\") if part]
        return unc_root.joinpath(*unc_parts, *parts[1:])

    return Path(win_path.as_posix())

def _normalize_posix_path(raw: str) -> Path:
    posix_path = PurePosixPath(raw)
    if os.name != "nt":
        return Path(posix_path)

    parts = posix_path.parts
    if len(parts) >= 3 and parts[1].lower() in ("home", "users"):
        return Path.home().joinpath(*parts[3:])
    if len(parts) >= 3 and parts[1].lower() in ("mnt", "cygdrive"):
        drive = parts[2]
        if len(drive) == 1 and drive.isalpha():
            return Path(f"{drive.upper()}:\\").joinpath(*parts[3:])
    return Path(posix_path.as_posix())

def _normalize_path(raw: str) -> Path:
    candidate = (raw or "").strip().strip('"').strip("'")
    if not candidate:
        raise ValueError("Empty path.")

    candidate = os.path.expandvars(candidate)
    if candidate.startswith("~"):
        candidate = os.path.expanduser(candidate)

    if _looks_like_windows_path(candidate):
        path = _normalize_windows_path(candidate)
    elif candidate.startswith("/"):
        path = _normalize_posix_path(candidate)
    else:
        path = Path(candidate)

    if not path.is_absolute():
        path = (script_dir() / path).resolve()
    return path

def _parse_path_list(raw: str) -> List[Path]:
    if not raw:
        return []
    parts = re.split(r"[\n,;]+", raw)
    paths: List[Path] = []
    for part in parts:
        chunk = part.strip().strip('"')
        if not chunk:
            continue
        try:
            paths.append(_normalize_path(chunk))
        except ValueError:
            continue
    return paths

def _structured_path_segments(text: str | None) -> List[str]:
    if not text:
        return []
    segments: List[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header = lines[i].strip()
        if not header:
            i += 1
            continue
        header_lower = header.lower()
        if "::" in header and "path" in header_lower:
            _, _, inline = header.partition("::")
            inline_val = inline.strip()
            if inline_val:
                segments.append(inline_val)
                i += 1
                continue
            i += 1
            value_lines: List[str] = []
            while i < len(lines) and lines[i].strip():
                value_lines.append(lines[i].strip())
                i += 1
            if value_lines:
                segments.append("\n".join(value_lines))
        else:
            i += 1
    return segments

def _collect_paths_from_text(text: str | None) -> List[Path]:
    if not text:
        return []
    collected: List[Path] = []
    segments = _structured_path_segments(text)
    for segment in segments:
        collected.extend(_parse_path_list(segment))
    if not collected:
        collected.extend(_parse_path_list(text))
    return collected

def _paths_from_params(node) -> List[Path]:
    if not node:
        return []
    params = getattr(node, "params", []) or []
    paths: List[Path] = []
    for entry in params:
        name = (entry.get("name") or "").lower()
        value = entry.get("value") or ""
        if not value:
            continue
        if "path" in name or "path" in value.lower():
            paths.extend(_collect_paths_from_text(value))
    return paths

def _dedupe_paths(paths: Sequence[Path]) -> List[Path]:
    seen = set()
    deduped: List[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped

def _load_file_contexts(paths: Sequence[Path]) -> Tuple[List[Tuple[Path, str]], List[str]]:
    contexts: List[Tuple[Path, str]] = []
    warnings: List[str] = []
    total = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            warnings.append(f"{path}: {exc}")
            continue
        data = text.strip()
        if not data:
            warnings.append(f"{path}: file is empty")
            continue
        if len(data) > MAX_FILE_CHARS:
            data = data[:MAX_FILE_CHARS] + "\n...\n"
            warnings.append(f"{path.name} trimmed to {MAX_FILE_CHARS} characters.")
        if total + len(data) > MAX_TOTAL_CHARS:
            remaining = max(0, MAX_TOTAL_CHARS - total)
            if remaining <= 0:
                warnings.append("Context limit reached; skipped remaining files.")
                break
            data = data[:remaining] + "\n...\n"
            warnings.append(f"{path.name} truncated to stay under total context limit.")
        total += len(data)
        contexts.append((path, data))
    return contexts, warnings

def _compose_prompt(prompt: str, contexts: Sequence[Tuple[Path, str]]) -> str:
    cleaned_prompt = (prompt or "").strip()
    if not contexts:
        return cleaned_prompt
    blocks = []
    for path, snippet in contexts:
        blocks.append(f"### {path.name}\n{snippet}")
    context_blob = "\n\n".join(blocks)
    return f"{cleaned_prompt}\n\n# Attached Files\n{context_blob}"

def _extract_response_text(payload: dict) -> str:
    texts: List[str] = []

    def _push(value):
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                texts.append(trimmed)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    _push(entry)

    for entry in payload.get("output") or []:
        entry_type = (entry.get("type") or "").lower()
        if entry_type == "message":
            for fragment in entry.get("content") or []:
                _push(fragment.get("text"))
        else:
            _push(entry.get("text"))
    if not texts and "choices" in payload:
        for choice in payload.get("choices") or []:
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                for fragment in content:
                    _push(fragment.get("text"))
            else:
                _push(content)
    if not texts:
        _push(payload.get("output_text"))
    return "\n\n".join(texts).strip()

def _call_openai(api_key: str, model: str, temperature: float, prompt_text: str) -> tuple[str, dict]:
    body = {
        "model": model or DEFAULT_MODEL,
        "input": prompt_text,
        "temperature": max(0.0, min(2.0, temperature)),
        "max_output_tokens": 2048,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI request failed: {err.code} {err.reason}\n{detail}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"OpenAI request failed: {err.reason}") from err
    return _extract_response_text(payload), payload

def _parse_ollama_payload(raw: str) -> dict:
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    payload = {}
    responses: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            piece = json.loads(line)
        except Exception:
            continue
        if not isinstance(piece, dict):
            continue
        if "response" in piece:
            responses.append(piece.get("response") or "")
        payload = piece
    if responses:
        payload = dict(payload)
        payload["response"] = "".join(responses)
    return payload

def _call_ollama(ollama_url: str, model: str, temperature: float, prompt_text: str) -> tuple[str, dict]:
    url = f"{(ollama_url or DEFAULT_OLLAMA_URL).rstrip('/')}/api/generate"
    body = {
        "model": model or DEFAULT_OLLAMA_MODEL,
        "prompt": prompt_text,
        "stream": False,
        "options": {
            "temperature": max(0.0, min(2.0, temperature)),
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Ollama request failed: {err.code} {err.reason}\n{detail}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Ollama request failed: {err.reason}") from err
    payload = _parse_ollama_payload(raw)
    response_text = (payload.get("response") or "").strip()
    return response_text, payload

def _notify(card, message: str, *, error: bool = False) -> None:
    def _show():
        fn = QtWidgets.QMessageBox.critical if error else QtWidgets.QMessageBox.information
        fn(card, APP_TITLE, message)
    QtCore.QMetaObject.invokeMethod(card, _show, QtCore.Qt.QueuedConnection)


def _param_value_from_node(node_item, name: str) -> str:
    model = getattr(node_item, "model", None)
    params = getattr(model, "params", None) or []
    key = (name or "").strip().lower()
    for p in params:
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value") or ""
    return ""


def _connected_database(card, node_item):
    sc = getattr(card, "_graph_scene", None)
    if sc is None or node_item is None:
        return None
    try:
        in_edges = sc._in_edges(node_item)
    except Exception:
        in_edges = []
    for edge in in_edges:
        port_name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        kind = (model.kind or "").strip().lower() if model else ""
        if kind == "database":
            return {
                "node_name": getattr(model, "name", ""),
                "mongo_uri": _param_value_from_node(src, "mongo_uri") or "mongodb://localhost:27017",
                "project": _param_value_from_node(src, "project"),
                "note": _param_value_from_node(src, "note"),
                "collection": _param_value_from_node(src, "collection") or COLLECTION,
            }
    return None
    return None


def _write_to_mongo(
    cfg: dict,
    prompt_text: str,
    response_text: str,
    raw_payload: dict,
    model: str,
    temperature: float,
    files: list[str] | None = None,
):
    if MongoClient is None:
        raise RuntimeError("pymongo is not installed; cannot write to Mongo. Install pymongo or disconnect the Database node.")
    uri = cfg.get("mongo_uri") or "mongodb://localhost:27017"
    project = (cfg.get("project") or "").strip()
    note = cfg.get("note") or ""
    collection_name = (cfg.get("collection") or COLLECTION).strip() or COLLECTION
    if not project:
        raise RuntimeError("Database node is connected but no project is selected.")
    client = MongoClient(uri)
    coll = client[DB_NAME][collection_name]
    filter_doc = {"$or": [{"name": project}, {"project": project}]}
    # Ensure project doc exists or update legacy doc keyed by name
    coll.update_one(
        filter_doc,
        {
            "$setOnInsert": {
                "name": project,
                "project": project,
                "type": "project",
                "created_at": datetime.datetime.utcnow().isoformat(),
                "history": [],
            },
        },
        upsert=True,
    )
    entry = {
        "role": "exchange",
        "prompt": prompt_text,
        "response": response_text,
        "model": model,
        "temperature": temperature,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    if note:
        entry["note"] = note
    if files:
        entry["files"] = list(files)
    coll.update_one({"$or": [{"name": project}, {"project": project}]}, {"$push": {"history": entry}})
    return project

def augment_infocard_footer(card, footer_layout) -> bool:
    print(f"[llm_prompt] augment_infocard_footer for node {getattr(getattr(card, '_node_ref', None), 'name', '?')}")
    node = getattr(card, "_node_ref", None)
    node_kind = (node.kind or "").strip().lower() if node else ""
    if not node or node_kind not in PROMPT_NODE_KINDS:
        return False

    def _node_item() -> object | None:
        sc = getattr(card, "_graph_scene", None)
        if not sc:
            return None
        try:
            return sc._node_items.get(node.name)
        except Exception:
            return None

    def _param_value(name: str) -> str:
        for param in (getattr(node, "params", None) or []):
            if (param.get("name") or "").strip().lower() == name:
                return param.get("value") or ""
        return ""

    def _val(name: str) -> str:
        item = _node_item()
        if item:
            wired = _text_from_input(card, item, name)
            if wired:
                return wired
        return _param_value(name)

    def _set_param(name: str, value: str) -> None:
        params = list(getattr(node, "params", None) or [])
        key = (name or "").strip().lower()
        found = False
        for entry in params:
            if (entry.get("name") or "").strip().lower() == key:
                entry["value"] = value
                found = True
                break
        if not found:
            params.append({"name": name, "value": value})
        node.params = params
        sc = getattr(card, "_graph_scene", None)
        if sc:
            try:
                sc.set_node_params(node.name, params)
                sc.refresh_node_widget(node.name)
            except Exception:
                pass
        try:
            if hasattr(card, "refresh_params_from_model"):
                card.refresh_params_from_model()
        except Exception:
            pass

    def _float_value(raw: str, default: float) -> float:
        try:
            return float(raw.strip())
        except Exception:
            return default

    def _api_key() -> str:
        wired = _val("api_key").strip()
        if wired:
            return wired
        return os.environ.get("OPENAI_API_KEY", "").strip()

    def _ollama_url() -> str:
        return _normalize_ollama_url(_val("ollama_url"))

    def _run():
        prompt_text = _val("prompt")
        if not prompt_text.strip():
            QtWidgets.QMessageBox.warning(card, APP_TITLE, "Prompt text is empty.")
            return

        model_raw = (_val("model") or "").strip()
        provider = _normalize_provider(_val("provider"), model_raw)
        model = model_raw or (DEFAULT_OLLAMA_MODEL if provider == "ollama" else DEFAULT_MODEL)
        provider_label = "OpenAI" if provider == "openai" else "Ollama"

        api_key = ""
        ollama_url = ""
        if provider == "openai":
            api_key = _api_key()
            if not api_key:
                QtWidgets.QMessageBox.warning(
                    card,
                    APP_TITLE,
                    "Provide an API key (input port, param, or OPENAI_API_KEY env var).",
                )
                return
        else:
            ollama_url = _ollama_url()
            if not ollama_url:
                QtWidgets.QMessageBox.warning(
                    card,
                    APP_TITLE,
                    "Provide an Ollama URL (input port, param, or OLLAMA_HOST/OLLAMA_URL env var).",
                )
                return

        output_raw = _val("output_path")
        db_cfg = _connected_database(card, _node_item())
        if not output_raw.strip() and not db_cfg:
            QtWidgets.QMessageBox.warning(card, APP_TITLE, "Output path is required (or connect a Database node).")
            return
        if db_cfg and not (db_cfg.get("project") or "").strip():
            QtWidgets.QMessageBox.warning(card, APP_TITLE, "Select or create a project on the connected Database node.")
            return

        output_path = None
        if output_raw.strip() and not db_cfg:
            try:
                output_path = _normalize_path(output_raw)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(card, APP_TITLE, f"Invalid output path:\n{exc}")
                return

        files_value = _val("files")
        wired_paths = _collect_paths_from_text(files_value)
        param_paths = _paths_from_params(node)
        file_paths = _dedupe_paths(param_paths + wired_paths)
        file_labels = [p.name for p in file_paths]
        contexts, warnings = _load_file_contexts(file_paths)
        combined_prompt = _compose_prompt(prompt_text, contexts)
        temperature = _float_value(_val("temperature"), DEFAULT_TEMPERATURE)
        node_item = _node_item()

        def _set_busy(active: bool, label: str = "") -> None:
            if not node_item or not hasattr(node_item, "setBusyState"):
                return
            try:
                QtCore.QMetaObject.invokeMethod(
                    node_item,
                    "setBusyState",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(bool, bool(active)),
                    QtCore.Q_ARG(str, label),
                )
            except Exception:
                try:
                    node_item.setBusyState(active, label)
                except Exception:
                    pass

        def _worker():
            try:
                if provider == "openai":
                    response_text, raw_payload = _call_openai(api_key, model, temperature, combined_prompt)
                else:
                    response_text, raw_payload = _call_ollama(ollama_url, model, temperature, combined_prompt)
                response_text = (response_text or "").strip()
                if db_cfg:
                    project = _write_to_mongo(
                        db_cfg,
                        combined_prompt,
                        response_text,
                        raw_payload,
                        model,
                        temperature,
                        files=file_labels,
                    )
                else:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(response_text, encoding="utf-8")
                    log_path = output_path.with_name(f"{output_path.stem}_log.json")
                    log_path.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:  # pylint: disable=broad-except
                _notify(card, f"{provider_label} request failed:\n{exc}", error=True)
            else:
                if db_cfg:
                    message = f"Wrote response to Mongo project '{db_cfg.get('project', '')}'"
                else:
                    message = f"Wrote response to {output_path}"
                if warnings:
                    message += "\n\nWarnings:\n" + "\n".join(warnings[:6])
                    if len(warnings) > 6:
                        message += f"\n(+{len(warnings) - 6} more)"
                _notify(card, message, error=False)
            finally:
                _set_busy(False, "")

        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Dispatching prompt…", card)
        _set_busy(True, "sending")
        threading.Thread(target=_worker, daemon=True).start()

    current_model = (_param_value("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    current_provider = _normalize_provider(_param_value("provider"), current_model)

    model_combo = QtWidgets.QComboBox()
    for label, provider, model_name in MODEL_PRESETS:
        model_combo.addItem(label, {"provider": provider, "model": model_name})

    def _find_model_index(provider: str, model_name: str) -> int:
        for i in range(model_combo.count()):
            data = model_combo.itemData(i) or {}
            if data.get("provider") == provider and data.get("model") == model_name:
                return i
        return -1

    idx = _find_model_index(current_provider, current_model)
    if idx < 0:
        label_prefix = "OpenAI" if current_provider == "openai" else "Ollama"
        model_combo.addItem(f"{label_prefix}: {current_model}", {"provider": current_provider, "model": current_model})
        idx = model_combo.count() - 1
    model_combo.setCurrentIndex(idx)

    api_key_edit = QtWidgets.QLineEdit(_param_value("api_key"))
    api_key_edit.setPlaceholderText("sk-...")
    api_key_edit.editingFinished.connect(lambda: _set_param("api_key", api_key_edit.text().strip()))

    ollama_url_edit = QtWidgets.QLineEdit(_normalize_ollama_url(_param_value("ollama_url")))
    ollama_url_edit.setPlaceholderText(DEFAULT_OLLAMA_URL)
    ollama_url_edit.editingFinished.connect(lambda: _set_param("ollama_url", ollama_url_edit.text().strip()))

    api_label = QtWidgets.QLabel("OpenAI API Key")
    ollama_label = QtWidgets.QLabel("Ollama URL")

    form = QtWidgets.QFormLayout()
    form.addRow("Model", model_combo)
    form.addRow(api_label, api_key_edit)
    form.addRow(ollama_label, ollama_url_edit)

    def _apply_provider_ui(provider: str) -> None:
        is_openai = provider == "openai"
        api_label.setVisible(is_openai)
        api_key_edit.setVisible(is_openai)
        ollama_label.setVisible(not is_openai)
        ollama_url_edit.setVisible(not is_openai)

    def _select_model(_idx: int) -> None:
        data = model_combo.currentData() or {}
        provider = _normalize_provider(data.get("provider"), data.get("model") or "")
        model_name = (data.get("model") or model_combo.currentText()).strip()
        _set_param("provider", provider)
        _set_param("model", model_name)
        _apply_provider_ui(provider)

    model_combo.currentIndexChanged.connect(_select_model)
    _apply_provider_ui(current_provider)

    controls = QtWidgets.QWidget()
    controls.setLayout(form)
    footer_layout.addWidget(controls)

    btn = QtWidgets.QPushButton("Send to GPT")
    btn.setToolTip("Gather prompt/files inputs and call the selected OpenAI or Ollama model.")
    btn.clicked.connect(_run)
    footer_layout.addWidget(btn)
    return True

PROMPT_NODE_SPEC = Spec(
    stripe_color="#06b6d4",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)
