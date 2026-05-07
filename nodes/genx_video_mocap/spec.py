from __future__ import annotations

import os
import time
from pathlib import Path

from nodes.core import NodeKindSpec

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore


RIG_SOMA_BVH = "SOMA BVH (.bvh)"
RIG_UNITREE_G1 = "Unitree G1 (.csv + .bvh)"

GENX_NODE_KINDS = (
    "GEN-X-VideoMocap",
    "genx_video_mocap",
    "genx video mocap",
    "genx_videomocap",
    "genx videomocap",
    "gen-x-videomocap",
    "gen-x video mocap",
    "gemx_video_mocap",
    "gemx video mocap",
)

_VIDEO_FILTER = "Video Files (*.mp4 *.mov *.avi *.mkv *.webm);;All Files (*.*)"
_HIDDEN_PARAMS = (
    "source_video",
    "output_root",
    "rig_export",
    "last_bvh",
    "last_status",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _gemx_root() -> Path:
    return _repo_root() / "Mocap" / "GEM-X"


def _default_output_root() -> Path:
    return _gemx_root() / "outputs" / "demo_soma"


def _kind_matches(kind: str) -> bool:
    key = (kind or "").strip().lower()
    return key in {k.lower() for k in GENX_NODE_KINDS}


def _param_value(node, name: str, default: str = "") -> str:
    key = (name or "").strip().lower()
    for p in (getattr(node, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            value = p.get("value")
            return str(value) if value is not None else ""
    return default


def _set_param_value(card, node, name: str, value: str, *, rebuild: bool = False) -> None:
    key = (name or "").strip().lower()
    params = list(getattr(node, "params", None) or [])
    found = False
    for p in params:
        if (p.get("name") or "").strip().lower() == key:
            p["value"] = str(value)
            found = True
            break
    if not found:
        params.append({"name": name, "value": str(value)})
    node.params = params

    sc = getattr(card, "_graph_scene", None)
    if sc is not None and hasattr(sc, "set_node_params"):
        try:
            sc.set_node_params(node.name, params, rebuild=rebuild, emit=True)
        except TypeError:
            sc.set_node_params(node.name, params)
    else:
        try:
            if sc is not None and hasattr(sc, "paramChanged"):
                sc.paramChanged.emit(node.name, list(params))
        except Exception:
            pass

    if not rebuild:
        try:
            card.refresh_params_from_model()
        except Exception:
            pass


def _ensure_default_params(card, node) -> None:
    params = list(getattr(node, "params", None) or [])
    names = {(p.get("name") or "").strip().lower() for p in params}
    defaults = (
        ("source_video", ""),
        ("output_root", str(_default_output_root())),
        ("rig_export", RIG_SOMA_BVH),
        ("last_bvh", ""),
        ("last_status", ""),
    )
    changed = False
    for name, value in defaults:
        if name.lower() not in names:
            params.append({"name": name, "value": value})
            names.add(name.lower())
            changed = True
    if "__ui_hidden_params" not in names:
        params.append({"name": "__ui_hidden_params", "value": ",".join(_HIDDEN_PARAMS)})
        changed = True
    if changed:
        node.params = params
        sc = getattr(card, "_graph_scene", None)
        if sc is not None and hasattr(sc, "set_node_params"):
            try:
                sc.set_node_params(node.name, params, rebuild=False, emit=True)
            except TypeError:
                sc.set_node_params(node.name, params)


def _clean_path_text(text: str) -> str:
    value = (text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _resolve_path(text: str, base: Path) -> Path:
    raw = _clean_path_text(text)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _format_process_error(proc: QtCore.QProcess) -> str:
    try:
        return str(proc.errorString() or "Process failed.")
    except Exception:
        return "Process failed."


def _read_bytes(data) -> str:
    try:
        return bytes(data).decode("utf-8", errors="replace")
    except Exception:
        try:
            return str(data, errors="replace")
        except Exception:
            return str(data)


def _append_log(widget: QtWidgets.QPlainTextEdit | None, text: str) -> None:
    if widget is None or not text:
        return
    try:
        cursor = widget.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        widget.setTextCursor(cursor)
        widget.ensureCursorVisible()
    except Exception:
        try:
            widget.appendPlainText(text.rstrip())
        except Exception:
            pass


def _latest_bvh(output_root: Path, video_path: Path, start_time: float) -> Path | None:
    search_roots = [output_root / video_path.stem, output_root]
    candidates: dict[str, Path] = {}
    for root in search_roots:
        try:
            if not root.exists():
                continue
            for path in root.rglob("*.bvh"):
                if path.is_file():
                    candidates[str(path.resolve()).lower()] = path
        except Exception:
            continue
    if not candidates:
        return None

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0

    recent = [p for p in candidates.values() if _mtime(p) >= (start_time - 5.0)]
    pool = recent or list(candidates.values())
    return max(pool, key=_mtime)


def _running_state():
    try:
        return QtCore.QProcess.ProcessState.NotRunning
    except Exception:
        return QtCore.QProcess.NotRunning


def _is_process_running(proc) -> bool:
    if proc is None:
        return False
    try:
        return proc.state() != _running_state()
    except Exception:
        return False


def _node_item_for(card, node):
    sc = getattr(card, "_graph_scene", None)
    try:
        return sc._node_items.get(node.name) if sc is not None else None
    except Exception:
        return None


def _set_node_busy(card, node, active: bool) -> None:
    item = _node_item_for(card, node)
    if item is None or not hasattr(item, "setBusyState"):
        return
    try:
        item.setBusyState(bool(active), "genx-running" if active else "")
    except Exception:
        pass


def _register_mocap_import() -> None:
    try:
        from nodes import mocap_import  # type: ignore

        if hasattr(mocap_import, "register"):
            mocap_import.register()
    except Exception:
        pass


def _create_mocap_bvh_node(card, node, bvh_path: Path) -> bool:
    sc = getattr(card, "_graph_scene", None)
    if sc is None or not hasattr(sc, "add_node"):
        return False

    _register_mocap_import()
    try:
        from echograph.model import GraphNode
    except Exception:
        return False

    try:
        name = sc._unique_node_name("MocapBVH", "mocap_import")
    except Exception:
        name = "MocapBVH"

    params = [{"name": "path", "value": str(bvh_path)}]
    new_node = GraphNode(
        name=name,
        kind="mocap_import",
        info=f"Generated by {getattr(node, 'name', 'GEN-X')}",
        params=params,
    )

    try:
        src_item = _node_item_for(card, node)
        if src_item is not None:
            offset_x = float(getattr(src_item, "width", 220.0) or 220.0) + 90.0
            pos = src_item.scenePos() + QtCore.QPointF(offset_x, 0.0)
        else:
            pos = QtCore.QPointF(0.0, 0.0)
        try:
            new_node.pos_xy = (float(pos.x()), float(pos.y()))
            new_node.pos = pos
        except Exception:
            pass
        item = sc.add_node(new_node, pos)
        try:
            item.setPos(pos)
            new_node.pos_xy = (float(pos.x()), float(pos.y()))
            new_node.pos = pos
        except Exception:
            pass
        try:
            item.setSelected(True)
        except Exception:
            pass
        try:
            if callable(getattr(sc, "on_info", None)):
                sc.on_info(new_node)
        except Exception:
            pass
        try:
            if hasattr(sc, "linksChanged"):
                sc.linksChanged.emit()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _open_full_ui(card) -> None:
    root = _gemx_root()
    vbs = root / "run_qt_ui.vbs"
    if not vbs.is_file():
        QtWidgets.QMessageBox.warning(card, "GEN-X", f"GEM-X launcher was not found:\n{vbs}")
        return
    try:
        result = QtCore.QProcess.startDetached("wscript.exe", [str(vbs)], str(root))
    except TypeError:
        result = QtCore.QProcess.startDetached(f'wscript.exe "{vbs}"')
    ok = bool(result[0]) if isinstance(result, tuple) else bool(result)
    if not ok:
        QtWidgets.QMessageBox.warning(card, "GEN-X", "Could not start the GEM-X launcher.")


def _style_line_edit(edit: QtWidgets.QLineEdit) -> None:
    edit.setMinimumWidth(260)
    edit.setStyleSheet(
        "QLineEdit{background:#11151b;color:#e6edf3;border:1px solid #3c4450;"
        "border-radius:4px;padding:4px 6px;}"
    )


def _make_tool_button(text: str, tooltip: str = "", *, role: str = "") -> QtWidgets.QPushButton:
    btn = QtWidgets.QPushButton(text)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setMinimumHeight(28)
    if role == "green":
        btn.setStyleSheet(
            "QPushButton{background:#16a34a;color:#f8fafc;border:1px solid #22c55e;"
            "border-radius:5px;padding:5px 10px;font-weight:600;}"
            "QPushButton:hover{background:#15803d;}"
            "QPushButton:pressed{background:#166534;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
    elif role == "blue":
        btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border:1px solid #60a5fa;"
            "border-radius:5px;padding:5px 10px;font-weight:600;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:pressed{background:#1e40af;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
    return btn


def _build_process_args(gemx_root: Path, video_path: Path, output_root: Path, rig: str) -> tuple[Path, list[str]]:
    python_exe = gemx_root / ".venv" / "Scripts" / "python.exe"
    script = gemx_root / "scripts" / "demo" / "demo_soma.py"
    args = [
        "-u",
        str(script),
        "--video",
        str(video_path),
        "--output_root",
        str(output_root),
    ]
    if (rig or "").strip() == RIG_UNITREE_G1:
        args.append("--retarget")
    else:
        args.append("--export_bvh")
    return python_exe, args


def _start_genx_run(
    card,
    node,
    video_edit: QtWidgets.QLineEdit,
    output_edit: QtWidgets.QLineEdit,
    rig_combo: QtWidgets.QComboBox,
    run_btn: QtWidgets.QPushButton,
    status_label: QtWidgets.QLabel,
    log_box: QtWidgets.QPlainTextEdit,
) -> None:
    existing = getattr(node, "_genx_process", None)
    if _is_process_running(existing):
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "GEN-X is already running for this node.", card)
        return

    gemx_root = _gemx_root()
    try:
        video_path = _resolve_path(video_edit.text(), _repo_root())
    except Exception:
        QtWidgets.QMessageBox.warning(card, "GEN-X", "Select a valid source video.")
        return
    if not video_path.is_file():
        QtWidgets.QMessageBox.warning(card, "GEN-X", f"Source video was not found:\n{video_path}")
        return

    try:
        output_root = _resolve_path(output_edit.text() or str(_default_output_root()), gemx_root)
        output_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        QtWidgets.QMessageBox.warning(card, "GEN-X", f"Output folder is not writable:\n{exc}")
        return

    python_exe, args = _build_process_args(gemx_root, video_path, output_root, rig_combo.currentText())
    if not python_exe.is_file():
        QtWidgets.QMessageBox.warning(card, "GEN-X", f"GEM-X venv was not found:\n{python_exe}\n\nRun Mocap\\setup.bat first.")
        return
    if not Path(args[1]).is_file():
        QtWidgets.QMessageBox.warning(card, "GEN-X", f"GEM-X demo script was not found:\n{args[1]}")
        return

    _set_param_value(card, node, "source_video", str(video_path), rebuild=False)
    _set_param_value(card, node, "output_root", str(output_root), rebuild=False)
    _set_param_value(card, node, "rig_export", rig_combo.currentText(), rebuild=False)
    _set_param_value(card, node, "last_status", "running", rebuild=False)

    proc_parent = getattr(card, "_graph_scene", None) or card
    proc = QtCore.QProcess(proc_parent)
    proc.setWorkingDirectory(str(gemx_root))
    proc.setProgram(str(python_exe))
    proc.setArguments(args)

    env = QtCore.QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUNBUFFERED", "1")
    existing_pythonpath = env.value("PYTHONPATH")
    env.insert(
        "PYTHONPATH",
        str(gemx_root) + (os.pathsep + existing_pythonpath if existing_pythonpath else ""),
    )
    scripts_dir = str(gemx_root / ".venv" / "Scripts")
    env.insert("PATH", scripts_dir + os.pathsep + env.value("PATH"))
    proc.setProcessEnvironment(env)

    start_time = time.time()
    node._genx_process = proc
    node._genx_log = ""
    run_btn.setEnabled(False)
    status_label.setText("Running")
    log_box.clear()
    _append_log(log_box, f"{python_exe} {' '.join(args)}\n\n")
    _set_node_busy(card, node, True)

    def _read_stdout() -> None:
        text = _read_bytes(proc.readAllStandardOutput())
        if text:
            node._genx_log = (getattr(node, "_genx_log", "") + text)[-12000:]
            _append_log(log_box, text)

    def _read_stderr() -> None:
        text = _read_bytes(proc.readAllStandardError())
        if text:
            node._genx_log = (getattr(node, "_genx_log", "") + text)[-12000:]
            _append_log(log_box, text)

    def _on_error(_err=None) -> None:
        status_label.setText("Failed")
        _set_param_value(card, node, "last_status", _format_process_error(proc), rebuild=False)
        _append_log(log_box, "\n[GEN-X] " + _format_process_error(proc) + "\n")

    def _on_finished(exit_code: int, _exit_status=None) -> None:
        _read_stdout()
        _read_stderr()
        run_btn.setEnabled(True)
        _set_node_busy(card, node, False)

        if exit_code == 0:
            bvh_path = _latest_bvh(output_root, video_path, start_time)
            if bvh_path is not None:
                _set_param_value(card, node, "last_bvh", str(bvh_path), rebuild=False)
                created = _create_mocap_bvh_node(card, node, bvh_path)
                status = f"Done: {bvh_path.name}"
                if not created:
                    status += " (MocapBVH node was not created)"
                _set_param_value(card, node, "last_status", status, rebuild=False)
                status_label.setText(status)
            else:
                msg = "GEN-X finished, but no BVH file was found in the output folder."
                _set_param_value(card, node, "last_status", msg, rebuild=False)
                status_label.setText("No BVH found")
                QtWidgets.QMessageBox.warning(card, "GEN-X", msg)
        else:
            msg = f"GEN-X failed with exit code {exit_code}."
            _set_param_value(card, node, "last_status", msg, rebuild=False)
            status_label.setText("Failed")
            QtWidgets.QMessageBox.critical(card, "GEN-X", msg)

        try:
            if getattr(node, "_genx_process", None) is proc:
                node._genx_process = None
        except Exception:
            pass
        try:
            proc.deleteLater()
        except Exception:
            pass

    proc.readyReadStandardOutput.connect(_read_stdout)
    proc.readyReadStandardError.connect(_read_stderr)
    proc.errorOccurred.connect(_on_error)
    proc.finished.connect(_on_finished)
    proc.start()
    if not proc.waitForStarted(3000):
        _set_node_busy(card, node, False)
        run_btn.setEnabled(True)
        status_label.setText("Failed to start")
        _set_param_value(card, node, "last_status", _format_process_error(proc), rebuild=False)
        QtWidgets.QMessageBox.critical(card, "GEN-X", _format_process_error(proc))


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None or not _kind_matches(getattr(node, "kind", "")):
        return False

    _ensure_default_params(card, node)

    container = QtWidgets.QWidget(card)
    root = QtWidgets.QVBoxLayout(container)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(6)

    top_row = QtWidgets.QHBoxLayout()
    top_row.setContentsMargins(0, 0, 0, 0)
    open_btn = _make_tool_button("Open Full UI", "Open the GEM-X Qt launcher without a command window", role="green")
    open_btn.clicked.connect(lambda: _open_full_ui(card))
    top_row.addWidget(open_btn)
    top_row.addStretch(1)
    root.addLayout(top_row)

    grid = QtWidgets.QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(5)

    video_edit = QtWidgets.QLineEdit(_param_value(node, "source_video", ""))
    video_edit.setPlaceholderText("Source video")
    _style_line_edit(video_edit)
    video_browse = _make_tool_button("...", "Browse for source video")
    video_browse.setFixedWidth(32)

    output_edit = QtWidgets.QLineEdit(_param_value(node, "output_root", str(_default_output_root())))
    output_edit.setPlaceholderText("Output folder")
    _style_line_edit(output_edit)
    output_browse = _make_tool_button("...", "Browse for output folder")
    output_browse.setFixedWidth(32)

    rig_combo = QtWidgets.QComboBox()
    rig_combo.addItems([RIG_SOMA_BVH, RIG_UNITREE_G1])
    current_rig = _param_value(node, "rig_export", RIG_SOMA_BVH)
    idx = rig_combo.findText(current_rig)
    rig_combo.setCurrentIndex(idx if idx >= 0 else 0)

    run_btn = _make_tool_button("Run", "Run GEN-X and create a MocapBVH node from the generated BVH", role="blue")
    run_btn.setFixedWidth(96)
    status_label = QtWidgets.QLabel(_param_value(node, "last_status", "Idle") or "Idle")
    status_label.setStyleSheet("QLabel{color:#cbd5e1;}")

    log_box = QtWidgets.QPlainTextEdit()
    log_box.setReadOnly(True)
    log_box.setMaximumHeight(92)
    log_box.setStyleSheet(
        "QPlainTextEdit{background:#0f1216;color:#cbd5e1;border:1px solid #3c4450;"
        "border-radius:6px;font-family:Consolas,monospace;font-size:10px;}"
    )
    last_log = getattr(node, "_genx_log", "")
    if last_log:
        log_box.setPlainText(last_log)

    def _browse_video() -> None:
        start_dir = str(Path(_param_value(node, "source_video", "") or str(_repo_root())).parent)
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(card, "Select Source Video", start_dir, _VIDEO_FILTER)
        if path:
            video_edit.setText(path)
            _set_param_value(card, node, "source_video", path, rebuild=False)

    def _browse_output() -> None:
        start_dir = output_edit.text().strip() or str(_default_output_root())
        folder = QtWidgets.QFileDialog.getExistingDirectory(card, "Select Output Folder", start_dir)
        if folder:
            output_edit.setText(folder)
            _set_param_value(card, node, "output_root", folder, rebuild=False)

    video_browse.clicked.connect(_browse_video)
    output_browse.clicked.connect(_browse_output)
    video_edit.editingFinished.connect(lambda: _set_param_value(card, node, "source_video", video_edit.text(), rebuild=False))
    output_edit.editingFinished.connect(lambda: _set_param_value(card, node, "output_root", output_edit.text(), rebuild=False))
    rig_combo.currentTextChanged.connect(lambda value: _set_param_value(card, node, "rig_export", value, rebuild=False))
    run_btn.clicked.connect(
        lambda: _start_genx_run(card, node, video_edit, output_edit, rig_combo, run_btn, status_label, log_box)
    )

    grid.addWidget(QtWidgets.QLabel("Source"), 0, 0)
    grid.addWidget(video_edit, 0, 1)
    grid.addWidget(video_browse, 0, 2)
    grid.addWidget(QtWidgets.QLabel("Output"), 1, 0)
    grid.addWidget(output_edit, 1, 1)
    grid.addWidget(output_browse, 1, 2)
    grid.addWidget(QtWidgets.QLabel("Rig"), 2, 0)
    grid.addWidget(rig_combo, 2, 1, 1, 2)
    grid.addWidget(run_btn, 3, 1, 1, 1, QtCore.Qt.AlignLeft)
    grid.setColumnStretch(1, 1)
    root.addLayout(grid)
    root.addWidget(status_label)
    root.addWidget(log_box)

    existing = getattr(node, "_genx_process", None)
    if _is_process_running(existing):
        run_btn.setEnabled(False)
        status_label.setText("Running")
        _set_node_busy(card, node, True)

    footer_layout.addWidget(container, 1)
    return True


GENX_VIDEO_MOCAP_SPEC = NodeKindSpec(
    stripe_color="#14b8a6",
    augment_infocard_footer=augment_infocard_footer,
)
