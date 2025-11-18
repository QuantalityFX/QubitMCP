from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Sequence, Tuple

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore, QtGui
from echograph.constants import APP_TITLE, script_dir

DEFAULT_MODEL = "gpt-4.1-mini"
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
    _ensure_param(node_item, "model", DEFAULT_MODEL)
    _ensure_param(node_item, "temperature", str(DEFAULT_TEMPERATURE))
    for port in ("prompt", "files", "output_path", "api_key", "model", "temperature"):
        _ensure_input(node_item, port)

def _normalize_path(raw: str) -> Path:
    candidate = (raw or "").strip().strip('"')
    if not candidate:
        raise ValueError("Empty path.")
    path = Path(candidate).expanduser()
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

def _notify(card, message: str, *, error: bool = False) -> None:
    def _show():
        fn = QtWidgets.QMessageBox.critical if error else QtWidgets.QMessageBox.information
        fn(card, APP_TITLE, message)
    QtCore.QMetaObject.invokeMethod(card, _show, QtCore.Qt.QueuedConnection)

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

    def _run():
        prompt_text = _val("prompt")
        if not prompt_text.strip():
            QtWidgets.QMessageBox.warning(card, APP_TITLE, "Prompt text is empty.")
            return

        api_key = _api_key()
        if not api_key:
            QtWidgets.QMessageBox.warning(card, APP_TITLE, "Provide an API key (input port, param, or OPENAI_API_KEY env var).")
            return

        output_raw = _val("output_path")
        if not output_raw.strip():
            QtWidgets.QMessageBox.warning(card, APP_TITLE, "Output path is required.")
            return

        try:
            output_path = _normalize_path(output_raw)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(card, APP_TITLE, f"Invalid output path:\n{exc}")
            return

        files_value = _val("files")
        wired_paths = _collect_paths_from_text(files_value)
        param_paths = _paths_from_params(node)
        file_paths = _dedupe_paths(param_paths + wired_paths)
        contexts, warnings = _load_file_contexts(file_paths)
        combined_prompt = _compose_prompt(prompt_text, contexts)
        model = (_val("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
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
                response_text, raw_payload = _call_openai(api_key, model, temperature, combined_prompt)
                response_text = (response_text or "").strip()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(response_text, encoding="utf-8")
                log_path = output_path.with_name(f"{output_path.stem}_log.json")
                log_path.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:  # pylint: disable=broad-except
                _notify(card, f"GPT request failed:\n{exc}", error=True)
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

    btn = QtWidgets.QPushButton("Send to GPT")
    btn.setToolTip("Gather prompt/files/API key inputs and call the OpenAI Responses API.")
    btn.clicked.connect(_run)
    footer_layout.addWidget(btn)
    return True

PROMPT_NODE_SPEC = Spec(
    stripe_color="#06b6d4",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)
