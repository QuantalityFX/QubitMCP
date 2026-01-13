from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    _HAS_QT6 = True
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    _HAS_QT6 = False

QOpenGLWidget = None  # type: ignore
QOpenGLShader = None  # type: ignore
QOpenGLShaderProgram = None  # type: ignore
QOpenGLTexture = None  # type: ignore
QOpenGLBuffer = None  # type: ignore
QOpenGLVertexArrayObject = None  # type: ignore

if _HAS_QT6:
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
    except Exception:
        QOpenGLWidget = None  # type: ignore
    try:
        from PySide6 import QtGui as _QtGui  # type: ignore
        QOpenGLShader = getattr(_QtGui, "QOpenGLShader", None)
        QOpenGLShaderProgram = getattr(_QtGui, "QOpenGLShaderProgram", None)
        QOpenGLTexture = getattr(_QtGui, "QOpenGLTexture", None)
        QOpenGLBuffer = getattr(_QtGui, "QOpenGLBuffer", None)
        QOpenGLVertexArrayObject = getattr(_QtGui, "QOpenGLVertexArrayObject", None)
    except Exception:
        QOpenGLShader = None  # type: ignore
        QOpenGLShaderProgram = None  # type: ignore
        QOpenGLTexture = None  # type: ignore
        QOpenGLBuffer = None  # type: ignore
    if (
        QOpenGLShader is None
        or QOpenGLShaderProgram is None
        or QOpenGLTexture is None
        or QOpenGLBuffer is None
        or QOpenGLVertexArrayObject is None
    ):
        try:
            from PySide6 import QtOpenGL as _QtOpenGL  # type: ignore
            if QOpenGLShader is None:
                QOpenGLShader = getattr(_QtOpenGL, "QOpenGLShader", None)
            if QOpenGLShaderProgram is None:
                QOpenGLShaderProgram = getattr(_QtOpenGL, "QOpenGLShaderProgram", None)
            if QOpenGLTexture is None:
                QOpenGLTexture = getattr(_QtOpenGL, "QOpenGLTexture", None)
            if QOpenGLBuffer is None:
                QOpenGLBuffer = getattr(_QtOpenGL, "QOpenGLBuffer", None)
            if QOpenGLVertexArrayObject is None:
                QOpenGLVertexArrayObject = getattr(_QtOpenGL, "QOpenGLVertexArrayObject", None)
        except Exception:
            pass
else:
    try:
        from PySide2.QtWidgets import QOpenGLWidget  # type: ignore
    except Exception:
        QOpenGLWidget = None  # type: ignore
    try:
        from PySide2 import QtGui as _QtGui  # type: ignore
        QOpenGLShader = getattr(_QtGui, "QOpenGLShader", None)
        QOpenGLShaderProgram = getattr(_QtGui, "QOpenGLShaderProgram", None)
        QOpenGLTexture = getattr(_QtGui, "QOpenGLTexture", None)
        QOpenGLBuffer = getattr(_QtGui, "QOpenGLBuffer", None)
        QOpenGLVertexArrayObject = getattr(_QtGui, "QOpenGLVertexArrayObject", None)
    except Exception:
        QOpenGLShader = None  # type: ignore
        QOpenGLShaderProgram = None  # type: ignore
        QOpenGLTexture = None  # type: ignore
        QOpenGLBuffer = None  # type: ignore
    if (
        QOpenGLShader is None
        or QOpenGLShaderProgram is None
        or QOpenGLTexture is None
        or QOpenGLBuffer is None
        or QOpenGLVertexArrayObject is None
    ):
        try:
            from PySide2 import QtOpenGL as _QtOpenGL  # type: ignore
            if QOpenGLShader is None:
                QOpenGLShader = getattr(_QtOpenGL, "QOpenGLShader", None)
            if QOpenGLShaderProgram is None:
                QOpenGLShaderProgram = getattr(_QtOpenGL, "QOpenGLShaderProgram", None)
            if QOpenGLTexture is None:
                QOpenGLTexture = getattr(_QtOpenGL, "QOpenGLTexture", None)
            if QOpenGLBuffer is None:
                QOpenGLBuffer = getattr(_QtOpenGL, "QOpenGLBuffer", None)
            if QOpenGLVertexArrayObject is None:
                QOpenGLVertexArrayObject = getattr(_QtOpenGL, "QOpenGLVertexArrayObject", None)
        except Exception:
            pass

# OpenGL constants (avoid optional PyOpenGL dependency).
GL_TRIANGLES = 0x0004
GL_TRIANGLE_STRIP = 0x0005
GL_LINES = 0x0001
GL_FLOAT = 0x1406
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71

_MODEL_LOADERS: Dict[str, Callable[[Path], "ModelData"]] = {}


def register_model_loader(exts: Iterable[str], loader: Callable[[Path], "ModelData"]) -> None:
    for ext in exts:
        key = (ext or "").strip().lower()
        if not key:
            continue
        if not key.startswith("."):
            key = f".{key}"
        _MODEL_LOADERS[key] = loader


def load_model(path: Path) -> Optional["ModelData"]:
    ext = path.suffix.lower()
    loader = _MODEL_LOADERS.get(ext)
    if loader is None:
        return None
    try:
        return loader(path)
    except Exception:
        return None


@dataclass
class ModelData:
    vertices: List[float]
    bounds: Tuple[float, float, float, float, float, float]


