from __future__ import annotations

import os
import random
import time
import math
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec

SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb"}
PREVIEW_SIZE = 72
TEXTURE_SIZE = 256
CHECKER_CELLS = 8
DEFAULT_PATTERN = "checkerboard"
PATTERN_OPTIONS = [
    ("Checkerboard", "checkerboard"),
    ("Matrix Rain", "matrix_rain"),
]
PATTERN_LABELS = {key: label for label, key in PATTERN_OPTIONS}
TILING_MIN = 1
TILING_MAX = 100
PACK_MIN = 1
PACK_MAX = 100
RESOLUTION_OPTIONS = [256, 512, 1024]

MATRIX_GLYPHS = (
    "ｦｧｨｩｪｫｬｭｮｯｰ"
    "ｱｲｳｴｵ"
    "ｶｷｸｹｺ"
    "ｻｼｽｾｿ"
    "ﾀﾁﾂﾃﾄ"
    "ﾅﾆﾇﾈﾉ"
    "ﾊﾋﾌﾍﾎ"
    "ﾏﾐﾑﾒﾓ"
    "ﾔﾕﾖ"
    "ﾗﾘﾙﾚﾛ"
    "ﾜﾝ"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
)

GLYPH_ATLAS_COLS = 16
GLYPH_ATLAS_CELL = 32
_GLYPH_ATLAS = None
_GLYPH_ATLAS_GRID = None


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


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


