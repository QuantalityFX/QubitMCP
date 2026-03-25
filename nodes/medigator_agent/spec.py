from __future__ import annotations

import datetime
import hashlib
import os
import subprocess
import threading
from pathlib import Path

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore


MEDIGATOR_NODE_KIND = "medigator_agent"
MEDIGATOR_NODE_ALIASES = [
    "mediator_agent",
    "medigator",
    "mediator",
]
MEDIGATOR_NODE_KINDS = {MEDIGATOR_NODE_KIND, *MEDIGATOR_NODE_ALIASES}

MEDIGATOR_BODY_W = 560
MEDIGATOR_BODY_H = 340
MEDIGATOR_MEMORY_ROOT = "medigator_agents"
MEDIGATOR_DEFAULT_SYSTEM_PROMPT = (
    "You are Medigator, a conversation mediator. "
    "Use the provided history and latest voice input to craft the next assistant reply. "
    "Return only the assistant response text."
)
MEDIGATOR_MAX_SYSTEM_CHARS = 4000
MEDIGATOR_MAX_HISTORY_CHARS = 24000
MEDIGATOR_MAX_VOICE_CHARS = 8000
MEDIGATOR_CODEX_MODEL = "gpt-5.3-codex"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
        text = "medigator"
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "medigator"


def _workspace_dir_for_node(node_item) -> Path:
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    safe_name = _sanitize_folder_name(node_name)
    workspace = _repo_root() / "logs" / MEDIGATOR_MEMORY_ROOT / safe_name
    workspace.mkdir(parents=True, exist_ok=True)
    memory_file = workspace / "memory.md"
    if not memory_file.exists():
        memory_file.write_text(
            "# Medigator Memory\n\n"
            "This folder is dedicated to this node.\n"
            "Store notes, summaries, and agent context here.\n",
            encoding="utf-8",
        )
    return workspace


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
    if out:
        return out
    return edges


def _text_from_input(scene, node_item, port_name: str) -> str:
    named_edges = _input_edges(scene, node_item, port_name)
    if not named_edges:
        return ""

    parts = []
    for edge in named_edges:
        src = getattr(edge, "src", None)
        if src is None:
            continue
        if _kind_of_item(src) in {"voice_actor", "voice actor", "voiceactor"}:
            text = _voice_actor_transcript(src)
        else:
            try:
                text = scene.resolve_text_value(src)
            except Exception:
                text = ""
        if text:
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


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