class _GLMesh:
    def __init__(self, vertices: List[float]):
        self.vertices = vertices
        self.count = max(0, len(vertices) // 3)
        self.vbo = None

    def upload(self):
        if QOpenGLBuffer is None:
            return
        if self.vbo is None:
            self.vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self.vbo.create()
        if not self.vbo.bind():
            return
        data = QtCore.QByteArray(struct.pack(f"{len(self.vertices)}f", *self.vertices))
        self.vbo.allocate(data, data.size())
        self.vbo.release()


def _decode_data_uri(uri: str) -> bytes:
    header, _, data = uri.partition(",")
    if "base64" in header:
        return base64.b64decode(data)
    return data.encode("utf-8")


def _read_glb(path: Path) -> Tuple[dict, List[bytes]]:
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        raise ValueError("Not a GLB file.")
    if len(raw) < 12:
        raise ValueError("Invalid GLB header.")
    total_len = struct.unpack_from("<I", raw, 8)[0]
    if total_len > len(raw):
        total_len = len(raw)
    offset = 12
    gltf = None
    buffers: List[bytes] = []
    while offset + 8 <= total_len:
        chunk_len, chunk_type = struct.unpack_from("<II", raw, offset)
        offset += 8
        chunk_data = raw[offset: offset + chunk_len]
        offset += chunk_len
        if chunk_type == 0x4E4F534A:  # JSON
            gltf = json.loads(chunk_data.decode("utf-8"))
        elif chunk_type == 0x004E4942:  # BIN
            buffers.append(bytes(chunk_data))
    if gltf is None:
        raise ValueError("GLB missing JSON chunk.")
    return gltf, buffers


def _read_gltf(path: Path) -> Tuple[dict, List[bytes]]:
    gltf = json.loads(path.read_text(encoding="utf-8"))
    buffers: List[bytes] = []
    for buf in gltf.get("buffers", []) or []:
        uri = (buf.get("uri") or "").strip()
        if uri.startswith("data:"):
            buffers.append(_decode_data_uri(uri))
        else:
            buf_path = (path.parent / uri).resolve()
            buffers.append(buf_path.read_bytes())
    return gltf, buffers


_COMPONENT_SIZES = {
    5120: 1,  # BYTE
    5121: 1,  # UNSIGNED_BYTE
    5122: 2,  # SHORT
    5123: 2,  # UNSIGNED_SHORT
    5125: 4,  # UNSIGNED_INT
    5126: 4,  # FLOAT
}

_COMPONENT_FORMATS = {
    5120: "b",
    5121: "B",
    5122: "h",
    5123: "H",
    5125: "I",
    5126: "f",
}

_TYPE_COUNTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def _read_accessor(gltf: dict, buffers: List[bytes], accessor_index: int) -> List[float]:
    accessor = gltf.get("accessors", [])[accessor_index]
    buffer_view = gltf.get("bufferViews", [])[accessor["bufferView"]]
    buffer_data = buffers[buffer_view["buffer"]]
    offset = int(buffer_view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    count = int(accessor.get("count", 0))
    ctype = int(accessor.get("componentType", 5126))
    fmt = _COMPONENT_FORMATS.get(ctype, "f")
    comp_size = _COMPONENT_SIZES.get(ctype, 4)
    ncomp = _TYPE_COUNTS.get(accessor.get("type", "SCALAR"), 1)
    stride = int(buffer_view.get("byteStride", comp_size * ncomp))
    out: List[float] = []
    for i in range(count):
        base = offset + i * stride
        for c in range(ncomp):
            val = struct.unpack_from("<" + fmt, buffer_data, base + c * comp_size)[0]
            out.append(float(val))
    return out


def _matrix_from_node(node: dict) -> List[float]:
    if "matrix" in node:
        m = node.get("matrix") or []
        if len(m) == 16:
            return [float(v) for v in m]
    t = node.get("translation") or [0.0, 0.0, 0.0]
    r = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    s = node.get("scale") or [1.0, 1.0, 1.0]
    tx, ty, tz = [float(v) for v in t]
    rx, ry, rz, rw = [float(v) for v in r]
    sx, sy, sz = [float(v) for v in s]
    # Quaternion to matrix
    xx = rx * rx
    yy = ry * ry
    zz = rz * rz
    xy = rx * ry
    xz = rx * rz
    yz = ry * rz
    wx = rw * rx
    wy = rw * ry
    wz = rw * rz
    m00 = 1.0 - 2.0 * (yy + zz)
    m01 = 2.0 * (xy - wz)
    m02 = 2.0 * (xz + wy)
    m10 = 2.0 * (xy + wz)
    m11 = 1.0 - 2.0 * (xx + zz)
    m12 = 2.0 * (yz - wx)
    m20 = 2.0 * (xz - wy)
    m21 = 2.0 * (yz + wx)
    m22 = 1.0 - 2.0 * (xx + yy)
    return [
        m00 * sx, m01 * sy, m02 * sz, 0.0,
        m10 * sx, m11 * sy, m12 * sz, 0.0,
        m20 * sx, m21 * sy, m22 * sz, 0.0,
        tx, ty, tz, 1.0,
    ]


def _mul_mat4(a: List[float], b: List[float]) -> List[float]:
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = (
                a[row * 4 + 0] * b[0 * 4 + col]
                + a[row * 4 + 1] * b[1 * 4 + col]
                + a[row * 4 + 2] * b[2 * 4 + col]
                + a[row * 4 + 3] * b[3 * 4 + col]
            )
    return out


def _apply_mat4(m: List[float], x: float, y: float, z: float) -> Tuple[float, float, float]:
    nx = x * m[0] + y * m[4] + z * m[8] + m[12]
    ny = x * m[1] + y * m[5] + z * m[9] + m[13]
    nz = x * m[2] + y * m[6] + z * m[10] + m[14]
    return nx, ny, nz


def _gltf_collect_nodes(gltf: dict, node_indices: List[int], parent: List[float]) -> List[List[float]]:
    out: List[List[float]] = []
    nodes = gltf.get("nodes", []) or []
    for idx in node_indices:
        if idx < 0 or idx >= len(nodes):
            continue
        node = nodes[idx]
        local = _matrix_from_node(node)
        world = _mul_mat4(parent, local)
        out.append((node, world))
        children = node.get("children") or []
        out.extend(_gltf_collect_nodes(gltf, list(children), world))
    return out


def _load_gltf_model(path: Path) -> ModelData:
    if path.suffix.lower() == ".glb":
        gltf, buffers = _read_glb(path)
    else:
        gltf, buffers = _read_gltf(path)
    meshes = gltf.get("meshes", []) or []
    nodes = gltf.get("nodes", []) or []
    scene_index = gltf.get("scene", 0) or 0
    scenes = gltf.get("scenes", []) or []
    node_roots = []
    if scenes and scene_index < len(scenes):
        node_roots = scenes[scene_index].get("nodes", []) or []
    elif nodes:
        node_roots = list(range(len(nodes)))

    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    node_pairs = _gltf_collect_nodes(gltf, node_roots, identity)

    vertices: List[float] = []
    bounds = [math.inf, math.inf, math.inf, -math.inf, -math.inf, -math.inf]

    for node, world in node_pairs:
        mesh_index = node.get("mesh")
        if mesh_index is None or mesh_index >= len(meshes):
            continue
        mesh = meshes[mesh_index]
        for prim in mesh.get("primitives", []) or []:
            attrs = prim.get("attributes") or {}
            pos_accessor = attrs.get("POSITION")
            if pos_accessor is None:
                continue
            positions = _read_accessor(gltf, buffers, int(pos_accessor))
            indices = None
            if "indices" in prim:
                idx_data = _read_accessor(gltf, buffers, int(prim["indices"]))
                indices = [int(i) for i in idx_data]
            tri_mode = int(prim.get("mode", 4))
            if tri_mode != 4:
                continue
            primitive_vertices: List[float] = []
            if indices:
                for idx in indices:
                    base = idx * 3
                    x, y, z = positions[base], positions[base + 1], positions[base + 2]
                    x, y, z = _apply_mat4(world, x, y, z)
                    primitive_vertices.extend([x, y, z])
            else:
                for i in range(0, len(positions), 3):
                    x, y, z = positions[i], positions[i + 1], positions[i + 2]
                    x, y, z = _apply_mat4(world, x, y, z)
                    primitive_vertices.extend([x, y, z])
            for i in range(0, len(primitive_vertices), 3):
                vx, vy, vz = (
                    primitive_vertices[i],
                    primitive_vertices[i + 1],
                    primitive_vertices[i + 2],
                )
                bounds[0] = min(bounds[0], vx)
                bounds[1] = min(bounds[1], vy)
                bounds[2] = min(bounds[2], vz)
                bounds[3] = max(bounds[3], vx)
                bounds[4] = max(bounds[4], vy)
                bounds[5] = max(bounds[5], vz)
            vertices.extend(primitive_vertices)

    if not vertices:
        bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return ModelData(vertices=vertices, bounds=tuple(bounds))


def _load_obj_model(path: Path) -> ModelData:
    positions: List[Tuple[float, float, float]] = []
    vertices: List[float] = []
    bounds = [math.inf, math.inf, math.inf, -math.inf, -math.inf, -math.inf]

    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = path.read_text(errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        head = parts[0].lower()
        if head == "v" and len(parts) >= 4:
            try:
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            except Exception:
                continue
            positions.append((x, y, z))
        elif head == "f" and len(parts) >= 4:
            indices: List[int] = []
            for token in parts[1:]:
                if not token:
                    continue
                idx_str = token.split("/")[0]
                if not idx_str:
                    continue
                try:
                    idx = int(idx_str)
                except Exception:
                    continue
                if idx < 0:
                    idx = len(positions) + idx + 1
                if idx <= 0 or idx > len(positions):
                    continue
                indices.append(idx - 1)
            if len(indices) < 3:
                continue
            root = indices[0]
            for i in range(1, len(indices) - 1):
                tri = (root, indices[i], indices[i + 1])
                for vidx in tri:
                    try:
                        vx, vy, vz = positions[vidx]
                    except Exception:
                        continue
                    vertices.extend([vx, vy, vz])
                    bounds[0] = min(bounds[0], vx)
                    bounds[1] = min(bounds[1], vy)
                    bounds[2] = min(bounds[2], vz)
                    bounds[3] = max(bounds[3], vx)
                    bounds[4] = max(bounds[4], vy)
                    bounds[5] = max(bounds[5], vz)

    if not vertices:
        bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return ModelData(vertices=vertices, bounds=tuple(bounds))


register_model_loader([".gltf", ".glb"], _load_gltf_model)
register_model_loader([".obj"], _load_obj_model)


class GraphGLView(QOpenGLWidget if QOpenGLWidget is not None else QtWidgets.QWidget):
    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self._scene = scene
        self._scene_texture = None
        self._scene_texture_dirty = False
        self._scene_src_rect = QtCore.QRectF()
        self._quad_vbo = None
        self._quad_ready = False
        self._vao = None
        self._quad_program = None
        self._mesh_program = None
        self._quad_pos_loc = -1
        self._quad_uv_loc = -1
        self._mesh_pos_loc = -1
        self._shader_error = ""
        self._debug_overlay = True
        self._mipmaps_enabled = False
        self._capture_view = None
        self._scene_content_blank = False
        self._debug_mesh = None
        self._debug_mesh_color = QtGui.QColor("#f59e0b")
        self._debug_mesh_scale = 200.0
        self._debug_mesh_scale_base = 200.0
        self._debug_mesh_center = QtCore.QPointF(0.0, 0.0)
        self._grid_vertices: List[float] = []
        self._grid_vbo = None
        self._grid_count = 0
        self._grid_dirty = True
        self._grid_color = QtGui.QColor("#334155")
        self._grid_center = QtCore.QPointF(0.0, 0.0)
        self._grid_z = 0.5
        self._meshes: Dict[str, _GLMesh] = {}
        self._mesh_colors: Dict[str, QtGui.QColor] = {}
        self._mesh_transforms: Dict[str, QtGui.QMatrix4x4] = {}
        self._mesh_meta: Dict[str, dict] = {}
        self._model_scale_multiplier = 1.0
        self._manual_model_path: Optional[Path] = None
        self._auto_frame_on_scale = True
        self._drag_divisor = 13.0
        self._zoom_multiplier = 1.1
        self._min_cam_dist = 200.0
        self._max_cam_dist = 200000000.0
        self._orbit_sensitivity = 0.005

        self._cam_yaw = 0.45
        self._cam_pitch = -0.35
        self._cam_dist = 2400.0
        self._cam_focal = 1200.0
        self._fov_deg = 50.0
        self._cam_target = QtCore.QPointF(0.0, 0.0)

        self._orbit_dragging = False
        self._pan_dragging = False
        self._dolly_dragging = False
        self._orbit_last_pos = None
        self._pan_last_pos = None
        self._dolly_press_pos = None
        self._dolly_start_dist = None

        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._build_scale_controls()
        self._build_debug_copy_button()

    def set_scene(self, scene) -> None:
        self._scene = scene

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_controls", None) is not None:
            h = int(getattr(self, "_controls_h", 44))
            self._controls.setGeometry(0, max(0, self.height() - h), self.width(), h)
        btn = getattr(self, "_debug_copy_btn", None)
        if btn is not None:
            btn.setGeometry(10, 10, 46, 22)

    def paintEvent(self, event):
        if QOpenGLWidget is None:
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("#0f172a"))
            painter.setPen(QtGui.QColor("#e2e8f0"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "OpenGL widgets not available.")
            painter.end()
            return
        super().paintEvent(event)
        if not hasattr(self, "_gl"):
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("#0f172a"))
            painter.end()
        self._draw_overlay()

    def refresh_from_scene(self) -> None:
        self._capture_scene_texture()
        self._load_scene_models()
        self._reset_camera()
        self.update()

    def _build_scale_controls(self) -> None:
        try:
            controls = QtWidgets.QFrame(self)
            controls.setObjectName("GLControls")
            controls.setStyleSheet(
                "#GLControls{background:rgba(15,23,42,210);border-top:1px solid #334155;}"
                "#GLControls QLabel{color:#e2e8f0;font-size:11px;}"
                "#GLControls QPushButton{padding:3px 10px;font-weight:600;color:#e2e8f0;"
                "background:#1f2937;border-radius:4px;}"
                "#GLControls QPushButton:hover{background:#334155;}"
            )
            layout = QtWidgets.QHBoxLayout(controls)
            layout.setContentsMargins(10, 6, 10, 6)
            layout.setSpacing(10)

            self._model_pick_btn = QtWidgets.QPushButton("Model...")
            self._model_pick_btn.clicked.connect(self._on_pick_model)
            layout.addWidget(self._model_pick_btn, 0)

            self._frame_btn = QtWidgets.QPushButton("Frame")
            self._frame_btn.clicked.connect(self._on_frame_clicked)
            layout.addWidget(self._frame_btn, 0)

            self._model_scale_label = QtWidgets.QLabel("Scale 1.00x")
            self._model_scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self._model_scale_slider.setRange(1, 1000)
            self._model_scale_slider.setValue(int(self._model_scale_multiplier * 100))
            self._model_scale_slider.setFixedWidth(160)
            self._model_scale_slider.valueChanged.connect(self._on_model_scale_changed)

            layout.addWidget(self._model_scale_label, 0)
            layout.addWidget(self._model_scale_slider, 0)
            layout.addStretch(1)
            self._controls = controls
            self._controls_h = 44
            self._controls.show()
        except Exception:
            self._controls = None
            self._controls_h = 0

    def _build_debug_copy_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setText("Copy")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QToolButton{background:rgba(15,23,42,210);border:1px solid #334155;"
                "color:#e2e8f0;padding:2px 6px;border-radius:4px;font-size:10px;}"
                "QToolButton:hover{background:rgba(30,41,59,230);}"
            )
            btn.clicked.connect(self._copy_debug_details)
            self._debug_copy_btn = btn
            btn.show()
        except Exception:
            self._debug_copy_btn = None

    def _copy_debug_details(self) -> None:
        try:
            lines = self._debug_status_lines(include_paths=True)
            QtWidgets.QApplication.clipboard().setText("\n".join(lines))
        except Exception:
            pass

    def _on_model_scale_changed(self, value: int) -> None:
        try:
            scale = max(0.1, float(value) / 100.0)
        except Exception:
            scale = 1.0
        self._model_scale_multiplier = scale
        if hasattr(self, "_model_scale_label"):
            self._model_scale_label.setText(f"Scale {scale:.2f}x")
        self._rebuild_mesh_transforms()
        self._debug_mesh_scale = self._debug_mesh_scale_base * self._model_scale_multiplier
        self._update_quad_vbo()
        if self._auto_frame_on_scale:
            self._reset_camera()
        self.update()

    def _on_frame_clicked(self) -> None:
        self._reset_camera()
        self.update()

    def _default_models_dir(self) -> Optional[Path]:
        try:
            root = Path(__file__).resolve().parents[2]
        except Exception:
            return None
        candidate = root / "echograph" / "3dmodels"
        if candidate.is_dir():
            return candidate
        return None

    def _on_pick_model(self) -> None:
        try:
            start_dir = self._default_models_dir()
            directory = str(start_dir) if start_dir is not None else ""
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Select Model",
                directory,
                "3D Models (*.obj *.gltf *.glb);;All Files (*)",
            )
        except Exception:
            filename = ""
        if not filename:
            return
        try:
            self._manual_model_path = Path(filename)
        except Exception:
            self._manual_model_path = None
        self._load_scene_models()
        self._reset_camera()
        self.update()

    def _capture_scene_texture(self) -> None:
        if self._scene is None:
            return
        rect = QtCore.QRectF(self._scene.itemsBoundingRect())
        if rect.isNull():
            rect = QtCore.QRectF(self._scene.sceneRect())
        margin = 80.0
        rect = rect.adjusted(-margin, -margin, margin, margin)
        max_dim = 4096
        scale = 1.0
        if rect.width() > 0 and rect.height() > 0:
            scale = min(1.0, max_dim / rect.width(), max_dim / rect.height())
        img_w = max(1, int(rect.width() * scale))
        img_h = max(1, int(rect.height() * scale))
        if hasattr(QtGui.QImage, "Format_RGBA8888"):
            fmt = QtGui.QImage.Format_RGBA8888
        else:
            fmt = QtGui.QImage.Format_ARGB32
        image = QtGui.QImage(img_w, img_h, fmt)
        bg = QtGui.QColor("#1a1f24")
        try:
            if self._scene is not None:
                brush = self._scene.backgroundBrush()
                if brush.style() != QtCore.Qt.NoBrush:
                    bg = brush.color()
        except Exception:
            pass
        image.fill(bg)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        if not self._render_scene_with_view(painter, rect, img_w, img_h):
            self._scene.render(
                painter,
                QtCore.QRectF(0, 0, img_w, img_h),
                rect,
                QtCore.Qt.IgnoreAspectRatio,
            )
        painter.end()
        self._scene_src_rect = rect
        self._scene_texture_dirty = True
        self._pending_image = image
        self._scene_content_blank = self._estimate_scene_blank(image, bg)

    def _render_scene_with_view(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        img_w: int,
        img_h: int,
    ) -> bool:
        view = self._ensure_capture_view()
        if view is None:
            return False
        try:
            view.setScene(self._scene)
            view.setSceneRect(rect)
            view.resetTransform()
            view.setFixedSize(img_w, img_h)
            view.fitInView(rect, QtCore.Qt.KeepAspectRatio)
            source = QtCore.QRectF(view.viewport().rect())
            target = QtCore.QRectF(0.0, 0.0, img_w, img_h)
            view.render(painter, target, source, QtCore.Qt.IgnoreAspectRatio)
            return True
        except Exception:
            return False

    def _ensure_capture_view(self) -> QtWidgets.QGraphicsView | None:
        if self._capture_view is not None:
            return self._capture_view
        try:
            view = QtWidgets.QGraphicsView(self._scene)
            view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
            view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            view.setFrameStyle(QtWidgets.QFrame.NoFrame)
            view.setRenderHint(QtGui.QPainter.Antialiasing, True)
            view.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            view.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            try:
                view.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
            except Exception:
                pass
            self._capture_view = view
            return view
        except Exception:
            return None

    def _choose_grid_spacing(self, extent: float) -> float:
        target = max(1.0, extent / 10.0)
        candidates = [25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0]
        best = candidates[0]
        best_delta = abs(best - target)
        for cand in candidates[1:]:
            delta = abs(cand - target)
            if delta < best_delta:
                best = cand
                best_delta = delta
        return best

    def _update_grid(self, extent: float) -> None:
        extent = max(200.0, float(extent))
        spacing = self._choose_grid_spacing(extent)
        count = int(max(1, extent / spacing))
        count = min(count, 200)
        extent = count * spacing
        verts: List[float] = []
        for i in range(-count, count + 1):
            x = i * spacing
            verts.extend([x, -extent, self._grid_z, x, extent, self._grid_z])
            y = i * spacing
            verts.extend([-extent, y, self._grid_z, extent, y, self._grid_z])
        self._grid_vertices = verts
        self._grid_count = len(verts) // 3
        self._grid_dirty = True

    def _upload_grid(self) -> None:
        if not self._grid_dirty:
            return
        if QOpenGLBuffer is None or not self._grid_vertices:
            return
        if self._grid_vbo is None:
            self._grid_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._grid_vbo.create()
        if not self._grid_vbo.bind():
            return
        data = QtCore.QByteArray(struct.pack(f"{len(self._grid_vertices)}f", *self._grid_vertices))
        self._grid_vbo.allocate(data, data.size())
        self._grid_vbo.release()
        self._grid_dirty = False

    def _estimate_scene_blank(self, image: QtGui.QImage, bg: QtGui.QColor) -> bool:
        if image is None or image.isNull():
            return True
        bg_r = bg.red()
        bg_g = bg.green()
        bg_b = bg.blue()
        w = max(1, image.width())
        h = max(1, image.height())
        samples = 0
        for ix in range(0, 5):
            x = int((w - 1) * (ix / 4.0))
            for iy in range(0, 5):
                y = int((h - 1) * (iy / 4.0))
                col = QtGui.QColor(image.pixel(x, y))
                dr = abs(col.red() - bg_r)
                dg = abs(col.green() - bg_g)
                db = abs(col.blue() - bg_b)
                if dr + dg + db > 12:
                    return False
                samples += 1
        return True

    def _load_scene_models(self) -> None:
        self._meshes.clear()
        self._mesh_colors.clear()
        self._mesh_transforms.clear()
        self._mesh_meta.clear()
        if self._scene is None:
            return
        used_paths = set()
        for name, item in getattr(self._scene, "_node_items", {}).items():
            model = getattr(item, "model", None)
            if model is None:
                continue
            path_val = ""
            for p in (model.params or []):
                if (p.get("name") or "").strip().lower() == "path":
                    path_val = (p.get("value") or "").strip()
                    break
            if not path_val:
                continue
            path = Path(path_val)
            if not path.is_file():
                continue
            model_data = load_model(path)
            if not model_data or not model_data.vertices:
                continue
            used_paths.add(str(path))
            tx, ty = 0.0, 0.0
            tz = 0.0
            try:
                tx, ty = model.pos_xy
            except Exception:
                pass
            try:
                tz = float(getattr(model, "pos_z", 0.0))
            except Exception:
                tz = 0.0
            mesh = _GLMesh(model_data.vertices)
            self._meshes[name] = mesh
            self._mesh_colors[name] = QtGui.QColor("#60a5fa")
            model_mat = QtGui.QMatrix4x4()
            min_x, min_y, min_z, max_x, max_y, max_z = model_data.bounds
            cx = (min_x + max_x) * 0.5
            cy = (min_y + max_y) * 0.5
            cz = (min_z + max_z) * 0.5
            extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)
            base_scale = 18.0 / extent
            scale = base_scale * self._model_scale_multiplier
            model_mat.translate(float(tx), float(ty), float(tz))
            model_mat.scale(scale, scale, scale)
            model_mat.translate(-cx, -cy, -cz)
            self._mesh_transforms[name] = model_mat
            self._mesh_meta[name] = {
                "center": (cx, cy, cz),
                "bounds": model_data.bounds,
                "pos": (float(tx), float(ty), float(tz)),
                "base_scale": base_scale,
                "path": str(path),
            }

        manual_path = self._manual_model_path
        if manual_path is not None and manual_path.is_file():
            if str(manual_path) not in used_paths:
                model_data = load_model(manual_path)
                if model_data and model_data.vertices:
                    mesh = _GLMesh(model_data.vertices)
                    self._meshes["__manual__"] = mesh
                    self._mesh_colors["__manual__"] = QtGui.QColor("#f59e0b")
                    min_x, min_y, min_z, max_x, max_y, max_z = model_data.bounds
                    cx = (min_x + max_x) * 0.5
                    cy = (min_y + max_y) * 0.5
                    cz = (min_z + max_z) * 0.5
                    extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)
                    base_scale = 18.0 / extent
                    scale = base_scale * self._model_scale_multiplier
                    model_mat = QtGui.QMatrix4x4()
                    model_mat.translate(0.0, 0.0, 0.0)
                    model_mat.scale(scale, scale, scale)
                    model_mat.translate(-cx, -cy, -cz)
                    self._mesh_transforms["__manual__"] = model_mat
                    self._mesh_meta["__manual__"] = {
                        "center": (cx, cy, cz),
                        "bounds": model_data.bounds,
                        "pos": (0.0, 0.0, 0.0),
                        "base_scale": base_scale,
                        "path": str(manual_path),
                    }

    def _rebuild_mesh_transforms(self) -> None:
        if not self._mesh_meta:
            return
        self._mesh_transforms.clear()
        for name, meta in self._mesh_meta.items():
            cx, cy, cz = meta.get("center", (0.0, 0.0, 0.0))
            tx, ty, tz = meta.get("pos", (0.0, 0.0, 0.0))
            base_scale = float(meta.get("base_scale", 1.0))
            scale = base_scale * self._model_scale_multiplier
            model_mat = QtGui.QMatrix4x4()
            model_mat.translate(float(tx), float(ty), float(tz))
            model_mat.scale(scale, scale, scale)
            model_mat.translate(-cx, -cy, -cz)
            self._mesh_transforms[name] = model_mat

    def _mesh_bounds_world(self) -> Optional[Tuple[float, float, float, float, float, float]]:
        if not self._mesh_meta:
            return None
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        for meta in self._mesh_meta.values():
            bounds = meta.get("bounds")
            if not bounds:
                continue
            cx, cy, cz = meta.get("center", (0.0, 0.0, 0.0))
            tx, ty, tz = meta.get("pos", (0.0, 0.0, 0.0))
            base_scale = float(meta.get("base_scale", 1.0))
            scale = base_scale * self._model_scale_multiplier
            min_x = min(min_x, tx + (bounds[0] - cx) * scale)
            max_x = max(max_x, tx + (bounds[3] - cx) * scale)
            min_y = min(min_y, ty + (bounds[1] - cy) * scale)
            max_y = max(max_y, ty + (bounds[4] - cy) * scale)
            min_z = min(min_z, tz + (bounds[2] - cz) * scale)
            max_z = max(max_z, tz + (bounds[5] - cz) * scale)
        if min_x == float("inf"):
            return None
        return min_x, min_y, min_z, max_x, max_y, max_z

    def initializeGL(self) -> None:
        ctx = self.context()
        if ctx is not None:
            self._gl = ctx.functions()
        if QOpenGLShaderProgram is None:
            return
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._gl.glEnable(GL_DEPTH_TEST)
        try:
            self._gl.glLineWidth(1.0)
        except Exception:
            pass

        if QOpenGLVertexArrayObject is not None:
            self._vao = QOpenGLVertexArrayObject()
            try:
                self._vao.create()
            except Exception:
                self._vao = None

        self._quad_program = QOpenGLShaderProgram()
        self._quad_program.addShaderFromSourceCode(
            QOpenGLShader.Vertex,
            """
            attribute vec3 a_pos;
            attribute vec2 a_uv;
            uniform mat4 u_mvp;
            varying vec2 v_uv;
            void main() {
                gl_Position = u_mvp * vec4(a_pos, 1.0);
                v_uv = a_uv;
            }
            """,
        )
        self._quad_program.addShaderFromSourceCode(
            QOpenGLShader.Fragment,
            """
            uniform sampler2D u_tex;
            varying vec2 v_uv;
            void main() {
                gl_FragColor = texture2D(u_tex, v_uv);
            }
            """,
        )
        if not self._quad_program.link():
            self._shader_error = self._quad_program.log()
        self._quad_pos_loc = self._quad_program.attributeLocation("a_pos")
        self._quad_uv_loc = self._quad_program.attributeLocation("a_uv")

        self._mesh_program = QOpenGLShaderProgram()
        self._mesh_program.addShaderFromSourceCode(
            QOpenGLShader.Vertex,
            """
            attribute vec3 a_pos;
            uniform mat4 u_mvp;
            void main() {
                gl_Position = u_mvp * vec4(a_pos, 1.0);
            }
            """,
        )
        self._mesh_program.addShaderFromSourceCode(
            QOpenGLShader.Fragment,
            """
            uniform vec4 u_color;
            void main() {
                gl_FragColor = u_color;
            }
            """,
        )
        if not self._mesh_program.link():
            self._shader_error = (self._shader_error + "\n" + self._mesh_program.log()).strip()
        self._mesh_pos_loc = self._mesh_program.attributeLocation("a_pos")

        if QOpenGLBuffer is not None:
            self._quad_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._quad_vbo.create()
        self._debug_mesh = _GLMesh(self._debug_cube_vertices())

        self._upload_scene_texture()
        for mesh in self._meshes.values():
            mesh.upload()
        if self._debug_mesh is not None:
            self._debug_mesh.upload()

    def resizeGL(self, w: int, h: int) -> None:
        if hasattr(self, "_gl"):
            self._gl.glViewport(0, 0, w, h)

    def _upload_scene_texture(self) -> None:
        if not getattr(self, "_scene_texture_dirty", False):
            return
        image = getattr(self, "_pending_image", None)
        if image is None:
            return
        if QOpenGLTexture is None:
            return
        if self._scene_texture is not None:
            try:
                self._scene_texture.destroy()
            except Exception:
                pass
        if hasattr(QtGui.QImage, "Format_RGBA8888"):
            image = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
        else:
            image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
        image = image.mirrored()
        self._scene_texture = QOpenGLTexture(image)
        self._scene_texture.setMagnificationFilter(QOpenGLTexture.Linear)
        try:
            self._scene_texture.setWrapMode(QOpenGLTexture.ClampToEdge)
        except Exception:
            pass
        self._mipmaps_enabled = False
        try:
            if hasattr(self._scene_texture, "setAutoMipMapGenerationEnabled"):
                self._scene_texture.setAutoMipMapGenerationEnabled(True)
            if hasattr(self._scene_texture, "generateMipMaps"):
                self._scene_texture.generateMipMaps()
            mip_levels = None
            if hasattr(self._scene_texture, "mipLevels"):
                mip_levels = int(self._scene_texture.mipLevels())
            if hasattr(self._scene_texture, "hasMipMaps"):
                self._mipmaps_enabled = bool(self._scene_texture.hasMipMaps())
            elif mip_levels is not None:
                self._mipmaps_enabled = mip_levels > 1
        except Exception:
            self._mipmaps_enabled = False
        if self._mipmaps_enabled and hasattr(QOpenGLTexture, "LinearMipMapLinear"):
            self._scene_texture.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
        else:
            self._scene_texture.setMinificationFilter(QOpenGLTexture.Linear)
        self._scene_texture_dirty = False
        self._update_quad_vbo()

    def _update_quad_vbo(self) -> None:
        if self._quad_vbo is None:
            return
        rect = self._scene_src_rect
        x0, y0 = rect.left(), rect.top()
        x1, y1 = rect.right(), rect.bottom()
        extent = max(abs(x1 - x0), abs(y1 - y0), 1.0)
        plane_z = -max(10.0, extent * 0.05)
        verts = [
            x0, y0, plane_z, 0.0, 1.0,
            x1, y0, plane_z, 1.0, 1.0,
            x0, y1, plane_z, 0.0, 0.0,
            x1, y1, plane_z, 1.0, 0.0,
        ]
        data = QtCore.QByteArray(struct.pack(f"{len(verts)}f", *verts))
        if self._quad_vbo.bind():
            self._quad_vbo.allocate(data, data.size())
            self._quad_vbo.release()
        self._quad_ready = True

    def _projection_matrix(self) -> QtGui.QMatrix4x4:
        w = max(1, self.width())
        h = max(1, self.height())
        proj = QtGui.QMatrix4x4()
        far_plane = max(1000000.0, float(self._cam_dist) * 10.0)
        far_plane = max(1000000.0, float(self._cam_dist) * 50.0)
        proj.perspective(float(self._fov_deg), w / float(h), 0.1, far_plane)
        return proj

    def _view_matrix(self) -> QtGui.QMatrix4x4:
        target = self._cam_target
        cy = math.cos(self._cam_yaw)
        sy = math.sin(self._cam_yaw)
        cp = math.cos(self._cam_pitch)
        sp = math.sin(self._cam_pitch)
        cam_x = target.x() + self._cam_dist * cp * sy
        cam_y = target.y() + self._cam_dist * sp
        cam_z = self._cam_dist * cp * cy
        m = QtGui.QMatrix4x4()
        m.lookAt(
            QtGui.QVector3D(cam_x, cam_y, cam_z),
            QtGui.QVector3D(target.x(), target.y(), 0.0),
            QtGui.QVector3D(0.0, -1.0, 0.0),
        )
        return m

    def paintGL(self) -> None:
        if not hasattr(self, "_gl"):
            return
        self._upload_scene_texture()
        self._upload_grid()
        self._gl.glClearColor(0.10, 0.12, 0.14, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if QOpenGLShaderProgram is None:
            return
        if self._vao is not None:
            try:
                self._vao.bind()
            except Exception:
                pass
        proj = self._projection_matrix()
        view = self._view_matrix()

        if self._scene_texture and self._quad_program and self._quad_ready:
            self._quad_program.bind()
            mvp = proj * view
            self._quad_program.setUniformValue("u_mvp", mvp)
            self._quad_program.setUniformValue("u_tex", 0)
            self._scene_texture.bind(0)
            if self._quad_vbo and self._quad_vbo.bind():
                stride = 5 * 4
                if self._quad_pos_loc >= 0:
                    self._quad_program.enableAttributeArray(self._quad_pos_loc)
                    self._quad_program.setAttributeBuffer(self._quad_pos_loc, GL_FLOAT, 0, 3, stride)
                if self._quad_uv_loc >= 0:
                    self._quad_program.enableAttributeArray(self._quad_uv_loc)
                    self._quad_program.setAttributeBuffer(self._quad_uv_loc, GL_FLOAT, 12, 2, stride)
                self._gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
                self._quad_vbo.release()
            self._scene_texture.release()
            self._quad_program.release()

        if self._mesh_program and self._grid_count > 0 and self._grid_vbo is not None:
            self._mesh_program.bind()
            model = QtGui.QMatrix4x4()
            model.translate(self._grid_center.x(), self._grid_center.y(), 0.0)
            mvp = proj * view * model
            self._mesh_program.setUniformValue("u_mvp", mvp)
            color = self._grid_color
            self._mesh_program.setUniformValue(
                "u_color",
                QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.55),
            )
            if self._grid_vbo.bind():
                if self._mesh_pos_loc >= 0:
                    self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                    self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                self._gl.glDrawArrays(GL_LINES, 0, self._grid_count)
                self._grid_vbo.release()
            self._mesh_program.release()

        if self._mesh_program:
            for name, mesh in self._meshes.items():
                if mesh.count == 0:
                    continue
                if mesh.vbo is None:
                    mesh.upload()
                if mesh.vbo is None:
                    continue
                model = self._mesh_transforms.get(name, QtGui.QMatrix4x4())
                mvp = proj * view * model
                color = self._mesh_colors.get(name, QtGui.QColor("#60a5fa"))
                self._mesh_program.bind()
                self._mesh_program.setUniformValue("u_mvp", mvp)
                self._mesh_program.setUniformValue(
                    "u_color",
                    QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.85),
                )
                if mesh.vbo.bind():
                    if self._mesh_pos_loc >= 0:
                        self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                        self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                    self._gl.glDrawArrays(GL_TRIANGLES, 0, mesh.count)
                    mesh.vbo.release()
                self._mesh_program.release()
        if self._mesh_program and self._debug_mesh and (self._scene_content_blank or not self._scene_texture):
            if self._debug_mesh.vbo is None:
                self._debug_mesh.upload()
            if self._debug_mesh.vbo is not None:
                model = QtGui.QMatrix4x4()
                model.translate(self._debug_mesh_center.x(), self._debug_mesh_center.y(), 0.0)
                model.scale(self._debug_mesh_scale)
                mvp = proj * view * model
                self._mesh_program.bind()
                self._mesh_program.setUniformValue("u_mvp", mvp)
                color = self._debug_mesh_color
                self._mesh_program.setUniformValue(
                    "u_color",
                    QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.9),
                )
                if self._debug_mesh.vbo.bind():
                    if self._mesh_pos_loc >= 0:
                        self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                        self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                    self._gl.glDrawArrays(GL_TRIANGLES, 0, self._debug_mesh.count)
                    self._debug_mesh.vbo.release()
                self._mesh_program.release()
        if self._vao is not None:
            try:
                self._vao.release()
            except Exception:
                pass

    def _draw_overlay(self) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        show_debug = self._debug_overlay or self._shader_error or not self._scene_texture
        if show_debug:
            lines = self._debug_status_lines()
            if lines:
                metrics = painter.fontMetrics()
                if hasattr(metrics, "horizontalAdvance"):
                    text_width = max(metrics.horizontalAdvance(line) for line in lines)
                else:
                    text_width = max(metrics.width(line) for line in lines)
                text_height = len(lines) * metrics.height() + max(0, len(lines) - 1) * 2
                pad = 8
                panel_top = 10.0
                btn = getattr(self, "_debug_copy_btn", None)
                if btn is not None and btn.isVisible():
                    panel_top = btn.geometry().bottom() + 6.0
                panel = QtCore.QRectF(10, panel_top, text_width + pad * 2, text_height + pad * 2)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(15, 23, 42, 210))
                painter.drawRoundedRect(panel, 6, 6)
                painter.setPen(QtGui.QColor("#e2e8f0"))
                x = panel.left() + pad
                y = panel.top() + pad + metrics.ascent()
                for line in lines:
                    painter.drawText(QtCore.QPointF(x, y), line)
                    y += metrics.height() + 2
        if self._shader_error:
            painter.setPen(QtGui.QColor("#fca5a5"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "3D View: shader error")
        elif not self._scene_texture:
            painter.setPen(QtGui.QColor("#e2e8f0"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "3D View: no scene texture")
        self._draw_axis_gizmo(painter)
        painter.end()

    def _debug_status_lines(self, include_paths: bool = False) -> List[str]:
        lines = ["3D View Debug"]
        lines.append(f"Viewport: {self.width()}x{self.height()}")
        lines.append(f"QOpenGLWidget: {'OK' if QOpenGLWidget is not None else 'missing'}")
        lines.append(f"GL context: {'OK' if hasattr(self, '_gl') else 'missing'}")
        if QOpenGLShaderProgram is None:
            lines.append("Shaders: unavailable")
        elif self._shader_error:
            lines.append("Shaders: error")
        elif self._quad_program and self._mesh_program:
            lines.append("Shaders: OK")
        else:
            lines.append("Shaders: init")
        lines.append(f"QOpenGLTexture: {'OK' if QOpenGLTexture is not None else 'missing'}")
        lines.append(f"QOpenGLBuffer: {'OK' if QOpenGLBuffer is not None else 'missing'}")
        lines.append(f"VAO: {'OK' if self._vao is not None else 'none'}")
        lines.append(f"Mipmaps: {'on' if self._mipmaps_enabled else 'off'}")
        if self._scene_texture is not None:
            lines.append("Scene tex: ready")
        else:
            lines.append("Scene tex: none")
        lines.append(f"Meshes: {len(self._meshes)}")
        lines.append(f"Model scale: {self._model_scale_multiplier:.2f}x")
        mesh_paths = []
        for meta in self._mesh_meta.values():
            path_val = meta.get("path")
            if path_val:
                mesh_paths.append(str(path_val))
        if mesh_paths:
            names = [Path(p).name for p in mesh_paths if p]
            if names:
                shown = ", ".join(names[:3])
                if len(names) > 3:
                    shown += f" +{len(names) - 3}"
                lines.append(f"Mesh files: {shown}")
        if include_paths and mesh_paths:
            lines.append("Mesh paths: " + "; ".join(mesh_paths))
        lines.append(f"Scene content: {'blank' if self._scene_content_blank else 'ok'}")
        if self._grid_count:
            lines.append(f"Grid lines: {self._grid_count}")
        rect = QtCore.QRectF(self._scene_src_rect)
        if not rect.isNull():
            lines.append(f"Scene rect: {int(rect.width())}x{int(rect.height())}")
        return lines

    def _rotate_vec(self, x: float, y: float, z: float):
        cy = math.cos(self._cam_yaw)
        sy = math.sin(self._cam_yaw)
        cp = math.cos(self._cam_pitch)
        sp = math.sin(self._cam_pitch)
        x1 = x * cy + z * sy
        z1 = -x * sy + z * cy
        y1 = y
        y2 = y1 * cp - z1 * sp
        z2 = y1 * sp + z1 * cp
        x2 = x1
        return x2, y2, z2

    def _draw_axis_gizmo(self, p: QtGui.QPainter) -> None:
        vp = self.rect()
        if vp.isNull():
            return
        size = 58.0
        margin = 14.0
        origin = QtCore.QPointF(vp.right() - margin - size * 0.5, vp.top() + margin + size * 0.5)
        radius = size * 0.45

        axes = (
            ("X", QtGui.QColor("#ef4444"), (1.0, 0.0, 0.0)),
            ("Y", QtGui.QColor("#22c55e"), (0.0, 1.0, 0.0)),
            ("Z", QtGui.QColor("#3b82f6"), (0.0, 0.0, 1.0)),
        )
        projected = []
        max_len = 0.0
        for label, color, vec in axes:
            x2, y2, z2 = self._rotate_vec(vec[0], vec[1], vec[2])
            length = math.hypot(x2, y2)
            if length > max_len:
                max_len = length
            projected.append((label, color, z2, x2, y2))
        if max_len <= 1e-6:
            return
        norm = radius / max_len
        for label, color, z2, vx, vy in projected:
            v2 = QtCore.QPointF(vx * norm, -vy * norm)
            pen = QtGui.QPen(color, 2.2)
            if z2 < 0.0:
                faded = QtGui.QColor(color)
                faded.setAlpha(140)
                pen.setColor(faded)
            p.setPen(pen)
            p.drawLine(origin, origin + v2)
            p.setPen(QtGui.QPen(color))
            p.drawText(origin + v2 + QtCore.QPointF(4.0, -2.0), label)
        p.setPen(QtGui.QPen(QtGui.QColor("#e2e8f0")))
        p.setBrush(QtGui.QBrush(QtGui.QColor("#0f172a")))
        p.drawEllipse(origin, 3.2, 3.2)

    def _reset_camera(self) -> None:
        bounds = self._mesh_bounds_world()
        if bounds is not None:
            min_x, min_y, min_z, max_x, max_y, max_z = bounds
            self._cam_target = QtCore.QPointF((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
            extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)
        else:
            rect = QtCore.QRectF(self._scene_src_rect)
            if rect.isNull():
                self._cam_target = QtCore.QPointF(0.0, 0.0)
                extent = 1000.0
            else:
                self._cam_target = rect.center()
                extent = max(rect.width(), rect.height(), 1.0)
        if self._scene_content_blank:
            self._cam_target = QtCore.QPointF(0.0, 0.0)
            extent = 1000.0
        self._cam_yaw = 0.45
        self._cam_pitch = -0.35
        half_extent = extent * 0.5
        fov_rad = math.radians(self._fov_deg)
        fit_dist = half_extent / max(1e-3, math.tan(fov_rad * 0.5))
        self._cam_dist = max(1200.0, fit_dist * 2.0)
        self._cam_focal = max(800.0, self.height() * 0.5 / max(1e-3, math.tan(fov_rad * 0.5)))
        self._debug_mesh_scale_base = max(12.0, min(60.0, extent * 0.02))
        self._debug_mesh_scale = self._debug_mesh_scale_base * self._model_scale_multiplier
        self._debug_mesh_center = QtCore.QPointF(self._cam_target)
        self._grid_center = QtCore.QPointF(self._cam_target)
        self._update_grid(extent)

    def _debug_cube_vertices(self) -> List[float]:
        return [
            # front
            -0.5, -0.5,  0.5,  0.5, -0.5,  0.5,  0.5,  0.5,  0.5,
            -0.5, -0.5,  0.5,  0.5,  0.5,  0.5, -0.5,  0.5,  0.5,
            # back
             0.5, -0.5, -0.5, -0.5, -0.5, -0.5, -0.5,  0.5, -0.5,
             0.5, -0.5, -0.5, -0.5,  0.5, -0.5,  0.5,  0.5, -0.5,
            # left
            -0.5, -0.5, -0.5, -0.5, -0.5,  0.5, -0.5,  0.5,  0.5,
            -0.5, -0.5, -0.5, -0.5,  0.5,  0.5, -0.5,  0.5, -0.5,
            # right
             0.5, -0.5,  0.5,  0.5, -0.5, -0.5,  0.5,  0.5, -0.5,
             0.5, -0.5,  0.5,  0.5,  0.5, -0.5,  0.5,  0.5,  0.5,
            # top
            -0.5,  0.5,  0.5,  0.5,  0.5,  0.5,  0.5,  0.5, -0.5,
            -0.5,  0.5,  0.5,  0.5,  0.5, -0.5, -0.5,  0.5, -0.5,
            # bottom
            -0.5, -0.5, -0.5,  0.5, -0.5, -0.5,  0.5, -0.5,  0.5,
            -0.5, -0.5, -0.5,  0.5, -0.5,  0.5, -0.5, -0.5,  0.5,
        ]

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._orbit_dragging = True
            self._orbit_last_pos = e.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == QtCore.Qt.MiddleButton:
            self._pan_dragging = True
            self._pan_last_pos = e.pos()
            self.setCursor(QtCore.Qt.OpenHandCursor)
            e.accept()
            return
        if e.button() == QtCore.Qt.RightButton:
            self._dolly_dragging = True
            self._dolly_press_pos = e.pos()
            self._dolly_start_dist = float(self._cam_dist)
            self.setCursor(QtCore.Qt.SizeVerCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._orbit_dragging and self._orbit_last_pos is not None:
            if not (e.buttons() & QtCore.Qt.LeftButton):
                self._orbit_dragging = False
                self._orbit_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            delta = e.pos() - self._orbit_last_pos
            self._orbit_last_pos = e.pos()
            self._cam_yaw -= float(delta.x()) * self._orbit_sensitivity
            self._cam_pitch -= float(delta.y()) * self._orbit_sensitivity
            self._cam_pitch = max(-1.45, min(1.45, self._cam_pitch))
            self.update()
            e.accept()
            return
        if self._pan_dragging and self._pan_last_pos is not None:
            if not (e.buttons() & QtCore.Qt.MiddleButton):
                self._pan_dragging = False
                self._pan_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            delta = e.pos() - self._pan_last_pos
            self._pan_last_pos = e.pos()
            fov_rad = math.radians(self._fov_deg)
            scale = (2.0 * self._cam_dist * math.tan(fov_rad * 0.5)) / max(1.0, self.height())
            self._cam_target = QtCore.QPointF(
                self._cam_target.x() - float(delta.x()) * scale,
                self._cam_target.y() - float(delta.y()) * scale,
            )
            self.update()
            e.accept()
            return
        if self._dolly_dragging and self._dolly_press_pos is not None:
            if not (e.buttons() & QtCore.Qt.RightButton):
                self._dolly_dragging = False
                self._dolly_press_pos = None
                self._dolly_start_dist = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            dx = e.pos().x() - self._dolly_press_pos.x()
            dy = e.pos().y() - self._dolly_press_pos.y()
            distance = dy - dx
            exponent = abs(distance) / self._drag_divisor
            base = self._zoom_multiplier
            factor = base ** (-exponent) if distance > 0 else base ** (exponent)
            target = (self._dolly_start_dist or self._cam_dist) / factor
            self._cam_dist = max(self._min_cam_dist, min(self._max_cam_dist, target))
            self.update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._orbit_dragging = False
            self._orbit_last_pos = None
        if e.button() == QtCore.Qt.MiddleButton:
            self._pan_dragging = False
            self._pan_last_pos = None
        if e.button() == QtCore.Qt.RightButton:
            self._dolly_dragging = False
            self._dolly_press_pos = None
            self._dolly_start_dist = None
        self.setCursor(QtCore.Qt.ArrowCursor)
        super().mouseReleaseEvent(e)
