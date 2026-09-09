from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from nodes.core import Spec
from echograph.qt_compat import QtCore, QtWidgets
from echograph.services import codex_cli_runner


CODEX_SANDBOX_NODE_KIND = "codex_sandbox"
CODEX_SANDBOX_NODE_ALIASES = [
    "codex sandbox",
    "sandbox",
]
CODEX_SANDBOX_NODE_KINDS = {CODEX_SANDBOX_NODE_KIND, *CODEX_SANDBOX_NODE_ALIASES}

LEGACY_DEFAULT_EXECUTABLE = "npx @openai/codex"
DEFAULT_CODEX_EXECUTABLE = ""
DEFAULT_RUNNER_MODE = "installed_cli"
DEFAULT_MODEL = codex_cli_runner.DEFAULT_MODEL
DEFAULT_MODEL_REASONING_EFFORT = codex_cli_runner.DEFAULT_MODEL_REASONING_EFFORT
DEFAULT_SANDBOX_MODE = codex_cli_runner.DEFAULT_SANDBOX_MODE
DEFAULT_APPROVAL_POLICY = codex_cli_runner.DEFAULT_APPROVAL_POLICY
DEFAULT_CODEX_HOME = ""
DEFAULT_ACCOUNT_PROFILE = ""
DEFAULT_PROJECT_ROOT = ""
DEFAULT_WORKING_DIRECTORY = ""
DEFAULT_WRITABLE_ROOTS = ""
DEFAULT_NETWORK_ACCESS = "false"
DEFAULT_OUTPUT_CAPTURE = "last_message"

SANDBOX_MODES = ("read-only", "workspace-write")
APPROVAL_POLICIES = ("never", "on-request", "on-failure", "untrusted")
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
OUTPUT_CAPTURE_MODES = ("last_message", "stdout", "transcript")
RUNNER_MODES = ("installed_cli",)

PARAM_DEFAULTS = {
    "codex_executable": DEFAULT_CODEX_EXECUTABLE,
    "runner_mode": DEFAULT_RUNNER_MODE,
    "codex_home": DEFAULT_CODEX_HOME,
    "account_profile": DEFAULT_ACCOUNT_PROFILE,
    "model": DEFAULT_MODEL,
    "model_reasoning_effort": DEFAULT_MODEL_REASONING_EFFORT,
    "sandbox_mode": DEFAULT_SANDBOX_MODE,
    "approval_policy": DEFAULT_APPROVAL_POLICY,
    "project_root": DEFAULT_PROJECT_ROOT,
    "working_directory": DEFAULT_WORKING_DIRECTORY,
    "writable_roots": DEFAULT_WRITABLE_ROOTS,
    "network_access": DEFAULT_NETWORK_ACCESS,
    "output_capture": DEFAULT_OUTPUT_CAPTURE,
    "extra_args": "",
}

HIDDEN_NODE_PARAMS = (
    "codex_executable",
    "runner_mode",
    "codex_home",
    "account_profile",
    "model",
    "model_reasoning_effort",
    "sandbox_mode",
    "approval_policy",
    "project_root",
    "working_directory",
    "writable_roots",
    "network_access",
    "output_capture",
    "extra_args",
)


class _SandboxSignals(QtCore.QObject):
    message = QtCore.Signal(str, bool)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _hidden_subprocess_kwargs() -> dict:
    kwargs = {}
    if os.name != "nt":
        return kwargs
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        try:
            creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW"))
        except Exception:
            pass
    if creationflags:
        kwargs["creationflags"] = creationflags
    return kwargs


def _ensure_param(node_item, name: str, default: str = "") -> None:
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


def _ensure_hidden_params_default(node_item) -> None:
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
    default_value = ",".join(HIDDEN_NODE_PARAMS)
    for entry in params:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == "__ui_hidden_params":
            if not str(entry.get("value", "") or "").strip():
                entry["value"] = default_value
            return
    params.append({"name": "__ui_hidden_params", "value": default_value})


def _ensure_output(node_item, name: str) -> None:
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output(name)
    elif hasattr(node_item, "add_output_port"):
        node_item.add_output_port(name)
    elif hasattr(node_item, "add_output"):
        node_item.add_output(name)


