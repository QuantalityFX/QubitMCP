from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.sequence_to_mp4.spec import (
    SequenceInfo,
    _clean_path_text,
    _dialog_parent,
    _resolve_sequence,
    _workflow_dir_for_node,
)
from nodes.util_graph import param_change_relevant as _param_change_relevant


_POST_PROCESS_KINDS = {
    "post_process",
    "postprocess",
    "post_processing",
    "post_process_effect",
}
_VIDEO_PLAYER_KINDS = {"video_player", "video player", "videoplayer"}


@dataclass
class SequenceSource:
    text: str
    owner_item: object
    label: str
    from_connection: bool = False


@dataclass
class PostProcessResult:
    pattern_path: Path
    first_frame: Path
    output_dir: Path
    frame_count: int
    signature: str


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        setattr(model, "params", params)
    if not isinstance(params, list):
        params = list(params)
        setattr(model, "params", params)
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for p in params:
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = p
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    raw = str(entry.get("value") or "")
    hidden = {tok.strip().lower() for tok in raw.split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, notify_scene: bool = False) -> None:
    model = getattr(node_item, "model", None)
    if model is not None and _param_value(model, name) == str(value or ""):
        return
    try:
        node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
    except Exception:
        pass


def _safe_name(raw: str, default: str = "post_process") -> str:
    text = str(raw or "").strip() or default
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or default


def _kind_for_item(item) -> str:
    model = getattr(item, "model", None)
    return (getattr(model, "kind", "") or "").strip().lower()


def source_input_connected(node_item) -> bool:
    return _source_input_edge(node_item) is not None


def _ordered_in_edges(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return []
    try:
        return list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            return list(scene._in_edges(node_item))
        except Exception:
            return []


def _edge_port(edge) -> str:
    name = (
        getattr(edge, "dst_port_name", None)
        or getattr(edge, "dst_label", None)
        or getattr(edge, "dst_name", None)
        or ""
    )
    return str(name or "").strip().lower()


def _source_input_edge(node_item):
    edges = _ordered_in_edges(node_item)
    for edge in edges:
        if _edge_port(edge) == "source":
            return edge
    for edge in edges:
        if not _edge_port(edge):
            return edge
    return edges[0] if edges else None


def find_sequence_source(
    node_item,
    *,
    apply_postprocess: bool = False,
    require_connection: bool = False,
    _visited: set[int] | None = None,
) -> SequenceSource | None:
    visited = _visited if _visited is not None else set()
    if node_item is None or id(node_item) in visited:
        return None
    visited.add(id(node_item))

    edge = _source_input_edge(node_item)
    if edge is not None:
        source_item = getattr(edge, "src", None)
        source = _source_from_item(source_item, apply_postprocess=apply_postprocess, _visited=visited)
        if source is not None:
            source.from_connection = True
            return source

    if require_connection:
        return None

    model = getattr(node_item, "model", None)
    if model is None:
        return None
    text = (_param_value(model, "source") or _param_value(model, "path")).strip()
    if not text:
        return None
    label = str(getattr(model, "name", "") or getattr(model, "kind", "") or "Source")
    return SequenceSource(text=text, owner_item=node_item, label=label, from_connection=False)


def _source_from_item(item, *, apply_postprocess: bool, _visited: set[int]) -> SequenceSource | None:
    if item is None or id(item) in _visited:
        return None
    model = getattr(item, "model", None)
    if model is None:
        return None
    kind = _kind_for_item(item)
    label = str(getattr(model, "name", "") or getattr(model, "kind", "") or "Source")

    if kind in _POST_PROCESS_KINDS:
        if apply_postprocess:
            result = resolve_post_process_sequence(item, apply=True, _visited=_visited)
            return SequenceSource(
                text=str(result.pattern_path),
                owner_item=item,
                label=label,
                from_connection=True,
            )
        cached = (_param_value(model, "output_pattern") or "").strip()
        if cached:
            return SequenceSource(text=cached, owner_item=item, label=label, from_connection=True)
        return find_sequence_source(
            item,
            apply_postprocess=False,
            require_connection=False,
            _visited=_visited,
        )

    for name in ("path", "source", "output_pattern", "output"):
        text = (_param_value(model, name) or "").strip()
        if text:
            return SequenceSource(text=text, owner_item=item, label=label, from_connection=True)
    return find_sequence_source(
        item,
        apply_postprocess=apply_postprocess,
        require_connection=False,
        _visited=_visited,
    )


def _default_output_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    if workflow_dir is None:
        import tempfile

        workflow_dir = Path(tempfile.gettempdir()) / "EchoGraph"
    model = getattr(node_item, "model", None)
    name = _safe_name(getattr(model, "name", "") if model is not None else "", "post_process")
    return workflow_dir / "postprocess" / name


def _output_dir(node_item) -> Path:
    model = getattr(node_item, "model", None)
    raw = (_param_value(model, "output_dir") if model is not None else "") or ""
    text = _clean_path_text(raw)
    if text:
        path = Path(text).expanduser()
        if not path.is_absolute():
            base = _workflow_dir_for_node(node_item)
            if base is not None:
                path = base / path
    else:
        path = _default_output_dir(node_item)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings_from_node(node_item) -> dict:
    model = getattr(node_item, "model", None)
    try:
        levels = int(float(_param_value(model, "levels") or "4"))
    except Exception:
        levels = 4
    try:
        matrix = int(float(_param_value(model, "matrix") or "4"))
    except Exception:
        matrix = 4
    try:
        strength = float(_param_value(model, "strength") or "100")
    except Exception:
        strength = 100.0
    grayscale = (_param_value(model, "grayscale") or "").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "effect": "ordered_dither",
        "levels": max(2, min(16, levels)),
        "matrix": matrix if matrix in {2, 4, 8} else 4,
        "strength": max(0.0, min(200.0, strength)),
        "grayscale": bool(grayscale),
    }


