"""Previs + design references UI, using the app's existing media/path contracts."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from nodes.core import Spec
from nodes.util_prompt import connected_prompt
from nodes.qwen_image.port_item import PortDot
from nodes.minimax_h3_api_video.spec import (
    _button_style, _field_style, _default_api_key, _default_api_base_url,
    _param_value, _ensure_param, _ensure_hidden_params, _ordered_in_edges,
    _edge_port, _workflow_dir_for_node, _resolve_path, _dialog_parent,
)
from .client import Settings, Result, RATIOS, run_job
from .media import parse_refs


KIND = "minimax_h3_reference_video"
ALIASES = ("minimax h3 reference video", "minimax_h3_reference", "hailuo_h3_reference_video")
PREFIX = "__minimax_h3_ref_"
INPUTS = ("prompt", "reference_videos", "reference_images", "reference_audio")
OUTPUTS = ("mp4_path", "task_id", "h3_reference_status", "error", "metadata_path")
DEFAULT_PROMPT = (
    "Use reference video 1 for camera movement, framing, composition, body poses, "
    "action and performance timing. Use reference image 1 for the character's face, "
    "costume and body design. Use reference image 2 for the environment and lighting. "
    "Keep the character identity and scene design consistent throughout the shot."
)
DEFAULTS = {
    "prompt": "", "reference_videos": "", "reference_images": "", "reference_audio": "",
    "model": "MiniMax-H3", "resolution": "768P", "duration": "5", "ratio": "adaptive",
    "expansion": "balanced", "output_dir": "", "base_url": "", "task_output_dir": "",
}


def value(model, name, default=""):
    return _param_value(model, PREFIX + name if name in DEFAULTS else name, default)


def put(model, name, text):
    key = PREFIX + name if name in DEFAULTS else name
    for entry in model.params:
        if entry.get("name") == key:
            entry["value"] = str(text)
            return
    model.params.append({"name": key, "value": str(text)})


def incoming(item, port):
    if port == "prompt":
        return connected_prompt(item, preferred_params=("prompt", "text", "output"))
    scene = item.scene()
    edges = [edge for edge in _ordered_in_edges(item) if _edge_port(edge) == port]
    refs = []
    names = {"reference_videos": ("mp4_path", "video_path", "path", "output_path", "output"),
             "reference_images": ("image_path", "path", "source_image", "output", "preview_image"),
             "reference_audio": ("audio_path", "mp3_path", "wav_path", "path", "output")}[port]
    for edge in edges:
        source = edge.src
        model = source.model
        if port == "reference_images" and str(model.kind).lower() in {"image_collection", "imagecollection"}:
            state = getattr(model, "_image_collection_state", None) or {}
            collection = parse_refs(state.get("paths", []))
            if not collection:
                for name in ("reference_images", "image_paths", "images", "paths", "output"):
                    collection = parse_refs(_param_value(model, name))
                    if collection:
                        break
            refs.extend(collection)
            continue
        source_port = getattr(edge, "src_port_name", "") or ""
        text = next((_param_value(model, name) for name in (source_port, *names)
                     if name and _param_value(model, name).strip()), "")
        if not text:
            try:
                text = str(scene.resolve_text_value(source) or "")
            except Exception:
                text = ""
        refs.extend(parse_refs(text))
    return bool(edges), "\n".join(dict.fromkeys(refs))


class JobSignals(QtCore.QObject):
    progress = QtCore.Signal(str)
    submitted = QtCore.Signal(str)
    finished = QtCore.Signal(object)


class Worker(QtCore.QRunnable):
    def __init__(self, settings, signals, stop, resume_id):
        super().__init__()
        self.settings, self.signals, self.stop, self.resume_id = settings, signals, stop, resume_id

    def run(self):
        result = run_job(self.settings, progress=self.signals.progress.emit,
                         submitted=self.signals.submitted.emit, stop=self.stop, resume_id=self.resume_id)
        self.signals.finished.emit(result)


class Session(QtCore.QObject):
    """The model owns job state; rebuilding/deleting a widget cannot kill its worker."""
    changed = QtCore.Signal()

    def __init__(self, model):
        super().__init__(QtWidgets.QApplication.instance())
        self.model = model
        self.busy = False
        self.api_key = ""
        self.stop = threading.Event()
        self.signals = JobSignals(self)
        self.signals.progress.connect(self._progress)
        self.signals.submitted.connect(self._submitted)
        self.signals.finished.connect(self._finished)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.stop.set)

    def start(self, settings, resume_id=""):
        if self.busy:
            return
        self.busy = True
        self.stop.clear()
        self.output_dir = settings.output_dir
        if not resume_id:
            for name in OUTPUTS:
                put(self.model, name, "")
        put(self.model, "error", "")
        self._progress("Resuming saved task..." if resume_id else "Preparing reference generation...")
        QtCore.QThreadPool.globalInstance().start(Worker(settings, self.signals, self.stop, resume_id))

    @QtCore.Slot(str)
    def _submitted(self, task_id):
        put(self.model, "task_id", task_id)
        put(self.model, "task_output_dir", str(self.output_dir))
        self.changed.emit()

    @QtCore.Slot(str)
    def _progress(self, status):
        put(self.model, "h3_reference_status", status)
        self.changed.emit()

    @QtCore.Slot(object)
    def _finished(self, result: Result):
        self.busy = False
        if result.task_id:
            put(self.model, "task_id", result.task_id)
        if result.path:
            put(self.model, "mp4_path", result.path)
        put(self.model, "metadata_path", result.metadata_path)
        put(self.model, "error", result.error)
        put(self.model, "h3_reference_status", result.status)
        self.model.info = result.path or result.error or result.status
        self.changed.emit()


def session_for(model):
    session = getattr(model, "_minimax_h3_reference_session", None)
    if session is None:
        session = Session(model)
        model._minimax_h3_reference_session = session
    return session


class ReferenceVideoWidget(QtWidgets.QWidget):
    def __init__(self, item):
        super().__init__()
        self.item, self.model = item, item.model
        self.session = session_for(self.model)
        self.setMinimumWidth(560)
        self.setStyleSheet("QLabel{color:#b8c8dc;}" + _field_style())
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(7)
        title = QtWidgets.QLabel("MiniMax H3 · Previs + design references")
        title.setStyleSheet("font-weight:600;color:#67e8f9;")
        layout.addWidget(title)
        self.fields = {}
        self.browsers = {}
        self.prompt = QtWidgets.QPlainTextEdit(value(self.model, "prompt"))
        self.prompt.setPlaceholderText("Describe the final shot and what each numbered reference controls...")
        self.prompt.setFixedHeight(86)
        self.fields["prompt"] = self.prompt
        layout.addLayout(self._row("Prompt", self.prompt))
        template = QtWidgets.QPushButton("Use previs prompt template")
        template.clicked.connect(lambda: self.prompt.setPlainText(DEFAULT_PROMPT))
        self.template = template
        layout.addWidget(template)
        for port, label, height in (("reference_videos", "Previs videos", 54),
                                    ("reference_images", "Design images", 72),
                                    ("reference_audio", "Audio (optional)", 44)):
            edit = QtWidgets.QPlainTextEdit(value(self.model, port))
            edit.setFixedHeight(height)
            edit.setPlaceholderText("One local path, HTTPS URL or mm_file:// reference per line")
            edit.setToolTip("References are numbered in this order. Design Images merges all connected Image Collections, "
                            "with a combined limit of 9 images. Remote media limits are checked by MiniMax.")
            button = QtWidgets.QPushButton("Browse")
            button.setFixedWidth(64)
            button.clicked.connect(lambda checked=False, name=port: self._browse(name))
            self.fields[port], self.browsers[port] = edit, button
            row = self._row(label, edit)
            row.addWidget(button)
            layout.addLayout(row)
        hint = QtWidgets.QLabel("Up to 3 videos (15s total), 9 images, 3 audio clips (15s total); 12 files combined.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.addItems(["MiniMax-H3", "MiniMax-H3-Max"])
        self.model_combo.setCurrentText(value(self.model, "model", "MiniMax-H3"))
        self.resolution = QtWidgets.QComboBox()
        self.duration = QtWidgets.QSpinBox()
        self.duration.setSuffix(" s")
        self.ratio = QtWidgets.QComboBox()
        self.ratio.addItems(RATIOS)
        self.ratio.setCurrentText(value(self.model, "ratio", "adaptive"))
        row = self._row("Generation", self.model_combo)
        for control in (self.resolution, self.duration, self.ratio):
            row.addWidget(control)
        layout.addLayout(row)
        self.expansion = QtWidgets.QComboBox()
        self.expansion.addItems(["disabled", "balanced", "quality"])
        self.expansion.setCurrentText(value(self.model, "expansion", "balanced"))
        layout.addLayout(self._row("H3 Max prompt", self.expansion))
        self._limits()
        self.resolution.setCurrentText(value(self.model, "resolution", "768P"))
        try:
            self.duration.setValue(int(value(self.model, "duration", "5")))
        except ValueError:
            self.duration.setValue(5)
        self.api_key = QtWidgets.QLineEdit(self.session.api_key)
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key.setPlaceholderText("Pay-as-you-go API key (or MINIMAX_API_KEY)")
        self.api_key.setToolTip("Kept in this app session only. API keys are never saved in the workflow or task metadata.")
        layout.addLayout(self._row("API key", self.api_key))
        self.api_base = QtWidgets.QLineEdit(value(self.model, "base_url") or _default_api_base_url())
        layout.addLayout(self._row("API URL", self.api_base))
        self.output_dir = QtWidgets.QLineEdit(value(self.model, "output_dir"))
        self.output_dir.setPlaceholderText("Workflow/videos/minimax_h3_reference")
        folder = QtWidgets.QPushButton("Browse")
        folder.setFixedWidth(64)
        folder.clicked.connect(self._browse_output)
        self.folder = folder
        row = self._row("Save folder", self.output_dir)
        row.addWidget(folder)
        layout.addLayout(row)
        note = QtWidgets.QLabel("Generative reference following: camera, composition and poses can vary. "
                                "Generation uses your MiniMax pay-as-you-go balance; reference video is also billed.")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QtWidgets.QHBoxLayout()
        self.generate = QtWidgets.QPushButton("Generate")
        self.generate.setStyleSheet(_button_style(True))
        self.resume = QtWidgets.QPushButton("Resume task")
        self.stop_waiting = QtWidgets.QPushButton("Stop waiting")
        self.stop_waiting.setToolTip("Stops local waiting. MiniMax continues submitted work and may charge for it. Resume retrieves the same task.")
        self.generate.clicked.connect(lambda: self._start(False))
        self.resume.clicked.connect(lambda: self._start(True))
        self.stop_waiting.clicked.connect(self.session.stop.set)
        for button in (self.generate, self.resume, self.stop_waiting):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(46)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.output = QtWidgets.QLineEdit()
        self.output.setReadOnly(True)
        self.open_output = QtWidgets.QPushButton("Open video")
        self.open_output.clicked.connect(self._open)
        row = self._row("MP4 output", self.output)
        row.addWidget(self.open_output)
        layout.addLayout(row)
        self.task = QtWidgets.QLineEdit()
        self.task.setReadOnly(True)
        layout.addLayout(self._row("Task ID", self.task))
        for control in self.fields.values():
            control.textChanged.connect(self._commit)
        for control in (self.api_base, self.output_dir):
            control.editingFinished.connect(self._commit)
        self.api_key.textChanged.connect(self._key_changed)
        self.model_combo.currentTextChanged.connect(self._limits_changed)
        for control in (self.resolution, self.ratio, self.expansion):
            control.currentTextChanged.connect(self._commit)
        self.duration.valueChanged.connect(self._commit)
        self.session.changed.connect(self._state_changed)
        self._scene_connected = False
        QtCore.QTimer.singleShot(0, self._sync_scene)
        self._state_changed()

    def _row(self, label, control):
        row = QtWidgets.QHBoxLayout()
        text = QtWidgets.QLabel(label)
        text.setFixedWidth(92)
        row.addWidget(text)
        row.addWidget(control, 1)
        return row

    def _limits(self):
        is_max = self.model_combo.currentText() == "MiniMax-H3-Max"
        resolution = self.resolution.currentText() or "768P"
        self.resolution.blockSignals(True)
        self.resolution.clear()
        self.resolution.addItems(["480P", "768P"] if is_max else ["768P", "2K"])
        self.resolution.setCurrentText(resolution if resolution in [self.resolution.itemText(i) for i in range(self.resolution.count())] else "768P")
        self.resolution.blockSignals(False)
        self.duration.setRange(5 if is_max else 4, 15)
        self.expansion.setEnabled(is_max and not self.session.busy)

    def _limits_changed(self):
        self._limits()
        self._commit()

    def _key_changed(self, text):
        self.session.api_key = text

    def _commit(self):
        for name, edit in self.fields.items():
            put(self.model, name, edit.toPlainText())
        for name, control in (("model", self.model_combo), ("resolution", self.resolution),
                              ("ratio", self.ratio), ("expansion", self.expansion)):
            put(self.model, name, control.currentText())
        put(self.model, "duration", str(self.duration.value()))
        put(self.model, "base_url", self.api_base.text().strip())
        put(self.model, "output_dir", self.output_dir.text().strip())
        self._notify()

    def _notify(self):
        scene = self.item.scene()
        if scene is not None and hasattr(scene, "paramChanged"):
            scene.paramChanged.emit(self.model.name, list(self.model.params))

    def _sync_scene(self):
        scene = self.item.scene()
        if scene is not None and not self._scene_connected:
            for signal in ("linksChanged", "paramChanged"):
                if hasattr(scene, signal):
                    getattr(scene, signal).connect(self._refresh_inputs)
            self._scene_connected = True
        self._refresh_inputs()

    def _refresh_inputs(self, *_args):
        for port, field in self.fields.items():
            connected, text = incoming(self.item, port)
            field.setEnabled(not connected and not self.session.busy)
            field.setToolTip(("Connected value:\n" + text[:1500]) if connected else "One path/URL per line; list order determines reference numbers.")
            if port in self.browsers:
                self.browsers[port].setEnabled(not connected and not self.session.busy)
        self.template.setEnabled(self.prompt.isEnabled())

    def _browse(self, port):
        filters = {"reference_videos": "Video (*.mp4 *.mov)",
                   "reference_images": "Images (*.jpg *.jpeg *.png *.webp *.heic *.heif)",
                   "reference_audio": "Audio (*.wav *.mp3)"}
        # Native dialogs need the actual application window, not a widget
        # embedded in a QGraphicsProxyWidget (which can close them on Windows).
        parent = _dialog_parent(self.item)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(parent, "Add references (in numbered order)", "", filters[port])
        if paths:
            existing = parse_refs(self.fields[port].toPlainText())
            self.fields[port].setPlainText("\n".join(dict.fromkeys(existing + paths)))

    def _default_output_dir(self):
        workflow = _workflow_dir_for_node(self.item)
        return (workflow / "videos" / "minimax_h3_reference" if workflow else
                Path(tempfile.gettempdir()) / "EchoGraph" / "minimax_h3_reference")

    def _browse_output(self):
        parent = _dialog_parent(self.item)
        raw_dir = self.output_dir.text().strip()
        start = _resolve_path(self.item, raw_dir) if raw_dir else self._default_output_dir()
        try:
            start.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, "Save folder", f"Cannot open output folder:\n{start}\n\n{error}")
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(parent, "Save generated videos", str(start))
        if folder:
            self.output_dir.setText(folder)
            self._commit()

    def _settings(self, resume):
        self._commit()
        effective = {}
        for port, field in self.fields.items():
            connected, text = incoming(self.item, port)
            if connected and not text.strip() and not resume:
                if port == "prompt":
                    raise ValueError("Connected prompt is empty. Check the Note values you want to use.")
                raise ValueError(f"Connected {port} input has no output yet.")
            effective[port] = text if connected else field.toPlainText()
        workflow = _workflow_dir_for_node(self.item)
        base_dir = workflow or Path.cwd()
        raw_dir = value(self.model, "task_output_dir") if resume else ""
        raw_dir = raw_dir or self.output_dir.text().strip()
        output_dir = _resolve_path(self.item, raw_dir) if raw_dir else self._default_output_dir()
        return Settings(prompt=effective["prompt"], images=parse_refs(effective["reference_images"]),
                        videos=parse_refs(effective["reference_videos"]), audio=parse_refs(effective["reference_audio"]),
                        model=self.model_combo.currentText(), resolution=self.resolution.currentText(),
                        duration=self.duration.value(), ratio=self.ratio.currentText(), expansion=self.expansion.currentText(),
                        api_key=self.api_key.text().strip() or _default_api_key(), base_url=self.api_base.text().strip(),
                        base_dir=base_dir, output_dir=output_dir)

    def _start(self, resume):
        if self.session.busy:
            return
        try:
            settings = self._settings(resume)
            task_id = value(self.model, "task_id") if resume else ""
            if resume and not task_id:
                raise ValueError("There is no saved task to resume.")
            self.session.start(settings, task_id)
        except Exception as exc:
            self.status.setText(str(exc))

    def _state_changed(self):
        busy = self.session.busy
        task_id = value(self.model, "task_id")
        self.generate.setEnabled(not busy)
        self.generate.setText("Generate new" if task_id else "Generate")
        self.resume.setEnabled(not busy and bool(task_id))
        self.stop_waiting.setEnabled(busy)
        for control in (self.model_combo, self.resolution, self.duration, self.ratio, self.api_key,
                        self.api_base, self.output_dir, self.folder):
            control.setEnabled(not busy)
        self.expansion.setEnabled(not busy and self.model_combo.currentText() == "MiniMax-H3-Max")
        self.status.setText(value(self.model, "error") or value(self.model, "h3_reference_status") or "Ready. Add references and a prompt to generate.")
        self.output.setText(value(self.model, "mp4_path"))
        self.task.setText(task_id)
        self.open_output.setEnabled(bool(value(self.model, "mp4_path")))
        self._refresh_inputs()
        self._notify()

    def _open(self):
        path = Path(value(self.model, "mp4_path"))
        if path.is_file():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.resolve())))


def build_ports(item):
    for name, default in DEFAULTS.items():
        _ensure_param(item, PREFIX + name, default)
    for name in OUTPUTS:
        _ensure_param(item, name, "")
    _ensure_hidden_params(item.model, [*(PREFIX + name for name in DEFAULTS), *OUTPUTS])
    item._default_named_input = "prompt"
    item._show_default_input_with_named = True
    for name in INPUTS:
        item.ensure_input(name)
    for name in OUTPUTS:
        item.ensure_output(name)


def render_node_body(item, y_cursor):
    build_ports(item)
    body = ReferenceVideoWidget(item)
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(body)
    proxy.setPos(0, y_cursor)
    proxy.setZValue(item.zValue() + 0.1)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    width = max(560, hint.width(), int(item.width))
    height = hint.height()
    body.setFixedWidth(width)
    proxy.resize(width, height)
    body.layout().activate()
    item._minimax_reference_body_h = height
    item._minimax_reference_body_w = width
    item.prepareGeometryChange()
    item.width = width
    item.height = max(item.height, y_cursor + height + 14)
    item._plugin_proxies.append(proxy)
    for name, field in body.fields.items():
        y = field.mapTo(body, QtCore.QPoint(0, field.height() // 2)).y()
        item._input_port_pos[name] = (QtCore.QPointF(0, y_cursor + y), name)
    for index, name in enumerate(OUTPUTS):
        item._output_port_pos[name] = (QtCore.QPointF(width, y_cursor + height - 20 - index * 22), name)
    # Use the existing pin overlay: parenting to the proxy keeps the entire dot
    # above the embedded background, including when the node is brought forward.
    for side, positions in (("in", item._input_port_pos), ("out", item._output_port_pos)):
        for name, (position, _) in positions.items():
            PortDot(item, proxy, name, side, position)
    return y_cursor + height


SPEC = Spec(stripe_color="#22d3ee", build_ports=build_ports, render_node_body=render_node_body)