def _param_value_from_model(model, name: str, default: str = "") -> str:
    target = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == target:
            return str(entry.get("value", "") or "")
    return str(default or "")


def _bool_text(value: object) -> str:
    return "true" if codex_cli_runner.normalize_bool(value) else "false"


def _path_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(os.path.expandvars(os.path.expanduser(raw))).resolve(strict=False))
    except Exception:
        return raw


def _workflow_project_root(node_item=None, *, fallback_repo: bool = True) -> str:
    candidates = []
    try:
        scene_fn = getattr(node_item, "scene", None)
        scene = scene_fn() if callable(scene_fn) else None
    except Exception:
        scene = None
    parent = None
    if scene is not None:
        try:
            parent_fn = getattr(scene, "parent", None)
            parent = parent_fn() if callable(parent_fn) else None
        except Exception:
            parent = None
    for source in (scene, parent):
        if source is None:
            continue
        for attr in ("_current_path", "_filename", "workflow_path", "graph_path", "file_path"):
            try:
                value = getattr(source, attr, "")
            except Exception:
                value = ""
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = ""
            if str(value or "").strip():
                candidates.append(str(value).strip())
    for value in candidates:
        try:
            path = Path(os.path.expandvars(os.path.expanduser(value)))
            if path.suffix:
                path = path.parent
            if str(path):
                return str(path.resolve(strict=False))
        except Exception:
            continue
    return str(_repo_root()) if fallback_repo else ""


def _effective_defaults(node_item=None, model=None, *, fallback_project_root: bool = True) -> dict:
    if model is None:
        model = getattr(node_item, "model", node_item)
    codex_home = _param_value_from_model(model, "codex_home", "").strip()
    try:
        config_defaults = codex_cli_runner.codex_config_defaults(codex_home)
    except Exception:
        config_defaults = {}

    defaults = dict(PARAM_DEFAULTS)
    for key in ("codex_home", "model", "model_reasoning_effort", "sandbox_mode", "approval_policy"):
        value = str(config_defaults.get(key, "") or "").strip()
        if value:
            defaults[key] = value
    defaults["network_access"] = _bool_text(config_defaults.get("network_access", DEFAULT_NETWORK_ACCESS))
    config_roots = codex_cli_runner.split_writable_roots(config_defaults.get("writable_roots", []))
    if config_roots:
        defaults["writable_roots"] = "; ".join(config_roots)
    config_path = str(config_defaults.get("config_path", "") or "").strip()
    if config_path:
        defaults["_codex_config_path"] = _path_text(config_path)

    project_root = _workflow_project_root(node_item, fallback_repo=fallback_project_root)
    if project_root:
        defaults["project_root"] = project_root
        defaults["working_directory"] = project_root
        if defaults.get("sandbox_mode") == "workspace-write" and not defaults.get("writable_roots"):
            defaults["writable_roots"] = project_root
    return defaults


def _effective_param_value(model, name: str, defaults: dict) -> str:
    raw = _param_value_from_model(model, name, "").strip()
    if raw:
        return raw
    return str(defaults.get(name, PARAM_DEFAULTS.get(name, "")) or "")