def _build_glyph_atlas():
    glyphs = list(MATRIX_GLYPHS)
    cols = GLYPH_ATLAS_COLS
    rows = max(1, int(math.ceil(len(glyphs) / float(cols))))
    cell = GLYPH_ATLAS_CELL
    fmt = QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32
    img = QtGui.QImage(cols * cell, rows * cell, fmt)
    img.fill(QtGui.QColor(0, 0, 0, 255))
    painter = QtGui.QPainter(img)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    font = QtGui.QFont("Consolas")
    font.setStyleHint(QtGui.QFont.Monospace)
    font.setPixelSize(int(cell * 0.78))
    painter.setFont(font)
    painter.setPen(QtGui.QColor("#f1f5f9"))
    for idx, ch in enumerate(glyphs):
        x = (idx % cols) * cell
        y = (idx // cols) * cell
        rect = QtCore.QRectF(x, y, cell, cell)
        painter.drawText(rect, QtCore.Qt.AlignCenter, ch)
    painter.end()
    return img, (cols, rows)


def _get_glyph_atlas():
    global _GLYPH_ATLAS, _GLYPH_ATLAS_GRID
    if _GLYPH_ATLAS is None or _GLYPH_ATLAS_GRID is None:
        _GLYPH_ATLAS, _GLYPH_ATLAS_GRID = _build_glyph_atlas()
    return _GLYPH_ATLAS, _GLYPH_ATLAS_GRID


def build_ports(node_item) -> None:
    _ensure_param(node_item, "pattern", DEFAULT_PATTERN)
    _ensure_param(node_item, "tiling", "1")
    _ensure_param(node_item, "pack_x", "1")
    _ensure_param(node_item, "pack_y", "1")
    _ensure_param(node_item, "resolution", str(TEXTURE_SIZE))
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _resolve_input_path(node_item) -> str:
    model = getattr(node_item, "model", None)
    sc = node_item.scene()

    def _path_from_item(item, depth=0, visited=None) -> str:
        if item is None or depth > 8:
            return ""
        if visited is None:
            visited = set()
        if item in visited:
            return ""
        visited.add(item)

        m = getattr(item, "model", None)
        if m is None:
            return ""
        kind = (getattr(m, "kind", "") or "").strip().lower()
        if kind == "switch" and sc is not None:
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _path_from_item(getattr(edges[0], "src", None), depth + 1, visited)
        return _param_value(m, "path")

    if sc is not None:
        try:
            in_edges = list(sc._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(sc._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path"}:
                chosen = edge
                break
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is not None:
            src_item = getattr(chosen, "src", None)
            path = _path_from_item(src_item, 0, set())
            if path:
                return path

    if model is not None:
        return _param_value(model, "source") or _param_value(model, "path")
    return ""


def _resolve_input_item(node_item):
    model = getattr(node_item, "model", None)
    sc = node_item.scene()

    def _trace(item, depth=0, visited=None):
        if item is None or depth > 8:
            return None, "", ""
        if visited is None:
            visited = set()
        if item in visited:
            return None, "", ""
        visited.add(item)

        m = getattr(item, "model", None)
        if m is None:
            return None, "", ""
        kind = (getattr(m, "kind", "") or "").strip().lower()
        if kind == "switch" and sc is not None:
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _trace(getattr(edges[0], "src", None), depth + 1, visited)
        path = _param_value(m, "path")
        return item, kind, path

    if sc is not None:
        try:
            in_edges = list(sc._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(sc._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path"}:
                chosen = edge
                break
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is not None:
            src_item = getattr(chosen, "src", None)
            return _trace(src_item, 0, set())

    if model is not None:
        return None, "", _param_value(model, "source") or _param_value(model, "path")
    return None, "", ""


def _resolve_window(node_item):
    scene = node_item.scene()
    if scene is not None:
        try:
            views = scene.views()
            if views:
                return views[0].window()
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    try:
        aw = QtWidgets.QApplication.activeWindow()
        if aw is not None and aw.isWindow():
            return aw
    except Exception:
        pass
    return None


class CheckerboardGenerator:
    def __init__(self, size: int = TEXTURE_SIZE, cells: int = CHECKER_CELLS):
        self.size = int(size)
        self.base_cells = max(2, int(cells) if cells else 8)
        self.cells_x = int(self.base_cells)
        self.cells_y = int(self.base_cells)
        self._density = 1
        self._pack_x = 1
        self._pack_y = 1
        self._time = 0.0
        self._phase = -1
        self._image: Optional[QtGui.QImage] = None
        self._revision = 0
        self._last_frame_id: Optional[int] = None

    def ensure_image(self) -> QtGui.QImage:
        if self._image is None:
            self._phase = 0
            self._image = self._build_image(0)
            self._revision += 1
        return self._image

    def advance(self, dt: float, frame_id: Optional[int] = None) -> bool:
        if frame_id is not None and self._last_frame_id == frame_id:
            return False
        if frame_id is not None:
            self._last_frame_id = frame_id
        if dt <= 0.0:
            return False
        self._time += float(dt)
        phase = int(self._time) % 2
        if phase != self._phase or self._image is None:
            self._phase = phase
            self._image = self._build_image(phase)
            self._revision += 1
            return True
        return False

    @property
    def revision(self) -> int:
        return int(self._revision)

    def image(self) -> QtGui.QImage:
        return self.ensure_image()

    def set_density(self, density: int) -> None:
        density = max(1, int(density))
        if density == self._density:
            return
        self._density = density
        self._recompute_cells()

    def set_pack(self, pack_x: int, pack_y: int) -> None:
        pack_x = max(1, int(pack_x))
        pack_y = max(1, int(pack_y))
        if pack_x == self._pack_x and pack_y == self._pack_y:
            return
        self._pack_x = pack_x
        self._pack_y = pack_y
        self._recompute_cells()

    def set_size(self, size: int) -> None:
        size = max(16, int(size))
        if size == self.size:
            return
        self.size = size
        self._rebuild()

    def _recompute_cells(self) -> None:
        new_x = max(2, int(self.base_cells * self._density * self._pack_x))
        new_y = max(2, int(self.base_cells * self._density * self._pack_y))
        if new_x == self.cells_x and new_y == self.cells_y:
            return
        self.cells_x = new_x
        self.cells_y = new_y
        self._rebuild()

    def _rebuild(self) -> None:
        phase = self._phase if self._phase >= 0 else 0
        self._phase = phase
        self._image = self._build_image(phase)
        self._revision += 1

    def _build_image(self, phase: int) -> QtGui.QImage:
        size = max(16, int(self.size))
        cells_x = max(2, int(self.cells_x))
        cells_y = max(2, int(self.cells_y))
        cell_w = max(1, size // cells_x)
        cell_h = max(1, size // cells_y)

        if phase % 2 == 0:
            c0 = QtGui.QColor("#0f172a")
            c1 = QtGui.QColor("#e2e8f0")
        else:
            c0 = QtGui.QColor("#1d4ed8")
            c1 = QtGui.QColor("#f59e0b")

        fmt = QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32
        img = QtGui.QImage(size, size, fmt)
        painter = QtGui.QPainter(img)
        for y in range(0, size, cell_h):
            for x in range(0, size, cell_w):
                color = c0 if ((x // cell_w) + (y // cell_h)) % 2 == 0 else c1
                painter.fillRect(x, y, cell_w, cell_h, color)
        painter.end()
        return img


class MatrixRainGenerator:
    def __init__(self, size: int = TEXTURE_SIZE, cell: int = 12):
        self.size = int(size)
        self.base_cell = max(6, int(cell))
        self.cell_x = int(self.base_cell)
        self.cell_y = int(self.base_cell)
        self._density = 1
        self._pack_x = 1
        self._pack_y = 1
        self._image: Optional[QtGui.QImage] = None
        self._revision = 0
        self._last_frame_id: Optional[int] = None
        self._rng = random.Random()
        self._cols = []
        self._init_columns()

    def _init_columns(self) -> None:
        self._cols = []
        count = max(6, int(self.size // max(1, self.cell_x)))
        for i in range(count):
            self._cols.append(self._new_column(i))

    def _new_column(self, idx: int) -> dict:
        return {
            "x": idx * self.cell_x,
            "y": self._rng.uniform(-self.size, self.size),
            "speed": self._rng.uniform(6.0, 18.0),
            "trail": self._rng.randint(8, 16),
            "glyphs": [self._random_glyph() for _ in range(20)],
        }

    def _random_glyph(self) -> str:
        return self._rng.choice(MATRIX_GLYPHS)

    def advance(self, dt: float, frame_id: Optional[int] = None) -> bool:
        if frame_id is not None and self._last_frame_id == frame_id:
            return False
        if frame_id is not None:
            self._last_frame_id = frame_id
        if dt <= 0.0:
            return False
        height = self.size
        cell_y = self.cell_y
        for idx, col in enumerate(self._cols):
            col["y"] = float(col.get("y", 0.0)) + float(col.get("speed", 10.0)) * cell_y * dt
            trail = int(col.get("trail", 12))
            if col["y"] - (trail * cell_y) > height + cell_y:
                self._cols[idx] = self._new_column(idx)
                continue
            if self._rng.random() < 0.45:
                glyphs = col.get("glyphs") or []
                if glyphs:
                    glyphs[self._rng.randrange(len(glyphs))] = self._random_glyph()
        self._image = self._build_image()
        self._revision += 1
        return True

    @property
    def revision(self) -> int:
        return int(self._revision)

    def image(self) -> QtGui.QImage:
        if self._image is None:
            self._image = self._build_image()
            self._revision += 1
        return self._image

    def set_density(self, density: int) -> None:
        density = max(1, int(density))
        if density == self._density:
            return
        self._density = density
        self._recompute_cells()

    def set_pack(self, pack_x: int, pack_y: int) -> None:
        pack_x = max(1, int(pack_x))
        pack_y = max(1, int(pack_y))
        if pack_x == self._pack_x and pack_y == self._pack_y:
            return
        self._pack_x = pack_x
        self._pack_y = pack_y
        self._recompute_cells()

    def set_size(self, size: int) -> None:
        size = max(16, int(size))
        if size == self.size:
            return
        self.size = size
        self._init_columns()
        self._mark_dirty()

    def _recompute_cells(self) -> None:
        new_x = max(1, int(self.base_cell / max(1, self._density * self._pack_x)))
        new_y = max(1, int(self.base_cell / max(1, self._density * self._pack_y)))
        if new_x == self.cell_x and new_y == self.cell_y:
            return
        self.cell_x = new_x
        self.cell_y = new_y
        self._init_columns()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._image = None
        self._revision += 1

    def _build_image(self) -> QtGui.QImage:
        size = max(16, int(self.size))
        cell_x = max(1, int(self.cell_x))
        cell_y = max(1, int(self.cell_y))
        fmt = QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32
        img = QtGui.QImage(size, size, fmt)
        img.fill(QtGui.QColor("#050b07"))
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setPixelSize(int(min(cell_x, cell_y) * 0.9))
        painter.setFont(font)

        for col in self._cols:
            x = int(col.get("x", 0))
            head_y = float(col.get("y", 0.0))
            trail = int(col.get("trail", 12))
            glyphs = col.get("glyphs") or []
            for t in range(trail):
                y = head_y - t * cell_y
                if y < -cell_y or y > size:
                    continue
                if t == 0:
                    color = QtGui.QColor("#bbf7d0")
                    color.setAlpha(230)
                else:
                    alpha = max(0.05, 1.0 - (t / max(trail, 1)))
                    color = QtGui.QColor("#22c55e")
                    color.setAlpha(int(200 * alpha))
                painter.setPen(color)
                ch = glyphs[t % len(glyphs)] if glyphs else self._random_glyph()
                mirror = self._rng.random() < 0.22
                if mirror:
                    painter.save()
                    painter.translate(x + cell_x, y)
                    painter.scale(-1.0, 1.0)
                    rect = QtCore.QRectF(0, 0, cell_x, cell_y)
                    painter.drawText(rect, QtCore.Qt.AlignCenter, ch)
                    painter.restore()
                else:
                    rect = QtCore.QRectF(x, y, cell_x, cell_y)
                    painter.drawText(rect, QtCore.Qt.AlignCenter, ch)
        painter.end()
        return img


class TextureProProvider:
    def __init__(self, size: int = TEXTURE_SIZE, density: int = 1):
        self._size = max(16, int(size))
        self._density = max(1, int(density))
        self._pack_x = 1
        self._pack_y = 1
        self._gpu_seed = random.Random().randint(1, 1_000_000)
        self._generators = {
            "checkerboard": CheckerboardGenerator(size=self._size),
            "matrix_rain": MatrixRainGenerator(size=self._size),
        }
        self._mode = DEFAULT_PATTERN
        self._revision = 0
        self._apply_size(self._size)
        self._apply_density(self._density)
        self._apply_pack(self._pack_x, self._pack_y)
        self._last_gen_rev = self._generators[self._mode].revision

    def set_mode(self, mode: str) -> None:
        mode = (mode or "").strip().lower()
        if mode not in self._generators:
            mode = DEFAULT_PATTERN
        if mode == self._mode:
            return
        self._mode = mode
        self._revision += 1
        self._last_gen_rev = self._generators[self._mode].revision

    def mode(self) -> str:
        return self._mode

    def set_density(self, density: int) -> None:
        density = max(1, int(density))
        if density == self._density:
            return
        self._density = density
        self._apply_density(density)
        self._revision += 1
        self._last_gen_rev = self._generators[self._mode].revision

    def set_resolution(self, size: int) -> None:
        size = max(16, int(size))
        if size == self._size:
            return
        self._size = size
        self._apply_size(size)
        self._revision += 1
        self._last_gen_rev = self._generators[self._mode].revision

    def set_pack(self, pack_x: int, pack_y: int) -> None:
        pack_x = max(1, int(pack_x))
        pack_y = max(1, int(pack_y))
        if pack_x == self._pack_x and pack_y == self._pack_y:
            return
        self._pack_x = pack_x
        self._pack_y = pack_y
        self._apply_pack(pack_x, pack_y)
        self._revision += 1
        self._last_gen_rev = self._generators[self._mode].revision

    def advance(self, dt: float, frame_id: Optional[int] = None) -> bool:
        gen = self._generators[self._mode]
        changed = gen.advance(dt, frame_id)
        gen_rev = gen.revision
        if changed or gen_rev != self._last_gen_rev:
            self._last_gen_rev = gen_rev
            self._revision += 1
            return True
        return False

    def image(self) -> QtGui.QImage:
        return self._generators[self._mode].image()

    def gpu_state(self) -> dict:
        atlas, grid = _get_glyph_atlas()
        return {
            "mode": self._mode,
            "tiling": int(self._density),
            "pack_x": int(self._pack_x),
            "pack_y": int(self._pack_y),
            "seed": float(self._gpu_seed),
            "glyph_atlas": atlas,
            "glyph_grid": grid,
        }

    def _apply_density(self, density: int) -> None:
        for gen in self._generators.values():
            fn = getattr(gen, "set_density", None)
            if callable(fn):
                try:
                    fn(density)
                except Exception:
                    pass

    def _apply_size(self, size: int) -> None:
        for gen in self._generators.values():
            fn = getattr(gen, "set_size", None)
            if callable(fn):
                try:
                    fn(size)
                except Exception:
                    pass

    def _apply_pack(self, pack_x: int, pack_y: int) -> None:
        for gen in self._generators.values():
            fn = getattr(gen, "set_pack", None)
            if callable(fn):
                try:
                    fn(pack_x, pack_y)
                except Exception:
                    pass

    @property
    def revision(self) -> int:
        return int(self._revision)


def _get_provider(node_item) -> TextureProProvider:
    model = getattr(node_item, "model", None)
    provider = getattr(model, "_texture_pro_provider", None) if model is not None else None
    if not isinstance(provider, TextureProProvider):
        provider = TextureProProvider()
        if model is not None:
            try:
                setattr(model, "_texture_pro_provider", provider)
            except Exception:
                pass
    return provider


class TextureProWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False
        self._provider = _get_provider(node_item)
        self._last_rev = -1
        self._frame_hooked = False
        self._last_frame_ts = 0.0
        self._input_item = None
        self._input_kind = ""
        self._scene_input = False

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self._preview = QtWidgets.QLabel()
        self._preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setStyleSheet(
            "QLabel{background:#0f172a;border:1px solid #334155;border-radius:4px;}"
        )
        layout.addWidget(self._preview, 0)

        right = QtWidgets.QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

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
        for label_text, key in PATTERN_OPTIONS:
            self._combo.addItem(label_text, key)
        self._combo.currentIndexChanged.connect(self._on_pattern_changed)
        right.addWidget(self._combo, 0)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)

        self._tiling = QtWidgets.QSpinBox()
        self._tiling.setRange(TILING_MIN, TILING_MAX)
        self._tiling.setSingleStep(1)
        self._tiling.setFixedWidth(48)
        self._tiling.setAlignment(QtCore.Qt.AlignRight)
        self._tiling.setSuffix("x")
        self._tiling.setToolTip("Tile density")
        self._tiling.setStyleSheet(
            "QSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 4px;}"
            "QSpinBox::up-button{width:10px;border:none;}"
            "QSpinBox::down-button{width:10px;border:none;}"
        )
        self._tiling.valueChanged.connect(self._on_tiling_changed)
        settings_row.addWidget(self._tiling, 0)

        settings_row.addStretch(1)

        self._res_combo = QtWidgets.QComboBox()
        self._res_combo.setFixedWidth(70)
        self._res_combo.setToolTip("Texture resolution")
        self._res_combo.setStyleSheet(
            "QComboBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 6px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{"
            "  background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "  outline:0px;}"
            "QComboBox QAbstractItemView::item{padding:6px 10px;}"
            "QComboBox QAbstractItemView::item:hover{background:#1f2937;}"
            "QComboBox QAbstractItemView::item:selected{background:#22c55e;color:#0f1216;}"
        )
        for size in RESOLUTION_OPTIONS:
            self._res_combo.addItem(str(size), int(size))
        self._res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        settings_row.addWidget(self._res_combo, 0)

        right.addLayout(settings_row, 0)

        pack_row = QtWidgets.QHBoxLayout()
        pack_row.setContentsMargins(0, 0, 0, 0)
        pack_row.setSpacing(6)

        pack_row.addStretch(1)

        pack_x_label = QtWidgets.QLabel("X")
        pack_x_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        pack_row.addWidget(pack_x_label, 0)

        self._pack_x = QtWidgets.QSpinBox()
        self._pack_x.setRange(PACK_MIN, PACK_MAX)
        self._pack_x.setSingleStep(1)
        self._pack_x.setFixedWidth(48)
        self._pack_x.setAlignment(QtCore.Qt.AlignRight)
        self._pack_x.setToolTip("Horizontal packing")
        self._pack_x.setStyleSheet(
            "QSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 4px;}"
            "QSpinBox::up-button{width:10px;border:none;}"
            "QSpinBox::down-button{width:10px;border:none;}"
        )
        self._pack_x.valueChanged.connect(self._on_pack_changed)
        pack_row.addWidget(self._pack_x, 0)

        pack_y_label = QtWidgets.QLabel("Y")
        pack_y_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        pack_row.addWidget(pack_y_label, 0)

        self._pack_y = QtWidgets.QSpinBox()
        self._pack_y.setRange(PACK_MIN, PACK_MAX)
        self._pack_y.setSingleStep(1)
        self._pack_y.setFixedWidth(48)
        self._pack_y.setAlignment(QtCore.Qt.AlignRight)
        self._pack_y.setToolTip("Vertical packing")
        self._pack_y.setStyleSheet(
            "QSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 4px;}"
            "QSpinBox::up-button{width:10px;border:none;}"
            "QSpinBox::down-button{width:10px;border:none;}"
        )
        self._pack_y.valueChanged.connect(self._on_pack_changed)
        pack_row.addWidget(self._pack_y, 0)

        right.addLayout(pack_row, 0)
        right.addStretch(1)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        right.addWidget(self._view_btn, 0)

        layout.addLayout(right, 1)

        self._ensure_scene()
        self._refresh_preview()

        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.setInterval(60)
        self._frame_timer.timeout.connect(self._on_timer_tick)
        self._frame_timer.start()

        QtCore.QTimer.singleShot(0, self._hook_viewport)
        QtCore.QTimer.singleShot(0, self._update_inputs)

    def sizeHint(self):
        return QtCore.QSize(220, PREVIEW_SIZE + 30)

    def _is_selected(self) -> bool:
        sc = None
        try:
            sc = self._node_item.scene()
        except Exception:
            sc = None
        if sc is not None and hasattr(sc, "_active_node_item"):
            try:
                return getattr(sc, "_active_node_item", None) is self._node_item
            except Exception:
                return False
        try:
            return bool(self._node_item.isSelected())
        except Exception:
            return False

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._schedule_update)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(lambda *_: self._schedule_update())
            except Exception:
                pass
        self._scene_connected = True

    def _schedule_update(self):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(60, self._update_inputs)

    def _set_param(self, name: str, value: str, notify_scene: bool = True):
        try:
            current = ""
            for p in (getattr(self._node_item.model, "params", None) or []):
                if (p.get("name") or "").strip().lower() == (name or "").strip().lower():
                    current = p.get("value", "") or ""
                    break
            if current == value:
                return
        except Exception:
            pass
        try:
            self._node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
        except Exception:
            pass

    def _set_combo_value(self, pattern: str) -> None:
        try:
            idx = self._combo.findData(pattern)
        except Exception:
            idx = -1
        if idx >= 0 and self._combo.currentIndex() != idx:
            try:
                self._combo.blockSignals(True)
                self._combo.setCurrentIndex(idx)
            finally:
                self._combo.blockSignals(False)

    def _apply_pattern(self, pattern: str, notify_scene: bool = False) -> None:
        key = (pattern or "").strip().lower()
        if key not in PATTERN_LABELS:
            key = DEFAULT_PATTERN
        try:
            if hasattr(self._provider, "set_mode"):
                self._provider.set_mode(key)
        except Exception:
            pass
        self._set_param("pattern", key, notify_scene=notify_scene)
        self._set_combo_value(key)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _set_tiling_value(self, value: int) -> None:
        value = max(TILING_MIN, min(TILING_MAX, int(value)))
        if self._tiling.value() == value:
            return
        try:
            self._tiling.blockSignals(True)
            self._tiling.setValue(value)
        finally:
            self._tiling.blockSignals(False)

    def _apply_tiling(self, value: int, notify_scene: bool = False) -> None:
        value = max(TILING_MIN, min(TILING_MAX, int(value)))
        try:
            if hasattr(self._provider, "set_density"):
                self._provider.set_density(value)
        except Exception:
            pass
        self._set_param("tiling", str(value), notify_scene=notify_scene)
        self._set_tiling_value(value)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_tiling_changed(self):
        value = int(self._tiling.value())
        self._apply_tiling(value, notify_scene=True)

    def _set_pack_values(self, pack_x: int, pack_y: int) -> None:
        pack_x = max(PACK_MIN, min(PACK_MAX, int(pack_x)))
        pack_y = max(PACK_MIN, min(PACK_MAX, int(pack_y)))
        try:
            self._pack_x.blockSignals(True)
            self._pack_x.setValue(pack_x)
        finally:
            self._pack_x.blockSignals(False)
        try:
            self._pack_y.blockSignals(True)
            self._pack_y.setValue(pack_y)
        finally:
            self._pack_y.blockSignals(False)

    def _apply_pack(self, pack_x: int, pack_y: int, notify_scene: bool = False) -> None:
        pack_x = max(PACK_MIN, min(PACK_MAX, int(pack_x)))
        pack_y = max(PACK_MIN, min(PACK_MAX, int(pack_y)))
        try:
            if hasattr(self._provider, "set_pack"):
                self._provider.set_pack(pack_x, pack_y)
        except Exception:
            pass
        self._set_param("pack_x", str(pack_x), notify_scene=notify_scene)
        self._set_param("pack_y", str(pack_y), notify_scene=notify_scene)
        self._set_pack_values(pack_x, pack_y)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_pack_changed(self):
        pack_x = int(self._pack_x.value())
        pack_y = int(self._pack_y.value())
        self._apply_pack(pack_x, pack_y, notify_scene=True)

    def _set_resolution_value(self, value: int) -> None:
        try:
            idx = self._res_combo.findData(int(value))
        except Exception:
            idx = -1
        if idx >= 0 and self._res_combo.currentIndex() != idx:
            try:
                self._res_combo.blockSignals(True)
                self._res_combo.setCurrentIndex(idx)
            finally:
                self._res_combo.blockSignals(False)

    def _apply_resolution(self, value: int, notify_scene: bool = False) -> None:
        try:
            value = int(value)
        except Exception:
            value = TEXTURE_SIZE
        if value not in RESOLUTION_OPTIONS:
            value = RESOLUTION_OPTIONS[0]
        try:
            if hasattr(self._provider, "set_resolution"):
                self._provider.set_resolution(value)
        except Exception:
            pass
        self._set_param("resolution", str(value), notify_scene=notify_scene)
        self._set_resolution_value(value)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_resolution_changed(self):
        value = self._res_combo.currentData() or self._res_combo.currentText()
        self._apply_resolution(value, notify_scene=True)

    def _on_pattern_changed(self):
        key = self._combo.currentData() or self._combo.currentText()
        self._apply_pattern(str(key), notify_scene=True)

    def _update_inputs(self):
        self._pending = False
        self._ensure_scene()
        pattern = ""
        try:
            pattern = _param_value(self._node_item.model, "pattern").strip().lower()
        except Exception:
            pattern = ""
        if not pattern:
            pattern = DEFAULT_PATTERN
            self._set_param("pattern", pattern, notify_scene=False)
        self._apply_pattern(pattern, notify_scene=False)

        tiling = 1
        try:
            tiling = int(_param_value(self._node_item.model, "tiling") or 1)
        except Exception:
            tiling = 1
        tiling = max(TILING_MIN, min(TILING_MAX, int(tiling)))
        self._apply_tiling(tiling, notify_scene=False)

        pack_x = 1
        pack_y = 1
        try:
            pack_x = int(_param_value(self._node_item.model, "pack_x") or 1)
        except Exception:
            pack_x = 1
        try:
            pack_y = int(_param_value(self._node_item.model, "pack_y") or 1)
        except Exception:
            pack_y = 1
        pack_x = max(PACK_MIN, min(PACK_MAX, int(pack_x)))
        pack_y = max(PACK_MIN, min(PACK_MAX, int(pack_y)))
        self._apply_pack(pack_x, pack_y, notify_scene=False)

        resolution = TEXTURE_SIZE
        try:
            resolution = int(_param_value(self._node_item.model, "resolution") or TEXTURE_SIZE)
        except Exception:
            resolution = TEXTURE_SIZE
        if resolution not in RESOLUTION_OPTIONS:
            resolution = RESOLUTION_OPTIONS[0]
        self._apply_resolution(resolution, notify_scene=False)

        src_item, src_kind, src_path = _resolve_input_item(self._node_item)
        self._input_item = src_item
        self._input_kind = (src_kind or "").strip().lower()
        self._scene_input = self._input_kind in {"scene", "scene_assembly", "scene_outliner"}
        src_path = (src_path or "").strip()
        if not src_path:
            try:
                src_path = _param_value(self._node_item.model, "path").strip()
            except Exception:
                src_path = ""
        if src_path:
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=True)
        else:
            self._set_param("source", "", notify_scene=False)
            self._set_param("path", "", notify_scene=True)

        valid_mesh = bool(src_path) and os.path.exists(src_path) and Path(src_path).suffix.lower() in SUPPORTED_MESH_EXTS
        has_link = False
        try:
            sc = self._node_item.scene()
            if sc is not None:
                try:
                    in_edges = list(sc._ordered_in_edges(self._node_item))
                except Exception:
                    in_edges = list(sc._in_edges(self._node_item))
                has_link = bool(in_edges)
        except Exception:
            has_link = False

        enabled = bool(valid_mesh or has_link)
        self._view_btn.setEnabled(enabled)
        if not enabled:
            self._view_btn.setToolTip("Connect a mesh node.")
        elif not valid_mesh:
            self._view_btn.setToolTip("Waiting for mesh path.")
        else:
            if self._scene_input:
                self._view_btn.setToolTip("View textured scene")
            else:
                self._view_btn.setToolTip("View textured model")

    def _get_gl_view(self):
        win = _resolve_window(self._node_item)
        if win is not None:
            glv = getattr(win, "gl_view", None)
            if glv is not None:
                return glv
        return None

    def _hook_viewport(self):
        if self._frame_hooked:
            return
        glv = self._get_gl_view()
        if glv is None:
            return
        try:
            if hasattr(glv, "frameSwapped"):
                glv.frameSwapped.connect(self._on_frame_swapped)
                self._frame_hooked = True
        except Exception:
            self._frame_hooked = False

    def _on_frame_swapped(self):
        self._last_frame_ts = time.time()
        self._tick(from_view=True)

    def _on_timer_tick(self):
        if not self._frame_hooked:
            self._hook_viewport()
        if self._frame_hooked and (time.time() - self._last_frame_ts) < 0.5:
            return
        self._tick(from_view=False)

    def _view_fps(self, glv) -> float:
        fps = 0.0
        if glv is not None:
            try:
                fps = float(getattr(glv, "_fps", 0.0) or 0.0)
            except Exception:
                fps = 0.0
        if fps <= 1.0:
            fps = 60.0
        return fps

    def _provider_driven_by_view(self, glv) -> bool:
        if glv is None:
            return False
        try:
            if getattr(glv, "_mgl_proc_provider", None) is self._provider:
                return True
        except Exception:
            pass
        try:
            proc_map = getattr(glv, "_mgl_scene_proc_textures_by_owner", None)
            if isinstance(proc_map, dict):
                for entry in proc_map.values():
                    if entry.get("provider") is self._provider:
                        return True
        except Exception:
            pass
        return False

    def _tick(self, from_view: bool):
        glv = self._get_gl_view()
        fps = self._view_fps(glv)
        dt = 1.0 / max(fps, 1.0)
        selected = self._is_selected()
        if selected and not self._provider_driven_by_view(glv):
            try:
                frame_id = getattr(glv, "_mgl_frame_id", None) if from_view and glv is not None else None
                self._provider.advance(dt, frame_id=frame_id)
            except TypeError:
                try:
                    self._provider.advance(dt)
                except Exception:
                    pass
            except Exception:
                pass
        if selected:
            self._refresh_preview()
        else:
            if self._preview.pixmap() is None:
                self._refresh_preview(force=True)

    def _refresh_preview(self, force: bool = False):
        rev = None
        try:
            rev = int(getattr(self._provider, "revision", 0))
        except Exception:
            rev = None
        if not force and rev is not None and rev == self._last_rev:
            return
        try:
            img = self._provider.image()
        except Exception:
            img = None
        if img is None or img.isNull():
            return
        pix = QtGui.QPixmap.fromImage(img)
        pix = pix.scaled(
            self._preview.width(),
            self._preview.height(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self._preview.setPixmap(pix)
        if rev is not None:
            self._last_rev = rev

    def _on_view_clicked(self):
        if self._scene_input and self._input_item is not None:
            assets = []
            try:
                if hasattr(self._input_item, "_collect_scene_assets"):
                    assets = list(self._input_item._collect_scene_assets())
            except Exception:
                assets = []
            if not assets:
                QtWidgets.QMessageBox.warning(
                    _resolve_window(self._node_item) or self,
                    "Texture Pro",
                    "No valid scene assets connected.",
                )
                return
            for entry in assets:
                if not isinstance(entry, dict):
                    continue
                entry["texture_provider"] = self._provider
                entry["texture"] = ""
            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            if callable(handler):
                try:
                    handler(assets, frame=False)
                except TypeError:
                    try:
                        handler(assets)
                    except Exception:
                        pass
                except Exception:
                    pass
            return

        src_path = (_resolve_input_path(self._node_item) or "").strip()
        if not src_path or not os.path.exists(src_path):
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Texture Pro",
                "No valid input mesh connected.",
            )
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_3d_model", None) if win is not None else None
        if callable(handler):
            try:
                handler(src_path, None, frame=False)
            except TypeError:
                try:
                    handler(src_path, None)
                except Exception:
                    pass
            except Exception:
                pass
        glv = getattr(win, "gl_view", None) if win is not None else None
        if glv is not None and hasattr(glv, "set_procedural_texture_provider"):
            try:
                label = "Procedural"
                try:
                    mode = self._provider.mode() if hasattr(self._provider, "mode") else ""
                    label = PATTERN_LABELS.get(mode, label)
                except Exception:
                    label = "Procedural"
                glv.set_procedural_texture_provider(self._provider, label)
            except Exception:
                pass


def render_node_body(node_item, y_cursor: int) -> int:
    body = TextureProWidget(node_item)
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


TEXTURE_PRO_SPEC = Spec(
    stripe_color="#f97316",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
