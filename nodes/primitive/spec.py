from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

from nodes.core import Spec

PRIMITIVE_SHAPES = [
    ("Sphere", "sphere"),
    ("Plane", "plane"),
    ("Cube", "cube"),
    ("Cone", "cone"),
    ("Tube", "tube"),
    ("Torus", "torus"),
]


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "primitive"


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    scene_path = getattr(scene, "_filename", None) if scene is not None else None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass

    workflow_path = workflow_path or scene_path
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _primitive_dir(node_item=None) -> Path:
    base = _workflow_dir_for_node(node_item) if node_item is not None else None
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    prim_dir = base / "primitives"
    prim_dir.mkdir(parents=True, exist_ok=True)
    return prim_dir


def _primitive_path(node_item, node_name: str, shape: str) -> Path:
    safe = _sanitize_name(node_name)
    shape_key = _sanitize_name(shape)
    return _primitive_dir(node_item) / f"{safe}_{shape_key}.obj"


def _write_obj(
    path: Path,
    verts: list[tuple[float, float, float]],
    faces: list[list[int]],
    normals: list[tuple[float, float, float]] | None = None,
) -> None:
    lines = ["# EchoGraph primitive"]
    for x, y, z in verts:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    normals_list = [] if normals is None else list(normals)
    for nx, ny, nz in normals_list:
        lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")
    for face in faces:
        if not face or len(face) < 3:
            continue
        if normals_list and all(0 <= int(i) < len(normals_list) for i in face):
            idxs = " ".join(f"{int(i) + 1}//{int(i) + 1}" for i in face)
        else:
            idxs = " ".join(str(int(i) + 1) for i in face)
        lines.append(f"f {idxs}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _radial_normals(verts: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for x, y, z in verts:
        length = math.sqrt((x * x) + (y * y) + (z * z))
        if length > 1.0e-8:
            out.append((x / length, y / length, z / length))
        else:
            out.append((0.0, 1.0, 0.0))
    return out


def _cube(size: float = 1.0):
    s = size * 0.5
    verts = [
        (-s, -s, -s),
        (s, -s, -s),
        (s, s, -s),
        (-s, s, -s),
        (-s, -s, s),
        (s, -s, s),
        (s, s, s),
        (-s, s, s),
    ]
    faces = [
        [0, 3, 2, 1],  # back (-Z)
        [4, 5, 6, 7],  # front (+Z)
        [0, 4, 5, 1],  # bottom (-Y)
        [3, 2, 6, 7],  # top (+Y)
        [1, 2, 6, 5],  # right (+X)
        [0, 4, 7, 3],  # left (-X)
    ]
    return verts, faces


def _plane(size: float = 1.0):
    s = size * 0.5
    verts = [
        (-s, 0.0, -s),
        (s, 0.0, -s),
        (s, 0.0, s),
        (-s, 0.0, s),
    ]
    faces = [[0, 3, 2, 1]]
    return verts, faces


def _cone(radius: float = 0.5, height: float = 1.0, segments: int = 24):
    tip = (0.0, height * 0.5, 0.0)
    base_y = -height * 0.5
    verts = [tip]
    for i in range(segments):
        ang = 2.0 * math.pi * i / segments
        verts.append((radius * math.cos(ang), base_y, radius * math.sin(ang)))

    faces = []
    for i in range(segments):
        a = 1 + i
        b = 1 + ((i + 1) % segments)
        faces.append([0, a, b])

    # base polygon (reverse order for outward -Y normal)
    base_face = [1 + ((segments - 1 - i) % segments) for i in range(segments)]
    faces.append(base_face)
    return verts, faces


def _tube(radius: float = 0.5, height: float = 1.0, segments: int = 24):
    top_y = height * 0.5
    bot_y = -height * 0.5
    verts = []
    for i in range(segments):
        ang = 2.0 * math.pi * i / segments
        x = radius * math.cos(ang)
        z = radius * math.sin(ang)
        verts.append((x, top_y, z))
    for i in range(segments):
        ang = 2.0 * math.pi * i / segments
        x = radius * math.cos(ang)
        z = radius * math.sin(ang)
        verts.append((x, bot_y, z))

    faces = []
    for i in range(segments):
        a = i
        b = (i + 1) % segments
        c = segments + (i + 1) % segments
        d = segments + i
        faces.append([a, b, c, d])

    # caps as polygons (no triangulation)
    top_face = [i for i in range(segments)]
    bot_face = [segments + (segments - 1 - i) for i in range(segments)]
    faces.append(top_face)
    faces.append(bot_face)
    return verts, faces


def _sphere(radius: float = 0.5, segments: int = 24, rings: int = 12):
    verts = [(0.0, radius, 0.0)]  # top
    for i in range(1, rings):
        theta = math.pi * i / rings
        y = math.cos(theta) * radius
        r = math.sin(theta) * radius
        for j in range(segments):
            phi = 2.0 * math.pi * j / segments
            verts.append((r * math.cos(phi), y, r * math.sin(phi)))
    verts.append((0.0, -radius, 0.0))  # bottom

    faces: list[list[int]] = []
    for j in range(segments):
        a = 0
        b = 1 + j
        c = 1 + (j + 1) % segments
        faces.append([a, c, b])

    for i in range(1, rings - 1):
        ring_start = 1 + (i - 1) * segments
        next_start = ring_start + segments
        for j in range(segments):
            a = ring_start + j
            b = ring_start + (j + 1) % segments
            c = next_start + (j + 1) % segments
            d = next_start + j
            faces.append([a, b, c, d])

    bottom = len(verts) - 1
    last_ring = 1 + (rings - 2) * segments
    for j in range(segments):
        a = last_ring + j
        b = last_ring + (j + 1) % segments
        faces.append([a, b, bottom])

    return verts, faces


def _torus(major: float = 0.6, minor: float = 0.25, segments: int = 24, tube: int = 12):
    verts = []
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        ct = math.cos(theta)
        st = math.sin(theta)
        for j in range(tube):
            phi = 2.0 * math.pi * j / tube
            cp = math.cos(phi)
            sp = math.sin(phi)
            x = (major + minor * cp) * ct
            y = minor * sp
            z = (major + minor * cp) * st
            verts.append((x, y, z))

    faces: list[list[int]] = []
    for i in range(segments):
        for j in range(tube):
            a = i * tube + j
            b = ((i + 1) % segments) * tube + j
            c = ((i + 1) % segments) * tube + (j + 1) % tube
            d = i * tube + (j + 1) % tube
            faces.append([a, b, c, d])
    return verts, faces


def _build_primitive_mesh(shape: str):
    key = (shape or "").strip().lower()
    if key == "sphere":
        return _sphere()
    if key == "plane":
        return _plane()
    if key == "cube":
        return _cube()
    if key == "cone":
        return _cone()
    if key == "tube":
        return _tube()
    if key == "torus":
        return _torus()
    return _cube()


def _ensure_hidden_params(model, names: list[str]) -> None:
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    existing = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            existing = entry
            break
    if existing is None:
        existing = {"name": store_key, "value": ""}
        params.append(existing)

    raw = existing.get("value", "")
    cur = set()
    for part in str(raw).split(","):
        t = part.strip().lower()
        if t:
            cur.add(t)
    for name in names:
        if name:
            cur.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


class PrimitiveWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._shape = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._combo = QtWidgets.QComboBox()
        self._combo.setMinimumWidth(0)
        self._combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        try:
            lv = QtWidgets.QListView()
            lv.setMouseTracking(True)
            lv.setUniformItemSizes(True)
            self._combo.setView(lv)
        except Exception:
            pass
        self._combo.setStyleSheet(
            "QComboBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:2px 6px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{"
            "  background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "  outline:0px;}"
            "QComboBox QAbstractItemView::item{padding:6px 10px;}"
            "QComboBox QAbstractItemView::item:hover{background:#1f2937;}"
            "QComboBox QAbstractItemView::item:selected{background:#22c55e;color:#0f1216;}"
        )
        for label_text, key in PRIMITIVE_SHAPES:
            self._combo.addItem(label_text, key)
        layout.addWidget(self._combo, 1)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        model = getattr(self._node_item, "model", None)
        if model is not None:
            _ensure_hidden_params(model, ["primitive", "path"])

        current = ""
        if model is not None:
            for entry in (getattr(model, "params", None) or []):
                if (entry.get("name") or "").strip().lower() == "primitive":
                    current = (entry.get("value") or "").strip().lower()
                    break
        if not current:
            current = "cube"

        idx = max(0, self._combo.findData(current))
        self._combo.setCurrentIndex(idx)
        self._combo.currentIndexChanged.connect(self._on_shape_changed)
        self._apply_shape(current, notify_scene=False)

    def sizeHint(self):
        return QtCore.QSize(220, 32)

    def _apply_shape(self, shape: str, *, notify_scene: bool) -> None:
        shape = (shape or "").strip().lower() or "cube"
        if self._shape == shape and notify_scene:
            return
        self._shape = shape

        path = _primitive_path(self._node_item, getattr(self._node_item.model, "name", "primitive"), shape)
        try:
            verts, faces = _build_primitive_mesh(shape)
            if shape == "sphere":
                normals = _radial_normals(verts)
            elif shape == "plane":
                normals = [(0.0, 1.0, 0.0) for _ in verts]
            else:
                normals = None
            _write_obj(path, verts, faces, normals=normals)
        except Exception:
            pass

        try:
            self._node_item._set_param_value("primitive", shape, rebuild=False, notify_scene=notify_scene)
            self._node_item._set_param_value("path", str(path), rebuild=False, notify_scene=notify_scene)
        except Exception:
            pass

        try:
            self._view_btn.setEnabled(path.exists())
        except Exception:
            pass

    def _on_shape_changed(self):
        shape = self._combo.currentData() or self._combo.currentText()
        self._apply_shape(str(shape), notify_scene=True)

    def _on_view_clicked(self):
        try:
            path = ""
            for entry in (getattr(self._node_item.model, "params", None) or []):
                if (entry.get("name") or "").strip().lower() == "path":
                    path = (entry.get("value") or "").strip()
                    break
            if not path:
                return
            self._node_item._open_import_preview(path)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = PrimitiveWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    return y_cursor + h


PRIMITIVE_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
)