def _sequence_files_from_info(info: SequenceInfo) -> list[Path]:
    name = info.input_pattern.name
    match = re.search(r"%0?(\d*)d", name)
    if match:
        width = int(match.group(1) or "1")
        regex_src = re.escape(name).replace(re.escape(match.group(0)), rf"(\d{{{width},}})")
    else:
        regex_src = re.escape(name)
    rx = re.compile(rf"^{regex_src}$", flags=re.IGNORECASE)
    rows = []
    try:
        for child in info.input_pattern.parent.iterdir():
            if not child.is_file():
                continue
            m = rx.match(child.name)
            if not m:
                continue
            try:
                number = int(m.group(1)) if m.groups() else len(rows)
            except Exception:
                number = len(rows)
            rows.append((number, child))
    except Exception:
        rows = []
    rows.sort(key=lambda row: row[0])
    return [path for _number, path in rows]


def _signature(files: list[Path], settings: dict, source_text: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(settings, sort_keys=True).encode("utf-8"))
    h.update(str(source_text or "").encode("utf-8", errors="ignore"))
    for path in files:
        h.update(str(path).encode("utf-8", errors="ignore"))
        try:
            st = path.stat()
            h.update(str(int(st.st_size)).encode("ascii"))
            h.update(str(int(st.st_mtime_ns)).encode("ascii"))
        except Exception:
            h.update(b"missing")
    return h.hexdigest()


def _cache_path(output_dir: Path) -> Path:
    return output_dir / ".post_process_cache.json"


def _output_frame_paths(output_dir: Path, start_number: int, frame_count: int, padding: int) -> list[Path]:
    width = max(4, int(padding or 4))
    return [output_dir / f"frame_{start_number + idx:0{width}d}.png" for idx in range(int(frame_count))]


def _pattern_path(output_dir: Path, padding: int) -> Path:
    width = max(4, int(padding or 4))
    return output_dir / f"frame_%0{width}d.png"


def _cache_valid(output_dir: Path, signature: str, outputs: list[Path]) -> bool:
    try:
        data = json.loads(_cache_path(output_dir).read_text(encoding="utf-8"))
    except Exception:
        return False
    if str(data.get("signature") or "") != str(signature):
        return False
    return all(path.is_file() for path in outputs)


def _bayer_matrix(size: int):
    import numpy as np

    size = size if size in {2, 4, 8} else 4
    mat = np.array([[0, 2], [3, 1]], dtype=np.float32)
    while mat.shape[0] < size:
        mat = np.block([[4 * mat + 0, 4 * mat + 2], [4 * mat + 3, 4 * mat + 1]])
    return mat


