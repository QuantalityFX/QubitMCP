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
SPEED_MIN = 0.0
SPEED_MAX = 10.0
SPEED_DEFAULT = 1.0
EMISSIVE_MIN = 0.0
EMISSIVE_MAX = 2.0
EMISSIVE_DEFAULT = 0.6
SOFTNESS_MIN = 0.0
SOFTNESS_MAX = 1.0
SOFTNESS_DEFAULT = 0.35
RESOLUTION_OPTIONS = [256, 512, 1024]
LIGHTING_DEFAULT = 1.0
PAN_DEFAULT = False
LIFE_MIN_LIMIT = 1.0
LIFE_MAX_LIMIT = 30.0
LIFE_MIN_DEFAULT = 3.0
LIFE_MAX_DEFAULT = 14.0
HIDDEN_PARAMS = (
    "pattern",
    "tiling",
    "pack_x",
    "pack_y",
    "speed",
    "invert",
    "pan",
    "life_min",
    "life_max",
    "emissive",
    "softness",
    "resolution",
    "lighting",
    "source",
    "path",
    "bg_color",
    "bg_alpha",
)

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

MATRIX_KATAKANA_GLYPHS = (
    "\uff66", "\uff67", "\uff68", "\uff69", "\uff6a", "\uff6b", "\uff6c", "\uff6d", "\uff6e", "\uff6f", "\uff70",
    "\uff71", "\uff72", "\uff73", "\uff74", "\uff75",
    "\uff76", "\uff77", "\uff78", "\uff79", "\uff7a",
    "\uff7b", "\uff7c", "\uff7d", "\uff7e", "\uff7f",
    "\uff80", "\uff81", "\uff82", "\uff83", "\uff84",
    "\uff85", "\uff86", "\uff87", "\uff88", "\uff89",
    "\uff8a", "\uff8b", "\uff8c", "\uff8d", "\uff8e",
    "\uff8f", "\uff90", "\uff91", "\uff92", "\uff93",
    "\uff94", "\uff95", "\uff96",
    "\uff97", "\uff98", "\uff99", "\uff9a", "\uff9b",
    "\uff9c", "\uff9d",
)
MATRIX_LATIN_GLYPHS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
MATRIX_GLYPHS = MATRIX_KATAKANA_GLYPHS + MATRIX_LATIN_GLYPHS
MATRIX_FONT_FAMILIES = (
    "MS Gothic",
    "Meiryo UI",
    "Yu Gothic UI",
    "Noto Sans Mono CJK JP",
    "Consolas",
)

GLYPH_ATLAS_COLS = 16
GLYPH_ATLAS_CELL = 48
_GLYPH_ATLAS = None
_GLYPH_ATLAS_GRID = None
_GLYPH_ATLAS_SIG = None


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


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
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
    for name in names or []:
        if name:
            cur.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


def _build_glyph_atlas():
    glyphs = list(MATRIX_GLYPHS)
    cols = GLYPH_ATLAS_COLS
    rows = max(1, int(math.ceil(len(glyphs) / float(cols))))
    cell = GLYPH_ATLAS_CELL
    fmt = QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32
    img = QtGui.QImage(cols * cell, rows * cell, fmt)
    img.fill(QtGui.QColor(0, 0, 0, 255))
    painter = QtGui.QPainter(img)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, False)
    families = set()
    try:
        families = {str(name) for name in QtGui.QFontDatabase().families()}
    except Exception:
        families = set()
    family = next((name for name in MATRIX_FONT_FAMILIES if name in families), "Consolas")
    font = QtGui.QFont(family)
    if family == "Consolas":
        font.setStyleHint(QtGui.QFont.Monospace)
    font.setPixelSize(int(cell * 0.84))
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
    global _GLYPH_ATLAS, _GLYPH_ATLAS_GRID, _GLYPH_ATLAS_SIG
    sig = (MATRIX_GLYPHS, GLYPH_ATLAS_COLS, GLYPH_ATLAS_CELL)
    if _GLYPH_ATLAS is None or _GLYPH_ATLAS_GRID is None or _GLYPH_ATLAS_SIG != sig:
        _GLYPH_ATLAS, _GLYPH_ATLAS_GRID = _build_glyph_atlas()
        _GLYPH_ATLAS_SIG = sig
    return _GLYPH_ATLAS, _GLYPH_ATLAS_GRID