def sandbox_config_from_item(node_item) -> dict:
    model = getattr(node_item, "model", node_item)
    defaults = _effective_defaults(node_item, model)
    config = {}
    for name, default in PARAM_DEFAULTS.items():
        config[name] = _effective_param_value(model, name, defaults).strip()

    legacy_executable = _param_value_from_model(model, "executable", "").strip()
    if (
        not config.get("codex_executable")
        and legacy_executable
        and legacy_executable != LEGACY_DEFAULT_EXECUTABLE
    ):
        config["codex_executable"] = legacy_executable

    runner_mode = config.get("runner_mode") or DEFAULT_RUNNER_MODE
    if runner_mode not in RUNNER_MODES:
        runner_mode = DEFAULT_RUNNER_MODE
    mode = config.get("sandbox_mode") or DEFAULT_SANDBOX_MODE
    if mode not in SANDBOX_MODES:
        mode = DEFAULT_SANDBOX_MODE
    approval = config.get("approval_policy") or DEFAULT_APPROVAL_POLICY
    if approval not in APPROVAL_POLICIES:
        approval = DEFAULT_APPROVAL_POLICY
    reasoning = config.get("model_reasoning_effort") or DEFAULT_MODEL_REASONING_EFFORT
    reasoning = codex_cli_runner.normalize_reasoning_effort(reasoning)
    output_capture = config.get("output_capture") or DEFAULT_OUTPUT_CAPTURE
    if output_capture not in OUTPUT_CAPTURE_MODES:
        output_capture = DEFAULT_OUTPUT_CAPTURE
    network_raw = str(config.get("network_access") or "").strip().lower()

    config["runner_mode"] = runner_mode
    config["model"] = config.get("model") or DEFAULT_MODEL
    config["model_reasoning_effort"] = reasoning
    config["sandbox_mode"] = mode
    config["approval_policy"] = approval
    config["output_capture"] = output_capture
    config["network_access"] = "true" if network_raw in {"1", "true", "yes", "on"} else "false"
    config["_codex_config_path"] = str(defaults.get("_codex_config_path", "") or "")
    return config


def sandbox_summary_text(node_item) -> str:
    config = sandbox_config_from_item(node_item)
    executable = config.get("codex_executable") or "(auto-detect codex/codex.cmd)"
    codex_home = config.get("codex_home") or "(default user Codex home)"
    project_root = config.get("project_root") or "(not set)"
    working_dir = config.get("working_directory") or "(project root or Mediator log workspace)"
    writable_roots = config.get("writable_roots") or "(none)"
    return "\n".join(
        [
            "Codex Sandbox:",
            f"runner_mode: {config.get('runner_mode') or DEFAULT_RUNNER_MODE}",
            f"codex_executable: {executable}",
            f"codex_home: {codex_home}",
            f"account_profile: {config.get('account_profile') or '(default)'}",
            f"model: {config.get('model') or DEFAULT_MODEL}",
            f"model_reasoning_effort: {config.get('model_reasoning_effort') or DEFAULT_MODEL_REASONING_EFFORT}",
            f"sandbox_mode: {config.get('sandbox_mode') or DEFAULT_SANDBOX_MODE}",
            f"approval_policy: {config.get('approval_policy') or DEFAULT_APPROVAL_POLICY}",
            f"project_root: {project_root}",
            f"working_directory: {working_dir}",
            f"writable_roots: {writable_roots}",
            f"network_access: {config.get('network_access') or DEFAULT_NETWORK_ACCESS}",
            f"output_capture: {config.get('output_capture') or DEFAULT_OUTPUT_CAPTURE}",
            f"codex_config: {config.get('_codex_config_path') or '(not found)'}",
            f"extra_args: {config.get('extra_args') or '(none)'}",
        ]
    )