def _set_node_info(node_item, text: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    value = text or ""
    if (getattr(model, "info", "") or "") == value:
        return
    model.info = value
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _trim_text(text: str, limit: int, *, keep_tail: bool = False) -> str:
    clean = str(text or "").strip()
    if limit <= 0 or len(clean) <= limit:
        return clean
    if keep_tail:
        return clean[-limit:]
    return clean[:limit]


def _compose_medigator_prompt(system_prompt: str, chatbot_history: str, voice_input: str) -> tuple[str, str]:
    clean_system = _trim_text(system_prompt, MEDIGATOR_MAX_SYSTEM_CHARS, keep_tail=False)
    clean_history = _trim_text(chatbot_history, MEDIGATOR_MAX_HISTORY_CHARS, keep_tail=True)
    clean_voice = _trim_text(voice_input, MEDIGATOR_MAX_VOICE_CHARS, keep_tail=True)
    payload = "\n\n".join(
        [
            clean_system,
            clean_history,
            clean_voice,
        ]
    )
    signature = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    prompt = (
        f"{clean_system or MEDIGATOR_DEFAULT_SYSTEM_PROMPT}\n\n"
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


class MedigatorConsoleWidget(QtWidgets.QWidget):
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
        self._auto_baseline_ready = False
        self._pending_prompt = ""
        self._pending_signature = ""
        self._pending_source = ""

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
            self._command_edit.setText(f'& "{codex_script}"')

        self._console = QtWidgets.QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setStyleSheet(
            "QPlainTextEdit{background:#0b1220;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px;}"
        )

        self._status = QtWidgets.QLabel("Ready.")
        self._status.setStyleSheet("QLabel{color:#94a3b8;}")

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
        try:
            with self._console_log_path().open("a", encoding="utf-8") as handle:
                handle.write(payload)
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
            self._node_item.setBusyState(self._running, "medigator-running" if self._running else "")
        except Exception:
            pass

    def _update_controls(self) -> None:
        self._run_btn.setEnabled(not self._running)
        self._process_btn.setEnabled(not self._running)
        self._stop_btn.setEnabled(self._running)
        self._command_edit.setEnabled(not self._running)
        self._auto_chk.setEnabled(not self._running)

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
        else:
            self._last_voice_mode = _voice_actor_mode_from_item(voice_item)
        self._auto_baseline_ready = True

    def _connected_source_names(self) -> set[str]:
        scene = self._ensure_scene()
        if scene is None:
            return set()
        names = set()
        for edge in _ordered_in_edges(scene, self._node_item):
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
        for edge in _input_edges(scene, self._node_item, "voice_input"):
            src = getattr(edge, "src", None)
            if src is None:
                continue
            if _kind_of_item(src) in {"voice_actor", "voice actor", "voiceactor"}:
                return src
        return None

    def _collect_inputs(self) -> tuple[str, str, str]:
        scene = self._ensure_scene()
        if scene is None:
            return "", "", ""
        system_prompt = _text_from_input(scene, self._node_item, "system_prompt")
        chatbot_history = _text_from_input(scene, self._node_item, "chatbot_history")
        voice_input = _text_from_input(scene, self._node_item, "voice_input")
        return system_prompt, chatbot_history, voice_input

    def _queue_pending(self, prompt: str, signature: str, source: str) -> None:
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
        if voice_item is None:
            self._last_voice_mode = ""
        else:
            current_voice_mode = _voice_actor_mode_from_item(voice_item)
            if (
                not force
                and bool(self._last_voice_mode)
                and current_voice_mode != self._last_voice_mode
            ):
                # Mode toggles are control events and should not dispatch downstream calls.
                self._last_voice_mode = current_voice_mode
                return
            self._last_voice_mode = current_voice_mode

        system_prompt, chatbot_history, voice_input = self._collect_inputs()
        clean_voice_input = str(voice_input or "").strip()
        if not clean_voice_input:
            if not force:
                self._last_auto_voice_input = ""
            if force:
                self._set_status("No voice_input text available.", error=True)
            return
        if not force and clean_voice_input == self._last_auto_voice_input:
            return
        if not force:
            self._last_auto_voice_input = clean_voice_input

        prompt, signature = _compose_medigator_prompt(system_prompt, chatbot_history, voice_input)
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
        self._set_running(True)
        self._set_status(f"Running Codex ({mode})...")
        self._write_history(f"tools\\codex.ps1 -Exec <medigator:{mode}>")
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
            prompt_path = self._workspace_dir / f"medigator_prompt_{stamp}.md"
            output_path = self._workspace_dir / f"medigator_response_{stamp}.txt"
            prompt_path.write_text(prompt or "", encoding="utf-8")

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
                "-Cd",
                str(_repo_root()),
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
        if proc is None:
            self._set_status("No running command.")
            return
        try:
            proc.terminate()
            self._console_append.emit("Process termination requested.")
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

        if error_text:
            self._console_append.emit(error_text)
            self._set_status(error_text, error=True)
        elif is_codex_process:
            output = str(response_text or "").strip()
            if output:
                _set_node_info(self._node_item, output)
                self._console_append.emit("[medigator] Response published to node output.")
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
        body = MedigatorConsoleWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Medigator UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        msg = QtWidgets.QLabel("Medigator UI failed to load. Check console output for details.")
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