def build_ports(node_item) -> None:
    _ensure_param(node_item, "pattern", DEFAULT_PATTERN)
    _ensure_param(node_item, "tiling", "1")
    _ensure_param(node_item, "pack_x", "1")
    _ensure_param(node_item, "pack_y", "1")
    _ensure_param(node_item, "speed", f"{SPEED_DEFAULT:.2f}")
    _ensure_param(node_item, "invert", "0")
    _ensure_param(node_item, "pan", "1" if PAN_DEFAULT else "0")
    _ensure_param(node_item, "life_min", f"{LIFE_MIN_DEFAULT:.2f}")
    _ensure_param(node_item, "life_max", f"{LIFE_MAX_DEFAULT:.2f}")
    _ensure_param(node_item, "emissive", f"{EMISSIVE_DEFAULT:.2f}")
    _ensure_param(node_item, "softness", f"{SOFTNESS_DEFAULT:.2f}")
    _ensure_param(node_item, "lighting", f"{LIGHTING_DEFAULT:.2f}")
    _ensure_param(node_item, "bg_color", "")
    _ensure_param(node_item, "bg_alpha", "1.0")
    _ensure_param(node_item, "resolution", str(TEXTURE_SIZE))
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_hidden_params(getattr(node_item, "model", None), HIDDEN_PARAMS)
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
        self._bg_rgba = None

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

    def set_background(self, rgba: Optional[tuple]) -> None:
        if rgba == self._bg_rgba:
            return
        self._bg_rgba = rgba
        self._rebuild()

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
            c0_default = "#0f172a"
            c1 = QtGui.QColor("#e2e8f0")
        else:
            c0_default = "#1d4ed8"
            c1 = QtGui.QColor("#f59e0b")
        if self._bg_rgba is not None:
            c0 = QtGui.QColor(*self._bg_rgba)
        else:
            c0 = QtGui.QColor(c0_default)

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
        self._invert = False
        self._pan = True
        self._life_min = float(LIFE_MIN_DEFAULT)
        self._life_max = float(LIFE_MAX_DEFAULT)
        self._bg_rgba = None
        self._cols = []
        self._sticky = []
        self._init_columns()

    def _init_columns(self) -> None:
        self._cols = []
        count = max(6, int(self.size // max(1, self.cell_x)))
        for i in range(count):
            self._cols.append(self._new_column(i))

    def _new_column(self, idx: int) -> dict:
        life_low = max(LIFE_MIN_LIMIT, min(LIFE_MAX_LIMIT, float(self._life_min)))
        life_high = max(life_low, min(LIFE_MAX_LIMIT, float(self._life_max)))
        life = self._rng.uniform(life_low, life_high)
        trail = int(round(self._rng.uniform(6.0, 12.0) + life * self._rng.uniform(1.2, 2.0)))
        trail = max(6, min(28, trail))
        hold_base = self._rng.uniform(0.2, 0.8)
        hold_var = self._rng.uniform(0.6, 2.4)
        glyphs = [self._random_glyph() for _ in range(20)]
        holds = [hold_base + self._rng.random() * hold_var for _ in glyphs]
        static_rate = self._rng.uniform(0.25, 0.6)
        static_hold_base = self._rng.uniform(0.8, 1.6)
        static_hold_var = self._rng.uniform(1.2, 3.0)
        return {
            "x": idx * self.cell_x,
            "y": self._rng.uniform(-self.size, self.size),
            "speed": self._rng.uniform(0.6, 1.8),
            "life": life,
            "trail": trail,
            "glyphs": glyphs,
            "glyph_hold": holds,
            "hold_base": hold_base,
            "hold_var": hold_var,
            "static_rate": static_rate,
            "static_hold_base": static_hold_base,
            "static_hold_var": static_hold_var,
            "age": 0.0,
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
        direction = -1.0 if self._invert else 1.0
        sticky = self._sticky
        if sticky:
            for entry in list(sticky):
                try:
                    entry["life"] = float(entry.get("life", 0.0)) - float(dt)
                except Exception:
                    entry["life"] = 0.0
                try:
                    entry["hold"] = float(entry.get("hold", 0.0)) - float(dt)
                except Exception:
                    entry["hold"] = 0.0
                if entry.get("hold", 0.0) <= 0.0:
                    entry["glyph"] = self._random_glyph()
                    hold_base = float(entry.get("hold_base", 0.8))
                    hold_var = float(entry.get("hold_var", 1.5))
                    entry["hold"] = hold_base + self._rng.random() * hold_var
                if float(entry.get("life", 0.0)) <= 0.0:
                    try:
                        sticky.remove(entry)
                    except Exception:
                        pass
        for idx, col in enumerate(self._cols):
            col["y"] = float(col.get("y", 0.0)) + direction * float(col.get("speed", 10.0)) * cell_y * dt
            try:
                col["age"] = float(col.get("age", 0.0)) + abs(float(col.get("speed", 1.0))) * float(dt)
            except Exception:
                col["age"] = 0.0
            trail = int(col.get("trail", 12))
            if self._invert:
                if col["y"] + (trail * cell_y) < -cell_y:
                    self._cols[idx] = self._new_column(idx)
                    continue
            else:
                if col["y"] - (trail * cell_y) > height + cell_y:
                    self._cols[idx] = self._new_column(idx)
                    continue
            glyphs = col.get("glyphs") or []
            if glyphs:
                holds = col.get("glyph_hold") or []
                if len(holds) != len(glyphs):
                    hold_base = float(col.get("hold_base", 0.2))
                    hold_var = float(col.get("hold_var", 1.0))
                    holds = [hold_base + self._rng.random() * hold_var for _ in glyphs]
                    col["glyph_hold"] = holds
                hold_base = float(col.get("hold_base", 0.2))
                hold_var = float(col.get("hold_var", 1.0))
                for gi in range(len(glyphs)):
                    try:
                        holds[gi] = float(holds[gi]) - float(dt)
                    except Exception:
                        holds[gi] = 0.0
                    if holds[gi] <= 0.0:
                        glyphs[gi] = self._random_glyph()
                        holds[gi] = hold_base + self._rng.random() * hold_var
            rate = float(col.get("static_rate", 0.35))
            try:
                spawn_prob = max(0.0, min(1.0, rate * float(dt)))
            except Exception:
                spawn_prob = 0.0
            if spawn_prob > 0.0 and self._rng.random() < spawn_prob:
                head_y = float(col.get("y", 0.0))
                back_dir = -direction
                step = self._rng.uniform(1.0, max(2.0, float(trail)))
                y = head_y + back_dir * step * float(cell_y)
                y = round(y / max(1.0, float(cell_y))) * float(cell_y)
                if -cell_y <= y <= height + cell_y:
                    hold_base = float(col.get("static_hold_base", 1.0))
                    hold_var = float(col.get("static_hold_var", 1.5))
                    life_span = self._rng.uniform(self._life_min * 0.6, self._life_max * 1.2)
                    sticky.append({
                        "x": float(col.get("x", 0.0)),
                        "y": y,
                        "glyph": self._random_glyph(),
                        "life": max(0.4, life_span),
                        "hold": hold_base + self._rng.random() * hold_var,
                        "hold_base": hold_base,
                        "hold_var": hold_var,
                    })
                    if len(sticky) > 140:
                        del sticky[:20]
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

    def set_invert(self, invert: bool) -> None:
        invert = bool(invert)
        if invert == self._invert:
            return
        self._invert = invert
        self._mark_dirty()

    def set_pan(self, pan: bool) -> None:
        pan = bool(pan)
        if pan == self._pan:
            return
        self._pan = pan
        self._mark_dirty()

    def set_life_range(self, life_min: float, life_max: float) -> None:
        try:
            life_min = float(life_min)
        except Exception:
            life_min = float(LIFE_MIN_DEFAULT)
        try:
            life_max = float(life_max)
        except Exception:
            life_max = float(LIFE_MAX_DEFAULT)
        life_min = max(LIFE_MIN_LIMIT, min(LIFE_MAX_LIMIT, life_min))
        life_max = max(life_min, min(LIFE_MAX_LIMIT, life_max))
        if abs(life_min - self._life_min) < 1e-6 and abs(life_max - self._life_max) < 1e-6:
            return
        self._life_min = life_min
        self._life_max = life_max
        self._init_columns()
        self._mark_dirty()

    def set_background(self, rgba: Optional[tuple]) -> None:
        if rgba == self._bg_rgba:
            return
        self._bg_rgba = rgba
        self._mark_dirty()

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
        if self._bg_rgba is not None:
            bg_color = QtGui.QColor(*self._bg_rgba)
        else:
            bg_color = QtGui.QColor("#050b07")
        img.fill(bg_color)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setPixelSize(int(min(cell_x, cell_y) * 0.9))
        painter.setFont(font)

        for col in self._cols:
            x = int(col.get("x", 0))
            head_y = float(col.get("y", 0.0))
            if not self._pan:
                head_y = round(head_y / max(1.0, float(cell_y))) * float(cell_y)
            trail = int(col.get("trail", 12))
            age_cells = float(col.get("age", 0.0))
            eff_trail = max(1, min(trail, int(age_cells) + 1))
            glyphs = col.get("glyphs") or []
            for t in range(eff_trail):
                if self._invert:
                    y = head_y + t * cell_y
                else:
                    y = head_y - t * cell_y
                if y < -cell_y or y > size:
                    continue
                if t == 0:
                    color = QtGui.QColor("#f8fafc")
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
        if self._sticky:
            stick_color = QtGui.QColor("#16a34a")
            stick_color.setAlpha(180)
            painter.setPen(stick_color)
            for entry in list(self._sticky):
                try:
                    x = float(entry.get("x", 0.0))
                    y = float(entry.get("y", 0.0))
                except Exception:
                    continue
                if y < -cell_y or y > size:
                    continue
                ch = entry.get("glyph") or self._random_glyph()
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
        self._speed = float(SPEED_DEFAULT)
        self._invert = False
        self._pan = bool(PAN_DEFAULT)
        self._life_min = float(LIFE_MIN_DEFAULT)
        self._life_max = float(LIFE_MAX_DEFAULT)
        self._emissive = float(EMISSIVE_DEFAULT)
        self._softness = float(SOFTNESS_DEFAULT)
        self._lighting = float(LIGHTING_DEFAULT)
        self._bg_rgba = None
        self._gpu_seed = random.Random().random() * 4096.0
        self._generators = {
            "checkerboard": CheckerboardGenerator(size=self._size),
            "matrix_rain": MatrixRainGenerator(size=self._size),
        }
        self._mode = DEFAULT_PATTERN
        self._revision = 0
        self._apply_size(self._size)
        self._apply_density(self._density)
        self._apply_pack(self._pack_x, self._pack_y)
        self._apply_pan(self._pan)
        self._apply_life_range(self._life_min, self._life_max)
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

    def set_speed(self, speed: float) -> None:
        try:
            speed = float(speed)
        except Exception:
            speed = float(SPEED_DEFAULT)
        speed = max(SPEED_MIN, min(SPEED_MAX, speed))
        if abs(speed - self._speed) < 1e-6:
            return
        self._speed = speed
        self._revision += 1

    def set_invert(self, invert: bool) -> None:
        invert = bool(invert)
        if invert == self._invert:
            return
        self._invert = invert
        self._revision += 1
        try:
            gen = self._generators.get("matrix_rain")
            fn = getattr(gen, "set_invert", None)
            if callable(fn):
                fn(invert)
        except Exception:
            pass

    def set_pan(self, pan: bool) -> None:
        pan = bool(pan)
        if pan == self._pan:
            return
        self._pan = pan
        self._revision += 1
        self._apply_pan(pan)

    def set_life_range(self, life_min: float, life_max: float) -> None:
        try:
            life_min = float(life_min)
        except Exception:
            life_min = float(LIFE_MIN_DEFAULT)
        try:
            life_max = float(life_max)
        except Exception:
            life_max = float(LIFE_MAX_DEFAULT)
        life_min = max(LIFE_MIN_LIMIT, min(LIFE_MAX_LIMIT, life_min))
        life_max = max(life_min, min(LIFE_MAX_LIMIT, life_max))
        if abs(life_min - self._life_min) < 1e-6 and abs(life_max - self._life_max) < 1e-6:
            return
        self._life_min = life_min
        self._life_max = life_max
        self._revision += 1
        self._apply_life_range(life_min, life_max)

    def set_emissive(self, emissive: float) -> None:
        try:
            emissive = float(emissive)
        except Exception:
            emissive = float(EMISSIVE_DEFAULT)
        emissive = max(EMISSIVE_MIN, min(EMISSIVE_MAX, emissive))
        if abs(emissive - self._emissive) < 1e-6:
            return
        self._emissive = emissive
        self._revision += 1

    def set_softness(self, softness: float) -> None:
        try:
            softness = float(softness)
        except Exception:
            softness = float(SOFTNESS_DEFAULT)
        softness = max(SOFTNESS_MIN, min(SOFTNESS_MAX, softness))
        if abs(softness - self._softness) < 1e-6:
            return
        self._softness = softness
        self._revision += 1

    def set_lighting(self, value: float) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(LIGHTING_DEFAULT)
        value = max(0.0, min(1.0, value))
        if abs(value - self._lighting) < 1e-6:
            return
        self._lighting = value
        self._revision += 1

    def set_background(self, rgba: Optional[tuple]) -> None:
        if rgba is not None:
            try:
                r, g, b, a = rgba
                rgba = (
                    max(0, min(255, int(r))),
                    max(0, min(255, int(g))),
                    max(0, min(255, int(b))),
                    max(0, min(255, int(a))),
                )
            except Exception:
                rgba = None
        if rgba == self._bg_rgba:
            return
        self._bg_rgba = rgba
        self._revision += 1
        self._apply_background(rgba)

    def advance(self, dt: float, frame_id: Optional[int] = None) -> bool:
        gen = self._generators[self._mode]
        changed = gen.advance(float(dt) * float(self._speed), frame_id)
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
        bg_enabled = 0.0
        bg_color = (0.0, 0.0, 0.0, 1.0)
        if self._bg_rgba is not None:
            r, g, b, a = self._bg_rgba
            bg_color = (float(r) / 255.0, float(g) / 255.0, float(b) / 255.0, float(a) / 255.0)
            bg_enabled = 1.0
        return {
            "mode": self._mode,
            "tiling": int(self._density),
            "pack_x": int(self._pack_x),
            "pack_y": int(self._pack_y),
            "speed": float(self._speed),
            "invert": 1.0 if self._invert else 0.0,
            "pan": 1.0 if self._pan else 0.0,
            "life_min": float(self._life_min),
            "life_max": float(self._life_max),
            "emissive": float(self._emissive),
            "softness": float(self._softness),
            "light_mix": float(self._lighting),
            "seed": float(self._gpu_seed),
            "glyph_atlas": atlas,
            "glyph_grid": grid,
            "glyph_count": int(len(MATRIX_GLYPHS)),
            "bg_color": bg_color,
            "bg_enabled": float(bg_enabled),
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

    def _apply_pan(self, pan: bool) -> None:
        try:
            gen = self._generators.get("matrix_rain")
            fn = getattr(gen, "set_pan", None)
            if callable(fn):
                fn(bool(pan))
        except Exception:
            pass

    def _apply_life_range(self, life_min: float, life_max: float) -> None:
        try:
            gen = self._generators.get("matrix_rain")
            fn = getattr(gen, "set_life_range", None)
            if callable(fn):
                fn(float(life_min), float(life_max))
        except Exception:
            pass
        try:
            gen = self._generators.get("matrix_rain")
            fn = getattr(gen, "set_invert", None)
            if callable(fn):
                fn(self._invert)
        except Exception:
            pass

    def _apply_background(self, rgba: Optional[tuple]) -> None:
        for gen in self._generators.values():
            fn = getattr(gen, "set_background", None)
            if callable(fn):
                try:
                    fn(rgba)
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

        left = QtWidgets.QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)

        self._preview = QtWidgets.QLabel()
        self._preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setStyleSheet(
            "QLabel{background:#0f172a;border:1px solid #334155;border-radius:4px;}"
        )
        left.addWidget(self._preview, 0, QtCore.Qt.AlignLeft)

        self._bg_btn = QtWidgets.QPushButton("BG Color")
        self._bg_btn.setFixedWidth(PREVIEW_SIZE)
        self._bg_btn.setToolTip("Background color")
        self._bg_btn.clicked.connect(self._on_bg_clicked)
        self._set_bg_button(None)
        left.addWidget(self._bg_btn, 0, QtCore.Qt.AlignLeft)

        self._bg_alpha_label = QtWidgets.QLabel("BG Alpha")
        self._bg_alpha_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        self._bg_alpha_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._bg_alpha_label.setFixedWidth(PREVIEW_SIZE)
        left.addWidget(self._bg_alpha_label, 0, QtCore.Qt.AlignLeft)

        self._bg_alpha = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._bg_alpha.setRange(0, 100)
        self._bg_alpha.setSingleStep(1)
        self._bg_alpha.setPageStep(10)
        self._bg_alpha.setFixedWidth(PREVIEW_SIZE)
        self._bg_alpha.setToolTip("Background alpha")
        self._bg_alpha.setStyleSheet(
            "QSlider::groove:horizontal{height:4px;background:#1f2937;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#22c55e;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#e2e8f0;border:1px solid #0f172a;"
            "width:10px;margin:-4px 0;border-radius:5px;}"
        )
        self._bg_alpha.valueChanged.connect(self._on_bg_alpha_changed)
        self._set_bg_alpha_value(1.0)
        left.addWidget(self._bg_alpha, 0, QtCore.Qt.AlignLeft)

        self._light_label = QtWidgets.QLabel("Lighting")
        self._light_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        self._light_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._light_label.setFixedWidth(PREVIEW_SIZE)
        left.addWidget(self._light_label, 0, QtCore.Qt.AlignLeft)

        self._light_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._light_slider.setRange(0, 100)
        self._light_slider.setSingleStep(1)
        self._light_slider.setPageStep(10)
        self._light_slider.setFixedWidth(PREVIEW_SIZE)
        self._light_slider.setToolTip("Lighting strength")
        self._light_slider.setStyleSheet(
            "QSlider::groove:horizontal{height:4px;background:#1f2937;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#60a5fa;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#e2e8f0;border:1px solid #0f172a;"
            "width:10px;margin:-4px 0;border-radius:5px;}"
        )
        self._light_slider.valueChanged.connect(self._on_lighting_changed)
        self._set_lighting_value(LIGHTING_DEFAULT)
        left.addWidget(self._light_slider, 0, QtCore.Qt.AlignLeft)

        self._emissive_label = QtWidgets.QLabel("Emissive")
        self._emissive_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        self._emissive_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._emissive_label.setFixedWidth(PREVIEW_SIZE)
        left.addWidget(self._emissive_label, 0, QtCore.Qt.AlignLeft)

        self._emissive = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._emissive.setRange(int(EMISSIVE_MIN * 100.0), int(EMISSIVE_MAX * 100.0))
        self._emissive.setSingleStep(1)
        self._emissive.setPageStep(10)
        self._emissive.setFixedWidth(PREVIEW_SIZE)
        self._emissive.setToolTip("Glow intensity for procedural text")
        self._emissive.setStyleSheet(
            "QSlider::groove:horizontal{height:4px;background:#1f2937;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#f59e0b;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#e2e8f0;border:1px solid #0f172a;"
            "width:10px;margin:-4px 0;border-radius:5px;}"
        )
        self._emissive.valueChanged.connect(self._on_emissive_changed)
        self._set_emissive_value(EMISSIVE_DEFAULT)
        left.addWidget(self._emissive, 0, QtCore.Qt.AlignLeft)

        self._softness_label = QtWidgets.QLabel("Softness")
        self._softness_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        self._softness_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._softness_label.setFixedWidth(PREVIEW_SIZE)
        left.addWidget(self._softness_label, 0, QtCore.Qt.AlignLeft)

        self._softness = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._softness.setRange(int(SOFTNESS_MIN * 100.0), int(SOFTNESS_MAX * 100.0))
        self._softness.setSingleStep(1)
        self._softness.setPageStep(10)
        self._softness.setFixedWidth(PREVIEW_SIZE)
        self._softness.setToolTip("Glyph edge softness")
        self._softness.setStyleSheet(
            "QSlider::groove:horizontal{height:4px;background:#1f2937;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#38bdf8;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#e2e8f0;border:1px solid #0f172a;"
            "width:10px;margin:-4px 0;border-radius:5px;}"
        )
        self._softness.valueChanged.connect(self._on_softness_changed)
        self._set_softness_value(SOFTNESS_DEFAULT)
        left.addWidget(self._softness, 0, QtCore.Qt.AlignLeft)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(PREVIEW_SIZE)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        left.addWidget(self._view_btn, 0, QtCore.Qt.AlignLeft)
        left.addStretch(1)

        layout.addLayout(left, 0)

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

        label_w = 52

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
        res_row = QtWidgets.QHBoxLayout()
        res_row.setContentsMargins(0, 0, 0, 0)
        res_row.setSpacing(6)

        res_label = QtWidgets.QLabel("Resolution")
        res_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        res_label.setFixedWidth(label_w)
        res_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        res_row.addWidget(res_label, 0)
        res_row.addWidget(self._res_combo, 0)
        res_row.addStretch(1)

        right.addLayout(res_row, 0)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)

        tiling_label = QtWidgets.QLabel("Tile")
        tiling_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        tiling_label.setFixedWidth(label_w)
        tiling_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        settings_row.addWidget(tiling_label, 0)

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

        right.addLayout(settings_row, 0)

        pack_x_row = QtWidgets.QHBoxLayout()
        pack_x_row.setContentsMargins(0, 0, 0, 0)
        pack_x_row.setSpacing(6)

        pack_x_label = QtWidgets.QLabel("X")
        pack_x_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        pack_x_label.setFixedWidth(label_w)
        pack_x_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        pack_x_row.addWidget(pack_x_label, 0)

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
        pack_x_row.addWidget(self._pack_x, 0)
        pack_x_row.addStretch(1)

        right.addLayout(pack_x_row, 0)

        pack_y_row = QtWidgets.QHBoxLayout()
        pack_y_row.setContentsMargins(0, 0, 0, 0)
        pack_y_row.setSpacing(6)

        pack_y_label = QtWidgets.QLabel("Y")
        pack_y_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        pack_y_label.setFixedWidth(label_w)
        pack_y_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        pack_y_row.addWidget(pack_y_label, 0)

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
        pack_y_row.addWidget(self._pack_y, 0)
        pack_y_row.addStretch(1)

        right.addLayout(pack_y_row, 0)

        speed_row = QtWidgets.QHBoxLayout()
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.setSpacing(6)

        speed_label = QtWidgets.QLabel("Speed")
        speed_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        speed_label.setFixedWidth(label_w)
        speed_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        speed_row.addWidget(speed_label, 0)

        self._speed = QtWidgets.QDoubleSpinBox()
        self._speed.setRange(SPEED_MIN, SPEED_MAX)
        self._speed.setSingleStep(0.1)
        self._speed.setDecimals(2)
        self._speed.setFixedWidth(70)
        self._speed.setAlignment(QtCore.Qt.AlignRight)
        self._speed.setSuffix("x")
        self._speed.setToolTip("Animation speed")
        self._speed.setStyleSheet(
            "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 4px;}"
            "QDoubleSpinBox::up-button{width:10px;border:none;}"
            "QDoubleSpinBox::down-button{width:10px;border:none;}"
        )
        self._speed.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self._speed, 0)
        speed_row.addStretch(1)

        right.addLayout(speed_row, 0)

        life_min_row = QtWidgets.QHBoxLayout()
        life_min_row.setContentsMargins(0, 0, 0, 0)
        life_min_row.setSpacing(6)

        life_min_label = QtWidgets.QLabel("Life Min")
        life_min_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        life_min_label.setFixedWidth(label_w)
        life_min_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        life_min_row.addWidget(life_min_label, 0)

        self._life_min = QtWidgets.QDoubleSpinBox()
        self._life_min.setRange(LIFE_MIN_LIMIT, LIFE_MAX_LIMIT)
        self._life_min.setSingleStep(0.5)
        self._life_min.setDecimals(2)
        self._life_min.setFixedWidth(70)
        self._life_min.setAlignment(QtCore.Qt.AlignRight)
        self._life_min.setToolTip("Minimum chain life (seconds)")
        self._life_min.setStyleSheet(
            "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 4px;}"
            "QDoubleSpinBox::up-button{width:10px;border:none;}"
            "QDoubleSpinBox::down-button{width:10px;border:none;}"
        )
        self._life_min.valueChanged.connect(self._on_life_min_changed)
        life_min_row.addWidget(self._life_min, 0)
        life_min_row.addStretch(1)

        right.addLayout(life_min_row, 0)

        life_max_row = QtWidgets.QHBoxLayout()
        life_max_row.setContentsMargins(0, 0, 0, 0)
        life_max_row.setSpacing(6)

        life_max_label = QtWidgets.QLabel("Life Max")
        life_max_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        life_max_label.setFixedWidth(label_w)
        life_max_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        life_max_row.addWidget(life_max_label, 0)

        self._life_max = QtWidgets.QDoubleSpinBox()
        self._life_max.setRange(LIFE_MIN_LIMIT, LIFE_MAX_LIMIT)
        self._life_max.setSingleStep(0.5)
        self._life_max.setDecimals(2)
        self._life_max.setFixedWidth(70)
        self._life_max.setAlignment(QtCore.Qt.AlignRight)
        self._life_max.setToolTip("Maximum chain life (seconds)")
        self._life_max.setStyleSheet(
            "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:1px 4px;}"
            "QDoubleSpinBox::up-button{width:10px;border:none;}"
            "QDoubleSpinBox::down-button{width:10px;border:none;}"
        )
        self._life_max.valueChanged.connect(self._on_life_max_changed)
        life_max_row.addWidget(self._life_max, 0)
        life_max_row.addStretch(1)

        right.addLayout(life_max_row, 0)

        invert_row = QtWidgets.QHBoxLayout()
        invert_row.setContentsMargins(0, 0, 0, 0)
        invert_row.setSpacing(6)

        invert_label = QtWidgets.QLabel("Invert")
        invert_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        invert_label.setFixedWidth(label_w)
        invert_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        invert_row.addWidget(invert_label, 0)

        self._invert = QtWidgets.QCheckBox()
        self._invert.setToolTip("Invert rain direction (upwards)")
        self._invert.setStyleSheet("QCheckBox{color:#e6edf3;}")
        self._invert.stateChanged.connect(self._on_invert_changed)
        invert_row.addWidget(self._invert, 0)
        invert_row.addSpacing(12)

        pan_label = QtWidgets.QLabel("Pan")
        pan_label.setStyleSheet("color:#94a3b8;font-size:10px;")
        pan_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        invert_row.addWidget(pan_label, 0)

        self._pan = QtWidgets.QCheckBox()
        self._pan.setToolTip("Slide cells smoothly (off = step by cell)")
        self._pan.setStyleSheet("QCheckBox{color:#e6edf3;}")
        self._pan.stateChanged.connect(self._on_pan_changed)
        invert_row.addWidget(self._pan, 0)
        invert_row.addStretch(1)

        right.addLayout(invert_row, 0)
        right.addStretch(1)

        layout.addLayout(right, 1)
        try:
            self.setMinimumHeight(self.sizeHint().height())
        except Exception:
            pass

        self._ensure_scene()
        self._refresh_preview()

        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.setInterval(60)
        self._frame_timer.timeout.connect(self._on_timer_tick)
        self._frame_timer.start()

        QtCore.QTimer.singleShot(0, self._hook_viewport)
        QtCore.QTimer.singleShot(0, self._update_inputs)

    def sizeHint(self):
        w = 250
        h = PREVIEW_SIZE + 66
        try:
            lay = self.layout()
            if lay is not None:
                hint = lay.sizeHint()
                if hint is not None:
                    w = max(w, int(hint.width()) + 12)
                    h = max(h, int(hint.height()) + 8)
        except Exception:
            pass
        return QtCore.QSize(w, h)

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

    def _bg_alpha_from_params(self) -> float:
        raw_alpha = ""
        try:
            raw_alpha = str(_param_value(self._node_item.model, "bg_alpha") or "").strip()
        except Exception:
            raw_alpha = ""
        alpha = 1.0
        if raw_alpha:
            try:
                alpha = float(raw_alpha)
            except Exception:
                alpha = 1.0
        if alpha > 1.0:
            alpha = min(alpha, 255.0) / 255.0
        return max(0.0, min(1.0, alpha))

    def _bg_rgba_from_params(self) -> Optional[tuple]:
        raw_color = ""
        try:
            raw_color = str(_param_value(self._node_item.model, "bg_color") or "").strip()
        except Exception:
            raw_color = ""
        if not raw_color:
            return None
        color = QtGui.QColor(raw_color)
        if not color.isValid():
            return None
        alpha = self._bg_alpha_from_params()
        color.setAlpha(int(round(alpha * 255)))
        return (color.red(), color.green(), color.blue(), color.alpha())

    def _default_bg_rgba(self) -> tuple:
        pattern = ""
        try:
            pattern = (self._combo.currentData() or self._combo.currentText() or "").strip().lower()
        except Exception:
            pattern = ""
        if not pattern:
            try:
                if hasattr(self._provider, "mode"):
                    pattern = str(self._provider.mode() or "").strip().lower()
            except Exception:
                pattern = ""
        if pattern == "matrix_rain":
            color = QtGui.QColor("#050b07")
        else:
            color = QtGui.QColor("#0f172a")
        color.setAlpha(255)
        return (color.red(), color.green(), color.blue(), color.alpha())

    def _set_bg_button(self, rgba: Optional[tuple]) -> None:
        if rgba is None:
            style = (
                "QPushButton{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
                "border-radius:4px;padding:2px 6px;}"
                "QPushButton:hover{border:1px solid #64748b;}"
            )
            self._bg_btn.setStyleSheet(style)
            return
        try:
            r, g, b, a = rgba
        except Exception:
            r, g, b, a = 15, 18, 22, 255
        alpha = max(0.0, min(1.0, float(a) / 255.0))
        lum = 0.2126 * float(r) + 0.7152 * float(g) + 0.0722 * float(b)
        text_color = "#0f172a" if lum > 140.0 else "#f8fafc"
        style = (
            "QPushButton{background-color: rgba("
            f"{int(r)},{int(g)},{int(b)},{alpha:.3f}"
            ");color:"
            f"{text_color}"
            ";border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
            "QPushButton:hover{border:1px solid #64748b;}"
        )
        self._bg_btn.setStyleSheet(style)

    def _set_bg_alpha_value(self, alpha: float) -> None:
        alpha = max(0.0, min(1.0, float(alpha)))
        value = int(round(alpha * 100.0))
        try:
            if self._bg_alpha.value() != value:
                self._bg_alpha.blockSignals(True)
                self._bg_alpha.setValue(value)
        finally:
            try:
                self._bg_alpha.blockSignals(False)
            except Exception:
                pass
        self._bg_alpha_label.setText(f"BG Alpha {value}%")

    def _lighting_from_params(self) -> float:
        raw = ""
        try:
            raw = str(_param_value(self._node_item.model, "lighting") or "").strip()
        except Exception:
            raw = ""
        value = float(LIGHTING_DEFAULT)
        if raw:
            try:
                value = float(raw)
            except Exception:
                value = float(LIGHTING_DEFAULT)
        if value > 1.0:
            value = min(value, 100.0) / 100.0
        return max(0.0, min(1.0, value))

    def _set_lighting_value(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        pct = int(round(value * 100.0))
        try:
            if self._light_slider.value() != pct:
                self._light_slider.blockSignals(True)
                self._light_slider.setValue(pct)
        finally:
            try:
                self._light_slider.blockSignals(False)
            except Exception:
                pass
        self._light_label.setText(f"Lighting {pct}%")

    def _apply_lighting(self, value: float, notify_scene: bool = False) -> None:
        value = max(0.0, min(1.0, float(value)))
        try:
            if hasattr(self._provider, "set_lighting"):
                self._provider.set_lighting(value)
        except Exception:
            pass
        self._set_param("lighting", f"{value:.2f}", notify_scene=notify_scene)
        self._set_lighting_value(value)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _apply_background(self, rgba: Optional[tuple], notify_scene: bool = False) -> None:
        try:
            if hasattr(self._provider, "set_background"):
                self._provider.set_background(rgba)
        except Exception:
            pass
        if rgba is None:
            self._set_param("bg_color", "", notify_scene=notify_scene)
            self._set_param("bg_alpha", "1.0", notify_scene=notify_scene)
            self._set_bg_alpha_value(1.0)
        else:
            try:
                r, g, b, a = rgba
            except Exception:
                r, g, b, a = 15, 18, 22, 255
            self._set_param("bg_color", f"#{int(r):02x}{int(g):02x}{int(b):02x}", notify_scene=notify_scene)
            self._set_param("bg_alpha", f"{float(a) / 255.0:.2f}", notify_scene=notify_scene)
            self._set_bg_alpha_value(float(a) / 255.0)
        self._set_bg_button(rgba)
        try:
            self._refresh_preview()
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

    def _set_speed_value(self, value: float) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(SPEED_DEFAULT)
        value = max(SPEED_MIN, min(SPEED_MAX, value))
        if abs(float(self._speed.value()) - value) < 1e-6:
            return
        try:
            self._speed.blockSignals(True)
            self._speed.setValue(value)
        finally:
            self._speed.blockSignals(False)

    def _apply_speed(self, value: float, notify_scene: bool = False) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(SPEED_DEFAULT)
        value = max(SPEED_MIN, min(SPEED_MAX, value))
        try:
            if hasattr(self._provider, "set_speed"):
                self._provider.set_speed(value)
        except Exception:
            pass
        self._set_param("speed", f"{value:.2f}", notify_scene=notify_scene)
        self._set_speed_value(value)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_speed_changed(self):
        value = float(self._speed.value())
        self._apply_speed(value, notify_scene=True)

    def _set_invert_value(self, value: bool) -> None:
        checked = bool(value)
        try:
            if self._invert.isChecked() == checked:
                return
            self._invert.blockSignals(True)
            self._invert.setChecked(checked)
        finally:
            try:
                self._invert.blockSignals(False)
            except Exception:
                pass

    def _apply_invert(self, value: bool, notify_scene: bool = False) -> None:
        checked = bool(value)
        try:
            if hasattr(self._provider, "set_invert"):
                self._provider.set_invert(checked)
        except Exception:
            pass
        self._set_param("invert", "1" if checked else "0", notify_scene=notify_scene)
        self._set_invert_value(checked)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_invert_changed(self):
        checked = bool(self._invert.isChecked())
        self._apply_invert(checked, notify_scene=True)

    def _set_pan_value(self, value: bool) -> None:
        checked = bool(value)
        try:
            if self._pan.isChecked() == checked:
                return
            self._pan.blockSignals(True)
            self._pan.setChecked(checked)
        finally:
            try:
                self._pan.blockSignals(False)
            except Exception:
                pass

    def _apply_pan(self, value: bool, notify_scene: bool = False) -> None:
        checked = bool(value)
        try:
            if hasattr(self._provider, "set_pan"):
                self._provider.set_pan(checked)
        except Exception:
            pass
        self._set_param("pan", "1" if checked else "0", notify_scene=notify_scene)
        self._set_pan_value(checked)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_pan_changed(self):
        checked = bool(self._pan.isChecked())
        self._apply_pan(checked, notify_scene=True)

    def _set_life_values(self, life_min: float, life_max: float) -> None:
        try:
            life_min = float(life_min)
        except Exception:
            life_min = float(LIFE_MIN_DEFAULT)
        try:
            life_max = float(life_max)
        except Exception:
            life_max = float(LIFE_MAX_DEFAULT)
        life_min = max(LIFE_MIN_LIMIT, min(LIFE_MAX_LIMIT, life_min))
        life_max = max(life_min, min(LIFE_MAX_LIMIT, life_max))
        try:
            if abs(float(self._life_min.value()) - life_min) > 1e-6:
                self._life_min.blockSignals(True)
                self._life_min.setValue(life_min)
        finally:
            try:
                self._life_min.blockSignals(False)
            except Exception:
                pass
        try:
            if abs(float(self._life_max.value()) - life_max) > 1e-6:
                self._life_max.blockSignals(True)
                self._life_max.setValue(life_max)
        finally:
            try:
                self._life_max.blockSignals(False)
            except Exception:
                pass

    def _apply_life_range(self, life_min: float, life_max: float, notify_scene: bool = False) -> None:
        try:
            life_min = float(life_min)
        except Exception:
            life_min = float(LIFE_MIN_DEFAULT)
        try:
            life_max = float(life_max)
        except Exception:
            life_max = float(LIFE_MAX_DEFAULT)
        life_min = max(LIFE_MIN_LIMIT, min(LIFE_MAX_LIMIT, life_min))
        life_max = max(life_min, min(LIFE_MAX_LIMIT, life_max))
        try:
            if hasattr(self._provider, "set_life_range"):
                self._provider.set_life_range(life_min, life_max)
        except Exception:
            pass
        self._set_param("life_min", f"{life_min:.2f}", notify_scene=notify_scene)
        self._set_param("life_max", f"{life_max:.2f}", notify_scene=notify_scene)
        self._set_life_values(life_min, life_max)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_life_min_changed(self):
        life_min = float(self._life_min.value())
        life_max = float(self._life_max.value())
        self._apply_life_range(life_min, life_max, notify_scene=True)

    def _on_life_max_changed(self):
        life_min = float(self._life_min.value())
        life_max = float(self._life_max.value())
        self._apply_life_range(life_min, life_max, notify_scene=True)

    def _set_emissive_value(self, value: float) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(EMISSIVE_DEFAULT)
        value = max(EMISSIVE_MIN, min(EMISSIVE_MAX, value))
        slider_value = int(round(value * 100.0))
        try:
            if int(self._emissive.value()) == slider_value:
                pass
            else:
                self._emissive.blockSignals(True)
                self._emissive.setValue(slider_value)
        finally:
            try:
                self._emissive.blockSignals(False)
            except Exception:
                pass
        try:
            self._emissive_label.setText(f"Emissive {value:.2f}")
        except Exception:
            pass

    def _apply_emissive(self, value: float, notify_scene: bool = False) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(EMISSIVE_DEFAULT)
        value = max(EMISSIVE_MIN, min(EMISSIVE_MAX, value))
        try:
            if hasattr(self._provider, "set_emissive"):
                self._provider.set_emissive(value)
        except Exception:
            pass
        self._set_param("emissive", f"{value:.2f}", notify_scene=notify_scene)
        self._set_emissive_value(value)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_emissive_changed(self):
        value = float(self._emissive.value()) / 100.0
        self._apply_emissive(value, notify_scene=True)

    def _set_softness_value(self, value: float) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(SOFTNESS_DEFAULT)
        value = max(SOFTNESS_MIN, min(SOFTNESS_MAX, value))
        slider_value = int(round(value * 100.0))
        try:
            if int(self._softness.value()) == slider_value:
                pass
            else:
                self._softness.blockSignals(True)
                self._softness.setValue(slider_value)
        finally:
            try:
                self._softness.blockSignals(False)
            except Exception:
                pass
        try:
            self._softness_label.setText(f"Softness {value:.2f}")
        except Exception:
            pass

    def _apply_softness(self, value: float, notify_scene: bool = False) -> None:
        try:
            value = float(value)
        except Exception:
            value = float(SOFTNESS_DEFAULT)
        value = max(SOFTNESS_MIN, min(SOFTNESS_MAX, value))
        try:
            if hasattr(self._provider, "set_softness"):
                self._provider.set_softness(value)
        except Exception:
            pass
        self._set_param("softness", f"{value:.2f}", notify_scene=notify_scene)
        self._set_softness_value(value)
        try:
            self._refresh_preview()
        except Exception:
            pass

    def _on_softness_changed(self):
        value = float(self._softness.value()) / 100.0
        self._apply_softness(value, notify_scene=True)

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

    def _on_bg_clicked(self):
        rgba = self._bg_rgba_from_params()
        if rgba is None:
            rgba = self._default_bg_rgba()
        try:
            init_color = QtGui.QColor(*rgba)
        except Exception:
            init_color = QtGui.QColor("#0f172a")
        parent = _resolve_window(self._node_item) or self
        picked = QtWidgets.QColorDialog.getColor(
            init_color,
            parent,
            "BG Color",
        )
        if not picked.isValid():
            return
        alpha = self._bg_alpha_from_params()
        rgba = (picked.red(), picked.green(), picked.blue(), int(round(alpha * 255)))
        self._apply_background(rgba, notify_scene=True)

    def _on_bg_alpha_changed(self, value: int):
        alpha = max(0.0, min(1.0, float(value) / 100.0))
        rgba = self._bg_rgba_from_params()
        if rgba is None:
            rgba = self._default_bg_rgba()
        try:
            r, g, b, _ = rgba
        except Exception:
            r, g, b = 15, 18, 22
        rgba = (int(r), int(g), int(b), int(round(alpha * 255)))
        self._apply_background(rgba, notify_scene=True)

    def _on_lighting_changed(self, value: int):
        lighting = max(0.0, min(1.0, float(value) / 100.0))
        self._apply_lighting(lighting, notify_scene=True)

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

        speed = float(SPEED_DEFAULT)
        try:
            speed = float(_param_value(self._node_item.model, "speed") or SPEED_DEFAULT)
        except Exception:
            speed = float(SPEED_DEFAULT)
        speed = max(SPEED_MIN, min(SPEED_MAX, float(speed)))
        self._apply_speed(speed, notify_scene=False)

        invert_flag = False
        try:
            raw = str(_param_value(self._node_item.model, "invert") or "").strip().lower()
            invert_flag = raw in {"1", "true", "yes", "on"}
        except Exception:
            invert_flag = False
        self._apply_invert(invert_flag, notify_scene=False)

        pan_flag = bool(PAN_DEFAULT)
        try:
            raw = str(_param_value(self._node_item.model, "pan") or "").strip().lower()
            if raw:
                pan_flag = raw in {"1", "true", "yes", "on"}
        except Exception:
            pan_flag = bool(PAN_DEFAULT)
        self._apply_pan(pan_flag, notify_scene=False)

        life_min = float(LIFE_MIN_DEFAULT)
        try:
            life_min = float(_param_value(self._node_item.model, "life_min") or LIFE_MIN_DEFAULT)
        except Exception:
            life_min = float(LIFE_MIN_DEFAULT)
        life_max = float(LIFE_MAX_DEFAULT)
        try:
            life_max = float(_param_value(self._node_item.model, "life_max") or LIFE_MAX_DEFAULT)
        except Exception:
            life_max = float(LIFE_MAX_DEFAULT)
        self._apply_life_range(life_min, life_max, notify_scene=False)

        emissive = float(EMISSIVE_DEFAULT)
        try:
            emissive = float(_param_value(self._node_item.model, "emissive") or EMISSIVE_DEFAULT)
        except Exception:
            emissive = float(EMISSIVE_DEFAULT)
        emissive = max(EMISSIVE_MIN, min(EMISSIVE_MAX, float(emissive)))
        self._apply_emissive(emissive, notify_scene=False)

        softness = float(SOFTNESS_DEFAULT)
        try:
            softness = float(_param_value(self._node_item.model, "softness") or SOFTNESS_DEFAULT)
        except Exception:
            softness = float(SOFTNESS_DEFAULT)
        softness = max(SOFTNESS_MIN, min(SOFTNESS_MAX, float(softness)))
        self._apply_softness(softness, notify_scene=False)

        lighting = self._lighting_from_params()
        self._apply_lighting(lighting, notify_scene=False)

        bg_rgba = self._bg_rgba_from_params()
        self._apply_background(bg_rgba, notify_scene=False)

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
    h = max(
        int(body.sizeHint().height()),
        int(body.minimumSizeHint().height()),
        int(body.minimumHeight() or 0),
    )
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