def _sync_info(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    try:
        model.info = sandbox_summary_text(node_item)
    except Exception:
        pass


def build_ports(node_item) -> None:
    defaults = _effective_defaults(node_item, fallback_project_root=False)
    for name, default in PARAM_DEFAULTS.items():
        _ensure_param(node_item, name, str(defaults.get(name, default) or ""))
    _ensure_hidden_params_default(node_item)
    _ensure_output(node_item, "config")
    _sync_info(node_item)


def _selected_cwd(config: dict) -> str:
    for key in ("working_directory", "project_root"):
        value = str(config.get(key) or "").strip()
        if value:
            try:
                path = Path(os.path.expandvars(os.path.expanduser(value)))
                if path.exists() and path.is_dir():
                    return str(path)
            except Exception:
                pass
    return str(_repo_root())


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None or str(getattr(node, "kind", "") or "").strip().lower() not in CODEX_SANDBOX_NODE_KINDS:
        return False

    signals = _SandboxSignals(card)
    scene = getattr(card, "_graph_scene", None)
    try:
        node_item = getattr(scene, "_node_items", {}).get(node.name) if scene is not None else None
    except Exception:
        node_item = None
    defaults = _effective_defaults(node_item or node, node)

    def _param_val(key: str) -> str:
        return _effective_param_value(node, key, defaults)

    def _set_param(key: str, value: str) -> None:
        params = list(getattr(node, "params", None) or [])
        found = False
        for entry in params:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name", "") or "").strip().lower() == key.strip().lower():
                entry["value"] = value
                found = True
                break
        if not found:
            params.append({"name": key, "value": value})
        node.params = params
        try:
            node.info = sandbox_summary_text(node)
        except Exception:
            pass
        if scene is not None:
            try:
                scene.set_node_params(node.name, params)
            except Exception:
                pass
            try:
                scene.paramChanged.emit(node.name, list(params))
            except Exception:
                pass

    def _combo(options, current):
        combo = QtWidgets.QComboBox()
        combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for option in options:
            combo.addItem(option)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def _line_edit(key: str, placeholder: str = ""):
        edit = QtWidgets.QLineEdit(_param_val(key))
        edit.setPlaceholderText(placeholder)
        edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        edit.editingFinished.connect(lambda e=edit, k=key: _set_param(k, e.text().strip()))
        return edit

    def _configure_form(form_layout) -> None:
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(6)
        form_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        form_layout.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        form_layout.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

    def _path_start(edit) -> str:
        raw = str(edit.text() or "").strip()
        roots = _path_list_entries(raw)
        if roots:
            raw = roots[-1]
        if not raw:
            return str(_repo_root())
        try:
            expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
            return str(expanded.parent if expanded.suffix else expanded)
        except Exception:
            return str(_repo_root())

    def _browse_file(edit, key: str, title: str) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            card,
            title,
            _path_start(edit),
            "Executables (*.exe *.cmd *.bat);;All Files (*.*)",
        )
        if path:
            edit.setText(path)
            _set_param(key, path)

    def _path_list_entries(value: str) -> list[str]:
        parts = []
        for part in str(value or "").replace("\n", ";").split(";"):
            part = part.strip().strip("\"'")
            if part:
                parts.append(part)
        return parts

    def _path_compare_key(value: str) -> str:
        try:
            return str(Path(os.path.expandvars(os.path.expanduser(value))).resolve(strict=False)).casefold()
        except Exception:
            return str(value or "").strip().casefold()

    def _browse_writable_root(edit) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(card, "Select Writable Root", _path_start(edit))
        if not path:
            return
        entries = _path_list_entries(edit.text())
        selected_key = _path_compare_key(path)
        if not any(_path_compare_key(entry) == selected_key for entry in entries):
            entries.append(path)
        value = "; ".join(entries) if entries else path
        edit.setText(value)
        _set_param("writable_roots", value)

    def _browse_folder(edit, key: str, title: str) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(card, title, _path_start(edit))
        if path:
            edit.setText(path)
            _set_param(key, path)

    def _path_row(edit, browse_callback, browse_tooltip: str = "Browse"):
        row = QtWidgets.QWidget()
        row.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(edit, 1)
        btn = QtWidgets.QPushButton("...")
        btn.setFixedWidth(30)
        btn.setToolTip(browse_tooltip)
        btn.clicked.connect(browse_callback)
        lay.addWidget(btn, 0)
        return row

    def _label(text: str, tooltip: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setToolTip(tooltip)
        label.setCursor(QtCore.Qt.WhatsThisCursor)
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        return label

    def _add_row(form_layout, text: str, tooltip: str, field) -> None:
        try:
            field.setToolTip(tooltip)
        except Exception:
            pass
        form_layout.addRow(_label(text, tooltip), field)

    def _current_config() -> dict:
        return sandbox_config_from_item(node)

    def _run_account_command(label: str, args: list[str]) -> None:
        config = _current_config()
        signals.message.emit(f"{label}...", False)

        def _worker() -> None:
            try:
                result = codex_cli_runner.run_codex_command_capture(
                    args,
                    codex_executable=config.get("codex_executable", ""),
                    codex_home=config.get("codex_home", ""),
                    cwd=_selected_cwd(config),
                    subprocess_kwargs=_hidden_subprocess_kwargs(),
                )
                text = result.text or f"{label} exited with code {result.exit_code}."
                signals.message.emit(text, result.exit_code != 0)
            except Exception as exc:
                signals.message.emit(str(exc), True)

        threading.Thread(target=_worker, daemon=True).start()

    def _start_login() -> None:
        config = _current_config()
        try:
            codex_cli_runner.start_visible_codex_command(
                ["login"],
                codex_executable=config.get("codex_executable", ""),
                codex_home=config.get("codex_home", ""),
                cwd=_selected_cwd(config),
            )
            signals.message.emit("Codex login started in a visible terminal.", False)
        except Exception as exc:
            signals.message.emit(str(exc), True)

    def _logout() -> None:
        answer = QtWidgets.QMessageBox.question(
            card,
            "Codex Sandbox",
            "Log out of the selected Codex account?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            _run_account_command("Logging out of Codex", ["logout"])

    def _detect_cli() -> None:
        found = codex_cli_runner.detect_codex_executable()
        if not found:
            signals.message.emit("Codex CLI was not found on PATH. Install Codex or select codex.cmd manually.", True)
            return
        executable_edit.setText(found)
        _set_param("codex_executable", found)
        signals.message.emit(f"Detected Codex CLI: {found}", False)

    panel = QtWidgets.QWidget(card)
    panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    panel_layout = QtWidgets.QVBoxLayout(panel)
    panel_layout.setContentsMargins(0, 0, 0, 0)
    panel_layout.setSpacing(6)

    title = QtWidgets.QLabel("Codex Sandbox")
    title.setToolTip("Configure how the Mediator launches the local Codex CLI for sandboxed coding runs.")
    title.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    title.setMinimumHeight(26)
    title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    title.setStyleSheet(
        "QLabel{"
        "background:#334155;"
        "color:#f8fafc;"
        "font-weight:700;"
        "border-radius:4px;"
        "padding:4px 8px;"
        "margin-top:6px;"
        "}"
    )
    panel_layout.addWidget(title)

    form = QtWidgets.QFormLayout()
    _configure_form(form)

    model_edit = _line_edit("model", DEFAULT_MODEL)
    _add_row(
        form,
        "Model",
        "Codex model used for sandbox runs. Leave the default unless you need a specific installed Codex model.",
        model_edit,
    )

    reasoning_combo = _combo(REASONING_EFFORTS, _param_val("model_reasoning_effort") or DEFAULT_MODEL_REASONING_EFFORT)
    reasoning_combo.currentTextChanged.connect(lambda value: _set_param("model_reasoning_effort", value))
    _add_row(
        form,
        "Reasoning",
        "Reasoning effort passed to Codex. Higher values spend more time and tokens on harder coding tasks.",
        reasoning_combo,
    )

    sandbox_combo = _combo(SANDBOX_MODES, _param_val("sandbox_mode") or DEFAULT_SANDBOX_MODE)
    sandbox_combo.currentTextChanged.connect(lambda value: _set_param("sandbox_mode", value))
    _add_row(
        form,
        "Sandbox",
        "Filesystem access mode. read-only can inspect files; workspace-write can edit allowed workspace paths.",
        sandbox_combo,
    )

    approval_combo = _combo(APPROVAL_POLICIES, _param_val("approval_policy") or DEFAULT_APPROVAL_POLICY)
    approval_combo.currentTextChanged.connect(lambda value: _set_param("approval_policy", value))
    _add_row(
        form,
        "Approval",
        "When Codex should ask before actions. The default never keeps runs non-interactive from this node.",
        approval_combo,
    )

    network_combo = _combo(("false", "true"), (_param_val("network_access") or DEFAULT_NETWORK_ACCESS).lower())
    network_combo.currentTextChanged.connect(lambda value: _set_param("network_access", value))
    _add_row(
        form,
        "Network",
        "Allows network access inside workspace-write sandbox runs when the selected Codex CLI supports it.",
        network_combo,
    )

    output_combo = _combo(OUTPUT_CAPTURE_MODES, _param_val("output_capture") or DEFAULT_OUTPUT_CAPTURE)
    output_combo.currentTextChanged.connect(lambda value: _set_param("output_capture", value))
    _add_row(
        form,
        "Output",
        "Which Codex output the Mediator captures: final answer, full stdout, or transcript-style output.",
        output_combo,
    )

    panel_layout.addLayout(form)

    advanced_toggle = QtWidgets.QToolButton()
    advanced_toggle.setText("Advanced")
    advanced_toggle.setToolTip("Show Codex CLI paths, project roots, writable roots, and extra arguments.")
    advanced_toggle.setCheckable(True)
    advanced_toggle.setChecked(False)
    advanced_toggle.setArrowType(QtCore.Qt.RightArrow)
    advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    advanced_toggle.setMinimumHeight(24)
    advanced_toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    advanced_toggle.setStyleSheet(
        "QToolButton{"
        "background:#1e293b;"
        "color:#e2e8f0;"
        "border:1px solid #334155;"
        "border-radius:4px;"
        "font-weight:700;"
        "padding:3px 8px;"
        "text-align:left;"
        "}"
        "QToolButton:hover{background:#263449;border-color:#475569;}"
        "QToolButton:checked{background:#334155;color:#f8fafc;}"
    )
    panel_layout.addWidget(advanced_toggle)

    advanced_widget = QtWidgets.QWidget()
    advanced_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    advanced_form = QtWidgets.QFormLayout(advanced_widget)
    _configure_form(advanced_form)
    advanced_widget.setVisible(False)

    executable_edit = _line_edit("codex_executable", "auto-detect codex/codex.cmd")
    _add_row(
        advanced_form,
        "Codex CLI",
        "Optional path to codex.cmd, codex, or a supported wrapper. Blank uses auto-detection.",
        _path_row(
            executable_edit,
            lambda: _browse_file(executable_edit, "codex_executable", "Select Codex CLI"),
            "Choose Codex CLI executable",
        ),
    )

    codex_home_edit = _line_edit("codex_home", codex_cli_runner.default_codex_home())
    _add_row(
        advanced_form,
        "Codex Home",
        "CODEX_HOME directory used for Codex account state and config.toml. Blank uses the current user default.",
        _path_row(
            codex_home_edit,
            lambda: _browse_folder(codex_home_edit, "codex_home", "Select Codex Home"),
            "Choose Codex home folder",
        ),
    )

    profile_edit = _line_edit("account_profile", "optional profile label")
    _add_row(
        advanced_form,
        "Profile",
        "Optional label for your own bookkeeping. It does not switch Codex accounts by itself.",
        profile_edit,
    )

    project_root_edit = _line_edit("project_root", _workflow_project_root(node_item))
    _add_row(
        advanced_form,
        "Project Root",
        "Main repository or project folder for this sandbox configuration. Also used as the default working directory.",
        _path_row(
            project_root_edit,
            lambda: _browse_folder(project_root_edit, "project_root", "Select Project Root"),
            "Choose project root folder",
        ),
    )

    workdir_edit = _line_edit("working_directory", "defaults to project root")
    _add_row(
        advanced_form,
        "Working Dir",
        "Directory passed to Codex as --cd. This is where commands run and the primary sandbox root.",
        _path_row(
            workdir_edit,
            lambda: _browse_folder(workdir_edit, "working_directory", "Select Working Directory"),
            "Choose working directory",
        ),
    )

    writable_roots_edit = _line_edit("writable_roots", "defaults to project root for workspace-write")
    _add_row(
        advanced_form,
        "Writable Roots",
        "Extra folders Codex may edit when Sandbox is workspace-write. Use semicolons for multiple roots.",
        _path_row(
            writable_roots_edit,
            lambda: _browse_writable_root(writable_roots_edit),
            "Add writable root folder",
        ),
    )

    extra_edit = _line_edit("extra_args", "optional extra global Codex CLI args")
    _add_row(
        advanced_form,
        "Extra Args",
        "Optional raw Codex CLI arguments appended before exec. Use only for advanced CLI options.",
        extra_edit,
    )

    def _toggle_advanced(checked: bool) -> None:
        advanced_toggle.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)
        advanced_widget.setVisible(bool(checked))

    advanced_toggle.toggled.connect(_toggle_advanced)
    panel_layout.addWidget(advanced_widget)

    detect_btn = QtWidgets.QPushButton("Detect CLI")
    status_btn = QtWidgets.QPushButton("Status")
    login_btn = QtWidgets.QPushButton("Login")
    logout_btn = QtWidgets.QPushButton("Logout")
    detect_btn.setToolTip("Search common install locations and PATH for the Codex CLI executable.")
    status_btn.setToolTip("Run codex login status using the selected Codex Home and CLI path.")
    login_btn.setToolTip("Open a visible terminal to sign in to the selected Codex Home.")
    logout_btn.setToolTip("Log out of the selected Codex account state.")
    detect_btn.setStyleSheet(
        "QPushButton{background:#334b73;color:#eaf2ff;border:1px solid #49658f;border-radius:4px;padding:5px 8px;font-weight:600;}"
        "QPushButton:hover{background:#3d5a87;border-color:#5f7daa;}"
        "QPushButton:pressed{background:#2a3f61;}"
    )
    status_btn.setStyleSheet(
        "QPushButton{background:#735134;color:#fff4e8;border:1px solid #8f6948;border-radius:4px;padding:5px 8px;font-weight:600;}"
        "QPushButton:hover{background:#875f3d;border-color:#aa805f;}"
        "QPushButton:pressed{background:#61432a;}"
    )
    login_btn.setStyleSheet(
        "QPushButton{background:#2f6b4f;color:#effaf4;border:1px solid #43805f;border-radius:4px;padding:5px 8px;font-weight:600;}"
        "QPushButton:hover{background:#397b5c;border-color:#5f9b77;}"
        "QPushButton:pressed{background:#275940;}"
    )
    logout_btn.setStyleSheet(
        "QPushButton{background:#7a3737;color:#fff0f0;border:1px solid #955151;border-radius:4px;padding:5px 8px;font-weight:600;}"
        "QPushButton:hover{background:#8c4242;border-color:#b06666;}"
        "QPushButton:pressed{background:#642d2d;}"
    )
    detect_btn.clicked.connect(_detect_cli)
    status_btn.clicked.connect(lambda: _run_account_command("Checking Codex status", ["login", "status"]))
    login_btn.clicked.connect(_start_login)
    logout_btn.clicked.connect(_logout)

    actions_row_1 = QtWidgets.QHBoxLayout()
    actions_row_1.setContentsMargins(0, 4, 0, 0)
    actions_row_1.setSpacing(6)
    actions_row_1.addWidget(detect_btn, 1)
    actions_row_1.addWidget(status_btn, 1)

    actions_row_2 = QtWidgets.QHBoxLayout()
    actions_row_2.setContentsMargins(0, 0, 0, 0)
    actions_row_2.setSpacing(6)
    actions_row_2.addWidget(login_btn, 1)
    actions_row_2.addWidget(logout_btn, 1)

    panel_layout.addLayout(actions_row_1)
    panel_layout.addLayout(actions_row_2)

    config_path = str(defaults.get("_codex_config_path", "") or codex_cli_runner.default_codex_config_path(_param_val("codex_home")))
    config_exists = bool(config_path and Path(config_path).exists())
    status_label = QtWidgets.QLabel("Using Codex config." if config_exists else "Codex config not found; using built-in defaults.")
    status_label.setToolTip(config_path)
    status_label.setWordWrap(True)
    status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    status_label.setStyleSheet("QLabel{color:#94a3b8;margin-top:4px;}")

    def _show_status(message: str, error: bool) -> None:
        status_label.setText(str(message or "").strip()[:1200] or "No output.")
        status_label.setStyleSheet(
            "QLabel{color:#fca5a5;margin-top:4px;}" if error else "QLabel{color:#94a3b8;margin-top:4px;}"
        )

    signals.message.connect(_show_status)
    panel_layout.addWidget(status_label)
    footer_layout.addWidget(panel, 1)
    return True


CODEX_SANDBOX_SPEC = Spec(
    stripe_color="#475569",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)