def _apply_ordered_dither(src: Path, dst: Path, settings: dict) -> None:
    import numpy as np
    from PIL import Image

    img = Image.open(src).convert("RGB")
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape[:2]
    levels = max(2, min(16, int(settings.get("levels") or 4)))
    step = 255.0 / float(levels - 1)
    strength = max(0.0, min(2.0, float(settings.get("strength") or 100.0) / 100.0))
    mat = _bayer_matrix(int(settings.get("matrix") or 4))
    threshold = ((mat + 0.5) / float(mat.size) - 0.5) * step * strength
    reps_y = (h + mat.shape[0] - 1) // mat.shape[0]
    reps_x = (w + mat.shape[1] - 1) // mat.shape[1]
    tiled = np.tile(threshold, (reps_y, reps_x))[:h, :w]

    if bool(settings.get("grayscale")):
        lum = arr[:, :, 0] * 0.2126 + arr[:, :, 1] * 0.7152 + arr[:, :, 2] * 0.0722
        q = np.round((lum + tiled) / step) * step
        out = np.repeat(np.clip(q, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)
    else:
        q = np.round((arr + tiled[:, :, None]) / step) * step
        out = np.clip(q, 0, 255).astype(np.uint8)

    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out, mode="RGB").save(dst)


def resolve_post_process_sequence(node_item, *, apply: bool = True, _visited: set[int] | None = None) -> PostProcessResult:
    source = find_sequence_source(
        node_item,
        apply_postprocess=True,
        require_connection=False,
        _visited=_visited,
    )
    if source is None or not (source.text or "").strip():
        raise ValueError("Connect a sequence source or choose a source image sequence.")

    info = _resolve_sequence(source.owner_item, source.text)
    files = _sequence_files_from_info(info)
    if not files:
        raise ValueError("No source frames were found for the post-process node.")

    settings = _settings_from_node(node_item)
    output_dir = _output_dir(node_item)
    outputs = _output_frame_paths(output_dir, info.start_number, len(files), info.padding)
    signature = _signature(files, settings, source.text)
    pattern = _pattern_path(output_dir, info.padding)

    if apply and not _cache_valid(output_dir, signature, outputs):
        for src, dst in zip(files, outputs):
            _apply_ordered_dither(src, dst, settings)
        data = {
            "signature": signature,
            "frame_count": len(files),
            "source": source.text,
            "pattern": str(pattern),
            "settings": settings,
        }
        _cache_path(output_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")

    first = outputs[0]
    _set_param_value(node_item, "output_dir", str(output_dir), notify_scene=False)
    _set_param_value(node_item, "output_pattern", str(pattern), notify_scene=False)
    _set_param_value(node_item, "frame_count", str(len(files)), notify_scene=False)
    return PostProcessResult(
        pattern_path=pattern,
        first_frame=first,
        output_dir=output_dir,
        frame_count=len(files),
        signature=signature,
    )


def build_ports(node_item) -> None:
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "output_dir", "")
    _ensure_param(node_item, "output_pattern", "")
    _ensure_param(node_item, "effect", "ordered_dither")
    _ensure_param(node_item, "levels", "4")
    _ensure_param(node_item, "matrix", "4")
    _ensure_param(node_item, "strength", "100")
    _ensure_param(node_item, "grayscale", "0")
    _ensure_param(node_item, "frame_count", "")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        ["source", "output_dir", "output_pattern", "effect", "levels", "matrix", "strength", "grayscale", "frame_count"],
    )
    try:
        node_item.ensure_input("source")
    except Exception:
        pass


class PostProcessWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._busy = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status, 0)

        source_row = QtWidgets.QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(4)
        self._source_edit = self._line_edit("Folder, first frame, or frame_%05d.png")
        source_row.addWidget(self._source_edit, 1)
        self._source_file_btn = self._button("File", 46)
        self._source_dir_btn = self._button("Dir", 42)
        self._source_file_btn.clicked.connect(self._on_source_file)
        self._source_dir_btn.clicked.connect(self._on_source_dir)
        source_row.addWidget(self._source_file_btn, 0)
        source_row.addWidget(self._source_dir_btn, 0)
        layout.addLayout(source_row, 0)

        output_row = QtWidgets.QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(4)
        self._output_edit = self._line_edit("Output sequence folder")
        output_row.addWidget(self._output_edit, 1)
        self._output_btn = self._button("...", 34)
        self._output_btn.setToolTip("Choose output sequence folder")
        self._output_btn.clicked.connect(self._on_output_browse)
        output_row.addWidget(self._output_btn, 0)
        layout.addLayout(output_row, 0)

        controls = QtWidgets.QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)

        controls.addWidget(self._label("Levels"), 0)
        self._levels_spin = QtWidgets.QSpinBox()
        self._levels_spin.setRange(2, 16)
        self._levels_spin.setMinimumHeight(24)
        self._levels_spin.setStyleSheet(self._input_style("QSpinBox"))
        controls.addWidget(self._levels_spin, 1)

        controls.addWidget(self._label("Matrix"), 0)
        self._matrix_combo = QtWidgets.QComboBox()
        self._matrix_combo.addItem("2x2", 2)
        self._matrix_combo.addItem("4x4", 4)
        self._matrix_combo.addItem("8x8", 8)
        self._matrix_combo.setMinimumHeight(24)
        self._matrix_combo.setStyleSheet(self._input_style("QComboBox"))
        controls.addWidget(self._matrix_combo, 1)
        layout.addLayout(controls, 0)

        controls2 = QtWidgets.QHBoxLayout()
        controls2.setContentsMargins(0, 0, 0, 0)
        controls2.setSpacing(4)
        controls2.addWidget(self._label("Strength"), 0)
        self._strength_spin = QtWidgets.QSpinBox()
        self._strength_spin.setRange(0, 200)
        self._strength_spin.setMinimumHeight(24)
        self._strength_spin.setToolTip("100 is normal strength")
        self._strength_spin.setStyleSheet(self._input_style("QSpinBox"))
        controls2.addWidget(self._strength_spin, 1)

        self._gray_check = QtWidgets.QCheckBox("Gray")
        self._gray_check.setMinimumHeight(24)
        self._gray_check.setStyleSheet("QCheckBox{color:#e2e8f0;font-size:11px;} QCheckBox:disabled{color:#64748b;}")
        controls2.addWidget(self._gray_check, 0)
        layout.addLayout(controls2, 0)

        self._apply_btn = self._button("Apply Dither", 0)
        self._apply_btn.setStyleSheet(
            "QPushButton{background:#6d28d9;color:#f8fafc;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#7c3aed;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn, 0)

        self._source_edit.editingFinished.connect(self._commit_controls)
        self._output_edit.editingFinished.connect(self._commit_controls)
        self._levels_spin.valueChanged.connect(self._on_settings_changed)
        self._matrix_combo.currentIndexChanged.connect(self._on_settings_changed)
        self._strength_spin.valueChanged.connect(self._on_settings_changed)
        self._gray_check.toggled.connect(self._on_settings_changed)

        self._ensure_scene()
        self._sync_controls_from_params()
        self._refresh_status()

    def sizeHint(self):
        return QtCore.QSize(240, 176)

    def _input_style(self, selector: str) -> str:
        return (
            f"{selector}{{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:4px;padding:2px 6px;}"
            f"{selector}:disabled{{background:#111827;color:#64748b;border-color:#334155;}}"
        )

    def _line_edit(self, placeholder: str) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(0)
        edit.setMinimumHeight(24)
        edit.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        edit.setStyleSheet(self._input_style("QLineEdit"))
        return edit

    def _label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(str(text or ""))
        label.setMinimumHeight(24)
        label.setStyleSheet("QLabel{color:#94a3b8;font-size:10px;}")
        return label

    def _button(self, text: str, width: int) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text)
        if width > 0:
            btn.setFixedWidth(width)
        btn.setMinimumHeight(24)
        btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:2px 6px;}"
            "QPushButton:hover{background:#273449;}"
            "QPushButton:disabled{background:#111827;color:#64748b;border-color:#334155;}"
        )
        return btn

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._sync_controls_from_params()
            self._refresh_status()

    def _sync_controls_from_params(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        connected = source_input_connected(self._node_item)
        source = (_param_value(model, "source") or "").strip()
        if connected:
            connected_source = find_sequence_source(self._node_item, apply_postprocess=False, require_connection=True)
            source = f"Connected: {connected_source.label if connected_source else 'source'}"
        output = (_param_value(model, "output_dir") or "").strip() or str(_default_output_dir(self._node_item))
        settings = _settings_from_node(self._node_item)

        try:
            self._source_edit.blockSignals(True)
            self._source_edit.setText(source)
            self._source_edit.setEnabled(not connected)
            self._source_file_btn.setEnabled(not connected)
            self._source_dir_btn.setEnabled(not connected)
        finally:
            self._source_edit.blockSignals(False)
        try:
            self._output_edit.blockSignals(True)
            self._output_edit.setText(output)
        finally:
            self._output_edit.blockSignals(False)
        try:
            self._levels_spin.blockSignals(True)
            self._levels_spin.setValue(int(settings["levels"]))
        finally:
            self._levels_spin.blockSignals(False)
        try:
            self._matrix_combo.blockSignals(True)
            idx = self._matrix_combo.findData(int(settings["matrix"]))
            self._matrix_combo.setCurrentIndex(max(0, idx))
        finally:
            self._matrix_combo.blockSignals(False)
        try:
            self._strength_spin.blockSignals(True)
            self._strength_spin.setValue(int(round(float(settings["strength"]))))
        finally:
            self._strength_spin.blockSignals(False)
        try:
            self._gray_check.blockSignals(True)
            self._gray_check.setChecked(bool(settings["grayscale"]))
        finally:
            self._gray_check.blockSignals(False)

    def _on_settings_changed(self, *_args):
        self._commit_controls()
        self._status.setText("Settings changed. Apply before using cached output.")

    def _commit_controls(self):
        model = getattr(self._node_item, "model", None)
        old_source = (_param_value(model, "source") or "").strip() if model is not None else ""
        source = old_source if source_input_connected(self._node_item) else (self._source_edit.text() or "").strip()
        output = (self._output_edit.text() or "").strip()
        default_output = str(_default_output_dir(self._node_item))
        if output == default_output:
            output = ""
        _set_param_value(self._node_item, "source", source, notify_scene=False)
        _set_param_value(self._node_item, "output_dir", output, notify_scene=False)
        _set_param_value(self._node_item, "effect", "ordered_dither", notify_scene=False)
        _set_param_value(self._node_item, "levels", str(int(self._levels_spin.value())), notify_scene=False)
        _set_param_value(self._node_item, "matrix", str(int(self._matrix_combo.currentData() or 4)), notify_scene=False)
        _set_param_value(self._node_item, "strength", str(int(self._strength_spin.value())), notify_scene=False)
        _set_param_value(self._node_item, "grayscale", "1" if self._gray_check.isChecked() else "0", notify_scene=False)

    def _refresh_status(self):
        source = find_sequence_source(self._node_item, apply_postprocess=False, require_connection=False)
        if source is None:
            self._status.setText("Connect or choose an image sequence.")
            return
        try:
            info = _resolve_sequence(source.owner_item, source.text)
            self._status.setText(f"Ready: {info.frame_count} frame(s) from {source.label}.")
        except Exception as exc:
            self._status.setText(str(exc))

    def _on_source_file(self):
        if source_input_connected(self._node_item):
            return
        parent = _dialog_parent(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Select Sequence Frame",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.exr);;All Files (*.*)",
        )
        if not path:
            return
        self._source_edit.setText(path)
        self._commit_controls()
        self._refresh_status()

    def _on_source_dir(self):
        if source_input_connected(self._node_item):
            return
        parent = _dialog_parent(self._node_item) or self
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select Sequence Folder", "")
        if not path:
            return
        self._source_edit.setText(path)
        self._commit_controls()
        self._refresh_status()

    def _on_output_browse(self):
        parent = _dialog_parent(self._node_item) or self
        start = self._output_edit.text().strip() or str(_default_output_dir(self._node_item))
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose Output Sequence Folder", start)
        if not path:
            return
        self._output_edit.setText(path)
        self._commit_controls()

    def _set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._apply_btn.setEnabled(not self._busy)
        self._apply_btn.setText("Applying..." if self._busy else "Apply Dither")

    def _on_apply(self):
        self._commit_controls()
        self._set_busy(True)
        try:
            result = resolve_post_process_sequence(self._node_item, apply=True)
            self._status.setText(f"Dithered {result.frame_count} frame(s) to {result.output_dir.name}.")
        except Exception as exc:
            parent = _dialog_parent(self._node_item) or self
            QtWidgets.QMessageBox.warning(parent, "Post Process", str(exc))
            self._refresh_status()
        finally:
            self._set_busy(False)


def _ensure_body_space(node_item, body_h: int) -> None:
    try:
        old_h = float(getattr(node_item, "height", 0.0) or 0.0)
        min_h = float(body_h) + 10.0
        if old_h < min_h:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_h
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = PostProcessWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    w = int(getattr(node_item, "width", 220))
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
    except Exception:
        pass
    h = body.sizeHint().height()
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    _ensure_body_space(node_item, int(y_cursor) + h)
    return y_cursor + h


POST_PROCESS_SPEC = Spec(
    stripe_color="#7c3aed",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
