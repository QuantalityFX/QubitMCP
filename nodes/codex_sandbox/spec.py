from __future__ import annotations

from nodes.core import Spec
from echograph.qt_compat import QtWidgets


CODEX_SANDBOX_NODE_KIND = "codex_sandbox"
CODEX_SANDBOX_NODE_ALIASES = [
    "codex sandbox",
    "sandbox",
]
CODEX_SANDBOX_NODE_KINDS = {CODEX_SANDBOX_NODE_KIND, *CODEX_SANDBOX_NODE_ALIASES}

DEFAULT_EXECUTABLE = "npx @openai/codex"
DEFAULT_SANDBOX_MODE = "read-only"
DEFAULT_APPROVAL_POLICY = "never"
DEFAULT_WORKING_DIRECTORY = ""
DEFAULT_NETWORK_ACCESS = "false"
DEFAULT_OUTPUT_CAPTURE = "last_message"

SANDBOX_MODES = ("read-only", "workspace-write")
APPROVAL_POLICIES = ("never", "on-request", "on-failure", "untrusted")
OUTPUT_CAPTURE_MODES = ("last_message", "stdout", "transcript")

PARAM_DEFAULTS = {
    "executable": DEFAULT_EXECUTABLE,
    "sandbox_mode": DEFAULT_SANDBOX_MODE,
    "approval_policy": DEFAULT_APPROVAL_POLICY,
    "working_directory": DEFAULT_WORKING_DIRECTORY,
    "network_access": DEFAULT_NETWORK_ACCESS,
    "output_capture": DEFAULT_OUTPUT_CAPTURE,
    "extra_args": "",
}


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


def sandbox_config_from_item(node_item) -> dict:
    model = getattr(node_item, "model", node_item)
    config = {}
    for name, default in PARAM_DEFAULTS.items():
        config[name] = _param_value_from_model(model, name, default).strip()
    mode = config.get("sandbox_mode") or DEFAULT_SANDBOX_MODE
    if mode not in SANDBOX_MODES:
        mode = DEFAULT_SANDBOX_MODE
    approval = config.get("approval_policy") or DEFAULT_APPROVAL_POLICY
    if approval not in APPROVAL_POLICIES:
        approval = DEFAULT_APPROVAL_POLICY
    output_capture = config.get("output_capture") or DEFAULT_OUTPUT_CAPTURE
    if output_capture not in OUTPUT_CAPTURE_MODES:
        output_capture = DEFAULT_OUTPUT_CAPTURE
    network_raw = str(config.get("network_access") or "").strip().lower()
    config["sandbox_mode"] = mode
    config["approval_policy"] = approval
    config["output_capture"] = output_capture
    config["network_access"] = "true" if network_raw in {"1", "true", "yes", "on"} else "false"
    return config


def sandbox_summary_text(node_item) -> str:
    config = sandbox_config_from_item(node_item)
    return "\n".join(
        [
            "Codex Sandbox:",
            f"executable: {config.get('executable') or DEFAULT_EXECUTABLE}",
            f"sandbox_mode: {config.get('sandbox_mode') or DEFAULT_SANDBOX_MODE}",
            f"approval_policy: {config.get('approval_policy') or DEFAULT_APPROVAL_POLICY}",
            f"working_directory: {config.get('working_directory') or '(LLM Prompt default)'}",
            f"network_access: {config.get('network_access') or DEFAULT_NETWORK_ACCESS}",
            f"output_capture: {config.get('output_capture') or DEFAULT_OUTPUT_CAPTURE}",
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
    for name, default in PARAM_DEFAULTS.items():
        _ensure_param(node_item, name, default)
    _ensure_output(node_item, "config")
    _sync_info(node_item)


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None or str(getattr(node, "kind", "") or "").strip().lower() not in CODEX_SANDBOX_NODE_KINDS:
        return False

    def _param_val(key: str) -> str:
        return _param_value_from_model(node, key, PARAM_DEFAULTS.get(key, ""))

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
        scene = getattr(card, "_graph_scene", None)
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
        for option in options:
            combo.addItem(option)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    title = QtWidgets.QLabel("Codex Sandbox")
    title.setStyleSheet("QLabel{font-weight:600;color:#e5e7eb;margin-top:6px;}")
    footer_layout.addWidget(title)

    form = QtWidgets.QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(6)

    executable_edit = QtWidgets.QLineEdit(_param_val("executable") or DEFAULT_EXECUTABLE)
    executable_edit.setPlaceholderText(DEFAULT_EXECUTABLE)
    executable_edit.editingFinished.connect(lambda: _set_param("executable", executable_edit.text().strip() or DEFAULT_EXECUTABLE))
    form.addRow("Executable", executable_edit)

    sandbox_combo = _combo(SANDBOX_MODES, _param_val("sandbox_mode") or DEFAULT_SANDBOX_MODE)
    sandbox_combo.currentTextChanged.connect(lambda value: _set_param("sandbox_mode", value))
    form.addRow("Sandbox", sandbox_combo)

    approval_combo = _combo(APPROVAL_POLICIES, _param_val("approval_policy") or DEFAULT_APPROVAL_POLICY)
    approval_combo.currentTextChanged.connect(lambda value: _set_param("approval_policy", value))
    form.addRow("Approval", approval_combo)

    workdir_edit = QtWidgets.QLineEdit(_param_val("working_directory"))
    workdir_edit.setPlaceholderText("optional working directory")
    workdir_edit.editingFinished.connect(lambda: _set_param("working_directory", workdir_edit.text().strip()))
    form.addRow("Working Dir", workdir_edit)

    network_combo = _combo(("false", "true"), (_param_val("network_access") or DEFAULT_NETWORK_ACCESS).lower())
    network_combo.currentTextChanged.connect(lambda value: _set_param("network_access", value))
    form.addRow("Network", network_combo)

    output_combo = _combo(OUTPUT_CAPTURE_MODES, _param_val("output_capture") or DEFAULT_OUTPUT_CAPTURE)
    output_combo.currentTextChanged.connect(lambda value: _set_param("output_capture", value))
    form.addRow("Output", output_combo)

    extra_edit = QtWidgets.QLineEdit(_param_val("extra_args"))
    extra_edit.setPlaceholderText("optional extra CLI/config args")
    extra_edit.editingFinished.connect(lambda: _set_param("extra_args", extra_edit.text().strip()))
    form.addRow("Extra Args", extra_edit)

    footer_layout.addLayout(form)
    return True


CODEX_SANDBOX_SPEC = Spec(
    stripe_color="#475569",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)

