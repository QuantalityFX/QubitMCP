# echograph/ui/node_item.py
from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from echograph.model import GraphNode

import os
import datetime
import re
import hashlib
import json
import time
from pathlib import Path
from echograph.qt_compat import QtCore, QtGui, QtWidgets, QAction, QShortcut, QKeySequence, _qexec
from echograph.ui.dialogs import BigTextEditDialog
from echograph.ui import node_icons
from echograph.ui import actions

from echograph.constants import (
    LLM_URL, LLM_NODE_W_BASE, LLM_NODE_H_BASE, LLM_SCALE_DEFAULT,
    DEFAULT_STRIPE_HEX,
)

import nodes.core as core

try:
    from nodes.gpt_prompt import spec as _gpt_prompt_spec  # optional plugin
except Exception:  # pragma: no cover - optional
    _gpt_prompt_spec = None

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer
    from pygments.formatters import HtmlFormatter
    _HAS_PYGMENTS = True
except Exception:
    highlight = None
    get_lexer_for_filename = guess_lexer = TextLexer = HtmlFormatter = None
    _HAS_PYGMENTS = False

try:
    from pypdf import PdfReader
    _HAS_PYPDF = True
except Exception:
    PdfReader = None
    _HAS_PYPDF = False

# Optional WebEngine
try:
    from PySide6 import QtWebEngineWidgets as WebEngine
except Exception:
    try:
        from PySide2 import QtWebEngineWidgets as WebEngine
    except Exception:
        WebEngine = None


class _FeatureResizeHandle(QtWidgets.QWidget):
    """Thin draggable grip used to resize per-featured text blocks."""
    def __init__(self, drag_cb, release_cb=None, parent=None):
        super().__init__(parent)
        self._cb = drag_cb
        self._release_cb = release_cb
        self._dragging = False
        self._start_y = 0
        self.setFixedHeight(10)
        self.setCursor(QtCore.Qt.SizeVerCursor)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._start_y = e.globalY()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging and callable(self._cb):
            dy = e.globalY() - self._start_y
            if dy != 0:
                try:
                    self._cb(dy)
                except Exception:
                    pass
                # reset anchor so small drags accumulate smoothly
                self._start_y = e.globalY()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            try:
                if callable(self._release_cb):
                    self._release_cb()
            except Exception:
                pass
            e.accept()
            return
        super().mouseReleaseEvent(e)

class _ParamNameLabel(QtWidgets.QLabel):
    def __init__(self, text: str = "", dbl_click_cb=None, parent=None):
        super().__init__(text, parent)
        self._dbl_click_cb = dbl_click_cb
        self.setCursor(QtCore.Qt.IBeamCursor)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and callable(self._dbl_click_cb):
            try:
                self._dbl_click_cb()
            except Exception:
                pass
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

def _top_level_parent_for_dialog() -> QtWidgets.QWidget | None:
    aw = QtWidgets.QApplication.activeWindow()
    if aw and aw.isWindow():
        return aw
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.isWindow() and w.isVisible():
                return w
        except Exception:
            continue
    return None

def _spec_stripe_color(kind: str) -> str:
    k = (kind or "node").lower()
    try:
        spec = core.get_spec(k)
    except Exception:
        spec = None
    if spec is not None:
        if isinstance(spec, dict):
            c = spec.get("stripe_color") or spec.get("color") or spec.get("stripe")
            if c:
                return str(c)
        else:
            for attr in ("stripe_color", "color", "stripe"):
                try:
                    val = getattr(spec, attr)
                    if val:
                        return str(val)
                except Exception:
                    pass
    return DEFAULT_STRIPE_HEX

class NodeItem(QtWidgets.QGraphicsObject):
    clicked = QtCore.Signal(object)
    requestCenter = QtCore.Signal(str)
    startWireDrag = QtCore.Signal(object)
    switchIndexChanged = QtCore.Signal(object, int)

    _BASE_W = 220
    _BASE_H = 72
    _PARAM_ROW_H = 24
    _PADDING = 8
    _NOTE_FEATURED_H = 160
    _NOTE_FEATURED_MIN_H = 60
    _NOTE_FEATURED_MAX_H = 1200
    _NOTE_FEATURED_CTRL_H = 28
    _NOTE_FEATURED_HANDLE_H = 10
    _PORT_HIT_TOL = 9.0
    _IMG_CANVAS_W = 720
    _IMG_CANVAS_H = 420
    _IMG_CTRL_H = 40
    _CHATBOT_BODY_W = 420
    _CHATBOT_BODY_H = 360
    _IMPORT_THUMB_H = 120
    
    def _bring_to_front(self) -> None:
        sc = self.scene()
        try:
            if sc is not None:
                z = getattr(sc, "_echograph_z_counter", None)
                if z is None:
                    try:
                        z = max((it.zValue() for it in sc.items()), default=1.0)
                    except Exception:
                        z = 1.0
                z = float(z) + 1.0
                setattr(sc, "_echograph_z_counter", z)
                self.setZValue(z)
            else:
                self.setZValue(float(self.zValue()) + 1.0)
        except Exception:
            return

        # Lift embedded proxies with the node
        try:
            z_ui = float(self.zValue()) + 0.1
            for pr in list(getattr(self, "_param_proxies", []) or []):
                if pr is not None:
                    pr.setZValue(z_ui)
            for pr in list(getattr(self, "_plugin_proxies", []) or []):
                if pr is not None:
                    pr.setZValue(z_ui)
            pr = getattr(self, "_switch_proxy", None)
            if pr is not None:
                pr.setZValue(z_ui)
            pr = getattr(self, "_llm_proxy", None)
            if pr is not None:
                pr.setZValue(z_ui)
        except Exception:
            pass


    def __init__(self, model: GraphNode):
        try:
            super().__init__()
        except TypeError:
            super(NodeItem, self).__init__()

        self.model = model
        self.width = self._BASE_W
        self.height = self._BASE_H
        self.radius = 10
        self._transparent_body = (self.model.kind or "").lower() in ("image_collection", "imagecollection")
        # Ensure the ImageCollection spec is registered even if the loader was skipped.
        if (self.model.kind or "").strip().lower() in ("image_collection", "imagecollection"):
            try:
                from nodes import image_collection as _imgcol  # type: ignore
                if hasattr(_imgcol, "register"):
                    _imgcol.register()
            except Exception:
                pass
        # Ensure Chatbot spec is registered even if the loader was skipped.
        if (self.model.kind or "").strip().lower() in ("chatbot", "chat bot", "chat_bot"):
            try:
                from nodes import chatbot as _chatbot  # type: ignore
                if hasattr(_chatbot, "register"):
                    _chatbot.register()
            except Exception:
                pass
        # Ensure Scene spec is registered even if the loader was skipped.
        if (self.model.kind or "").strip().lower() in ("scene", "scene_assembly", "scene_outliner"):
            try:
                from nodes import scene as _scene  # type: ignore
                if hasattr(_scene, "register"):
                    _scene.register()
            except Exception:
                pass

        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)

        try:
            self.setCacheMode(QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache)
        except AttributeError:
            self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)

        # QGraphicsItem cache can block proxy-widget visuals (thumbnail) from refreshing
        if (self.model.kind or "").strip().lower() == "import":
            try:
                self.setCacheMode(QtWidgets.QGraphicsItem.CacheMode.NoCache)
            except Exception:
                try:
                    self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
                except Exception:
                    pass

        self.setZValue(1)

        self.pen = QtGui.QPen(QtGui.QColor("#7a8793"))
        self.titlePen = QtGui.QPen(QtGui.QColor("#e6edf3"))

        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton | QtCore.Qt.RightButton | QtCore.Qt.MiddleButton)

        self._param_proxies = []
        self._plugin_proxies = []

        self._switch_proxy = None
        self._llm_proxy = None
        self._llm_view = None  # kept for API parity if ever needed
        self._input_port_pos = {}
        self._live_dialogs: set[QtWidgets.QDialog] = set()
        self._busy = False
        self._busy_message = ""
        self._busy_flash_on = False
        self._busy_timer = QtCore.QTimer(self)
        self._busy_timer.setInterval(320)
        self._busy_timer.timeout.connect(self._on_busy_timeout)
        self._note_resize_mode: str | None = None
        self._note_resize_start = QtCore.QPointF()
        self._note_scene_start = QtCore.QPointF()
        self._note_initial_rect = QtCore.QRectF()
        self._note_initial_pos = QtCore.QPointF()
        self._note_resizing = False
        self._import_path_committed = None
        self._skip_release_super = False
       # Let the spec add named inputs (e.g., Librarian: query/docs_dir/mode/top_k/action)
        try:
            spec = core.get_spec((self.model.kind or "node").lower())
            build_ports = getattr(spec, "build_ports", None)
            if callable(build_ports):
                build_ports(self)
        except Exception:
            pass

        # Fallback for optional GPT prompt node kinds (ensure ports even if spec failed to register)
        try:
            if _gpt_prompt_spec and ((self.model.kind or "").strip().lower() in _gpt_prompt_spec.PROMPT_NODE_KINDS):
                _gpt_prompt_spec.build_ports(self)
        except Exception:
            pass

        if (self.model.kind or "").lower() in ("import", "html_preview"):
            params = list(self.model.params or [])
            names = {(p.get("name") or "").strip().lower() for p in params}
            if "path" not in names:
                params.append({"name": "path", "value": ""})
                self.model.params = params
        elif (self.model.kind or "").lower() == "note":
            # Ensure notes always start with at least one parameter for convenience
            if not (self.model.params or []):
                self.model.params = [{"name": "note", "value": ""}]

        self._recompute_height()
        self._build_widgets()
        if (self.model.kind or "").lower() in ("import", "html_preview"):
            self._import_path_committed = (self._param_value("path") or "").strip()
        # --- named-input helpers (used by plugin specs like Librarian) ---

    def _ensure_named_inputs_set(self):
        try:
            store = getattr(self.model, "_named_inputs", None)
        except Exception:
            store = None

        if isinstance(store, list):
            return
        if isinstance(store, set):
            setattr(self.model, "_named_inputs", [str(n) for n in store if n])
            return
        if store is None:
            setattr(self.model, "_named_inputs", [])
            return
        try:
            setattr(self.model, "_named_inputs", [str(n) for n in store if n])
        except Exception:
            setattr(self.model, "_named_inputs", [])

    def input_port_names(self) -> list[str]:
        self._ensure_named_inputs_set()
        return [str(n) for n in getattr(self.model, "_named_inputs", []) if n]
    
    def _set_param_value(self, name: str, value: str, rebuild: bool = True, notify_scene: bool = True):
        key = (name or "").strip().lower()
        is_import_path = key == "path" and (self.model.kind or "").lower() in ("import", "html_preview")
        if is_import_path:
            value = (value or "").strip()

        params = list(self.model.params or [])
        found = False
        for p in params:
            if (p.get("name", "") or "").strip().lower() == key:
                p["value"] = value
                found = True
                break
        if not found:
            params.append({"name": name, "value": value})

        # keep local model in sync (for saving)
        self.model.params = params
        if is_import_path:
            self._import_path_committed = value

        # optionally notify the scene (this is what can cause reload side effects)
        if notify_scene:
            sc = self.scene()
            if sc:
                try:
                    sc.set_node_params(self.model.name, params, rebuild=rebuild)
                except Exception:
                    pass

    def _param_value(self, name: str) -> str:
        key = (name or "").strip().lower()
        for p in (self.model.params or []):
            if (p.get("name", "") or "").strip().lower() == key:
                return p.get("value", "") or ""
        return ""

    def _ui_hidden_params_set(self) -> set:
        """
        Returns the set of param names hidden on the node surface.

        Rule:
        - If the node has an explicit "__ui_hidden_params" param (even empty), use that list as the source of truth.
        - If it does NOT exist, fall back to defaults.
        """
        hidden = set()

        # read explicit list (if present)
        raw = None
        for p in (self.model.params or []):
            nm = (p.get("name") or "").strip().lower()
            if nm == "__ui_hidden_params":
                raw = p.get("value", "")
                break

        if raw is not None:
            # explicit override mode
            for tok in str(raw).split(","):
                key = tok.strip().lower()
                if key:
                    hidden.add(key)
            return hidden

        # fallback defaults (only when "__ui_hidden_params" not present at all)
        kind = (self.model.kind or "").lower()
        if kind == "import":
            hidden.update({"thumbnail", "thumbnail_rev", "thumbnail_choice"})
        elif kind in ("scene", "scene_assembly", "scene_outliner"):
            hidden.update({"thumbnail", "thumbnail_rev", "thumbnail_choice", "splat_depth_test"})

        return hidden


    @QtCore.Slot(bool, str)
    def setBusyState(self, busy: bool, message: str = "") -> None:
        busy = bool(busy)
        message = (message or "").strip()
        if busy == self._busy and message == self._busy_message:
            return
        self._busy = busy
        self._busy_message = message
        if busy:
            self._busy_flash_on = True
            if not self._busy_timer.isActive():
                self._busy_timer.start()
        else:
            self._busy_timer.stop()
            self._busy_flash_on = False
        self.update()

    def _on_busy_timeout(self) -> None:
        self._busy_flash_on = not self._busy_flash_on
        self.update()

    def port_anchor(self, name: str, side: str = "in") -> QtCore.QPointF:
        """Return scene-relative anchor point for a named port bead."""
        entry = getattr(self, "_input_port_pos", {}).get((name or "").strip().lower())
        if entry:
            point = entry[0]
            try:
                return self.mapToScene(point)
            except Exception:
                return self.scenePos() + QtCore.QPointF(point.x(), point.y())
        if side == "in":
            try:
                return self.mapToScene(QtCore.QPointF(0, self._BASE_H / 2.0))
            except Exception:
                return self.scenePos() + QtCore.QPointF(0, self._BASE_H / 2.0)
        try:
            return self.mapToScene(QtCore.QPointF(self.width, self._BASE_H / 2.0))
        except Exception:
            return self.scenePos() + QtCore.QPointF(self.width, self._BASE_H / 2.0)


    def ensure_input(self, name: str):
        self._ensure_named_inputs_set()
        if not name:
            return
        try:
            names = getattr(self.model, "_named_inputs", [])
            if str(name) not in names:
                names.append(str(name))
        except Exception:
            existing = [str(n) for n in getattr(self.model, "_named_inputs", []) if n]
            if str(name) not in existing:
                existing.append(str(name))
            setattr(self.model, "_named_inputs", existing)

    def input_port_hit(self, local_point, tolerance: float | None = None) -> str | None:
        """
        Return the named input hit by a left-socket interaction.
        Accepts a QPointF in local coords.
        """
        if not hasattr(local_point, "x"):
            return None
        try:
            lx = float(local_point.x())
            ly = float(local_point.y())
        except Exception:
            return None

        tol = float(self._PORT_HIT_TOL if tolerance is None else tolerance)
        for name_key, entry in getattr(self, "_input_port_pos", {}).items():
            pos, canonical = entry
            dx = lx - float(pos.x())
            dy = ly - float(pos.y())
            if (dx * dx + dy * dy) ** 0.5 <= tol:
                return canonical or name_key
        return None

    def _wired_named_inputs(self) -> set[str]:
        wired = set()
        sc = self.scene()
        if sc is None:
            return wired
        try:
            in_edges = sc._in_edges(self)
        except Exception:
            in_edges = []
        for e in in_edges:
            name = getattr(e, "dst_port_name", None) or getattr(e, "dst_label", None) or getattr(e, "dst_name", None)
            if name:
                wired.add(str(name).strip().lower())
        return wired

    # back-compat aliases some specs may call
    def add_input_port(self, name: str):
        self.ensure_input(name)

    def add_input(self, name: str):
        self.ensure_input(name)

    def _current_llm_scale(self) -> float:
        sc = self.scene()
        try:
            return float(getattr(sc, "_llm_scale", LLM_SCALE_DEFAULT))
        except Exception:
            return float(LLM_SCALE_DEFAULT)

    def _rebuild_deferred(self):
        """Recompute + rebuild on next event-loop tick to avoid re-entrancy/tearing."""
        try:
            from PySide6 import QtCore as _QtCore
        except Exception:
            from PySide2 import QtCore as _QtCore
        if getattr(self, "_rebuild_pending", False):
            return
        self._rebuild_pending = True
        _QtCore.QTimer.singleShot(0, lambda: (
            setattr(self, "_rebuild_pending", False),
            self._recompute_height(),
            self._build_widgets()
        ))


    def _get_featured_set(self):
        """Return a COPY of featured param names (set[str]) on this node's model."""
        feat = getattr(self.model, "_featured_params", None)

        out = set()
        if isinstance(feat, set):
            out.update(feat)
        elif isinstance(feat, (list, tuple)):
            out.update([str(x) for x in feat if x])
        elif isinstance(feat, str) and feat:
            out.add(feat)

        # migrate legacy single string once
        old = getattr(self.model, "_featured_param", "")
        if isinstance(old, str) and old:
            out.add(old)
            try: setattr(self.model, "_featured_param", "")
            except Exception: pass

        return out

    
    def _pruned_featured_set(self, valid_names: set[str]) -> set[str]:
        """Return featured set intersect valid_names and write back if anything was pruned."""
        feat = self._get_featured_set()
        pruned = {n for n in feat if n in valid_names}
        if pruned != feat:
            self._set_featured_set(pruned)
        self._featured_heights_map(valid_names)  # prune heights for missing params
        return pruned

    def _set_featured_set(self, names: set[str]):
        """Write back a NEW set instance (copy-on-write)."""
        setattr(self.model, "_featured_params", set(names))

    def _featured_heights_map(self, valid_names: set[str] | None = None) -> dict[str, float]:
        """Return a sanitized mapping of featured heights; prune names not in valid_names."""
        raw = getattr(self.model, "_featured_heights", None)
        out: dict[str, float] = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                name = str(k)
                if valid_names is not None and name not in valid_names:
                    continue
                try:
                    h = float(v)
                except Exception:
                    continue
                if h > 0:
                    out[name] = h
        if valid_names is not None and isinstance(raw, dict):
            pruned_keys = {str(k) for k in raw.keys() if str(k) in valid_names}
            if set(out.keys()) != pruned_keys:
                try:
                    setattr(self.model, "_featured_heights", dict(out))
                except Exception:
                    pass
        return out

    def _clamp_featured_height(self, h: float | None) -> float:
        try:
            val = float(h)
        except Exception:
            val = float(self._NOTE_FEATURED_H)
        return max(self._NOTE_FEATURED_MIN_H, min(self._NOTE_FEATURED_MAX_H, val))

    def _featured_block_height(self, name: str, heights_map: dict[str, float] | None = None) -> float:
        hm = heights_map if isinstance(heights_map, dict) else None
        if hm is None:
            hm = self._featured_heights_map()
        h = hm.get(name) if isinstance(hm, dict) else None
        return self._clamp_featured_height(h)

    def _set_featured_height(self, name: str, height: float):
        if not name:
            return
        h = self._clamp_featured_height(height)
        raw = getattr(self.model, "_featured_heights", None)
        if not isinstance(raw, dict):
            raw = {}
        raw[str(name)] = h
        try:
            setattr(self.model, "_featured_heights", raw)
        except Exception:
            pass

    def _rename_featured_param(self, old_name: str, new_name: str) -> None:
        if not old_name or not new_name or old_name == new_name:
            return
        try:
            feat = self._get_featured_set()
            if old_name in feat:
                feat.discard(old_name)
                feat.add(new_name)
                self._set_featured_set(feat)
        except Exception:
            pass
        raw = getattr(self.model, "_featured_heights", None)
        if isinstance(raw, dict) and old_name in raw:
            try:
                raw[new_name] = raw.pop(old_name)
                setattr(self.model, "_featured_heights", raw)
            except Exception:
                pass
        try:
            if getattr(self.model, "_featured_param", "") == old_name:
                setattr(self.model, "_featured_param", new_name)
        except Exception:
            pass

    def _rename_hidden_param_value(self, params: list, old_name: str, new_name: str) -> None:
        if not old_name or not new_name or old_name == new_name:
            return
        old_key = old_name.strip().lower()
        new_key = new_name.strip().lower()
        if not old_key or not new_key:
            return
        for p in params:
            nm = (p.get("name") or "").strip().lower()
            if nm != "__ui_hidden_params":
                continue
            raw = p.get("value", "")
            items = []
            changed = False
            for tok in str(raw).split(","):
                key = tok.strip()
                if not key:
                    continue
                if key.lower() == old_key:
                    items.append(new_key)
                    changed = True
                else:
                    items.append(key)
            if changed:
                p["value"] = ",".join(items)
            return

    def _unique_param_name_for_rename(self, desired: str, idx: int) -> str:
        base = (desired or "").strip() or "param"
        existing = {
            (p.get("name") or "").strip().lower()
            for i, p in enumerate(self.model.params or [])
            if i != idx
        }
        if base.strip().lower() not in existing:
            return base
        i = 2
        while True:
            candidate = f"{base} {i}"
            if candidate.strip().lower() not in existing:
                return candidate
            i += 1

    def _apply_param_rename(self, idx: int, new_name: str) -> str | None:
        params = list(self.model.params or [])
        if idx < 0 or idx >= len(params):
            return None
        old = (params[idx].get("name") or "").strip()
        desired = (new_name or "").strip()
        if not desired:
            return old
        final = self._unique_param_name_for_rename(desired, idx)
        if final == old:
            return old
        params[idx]["name"] = final
        self._rename_featured_param(old, final)
        self._rename_hidden_param_value(params, old, final)
        self.model.params = params

        def _apply_to_scene():
            sc = self.scene()
            if sc and hasattr(sc, "set_node_params"):
                try:
                    sc.set_node_params(self.model.name, params)
                except Exception:
                    pass
        try:
            QtCore.QTimer.singleShot(0, _apply_to_scene)
        except Exception:
            _apply_to_scene()

        self._schedule_rebuild()
        return final

    def _prompt_rename_param(self, idx: int) -> None:
        params = list(self.model.params or [])
        if idx < 0 or idx >= len(params):
            return
        old = (params[idx].get("name") or "").strip()
        if not old:
            old = "param"
        parent = _top_level_parent_for_dialog()
        text, ok = QtWidgets.QInputDialog.getText(
            parent, "Rename Parameter", "New name:", QtWidgets.QLineEdit.Normal, old
        )
        if not ok:
            return
        new_name = (text or "").strip()
        if not new_name:
            return
        self._apply_param_rename(idx, new_name)


    def _unique_param_name(self, base: str = "param") -> str:
        existing = {(p.get("name") or "").strip().lower() for p in (self.model.params or [])}
        if base.strip().lower() not in existing:
            return base
        i = 2
        while True:
            candidate = f"{base} {i}"
            if candidate.strip().lower() not in existing:
                return candidate
            i += 1

    def _append_param(self, name: str | None = None, value: str = ""):
        base = (name or "param").strip() or "param"
        nm = self._unique_param_name(base)
        params = list(self.model.params or [])
        params.append({"name": nm, "value": value})
        self.model.params = params
        # Defer scene updates to avoid re-entrancy while building widgets
        def _apply_to_scene():
            sc = self.scene()
            if sc and hasattr(sc, "set_node_params"):
                try:
                    sc.set_node_params(self.model.name, params)
                except Exception:
                    pass
        try:
            QtCore.QTimer.singleShot(0, _apply_to_scene)
        except Exception:
            _apply_to_scene()
        self._schedule_rebuild()

    def _pop_last_param(self):
        params = list(self.model.params or [])
        if len(params) <= 1:
            return
        removed = params.pop()
        self.model.params = params
        pname = (removed.get("name") or "").strip()
        # prune featured markers/heights
        try:
            feat = self._get_featured_set()
            if pname in feat:
                feat.discard(pname)
                self._set_featured_set(feat)
        except Exception:
            pass
        raw = getattr(self.model, "_featured_heights", None)
        if isinstance(raw, dict) and pname:
            try:
                raw.pop(pname, None)
                setattr(self.model, "_featured_heights", raw)
            except Exception:
                pass

        def _apply_to_scene():
            sc = self.scene()
            if sc and hasattr(sc, "set_node_params"):
                try:
                    sc.set_node_params(self.model.name, params)
                except Exception:
                    pass
        try:
            QtCore.QTimer.singleShot(0, _apply_to_scene)
        except Exception:
            _apply_to_scene()
        self._schedule_rebuild()


    def _normalize_url(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return LLM_URL
        if not re.match(r'^[a-zA-Z]+://', s):
            s = "http://" + s
        return s

    def _llm_url_from_params(self) -> str:
        for p in (self.model.params or []):
            nm = (p.get("name","") or "").lower()
            if nm in ("url", "address", "endpoint"):
                return self._normalize_url(p.get("value",""))
        return LLM_URL
    
    def _recompute_height(self):
        kind = (self.model.kind or "").lower()

        # Baseline used by y_cursor in _build_widgets
        header_h = 38 + 16 + self._PADDING

        # Switch row
        switch_h = self._PARAM_ROW_H if kind == "switch" else 0

        # Params block (regular rows) - must match actual node visibility rules
        hidden = self._ui_hidden_params_set()

        n_params = 0
        for p in (self.model.params or []):
            nm = (p.get("name", "") or "").strip().lower()
            if not nm:
                continue
            if nm == "__ui_hidden_params":
                continue
            if nm in hidden:
                continue
            n_params += 1

        params_h = n_params * self._PARAM_ROW_H

        # Note: add a big block per featured param (prune orphans first)
        feat_heights = {}
        add_ctrl_h = 0
        if kind == "note":
            try:
                current_names = { (p.get("name") or "") for p in (self.model.params or []) if (p.get("name") or "") }
                feat_set = self._pruned_featured_set(current_names)
                feat_heights = self._featured_heights_map(current_names)
                params_h += sum(self._featured_block_height(nm, feat_heights) for nm in feat_set)
            except Exception:
                pass
            add_ctrl_h = self._PARAM_ROW_H
            params_h += add_ctrl_h

        if n_params:
            params_h += self._PADDING  # breathing room below params

        # Kind-specific body additions
        if kind == "llm":
            S = self._current_llm_scale()
            body_h = int(LLM_NODE_H_BASE * S)
            node_w = max(self._BASE_W, int(LLM_NODE_W_BASE * S))
        elif kind == "append":
            count = max(1, len(self.model.switch_inputs or []))
            body_h = 6 + count * self._PARAM_ROW_H
            node_w = self._BASE_W
        elif kind == "import":
            node_w = self._BASE_W  # define first

            path = (self._param_value("path") or "").strip()
            ext = os.path.splitext(path)[1].lower()

            inner_w = max(40, int(node_w) - 12)
            detail, _ = self._file_detail_for_path(path)

            fm = QtGui.QFontMetrics(QtWidgets.QApplication.font())
            rect = fm.boundingRect(QtCore.QRect(0, 0, inner_w, 10_000), QtCore.Qt.TextWordWrap, detail)
            text_h = rect.height()

            obj_extra = self._PARAM_ROW_H if ext == ".obj" else 0  # texture row
            btn_h = 24
            spacing = 4
            bottom_margin = 0

            summary_h = max(
                self._PARAM_ROW_H * 2 + obj_extra,
                text_h + spacing + btn_h + bottom_margin + obj_extra,
            )

            #body_h = summary_h + self._PADDING
            body_h = summary_h  # summary widget already has its own bottom margin

            thumb = (self._param_value("thumbnail") or "").strip()
            if ext in (".fbx", ".obj", ".gltf", ".glb", ".ply") and thumb and os.path.exists(thumb):
                inner_w = max(40, int(node_w) - 12)  # matches preview inner width
                body_h += inner_w + self._PADDING    # square preview height
        elif kind == "html_preview":
            body_h = self._html_preview_body_height()
            preview_w, _ = self._html_preview_dimensions()
            path = (self._param_value("path") or "").strip()
            if path and os.path.exists(path):
                node_w = preview_w
            else:
                node_w = self._BASE_W
        elif kind in ("scene", "scene_assembly", "scene_outliner"):
            body_h = self._PARAM_ROW_H * 2 + self._PADDING
            node_w = self._BASE_W
            thumb = (self._param_value("thumbnail") or "").strip()
            if thumb and os.path.exists(thumb):
                inner_w = max(40, int(node_w) - 12)
                body_h += inner_w + self._PADDING
        elif kind in ("image_collection", "imagecollection"):
            body_h = self._IMG_CTRL_H + self._IMG_CANVAS_H
            node_w = max(self._BASE_W, self._IMG_CANVAS_W)
        elif kind in ("chatbot", "chat bot", "chat_bot"):
            body_h = self._CHATBOT_BODY_H
            node_w = max(self._BASE_W, self._CHATBOT_BODY_W)
        else:
            body_h = 0
            node_w = self._BASE_W

        new_w = max(node_w, self._BASE_W)
        extra_pad = self._PADDING
        if kind in ("import", "scene", "scene_assembly", "scene_outliner"):
            extra_pad = 0.0
        new_h = max(self._BASE_H, header_h + switch_h + params_h + body_h + extra_pad)

        if kind == "note":
            custom_size = getattr(self.model, "_note_size", None)
            if isinstance(custom_size, (list, tuple)) and len(custom_size) >= 2:
                try:
                    custom_w = float(custom_size[0])
                    custom_h = float(custom_size[1])
                except Exception:
                    custom_w = custom_h = None
                if custom_w is not None and custom_w > 0:
                    new_w = max(new_w, max(self._BASE_W, custom_w))
                if custom_h is not None and custom_h > 0:
                    new_h = max(new_h, max(self._BASE_H, custom_h))
        elif kind in ("chatbot", "chat bot", "chat_bot"):
            try:
                self._chatbot_min_w = float(new_w)
                self._chatbot_min_h = float(new_h)
            except Exception:
                pass
            custom_size = getattr(self.model, "_chatbot_size", None)
            if isinstance(custom_size, (list, tuple)) and len(custom_size) >= 2:
                try:
                    custom_w = float(custom_size[0])
                    custom_h = float(custom_size[1])
                except Exception:
                    custom_w = custom_h = None
                if custom_w is not None and custom_w > 0:
                    new_w = max(new_w, max(self._BASE_W, custom_w))
                if custom_h is not None and custom_h > 0:
                    new_h = max(new_h, max(self._BASE_H, custom_h))

        if new_w != getattr(self, "width", 0) or new_h != getattr(self, "height", 0):
            try:
                self.prepareGeometryChange()
            except Exception:
                pass
            self.width = new_w
            self.height = new_h


    def _clear_widget_proxies(self):
        """Safely tear down all embedded proxy widgets (switch, params, plugins, LLM)."""

        def _kill_proxy(pr):
            if not pr:
                return
            try:
                # If this is a QGraphicsProxyWidget, detach its child widget first
                if isinstance(pr, QtWidgets.QGraphicsProxyWidget):
                    try:
                        w = pr.widget()
                    except Exception:
                        w = None
                    try:
                        pr.setWidget(None)  # detach to avoid re-entrant destruction crashes
                    except Exception:
                        pass
                    if w is not None:
                        try:
                            w.deleteLater()  # schedule actual Qt widget deletion
                        except Exception:
                            pass
                # Remove the proxy itself from the scene
                try:
                    sc = self.scene()
                    if sc:
                        sc.removeItem(pr)
                except Exception:
                    pass
            except Exception:
                pass

        # --- Switch row proxy ---
        _kill_proxy(getattr(self, "_switch_proxy", None))
        self._switch_proxy = None

        # --- Parameter row proxies ---
        for pr in list(getattr(self, "_param_proxies", [])):
            _kill_proxy(pr)
        try:
            self._param_proxies[:] = []
        except Exception:
            self._param_proxies = []

        # --- Plugin-provided proxies (e.g., Note big preview area) ---
        for pr in list(getattr(self, "_plugin_proxies", [])):
            _kill_proxy(pr)
        try:
            self._plugin_proxies[:] = []
        except Exception:
            self._plugin_proxies = []

        # --- LLM proxy/view bits ---
        _kill_proxy(getattr(self, "_llm_proxy", None))
        self._llm_proxy = None

        # Clear any cached view/label/sampler refs
        try:
            self._llm_view = None
        except Exception:
            pass
        try:
            self._llm_label = None
        except Exception:
            pass
        try:
            # Some branches may still have this attribute around
            if hasattr(self, "_llm_sampler"):
                self._llm_sampler = None
        except Exception:
            pass

    def _render_image_collection_inline(self, y_cursor: int) -> int:
        """Fallback renderer if plugin registration failed; keeps button visible."""
        try:
            from PySide6 import QtWidgets as _QtWidgets, QtGui as _QtGui, QtCore as _QtCore
        except Exception:
            from PySide2 import QtWidgets as _QtWidgets, QtGui as _QtGui, QtCore as _QtCore  # type: ignore

        node_item = self

        class _InlineCanvas(_QtWidgets.QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setAttribute(_QtCore.Qt.WA_TranslucentBackground, True)
                self.setAutoFillBackground(False)
                self._apply_canvas_size()
                self.pixmaps = []
                self.offsets = []
                self.paths: list[str] = []
                self.rects: list[_QtCore.QRect] = []
                self.selected_index = -1
                self._edit_mode = False
                self._path_field = None

            def _apply_canvas_size(self):
                w = max(120, int(node_item.width) - 12)
                h = int(node_item._IMG_CANVAS_H)
                self.setFixedSize(w, h)

            def load_images(self, paths, *, append: bool = True):
                base = list(self.paths) if append else []
                combined = base + [p for p in paths if p]
                seen = set()
                dedup = []
                for p in combined:
                    if p in seen:
                        continue
                    seen.add(p)
                    dedup.append(p)

                loaded = []
                for p in dedup:
                    pm = _QtGui.QPixmap(p)
                    if not pm.isNull():
                        loaded.append((p, pm))
                if not loaded:
                    return
                self._apply_canvas_size()

                edge_pad = 4
                gap = 4
                avail_w = max(1, self.width() - 2 * edge_pad)
                avail_h = max(1, self.height() - 2 * edge_pad)

                # Choose an initial row height based on how many rows we expect.
                cols_hint = 3
                rows_est = max(1, (len(loaded) + cols_hint - 1) // cols_hint)
                target_row_h = max(60, min(220, int(avail_h / rows_est)))

                def _build_layout(target_h: int):
                    # Provisional widths at target_h
                    items = []
                    for pth, pm in loaded:
                        w, h = pm.width(), pm.height()
                        if w <= 0 or h <= 0:
                            continue
                        scaled_w = int(w * (target_h / float(h)))
                        items.append((pth, pm, scaled_w, target_h))

                    rows = []
                    row = []
                    row_w = 0
                    for pth, pm, sw, th in items:
                        est = row_w + sw if not row else row_w + gap + sw
                        if row and est > avail_w:
                            rows.append((row, row_w))
                            row = []
                            row_w = 0
                        row.append((pth, pm, sw, th))
                        row_w += sw if not row[:-1] else sw
                    if row:
                        rows.append((row, row_w))

                    row_heights = []
                    row_scales = []
                    for row, rw in rows:
                        n = len(row)
                        available_w = max(1, avail_w - gap * max(0, n - 1))
                        scale = available_w / float(rw) if rw > 0 else 1.0
                        row_scales.append(scale)
                        row_heights.append(int(target_h * scale))

                    total_h = sum(row_heights) + gap * max(0, len(row_heights) - 1)
                    return rows, row_scales, row_heights, total_h

                rows, row_scales, row_heights, total_h = _build_layout(target_row_h)
                # If too tall, reduce target_row_h proportionally and rebuild once.
                if total_h > avail_h:
                    shrink = float(avail_h) / float(total_h)
                    new_h = max(40, int(target_row_h * shrink))
                    rows, row_scales, row_heights, total_h = _build_layout(new_h)

                pixmaps_out = []
                offsets_out = []
                y = edge_pad
                for (row, _rw), rscale, rheight in zip(rows, row_scales, row_heights):
                    x = edge_pad
                    for pth, pm, sw, th in row:
                        final_w = max(1, int(sw * rscale))
                        final_h = max(1, int(th * rscale))
                        pixmaps_out.append(pm.scaled(final_w, final_h, _QtCore.Qt.KeepAspectRatio, _QtCore.Qt.SmoothTransformation))
                        offsets_out.append(_QtCore.QPoint(x, y))
                        x += final_w + gap
                    y += int(rheight) + gap

                self.pixmaps = pixmaps_out
                self.paths = [p for p, _ in loaded]
                self.offsets = offsets_out
                self.rects = []
                for pm, off in zip(self.pixmaps, self.offsets):
                    self.rects.append(_QtCore.QRect(int(off.x()), int(off.y()), pm.width(), pm.height()))
                if self.selected_index >= len(self.pixmaps):
                    self.selected_index = -1
                self.update()

            def paintEvent(self, _ev):
                painter = _QtGui.QPainter(self)
                for pm, off in zip(self.pixmaps, self.offsets):
                    painter.drawPixmap(off, pm)
                if self.selected_index >= 0 and self.selected_index < len(self.rects):
                    pen = _QtGui.QPen(_QtGui.QColor("#60a5fa"), 2)
                    pen.setCosmetic(True)
                    painter.setPen(pen)
                    painter.setBrush(_QtCore.Qt.NoBrush)
                    painter.drawRect(self.rects[self.selected_index].adjusted(-2, -2, 2, 2))
                painter.end()

            def sizeHint(self):
                return _QtCore.QSize(int(node_item._IMG_CANVAS_W), int(node_item._IMG_CANVAS_H))

            def set_edit_mode(self, enabled: bool):
                self._edit_mode = bool(enabled)
                if not enabled:
                    self.selected_index = -1
                    self.update()

            def mousePressEvent(self, ev):
                if not self._edit_mode:
                    return super().mousePressEvent(ev)
                pos = ev.pos()
                hit = -1
                for idx, rect in enumerate(self.rects):
                    if rect.contains(pos):
                        hit = idx
                        break
                if hit != -1:
                    self.selected_index = hit
                    self.update()
                    try:
                        if self._path_field is not None:
                            self._path_field.setText(self.paths[hit] if hit < len(self.paths) else "")
                    except Exception:
                        pass
                else:
                    super().mousePressEvent(ev)

            def selected_path(self) -> str:
                if self.selected_index >= 0 and self.selected_index < len(self.paths):
                    return self.paths[self.selected_index]
                return ""

            def remove_selected(self) -> str:
                if self.selected_index < 0 or self.selected_index >= len(self.paths):
                    return ""
                idx = self.selected_index
                removed_path = self.paths.pop(idx)
                try:
                    self.pixmaps.pop(idx)
                    self.offsets.pop(idx)
                    self.rects.pop(idx)
                except Exception:
                    pass
                # recompute rects positions stay same; selection clears
                self.selected_index = -1
                try:
                    if self._path_field is not None:
                        self._path_field.clear()
                except Exception:
                    pass
                self.update()
                return removed_path

        body = _QtWidgets.QWidget()
        body.setAttribute(_QtCore.Qt.WA_TranslucentBackground, True)
        body.setAutoFillBackground(False)
        v = _QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        header = _QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        btn = _QtWidgets.QPushButton("Load Images")
        btn.setSizePolicy(_QtWidgets.QSizePolicy.Fixed, _QtWidgets.QSizePolicy.Fixed)
        btn.setMinimumWidth(100)
        btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )
        edit_btn = _QtWidgets.QPushButton("Edit")
        edit_btn.setSizePolicy(_QtWidgets.QSizePolicy.Fixed, _QtWidgets.QSizePolicy.Fixed)
        edit_btn.setMinimumWidth(80)
        edit_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
            "QPushButton:checked{background:#38bdf8;color:#0f172a;border-color:#38bdf8;}"
        )
        header.addWidget(btn, 0)
        header.addWidget(edit_btn, 0)
        header.addStretch(1)
        remove_btn = _QtWidgets.QPushButton("Remove")
        remove_btn.setSizePolicy(_QtWidgets.QSizePolicy.Fixed, _QtWidgets.QSizePolicy.Fixed)
        remove_btn.setMinimumWidth(90)
        remove_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#7f1d1d;border-color:#b91c1c;}"
        )
        header.addWidget(remove_btn, 0)
        v.addLayout(header)

        canvas = _InlineCanvas()
        v.addWidget(canvas, 1)

        path_row = _QtWidgets.QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        path_row.addWidget(_QtWidgets.QLabel("Path:"))
        path_field = _QtWidgets.QLineEdit()
        path_field.setReadOnly(True)
        path_field.setPlaceholderText("Select an image in Edit mode to copy its path")
        path_field.setStyleSheet("QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;border-radius:4px;padding:4px 6px;}")
        path_row.addWidget(path_field, 1)
        explorer_btn = _QtWidgets.QToolButton()
        explorer_btn.setAutoRaise(True)
        explorer_btn.setToolTip("Open folder")
        try:
            exp_icon = _QtGui.QIcon(str(Path(__file__).resolve().parents[2] / "icons" / "explorer_button_icon.png"))
            explorer_btn.setIcon(exp_icon)
        except Exception:
            explorer_btn.setText("\N{OPEN FILE FOLDER}")
        explorer_btn.clicked.connect(lambda: _open_in_explorer(path_field.text()))
        path_row.addWidget(explorer_btn, 0)
        path_container = _QtWidgets.QWidget()
        path_container.setLayout(path_row)
        path_container.setVisible(False)
        v.addWidget(path_container)

        def _pick():
            # Prefer a native dialog tied to the top-level window to avoid overlay artifacts.
            try:
                parent_win = _top_level_parent_for_dialog()
            except Exception:
                parent_win = None
            paths, _ = _QtWidgets.QFileDialog.getOpenFileNames(
                parent_win,
                "Select Images",
                "",
                "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
            )
            if not paths:
                return
            canvas.load_images(paths, append=True)
            try:
                existing = getattr(node_item.model, "_image_collection_state", {}) or {}
                base = list(existing.get("paths", []))
                combined = base + list(paths)
                seen = set()
                dedup = []
                for p in combined:
                    if p in seen:
                        continue
                    seen.add(p)
                    dedup.append(p)
                setattr(node_item.model, "_image_collection_state", {"paths": dedup})
            except Exception:
                pass

        btn.clicked.connect(_pick)

        def _toggle_edit(enabled: bool):
            canvas.set_edit_mode(enabled)
            try:
                node_item.setFlag(_QtWidgets.QGraphicsItem.ItemIsMovable, not enabled)
            except Exception:
                pass
            if not enabled:
                try:
                    path_field.clear()
                except Exception:
                    pass
            try:
                path_container.setVisible(enabled)
            except Exception:
                pass

        edit_btn.setCheckable(True)
        edit_btn.toggled.connect(_toggle_edit)

        # give canvas a handle to update path when selecting
        try:
            canvas._path_field = path_field
        except Exception:
            pass

        def _remove_selected():
            if not canvas._edit_mode:
                _QtWidgets.QMessageBox.information(node_item, "Image Collection", "Enable Edit mode and select an image first.")
                return
            sel_path = canvas.selected_path()
            if not sel_path:
                _QtWidgets.QMessageBox.information(node_item, "Image Collection", "Select an image in Edit mode to remove it.")
                return
            removed = canvas.remove_selected()
            if removed:
                try:
                    st = getattr(node_item.model, "_image_collection_state", {}) or {}
                    paths = list(st.get("paths", []))
                    try:
                        paths = [p for p in paths if p != removed]
                    except Exception:
                        pass
                    setattr(node_item.model, "_image_collection_state", {"paths": paths})
                except Exception:
                    pass
            else:
                _QtWidgets.QMessageBox.information(node_item, "Image Collection", "Select an image in Edit mode to remove it.")

        remove_btn.clicked.connect(_remove_selected)

        # restore prior state paths
        try:
            st = getattr(node_item.model, "_image_collection_state", {}) or {}
            paths = st.get("paths", [])
            if paths:
                canvas.load_images(paths, append=False)
        except Exception:
            pass

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


    def _build_widgets(self):
        # prevent re-entrancy while we're tearing down/creating proxies
        if getattr(self, "_is_building", False):
            return
        self._is_building = True
        try:
            self._clear_widget_proxies()
            y_cursor = 38 + 16 + self._PADDING

            kind_lower = (self.model.kind or "").strip().lower()
            defer_plugin = kind_lower in ("chatbot", "chat bot", "chat_bot")
            deferred_render = None
            if kind_lower in ("chatbot", "chat bot", "chat_bot"):
                try:
                    params = list(self.model.params or [])
                    desired = ["llm_prompt", "database"]
                    ordered = []
                    used = set()
                    for name in desired:
                        key = name.strip().lower()
                        for idx, entry in enumerate(params):
                            if idx in used or not isinstance(entry, dict):
                                continue
                            if (entry.get("name") or "").strip().lower() == key:
                                ordered.append(entry)
                                used.add(idx)
                                break
                    for idx, entry in enumerate(params):
                        if idx not in used:
                            ordered.append(entry)
                    self.model.params = ordered
                except Exception:
                    pass

            # --- Inline ImageCollection body to guarantee the load button is present ---
            if kind_lower in ("image_collection", "imagecollection"):
                y_cursor = self._render_image_collection_inline(y_cursor)
                # Skip plugin render; inline version is authoritative
                kind_lower = None
            # --- Inline Scene body to guarantee the view button is present ---
            if kind_lower in ("scene", "scene_assembly", "scene_outliner"):
                kind_lower = None

            # --- Plugin body hook (lets specs draw a custom node body) ---
            try:
                spec = core.get_spec((self.model.kind or "node").lower()) if kind_lower else None
                render = None
                if isinstance(spec, dict):
                    render = spec.get("render_node_body")
                else:
                    render = getattr(spec, "render_node_body", None)
                if callable(render):
                    if defer_plugin:
                        deferred_render = render
                    else:
                        pre_plugin_count = len(getattr(self, "_plugin_proxies", []) or [])
                        new_y = render(self, y_cursor)
                        if isinstance(new_y, (int, float)):
                            y_cursor = int(new_y)
                        post_plugin_count = len(getattr(self, "_plugin_proxies", []) or [])
                        # If nothing was added, keep y_cursor unchanged
                        if post_plugin_count == pre_plugin_count:
                            y_cursor = y_cursor
            except Exception as e:
                print("[EchoGraph] render_node_body error:", e)

            # --- Append node body: list connected node names in current order ---
            if (self.model.kind or "").lower() == "append":
                body = QtWidgets.QWidget()
                v = QtWidgets.QVBoxLayout(body)
                v.setContentsMargins(6, 6, 6, 6)
                v.setSpacing(2)

                names = list(self.model.switch_inputs or [])
                if not names:
                    lbl = QtWidgets.QLabel("No inputs connected.")
                    lbl.setStyleSheet("color:#94a3b8;")
                    v.addWidget(lbl)
                else:
                    for nm in names:
                        row = QtWidgets.QLabel(f"• {nm}")
                        row.setStyleSheet("color:#e6edf3;")
                        v.addWidget(row)

                proxy = QtWidgets.QGraphicsProxyWidget(self)
                proxy.setWidget(body)
                proxy.setZValue(self.zValue() + 0.1)
                proxy.setPos(0, y_cursor)

                append_h = 6 + max(1, len(names)) * self._PARAM_ROW_H
                proxy.resize(self.width, append_h)
                self._param_proxies.append(proxy)

                y_cursor += append_h

            # --- Switch slider row ---
            if (self.model.kind or "").lower() == "switch":
                row = QtWidgets.QWidget()
                row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                lay = QtWidgets.QHBoxLayout(row)
                lay.setContentsMargins(6, 0, 6, 0)
                lay.setSpacing(6)

                lab = QtWidgets.QLabel(self._switch_label_text())
                lab.setStyleSheet("color:#cbd5e1;")

                slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                slider.setMinimum(0)
                slider.setMaximum(max(0, len(self.model.switch_inputs) - 1))
                slider.setSingleStep(1); slider.setPageStep(1)
                slider.setValue(max(0, min(self.model.switch_index, slider.maximum())))
                slider.valueChanged.connect(lambda v, L=lab: self._on_switch_slider(v, L))

                lay.addWidget(lab); lay.addWidget(slider, 1)

                proxy = QtWidgets.QGraphicsProxyWidget(self)
                proxy.setWidget(row)
                proxy.setZValue(self.zValue() + 0.1)
                proxy.setPos(0, y_cursor)
                proxy.resize(self.width, self._PARAM_ROW_H)
                self._switch_proxy = proxy

                y_cursor += self._PARAM_ROW_H

            # --- Named input bead layout (for plugins like Librarian) ---
            # --- Parameters ---
            self._input_port_pos = {}
            if self.model.params:
                kind = (self.model.kind or "").lower()
                if kind == "note" and self.model.params:
                    params = list(self.model.params)
                    seen = set()
                    changed = False

                    for p in params:
                        nm = (p.get("name") or "").strip() or "param"
                        base = nm
                        cand = nm
                        i = 2
                        while cand.lower() in seen:
                            cand = f"{base} {i}"
                            i += 1
                        if cand != nm:
                            p["name"] = cand
                            changed = True
                        seen.add(cand.lower())

                    if changed:
                        self.model.params = params

                if kind == "note":
                    current_names = { (p.get("name") or "") for p in (self.model.params or []) if (p.get("name") or "") }
                    feat_set = self._pruned_featured_set(current_names)
                    feat_heights = self._featured_heights_map(current_names)
                    # Cache note eye icons for expand/collapse
                    try:
                        eye_open, eye_close = getattr(self, "_note_eye_icons", (None, None))
                        if eye_open is None or eye_close is None:
                            icons_dir = Path(__file__).resolve().parents[2] / "icons"
                            eye_open = QtGui.QIcon(str(icons_dir / "EyeOpen_s_Icon.png"))
                            eye_close = QtGui.QIcon(str(icons_dir / "EyeClose_s_Icon.png"))
                            self._note_eye_icons = (eye_open, eye_close)
                    except Exception:
                        eye_open, eye_close = (None, None)
                else:
                    feat_set = set()
                    feat_heights = {}

                wired_inputs = self._wired_named_inputs()
                named_inputs = {n.strip().lower() for n in self.input_port_names()}

                for i, p in enumerate(self.model.params):
                    pname = p.get("name", "")
                    pval  = p.get("value", "")
                    pname_key = (pname or "").strip().lower()
                    hidden = self._ui_hidden_params_set()

                    if pname_key == "__ui_hidden_params":
                        continue
                    if pname_key in hidden:
                        continue

                    has_port = pname_key in named_inputs
                    wired = has_port and pname_key in wired_inputs

                    # Row 1: label + line edit
                    row = QtWidgets.QWidget()
                    row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                    lay = QtWidgets.QHBoxLayout(row)
                    lay.setContentsMargins(6, 0, 6, 0)
                    lay.setSpacing(6)

                    if kind == "note":
                        feat_btn = QtWidgets.QToolButton()
                        is_featured = pname in feat_set
                        feat_btn.setAutoRaise(True)
                        feat_btn.setToolTip("Toggle big view for this parameter")
                        if eye_open is not None and eye_close is not None:
                            feat_btn.setIcon(eye_open if is_featured else eye_close)
                            feat_btn.setIconSize(QtCore.QSize(16, 16))
                            feat_btn.setText("")
                        else:
                            feat_btn.setText("\N{SEE-NO-EVIL MONKEY}" if not is_featured else "\N{EYE}")

                        def _mk_toggle(nm=pname, btn=feat_btn, _open=eye_open, _close=eye_close):
                            def _toggle():
                                fs = set(self._get_featured_set())
                                if nm in fs:
                                    fs.remove(nm)
                                else:
                                    fs.add(nm)
                                try:
                                    setattr(self.model, "_featured_params", set(fs))
                                except Exception:
                                    pass
                                if _open is not None and _close is not None:
                                    try:
                                        btn.setIcon(_open if nm in fs else _close)
                                    except Exception:
                                        pass
                                self._schedule_rebuild()
                            return _toggle

                        feat_btn.clicked.connect(_mk_toggle())
                        lay.insertWidget(0, feat_btn, 0)

                    if has_port:
                        lay.addSpacing(10)
                        pin_center_x = 0.0
                    else:
                        lay.addSpacing(6)
                        pin_center_x = None

                    lab_holder = QtWidgets.QHBoxLayout()
                    lab_holder.setContentsMargins(0, 0, 0, 0)
                    lab_holder.setSpacing(4)

                    if kind == "note":
                        lab = _ParamNameLabel(
                            pname,
                            dbl_click_cb=lambda idx=i: self._prompt_rename_param(idx),
                        )
                        lab.setToolTip("Double-click to rename")
                    else:
                        lab = QtWidgets.QLabel(pname)
                    lab.setStyleSheet("color:#cbd5e1;")
                    lab.setMinimumWidth(50)
                    lab_holder.addWidget(lab, 0)

                    attach_import_browse = (
                        kind in ("import", "html_preview")
                        and pname_key == "path"
                        and hasattr(self, "_browse_import_file")
                    )
                    lab_holder.addStretch(1)
                    lay.addLayout(lab_holder)

                    edit = QtWidgets.QLineEdit(pval)
                    edit.setPlaceholderText("value")
                    edit.setStyleSheet(
                        "QLineEdit{background:#12151a;color:#e6edf3;"
                        "border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
                    )
                    if wired:
                        edit.setEnabled(False)
                        edit.setToolTip("Driven by connected input.")
                        edit.setStyleSheet(
                            "QLineEdit{background:#191d24;color:#94a3b8;"
                            "border:1px dashed #475569;border-radius:4px;padding:2px 6px;}"
                        )
                    else:
                        edit.setToolTip("")
                    edit.textChanged.connect(lambda txt, idx=i: self._on_param_changed(idx, txt))
                    lay.addWidget(edit, 1)
                    if attach_import_browse:
                        edit.editingFinished.connect(lambda e=edit: self._commit_import_path_edit(e))

                    if attach_import_browse:
                        browse_btn = QtWidgets.QToolButton()
                        btn_style = QtWidgets.QApplication.style()
                        if btn_style:
                            browse_btn.setIcon(btn_style.standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
                        browse_btn.setToolTip("Choose file…")
                        browse_btn.setFixedSize(22, 22)
                        browse_btn.clicked.connect(
                            lambda _=False: self._browse_import_file(self._param_value("path"))
                        )
                        lay.addWidget(browse_btn, 0)

                    # Big editor wiring
                    nm = p.get("name", "value")
                    actions.wire_big_editor_for_lineedit(self, edit, nm)

                    row_center_y = y_cursor + self._PARAM_ROW_H / 2.0
                    proxy = QtWidgets.QGraphicsProxyWidget(self)
                    proxy.setWidget(row)
                    proxy.setZValue(self.zValue() + 0.1)
                    proxy.setPos(0, y_cursor)
                    proxy.resize(self.width, self._PARAM_ROW_H)
                    self._param_proxies.append(proxy)
                    if has_port:
                        center_x = pin_center_x if pin_center_x is not None else 0.0
                        canonical = (pname or pname_key) or pname_key
                        self._input_port_pos[pname_key] = (QtCore.QPointF(center_x, row_center_y), canonical)

                    y_cursor += self._PARAM_ROW_H


                    # If featured -> add a SECOND row right BELOW with a large QTextEdit (draggable resize)
                    if kind == "note" and pname in feat_set:
                        block_h = self._featured_block_height(pname, feat_heights)
                        text_h = max(60, int(block_h - self._NOTE_FEATURED_HANDLE_H))

                        big_row = QtWidgets.QWidget()
                        big_row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                        vlay = QtWidgets.QVBoxLayout(big_row)
                        vlay.setContentsMargins(6, 4, 6, 2)  # leave a bit for the grip
                        vlay.setSpacing(2)

                        big = QtWidgets.QTextEdit()
                        big.setAcceptRichText(False)
                        big.setPlainText(pval)
                        big.setMinimumHeight(text_h)
                        big.setStyleSheet(
                            "QTextEdit{background:#0f1216;color:#e6edf3;"
                            "border:1px solid #3c4450;border-radius:6px;padding:6px;}"
                        )
                        def _sync_big(idx=i, w=big):
                            self._on_param_changed(idx, w.toPlainText())
                        big.textChanged.connect(_sync_big)
                        vlay.addWidget(big, 1)

                        big_row.setMinimumHeight(block_h)
                        big_row.setMaximumHeight(block_h)

                        big_proxy = QtWidgets.QGraphicsProxyWidget(self)
                        big_proxy.setWidget(big_row)
                        big_proxy.setZValue(self.zValue() + 0.1)
                        big_proxy.setPos(0, y_cursor)
                        big_proxy.resize(self.width, block_h)
                        self._param_proxies.append(big_proxy)

                        def _apply_height(new_h: float, nm=pname, proxy=big_proxy, row=big_row, editor=big):
                            h = self._clamp_featured_height(new_h)
                            self._set_featured_height(nm, h)
                            row.setMinimumHeight(h)
                            row.setMaximumHeight(h)
                            try:
                                proxy.resize(self.width, h)
                            except Exception:
                                pass
                            new_text_h = max(60, int(h - self._NOTE_FEATURED_HANDLE_H))
                            editor.setMinimumHeight(new_text_h)
                            editor.updateGeometry()

                        def _drag_height(delta: float, nm=pname, proxy=big_proxy, row=big_row, editor=big):
                            current_h = self._featured_block_height(nm)
                            _apply_height(current_h + float(delta), nm, proxy, row, editor)

                        grip = _FeatureResizeHandle(
                            lambda dy, nm=pname, proxy=big_proxy, row=big_row, editor=big: _drag_height(dy, nm, proxy, row, editor),
                            release_cb=self._schedule_rebuild
                        )
                        vlay.addWidget(grip, 0)

                        y_cursor += block_h

                # Quick add/remove param controls for Note nodes (bottom)
                if kind == "note":
                    add_row = QtWidgets.QWidget()
                    add_row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                    add_lay = QtWidgets.QHBoxLayout(add_row)
                    add_lay.setContentsMargins(6, 0, 6, 0)
                    add_lay.setSpacing(6)

                    add_btn = QtWidgets.QToolButton()
                    add_btn.setAutoRaise(True)
                    add_btn.setText("+")
                    add_btn.setToolTip("Add a new parameter to this Note")
                    add_btn.clicked.connect(lambda _=False: self._append_param("param"))
                    add_lay.addWidget(add_btn, 0)

                    remove_btn = QtWidgets.QToolButton()
                    remove_btn.setAutoRaise(True)
                    remove_btn.setText("-")
                    remove_btn.setToolTip("Remove the last parameter (leaves at least one)")
                    remove_btn.clicked.connect(lambda _=False: self._pop_last_param())
                    add_lay.addWidget(remove_btn, 0)

                    add_lay.addStretch(1)

                    add_proxy = QtWidgets.QGraphicsProxyWidget(self)
                    add_proxy.setWidget(add_row)
                    add_proxy.setZValue(self.zValue() + 0.1)
                    add_proxy.setPos(0, y_cursor)
                    add_proxy.resize(self.width, self._PARAM_ROW_H)
                    self._param_proxies.append(add_proxy)
                    y_cursor += self._PARAM_ROW_H

            # --- Deferred plugin body (Chatbot needs inputs at top) ---
            if defer_plugin and kind_lower in ("chatbot", "chat bot", "chat_bot"):
                try:
                    pre_plugin_count = len(getattr(self, "_plugin_proxies", []) or [])
                    if callable(deferred_render):
                        new_y = deferred_render(self, y_cursor)
                        if isinstance(new_y, (int, float)):
                            y_cursor = int(new_y)
                    post_plugin_count = len(getattr(self, "_plugin_proxies", []) or [])
                    if post_plugin_count == pre_plugin_count:
                        from nodes.chatbot import spec as _chatbot_spec  # type: ignore
                        new_y = _chatbot_spec.render_node_body(self, y_cursor)
                        if isinstance(new_y, (int, float)):
                            y_cursor = int(new_y)
                except Exception as e:
                    print("[EchoGraph] chatbot render fallback error:", e)

            kind_lower = (self.model.kind or "").lower()
            if kind_lower == "import":
                y_cursor = self._build_import_summary(y_cursor)
            elif kind_lower == "html_preview":
                y_cursor = self._build_html_preview(y_cursor)
            elif kind_lower in ("scene", "scene_assembly", "scene_outliner"):
                y_cursor = self._build_scene_summary(y_cursor)

            # --- LLM embedded webview ---
            if kind_lower == "llm":
                if WebEngine is None:
                    row = QtWidgets.QWidget()
                    lay = QtWidgets.QVBoxLayout(row); lay.setContentsMargins(6,0,6,0); lay.setSpacing(6)
                    warn = QtWidgets.QLabel("QtWebEngine not available.\nInstall PySide6-Qt6-WebEngine (or PySide2 QtWebEngine).")
                    warn.setStyleSheet("color:#fca5a5;")
                    lay.addWidget(warn, 0, QtCore.Qt.AlignLeft)

                    proxy = QtWidgets.QGraphicsProxyWidget(self)
                    proxy.setWidget(row)
                    proxy.setZValue(self.zValue() + 0.1)
                    proxy.setPos(0, y_cursor)
                    proxy.resize(self.width, max(200, int((LLM_NODE_H_BASE * self._current_llm_scale()) // 3)))
                    try:
                        proxy.setPreferredSize(self.width, max(100, int((LLM_NODE_H_BASE * self._current_llm_scale()) // 3)))
                    except AttributeError:
                        pass
                    except AttributeError: pass
                    self._llm_proxy = proxy
                else:
                    view = WebEngine.QWebEngineView()
                    view.setObjectName("LLMWebView")
                    try:
                        view.setZoomFactor(1.0)
                    except Exception:
                        pass
                    try:
                        url = QtCore.QUrl(self._llm_url_from_params())
                    except Exception:
                        url = QtCore.QUrl(LLM_URL)
                    view.setUrl(url)

                    view.resize(LLM_NODE_W_BASE, LLM_NODE_H_BASE)
                    view.setMinimumSize(LLM_NODE_W_BASE, LLM_NODE_H_BASE)
                    view.setMaximumSize(LLM_NODE_W_BASE, LLM_NODE_H_BASE)
                    view.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

                    proxy = QtWidgets.QGraphicsProxyWidget(self)
                    proxy.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
                    proxy.setWidget(view)
                    proxy.setZValue(self.zValue() + 0.1)

                    S = self._current_llm_scale()
                    proxy.setTransform(QtGui.QTransform().scale(S, S))
                    proxy.setPos(0, y_cursor)

                    try:
                        proxy.setPreferredSize(LLM_NODE_W_BASE, LLM_NODE_H_BASE)
                    except AttributeError:
                        pass

                    self._llm_proxy = proxy
                    self._llm_view  = view
                    self._llm_label = None
                    self._llm_sampler = None

                    S = self._current_llm_scale()
                    y_cursor += int(LLM_NODE_H_BASE * S)

        finally:
            self._is_building = False

    def _build_import_summary(self, y_cursor: int) -> int:
        self._sync_import_icon_cache()
        path = self._param_value("path")
        detail, btn_enabled = self._file_detail_for_path(path)
        ext = os.path.splitext((path or "").strip())[1].lower()
        extra = None
        if ext == ".obj":
            extra = self._make_import_texture_widget(self._param_value("texture"))

        thumb_path = (self._param_value("thumbnail") or "").strip()

        thumb_dbg = bool(getattr(self, "_thumb_debug", False))
        if thumb_dbg and not hasattr(self, "_thumb_dbg_once"):
            self._thumb_dbg_once = True
            print(
                "[THUMB] node:", getattr(self.model, "name", ""),
                "ext:", os.path.splitext((path or "").strip())[1].lower(),
                "thumb_path:", thumb_path,
                "exists:", bool(thumb_path and os.path.exists(thumb_path)),
                flush=True
            )
        thumb_widget = None

        if ext in (".fbx", ".obj", ".gltf", ".glb", ".ply") and thumb_path and os.path.exists(thumb_path):
            # full-width square preview inside the node
            inner_w = max(40, int(self.width) - 12)  # same idea as recompute_height

            thumb_widget = QtWidgets.QLabel()
            thumb_widget.setAlignment(QtCore.Qt.AlignCenter)
            thumb_widget.setFixedSize(inner_w, inner_w)
            self._import_thumb_label = thumb_widget

            pixmap = QtGui.QPixmap(thumb_path)
            if not pixmap.isNull():
                # scale to cover the square, then center-crop to exactly inner_w x inner_w
                scaled = pixmap.scaled(
                    inner_w, inner_w,
                    QtCore.Qt.KeepAspectRatioByExpanding,
                    QtCore.Qt.SmoothTransformation
                )
                x = max(0, (scaled.width() - inner_w) // 2)
                y = max(0, (scaled.height() - inner_w) // 2)
                cropped = scaled.copy(x, y, inner_w, inner_w)

                thumb_widget.setPixmap(cropped)

        return self._render_file_summary(y_cursor, detail, btn_enabled, path, extra_widget=extra, thumb_widget=thumb_widget)

    def _import_icon_for_ext(self, ext: str):
        ext = (ext or "").strip().lower()
        if ext == ".obj":
            return node_icons._obj_icon() or node_icons._import_icon()
        if ext == ".fbx":
            return node_icons._fbx_icon() or node_icons._import_icon()
        if ext in (".glb", ".glbf", ".gltf"):
            return node_icons._glb_icon() or node_icons._import_icon()
        if ext == ".ply":
            return node_icons._ply_icon() or node_icons._import_icon()
        return node_icons._import_icon()

    def _sync_import_icon_cache(self) -> None:
        if (self.model.kind or "").lower() != "import":
            return
        path = (self._param_value("path") or "").strip()
        ext = os.path.splitext(path)[1].lower()
        key = ext or ""
        if key == getattr(self, "_import_icon_key", None) and getattr(self, "_import_icon_pm", None) is not None:
            return
        self._import_icon_key = key
        self._import_icon_pm = self._import_icon_for_ext(ext)

    def _collect_scene_assets(self) -> list[dict]:
        supported = {".fbx", ".obj", ".gltf", ".glb", ".ply", ".stl", ".off", ".om"}
        sc = self.scene()
        if sc is None:
            return []
        try:
            in_edges = list(sc._ordered_in_edges(self))
        except Exception:
            try:
                in_edges = list(sc._in_edges(self))
            except Exception:
                in_edges = []
        assets = []
        seen = set()
        hidden = set()
        xforms = {}
        try:
            raw_hidden = getattr(getattr(self, "model", None), "_scene_hidden", None)
            if isinstance(raw_hidden, set):
                hidden = raw_hidden
            elif isinstance(raw_hidden, (list, tuple)):
                hidden = {str(x) for x in raw_hidden if x}
        except Exception:
            hidden = set()
        try:
            raw_xforms = getattr(getattr(self, "model", None), "_scene_xforms", None)
            if isinstance(raw_xforms, dict):
                xforms = raw_xforms
        except Exception:
            xforms = {}
        for edge in in_edges:
            src_item = getattr(edge, "src", None)
            model = getattr(src_item, "model", None)
            if model is None:
                continue
            model_name = (getattr(model, "name", "") or "").strip()
            path = ""
            for p in (model.params or []):
                if (p.get("name") or "").strip().lower() == "path":
                    path = (p.get("value") or "").strip()
                    break
            if not path:
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in supported:
                continue
            if path in seen:
                continue
            seen.add(path)
            texture = ""
            if ext == ".obj":
                for p in (model.params or []):
                    if (p.get("name") or "").strip().lower() == "texture":
                        texture = (p.get("value") or "").strip()
                        break
            xf = None
            try:
                if model_name and model_name in xforms:
                    xf = xforms.get(model_name)
                elif model_name:
                    nl = model_name.lower()
                    for k, v in xforms.items():
                        if str(k).strip().lower() == nl:
                            xf = v
                            break
            except Exception:
                xf = None
            assets.append(
                {
                    "path": path,
                    "texture": texture,
                    "ext": ext,
                    "node": model_name,
                    "visible": model_name not in hidden,
                    "xform": xf if isinstance(xf, dict) else None,
                }
            )
        return assets

    def _open_scene_assets(self) -> None:
        assets = self._collect_scene_assets()
        if not assets:
            QtWidgets.QMessageBox.information(
                _top_level_parent_for_dialog(), "Scene", "No 3D assets connected."
            )
            return

        # Ensure splats start visible on open (avoid auto-hidden splats)
        try:
            raw_hidden = getattr(getattr(self, "model", None), "_scene_hidden", None)
            if isinstance(raw_hidden, set):
                hidden_set = raw_hidden
            elif isinstance(raw_hidden, (list, tuple)):
                hidden_set = {str(x) for x in raw_hidden if x}
            else:
                hidden_set = set()
            changed = False
            for a in assets:
                if str(a.get("ext", "")).lower() == ".ply":
                    name = (a.get("node") or "").strip()
                    if name and name in hidden_set:
                        hidden_set.discard(name)
                        changed = True
                    a["visible"] = True
            if changed:
                try:
                    setattr(self.model, "_scene_hidden", hidden_set)
                except Exception:
                    pass
        except Exception:
            pass

        parent = _top_level_parent_for_dialog()
        if parent is None:
            QtWidgets.QMessageBox.warning(
                _top_level_parent_for_dialog(), "Scene", "3D view is not available."
            )
            return

        handler = getattr(parent, "open_scene_assets", None)
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                _top_level_parent_for_dialog(), "Scene", "3D view is not available."
            )
            return

        try:
            try:
                setattr(parent, "_active_scene_node", self.model)
                if not hasattr(self.model, "_rev_number"):
                    try:
                        counter = int(getattr(parent, "_scene_rev_counter", 0)) + 1
                    except Exception:
                        counter = 1
                    try:
                        setattr(parent, "_scene_rev_counter", counter)
                    except Exception:
                        pass
                    try:
                        setattr(self.model, "_rev_number", counter)
                    except Exception:
                        pass
            except Exception:
                pass
            handler(assets)

            # Push Scene-node render toggle into the viewport (used by splat draw)
            glv = getattr(parent, "gl_view", None)
            if glv is not None:
                raw = (self._param_value("splat_depth_test") or "").strip()
                glv._mgl_splat_depth_test = raw  # "1"/"0" or ""

        except Exception as exc:
            import traceback
            print("[SCENE] open_scene_assets failed:", exc, flush=True)
            print(traceback.format_exc(), flush=True)
            return

        # Auto-select this Scene node so the info card updates on View Scene.
        try:
            scene = self.scene()
            if scene is not None:
                try:
                    scene.clearSelection()
                except Exception:
                    for it in list(scene.selectedItems()):
                        if it is self:
                            continue
                        it.setSelected(False)
            self.setSelected(True)
            self.clicked.emit(self.model)
        except Exception:
            pass

        # restore camera from selected snapshot sidecar json (apply after load settles)
        try:
            thumb_base = (self._param_value("thumbnail") or "").strip()
            if thumb_base:
                base_path = Path(thumb_base)
                folder = base_path.parent

                # prefer UI selection from dropdown, else fall back to stored param
                sel = (getattr(self, "_scene_selected_snapshot", "") or "").strip()
                if not sel:
                    sel = (self._param_value("thumbnail_choice") or "").strip()

                snap_png = (folder / sel) if sel else base_path
                if not snap_png.exists():
                    snap_png = base_path

                cam_path = snap_png.with_suffix(".json")
                print("[SCENE] selected snapshot =", sel, flush=True)
                print("[SCENE] cam_path =", str(cam_path), "exists =", cam_path.exists(), flush=True)
                if cam_path.exists():

                    with open(cam_path, "r", encoding="utf-8") as f:
                        cam = json.load(f)
                    print("[SCENE] cam keys =", list(cam.keys())[:10], flush=True)
                    glv = getattr(parent, "gl_view", None)

                    def _apply_cam():
                        try:
                            if glv is None:
                                return
                            if hasattr(glv, "_mgl_queue_camera_state"):
                                glv._mgl_queue_camera_state(cam)
                            elif hasattr(glv, "_mgl_apply_camera_state"):
                                glv._mgl_apply_camera_state(cam)
                        except Exception:
                            pass

                    # apply now, then re-apply after typical load completion moments
                    QtCore.QTimer.singleShot(0, _apply_cam)
                    QtCore.QTimer.singleShot(250, _apply_cam)
                    QtCore.QTimer.singleShot(900, _apply_cam)


        except Exception as exc:
            print("[SCENE] camera restore failed:", exc, flush=True)



    def _update_scene_thumb_label(self, thumb_path: str) -> None:
        lab = getattr(self, "_scene_thumb_label", None)
        if lab is None:
            return
        try:
            if not isinstance(lab, QtWidgets.QLabel):
                return
            if not thumb_path or not os.path.exists(thumb_path):
                lab.clear()
                return

            inner_w = int(lab.width()) if lab.width() > 0 else max(40, int(self.width) - 12)
            pixmap = QtGui.QPixmap(thumb_path)
            if pixmap.isNull():
                lab.clear()
                return

            scaled = pixmap.scaled(
                inner_w, inner_w,
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation
            )
            x = max(0, (scaled.width() - inner_w) // 2)
            y = max(0, (scaled.height() - inner_w) // 2)
            cropped = scaled.copy(x, y, inner_w, inner_w)
            lab.setPixmap(cropped)
        except Exception:
            pass


    def _refresh_scene_snap_combo(self) -> None:
        combo = getattr(self, "_scene_snap_combo", None)
        if combo is None:
            return
        try:
            from pathlib import Path

            thumb_path = (self._param_value("thumbnail") or "").strip()
            if not thumb_path:
                combo.blockSignals(True)
                combo.clear()
                combo.blockSignals(False)
                return

            folder = Path(thumb_path).parent
            # oldest -> newest so newest becomes the biggest v###
            pngs = sorted(folder.glob("*.png"), key=lambda p: p.name)

            chosen = (self._param_value("thumbnail_choice") or "").strip()

            combo.blockSignals(True)
            combo.clear()
            for i, pth in enumerate(pngs, start=1):
                combo.addItem(f"v{i:03d}", pth.name)

            if chosen:
                for idx in range(combo.count()):
                    if combo.itemData(idx) == chosen:
                        combo.setCurrentIndex(idx)
                        break

            combo.blockSignals(False)
        except Exception:
            try:
                combo.blockSignals(False)
            except Exception:
                pass



    def _build_scene_summary(self, y_cursor: int) -> int:
        assets = self._collect_scene_assets()
        if not assets:
            detail = "No 3D assets connected."
            btn_enabled = False
        else:
            mesh_count = sum(1 for a in assets if a.get("ext") != ".ply")
            splat_count = len(assets) - mesh_count
            detail = f"{len(assets)} connected (mesh {mesh_count}"
            if splat_count:
                detail += f", splat {splat_count}"
            detail += ")"
            btn_enabled = True

        thumb_path = (self._param_value("thumbnail") or "").strip()
        thumb_widget = None
        if thumb_path and os.path.exists(thumb_path):
            inner_w = max(40, int(self.width) - 12)
            thumb_widget = QtWidgets.QLabel()
            thumb_widget.setAlignment(QtCore.Qt.AlignCenter)
            thumb_widget.setFixedSize(inner_w, inner_w)
            self._scene_thumb_label = thumb_widget

            pixmap = QtGui.QPixmap(thumb_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    inner_w, inner_w,
                    QtCore.Qt.KeepAspectRatioByExpanding,
                    QtCore.Qt.SmoothTransformation
                )
                x = max(0, (scaled.width() - inner_w) // 2)
                y = max(0, (scaled.height() - inner_w) // 2)
                cropped = scaled.copy(x, y, inner_w, inner_w)
                thumb_widget.setPixmap(cropped)

        row = QtWidgets.QWidget()
        row.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        # clamp to node width
        row.setFixedWidth(int(self.width))
        row.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)

        outer = QtWidgets.QVBoxLayout(row)
        outer.setContentsMargins(6, 0, 6, 6)
        outer.setSpacing(4)

        inner_w = max(40, int(self.width) - 12)

        if thumb_widget:
            outer.addWidget(thumb_widget, 0)

        label = QtWidgets.QLabel(detail)
        label.setStyleSheet("color:#cbd5e1;")
        label.setWordWrap(True)
        label.setFixedWidth(inner_w)
        label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        outer.addWidget(label, 0)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        btn = QtWidgets.QPushButton("View Scene")
        btn.setEnabled(btn_enabled)
        btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        btn.clicked.connect(lambda _=False: self._open_scene_assets())
        btn_row.addWidget(btn, 0, QtCore.Qt.AlignLeft)

        # Snapshot version dropdown for Scene nodes (uses thumbnail folder pngs)
        try:
            from pathlib import Path
            import time

            thumb_path = (self._param_value("thumbnail") or "").strip()
            if thumb_path:
                folder = Path(thumb_path).parent
                pngs = sorted(folder.glob("*.png"), key=lambda p: p.name)
                if pngs:
                    snap_combo = QtWidgets.QComboBox()
                    snap_combo.setFixedHeight(24)
                    snap_combo.setMaxVisibleItems(8)
                    self._scene_snap_combo = snap_combo

                    for i, pth in enumerate(pngs, start=1):
                        snap_combo.addItem(f"v{i:03d}", pth.name)

                    # Set the chosen version (if it exists)
                    chosen = (self._param_value("thumbnail_choice") or "").strip()
                    if chosen:
                        for idx in range(snap_combo.count()):
                            if snap_combo.itemData(idx) == chosen:
                                snap_combo.blockSignals(True)
                                snap_combo.setCurrentIndex(idx)
                                snap_combo.blockSignals(False)
                                break
                    # ensure thumb label matches the selected version on load
                    try:
                        tp = (self._param_value("thumbnail") or "").strip()
                        cur = snap_combo.currentData()
                        if tp and cur:
                            fol = Path(tp).parent
                            cur_thumb = str((fol / str(cur)).resolve())
                            self._scene_selected_snapshot = str(cur)
                            self._update_scene_thumb_label(cur_thumb)
                    except Exception:
                        pass

                    def _on_pick(_idx: int):
                        if getattr(self, "_snap_updating", False):
                            return
                        fname = snap_combo.currentData()
                        if not fname:
                            return

                        self._snap_updating = True
                        def _apply():
                            try:
                                tp = (self._param_value("thumbnail") or "").strip()
                                if not tp:
                                    return
                                fol = Path(tp).parent
                                new_thumb = str((fol / str(fname)).resolve())

                                # UI-only: do NOT touch node params here (prevents viewport reload)
                                self._scene_selected_snapshot = str(fname)
                                self._set_param_value("thumbnail_choice", str(fname), rebuild=False, notify_scene=False)  # persist without reload

                                self._update_scene_thumb_label(new_thumb)

                            finally:
                                self._snap_updating = False

                        QtCore.QTimer.singleShot(0, _apply)

                    snap_combo.currentIndexChanged.connect(_on_pick)

                    btn_row.addWidget(snap_combo, 0, QtCore.Qt.AlignLeft)
                    btn_row.addSpacing(6)

        except Exception:
            pass

        btn_row.addStretch(1)


        snap_btn = QtWidgets.QToolButton()
        icon = node_icons._screengrab_icon()
        if icon:
            snap_btn.setIcon(QtGui.QIcon(icon))
        snap_btn.setToolTip("Capture thumbnail from 3D view")
        snap_btn.setEnabled(btn_enabled)
        snap_btn.setFixedSize(24, 24)
        snap_btn.clicked.connect(lambda _=False: self._on_scene_screengrab_clicked())
        btn_row.addWidget(snap_btn, 0, QtCore.Qt.AlignLeft)
        outer.addLayout(btn_row)
        proxy = QtWidgets.QGraphicsProxyWidget(self)
        proxy.setWidget(row)
        proxy.setZValue(self.zValue() + 0.1)
        proxy.setPos(0, y_cursor)
        summary_h = max(self._PARAM_ROW_H * 2, row.sizeHint().height())
        proxy.resize(self.width, summary_h)
        self._plugin_proxies.append(proxy)

        return y_cursor + summary_h

    def _build_html_preview(self, y_cursor: int) -> int:
        path = self._param_value("path")
        detail, btn_enabled = self._file_detail_for_path(path)
        y_cursor = self._render_file_summary(y_cursor, detail, btn_enabled, path)

        if not btn_enabled:
            return y_cursor

        preview_widget = self._create_html_preview_widget(path)
        if preview_widget is None:
            return y_cursor

        y_cursor += self._PADDING * 2
        preview_w, preview_h = self._html_preview_dimensions()
        preview_w = max(preview_w, int(self.width))
        preview_widget.setMinimumSize(preview_w, preview_h)
        preview_widget.setMaximumSize(preview_w, preview_h)
        preview_widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        proxy = QtWidgets.QGraphicsProxyWidget(self)
        proxy.setWidget(preview_widget)
        proxy.setZValue(self.zValue() + 0.1)
        proxy.setPos(0, y_cursor)
        proxy.resize(self.width, preview_h)
        self._plugin_proxies.append(proxy)

        return y_cursor + preview_h + self._PADDING

    def _make_import_texture_widget(self, texture_path: str) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lab = QtWidgets.QLabel("Texture")
        lab.setStyleSheet("color:#cbd5e1;")
        lab.setMinimumWidth(50)
        lay.addWidget(lab, 0)

        edit = QtWidgets.QLineEdit(texture_path or "")
        edit.setPlaceholderText("Texture file")
        edit.setStyleSheet(
            "QLineEdit{background:#12151a;color:#e6edf3;"
            "border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
        )
        edit.editingFinished.connect(lambda e=edit: self._set_param_value("texture", e.text()))
        lay.addWidget(edit, 1)

        browse_btn = QtWidgets.QToolButton()
        btn_style = QtWidgets.QApplication.style()
        if btn_style:
            browse_btn.setIcon(btn_style.standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        browse_btn.setToolTip("Choose texture")
        browse_btn.setFixedSize(22, 22)
        browse_btn.clicked.connect(lambda _=False: self._browse_import_texture(self._param_value("texture")))
        lay.addWidget(browse_btn, 0)

        return row

    def _html_preview_dimensions(self) -> tuple[int, int]:
        scale = max(0.25, float(self._current_llm_scale()))
        width = max(self._BASE_W, int(LLM_NODE_W_BASE * scale))
        height = max(180, int(LLM_NODE_H_BASE * scale))
        return width, height

    def _html_preview_body_height(self) -> int:
        summary_h = self._PARAM_ROW_H * 2
        _, preview_h = self._html_preview_dimensions()
        path = (self._param_value("path") or "").strip()
        if not path or not os.path.exists(path):
            return summary_h
        return summary_h + preview_h + self._PADDING

    def _render_file_summary(
        self,
        y_cursor: int,
        detail: str,
        btn_enabled: bool,
        path: str,
        extra_widget: QtWidgets.QWidget | None = None,
        thumb_widget: QtWidgets.QWidget | None = None,
    ) -> int:
        row = QtWidgets.QWidget()
        row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        # NEW: hard clamp to node width
        row.setFixedWidth(int(self.width))
        row.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        outer = QtWidgets.QVBoxLayout(row)
        outer.setContentsMargins(6, 0, 6, 6)
        outer.setSpacing(4)

        inner_w = max(40, int(self.width) - 12)

        if thumb_widget:
            outer.addWidget(thumb_widget, 0)
            # keep a handle so version dropdown can update the preview without changing params
            if isinstance(thumb_widget, QtWidgets.QLabel):
                self._file_thumb_label = thumb_widget

        label = QtWidgets.QLabel(detail)
        label.setStyleSheet("color:#cbd5e1;")
        label.setWordWrap(True)
        label.setFixedWidth(inner_w)
        label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        outer.addWidget(label, 0)
        

        if extra_widget is not None:
            outer.addWidget(extra_widget, 0)

        # clamp the button row so it can't push outside the node
        btn_row_widget = QtWidgets.QWidget()
        btn_row_widget.setFixedWidth(inner_w)
        btn_row_widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        btn_row = QtWidgets.QHBoxLayout(btn_row_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        btn_row.setAlignment(QtCore.Qt.AlignLeft)

        kind = (self.model.kind or "").lower()

        # compute is_3d once (used for dropdown sizing and screengrab button)
        is_3d = bool(path) and self._is_3d_model_ext(os.path.splitext(path)[1].lower())

        btn = QtWidgets.QPushButton("View")
        btn.setEnabled(btn_enabled)
        btn.setFixedWidth(64)
        if kind == "import":
            btn.setStyleSheet(
                "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
                "QPushButton:hover{background:#1d4ed8;}"
                "QPushButton:disabled{background:#334155;color:#94a3b8;}"
            )

        btn.clicked.connect(lambda _=False, p=path: self._open_import_preview(p))
        btn_row.addWidget(btn, 0, QtCore.Qt.AlignLeft)

        # Snapshot version dropdown (import only) beside View
        if kind in ("import", "scene", "scene_assembly", "scene_outliner"):
            try:
                from pathlib import Path

                thumb_path = (self._param_value("thumbnail") or "").strip()
                if thumb_path:
                    #print("[snap_combo]", self.model.name, "kind=", kind, "thumb=", repr(thumb_path))
                    folder = Path(thumb_path).parent
                    pngs = sorted(folder.glob("*.png"), key=lambda p: p.name)

                    if pngs:
                        snap_combo = QtWidgets.QComboBox()

                        # Clamp width based on space left before right-side buttons
                        reserve = 6                  # spacing after combo area
                        reserve += 24 + 6            # reload button + spacing
                        if is_3d:
                            reserve += 24 + 6        # screengrab button + spacing
                        reserve += 8                 # extra padding safety

                        available = inner_w - (64 + 6) - reserve
                        combo_w = max(80, min(140, available))

                        snap_combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                        snap_combo.setFixedWidth(combo_w)
                        snap_combo.setMinimumWidth(combo_w)
                        snap_combo.setMaximumWidth(combo_w)
                        snap_combo.setFixedHeight(24)

                        # Smaller popup so it prefers opening downward
                        snap_combo.setMaxVisibleItems(8)

                        # Use a real QListView so hover works reliably
                        lv = QtWidgets.QListView()
                        lv.setMouseTracking(True)
                        lv.setUniformItemSizes(True)
                        snap_combo.setView(lv)

                        # Combo + popup styling (hover + selected)
                        snap_combo.setStyleSheet(
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

                        # Bring node to front when clicking the combo (prevents popup hiding under newer nodes)
                        _node = self

                        class _BringFrontFilter(QtCore.QObject):
                            def eventFilter(self, obj, ev):
                                try:
                                    if ev.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                                        if hasattr(ev, "button") and ev.button() == QtCore.Qt.LeftButton:
                                            _node._bring_to_front()
                                except Exception:
                                    pass
                                return False

                        snap_combo._bring_front_filter = _BringFrontFilter(snap_combo)  # keep alive
                        snap_combo.installEventFilter(snap_combo._bring_front_filter)

                        # Force popup to start at v001, keep selected highlighted, and keep popup anchored under combo
                        class _SnapPopupTopAndHighlight(QtCore.QObject):
                            def __init__(self, combo: QtWidgets.QComboBox):
                                super().__init__(combo)
                                self._combo = combo

                            def eventFilter(self, obj, ev):
                                if ev.type() == QtCore.QEvent.Show:
                                    QtCore.QTimer.singleShot(0, self._apply)
                                    QtCore.QTimer.singleShot(15, self._force_top_only)
                                return False

                            def _apply(self):
                                try:
                                    combo = self._combo
                                    view = combo.view()
                                    popup = view.window()

                                    # Freeze painting so user doesn't see the intermediate state
                                    popup.setUpdatesEnabled(False)
                                    view.setUpdatesEnabled(False)

                                    # Anchor popup directly under the combobox
                                    pos = combo.mapToGlobal(QtCore.QPoint(0, combo.height()))
                                    popup.move(pos)

                                    # Match widths
                                    popup.setFixedWidth(combo.width())
                                    view.setMinimumWidth(combo.width())

                                    # Force list to show from top (v001)
                                    self._scroll_to_top(view)

                                    # Keep current item highlighted WITHOUT letting Qt scroll to it
                                    m = view.model()
                                    sm = view.selectionModel()
                                    cur = m.index(combo.currentIndex(), 0)
                                    if sm is not None and cur.isValid():
                                        sm.setCurrentIndex(
                                            cur,
                                            QtCore.QItemSelectionModel.ClearAndSelect
                                            | QtCore.QItemSelectionModel.Rows
                                            | QtCore.QItemSelectionModel.NoUpdate
                                        )

                                    popup.raise_()
                                except Exception:
                                    pass
                                finally:
                                    try:
                                        view.setUpdatesEnabled(True)
                                    except Exception:
                                        pass
                                    try:
                                        popup.setUpdatesEnabled(True)
                                        popup.update()
                                    except Exception:
                                        pass

                            def _force_top_only(self):
                                try:
                                    view = self._combo.view()
                                    popup = view.window()
                                    popup.setUpdatesEnabled(False)
                                    view.setUpdatesEnabled(False)
                                    self._scroll_to_top(view)
                                except Exception:
                                    pass
                                finally:
                                    try:
                                        view.setUpdatesEnabled(True)
                                    except Exception:
                                        pass
                                    try:
                                        popup.setUpdatesEnabled(True)
                                        popup.update()
                                    except Exception:
                                        pass

                            def _scroll_to_top(self, view):
                                try:
                                    sb = view.verticalScrollBar()
                                    if sb is not None:
                                        sb.setValue(sb.minimum())
                                    elif hasattr(view, "scrollToTop"):
                                        view.scrollToTop()
                                except Exception:
                                    pass

                        snap_combo._popup_top_filter = _SnapPopupTopAndHighlight(snap_combo)  # keep alive
                        snap_combo.view().installEventFilter(snap_combo._popup_top_filter)

                        # Add items for the versions
                        for i, pth in enumerate(pngs, start=1):
                            snap_combo.addItem(f"v{i:03d}", pth.name)

                        # Set the chosen version (if it exists)
                        chosen = (self._param_value("thumbnail_choice") or "").strip()
                        if chosen:
                            for idx in range(snap_combo.count()):
                                if snap_combo.itemData(idx) == chosen:
                                    snap_combo.blockSignals(True)
                                    snap_combo.setCurrentIndex(idx)
                                    snap_combo.blockSignals(False)
                                    break

                        # ensure thumb label matches the selected version on load
                        try:
                            cur = snap_combo.currentData()
                            if cur:
                                self._import_selected_snapshot = str(cur)
                                chosen_thumb = str((folder / str(cur)).resolve())

                                lab = getattr(self, "_import_thumb_label", None)
                                if isinstance(lab, QtWidgets.QLabel):
                                    inner_w = int(lab.width()) if lab.width() > 0 else max(40, int(self.width) - 12)
                                    pixmap = QtGui.QPixmap(chosen_thumb)
                                    if not pixmap.isNull():
                                        scaled = pixmap.scaled(
                                            inner_w, inner_w,
                                            QtCore.Qt.KeepAspectRatioByExpanding,
                                            QtCore.Qt.SmoothTransformation
                                        )
                                        x = max(0, (scaled.width() - inner_w) // 2)
                                        y = max(0, (scaled.height() - inner_w) // 2)
                                        lab.setPixmap(scaled.copy(x, y, inner_w, inner_w))
                        except Exception:
                            pass

                        def _on_pick(_idx: int):
                            if getattr(self, "_snap_updating", False):
                                return
                            fname = snap_combo.currentData()
                            if not fname:
                                return

                            self._snap_updating = True

                            def _apply():
                                try:
                                    tp = (self._param_value("thumbnail") or "").strip()
                                    if not tp:
                                        return
                                    fol = Path(tp).parent
                                    chosen_thumb = str((fol / str(fname)).resolve())

                                    # UI-only for import: do NOT touch node params here (prevents viewport reload)
                                    if kind == "import":
                                        self._import_selected_snapshot = str(fname)

                                        # persist selection without triggering viewport reload
                                        self._set_param_value("thumbnail_choice", str(fname), rebuild=False, notify_scene=False)
                                    
                                        lab = getattr(self, "_import_thumb_label", None)
                                        if isinstance(lab, QtWidgets.QLabel):
                                            inner_w = int(lab.width()) if lab.width() > 0 else max(40, int(self.width) - 12)
                                            pixmap = QtGui.QPixmap(chosen_thumb)
                                            if not pixmap.isNull():
                                                scaled = pixmap.scaled(
                                                    inner_w, inner_w,
                                                    QtCore.Qt.KeepAspectRatioByExpanding,
                                                    QtCore.Qt.SmoothTransformation
                                                )
                                                x = max(0, (scaled.width() - inner_w) // 2)
                                                y = max(0, (scaled.height() - inner_w) // 2)
                                                lab.setPixmap(scaled.copy(x, y, inner_w, inner_w))
                                        return

                                    # non-import nodes can keep the old behavior if needed
                                    new_thumb = chosen_thumb
                                    self._set_param_value("thumbnail", new_thumb, rebuild=False)
                                    self._set_param_value("thumbnail_choice", str(fname), rebuild=False)
                                    self._set_param_value("thumbnail_rev", str(time.time()), rebuild=False)

                                finally:
                                    self._snap_updating = False

                            QtCore.QTimer.singleShot(0, _apply)

                        snap_combo.currentIndexChanged.connect(_on_pick)

                        # put it beside View, not under it
                        btn_row.addWidget(snap_combo, 0, QtCore.Qt.AlignLeft)
                        btn_row.addSpacing(6)

            except Exception:
                pass


        # IMPORTANT: stretch must be here so right buttons stay on the right
        btn_row.addStretch(1)


        if kind in ("import", "html_preview"):
            reload_btn = QtWidgets.QToolButton()
            btn_style = QtWidgets.QApplication.style()
            if btn_style:
                reload_btn.setIcon(btn_style.standardIcon(QtWidgets.QStyle.SP_BrowserReload))
            reload_btn.setToolTip("Reload file")
            reload_btn.setEnabled(bool(path))
            reload_btn.setFixedSize(24, 24)
            reload_btn.clicked.connect(lambda _=False: self._reload_import_path())
            btn_row.addWidget(reload_btn, 0, QtCore.Qt.AlignLeft)

            if is_3d:
                screengrab_btn = QtWidgets.QToolButton()
                icon = node_icons._screengrab_icon()
                if icon:
                    screengrab_btn.setIcon(QtGui.QIcon(icon))
                screengrab_btn.setToolTip("Capture thumbnail from 3D view")
                screengrab_btn.setEnabled(bool(path))
                screengrab_btn.setFixedSize(24, 24)
                screengrab_btn.clicked.connect(lambda _=False, p=path: self._on_screengrab_clicked(p))
                btn_row.addWidget(screengrab_btn, 0, QtCore.Qt.AlignLeft)

        outer.addWidget(btn_row_widget, 0)
        proxy = QtWidgets.QGraphicsProxyWidget(self)
        proxy.setWidget(row)
        proxy.setZValue(self.zValue() + 0.1)
        proxy.setPos(0, y_cursor)
        summary_h = max(self._PARAM_ROW_H * 2, row.sizeHint().height())
        proxy.resize(self.width, summary_h)
        self._plugin_proxies.append(proxy)

        return y_cursor + summary_h


    def _commit_import_path_edit(self, line_edit: QtWidgets.QLineEdit | None, *, force_refresh: bool = False) -> None:
        if line_edit is None:
            return
        value = (line_edit.text() or "").strip()
        if not force_refresh:
            last = ""
            if isinstance(self._import_path_committed, str):
                last = (self._import_path_committed or "").strip()
            if value == last:
                self._schedule_rebuild()
                return
        QtCore.QTimer.singleShot(0, lambda v=value: self._set_param_value("path", v))

    def _reload_import_path(self) -> None:
        if (self.model.kind or "").lower() not in ("import", "html_preview"):
            return
        value = (self._param_value("path") or "").strip()
        QtCore.QTimer.singleShot(0, lambda v=value: self._set_param_value("path", v))

    def _on_screengrab_clicked(self, path: str):
        path = (path or "").strip()
        if not path or not os.path.exists(path):
            return

        parent = _top_level_parent_for_dialog()
        if parent is None:
            return

        gl_view = getattr(parent, "gl_view", None)
        if gl_view is None or not hasattr(gl_view, "grabFramebuffer"):
            return

        # Ensure the 3D view is actually visible before grabbing
        # Do not change the UI mode. Only grab if the GL view is already visible.
        try:
            if hasattr(gl_view, "isVisible") and not gl_view.isVisible():
                print("[SNAP] gl_view not visible, skipping capture", flush=True)
                return
        except Exception:
            pass


        print("[SNAP] screengrab clicked ->", path, flush=True)

        def capture():
            print("[SNAP] capture start", flush=True)
            glv = gl_view
            if glv is None:
                return

            try:
                if hasattr(glv, "makeCurrent"):
                    glv.makeCurrent()

                image = glv.grabFramebuffer()

                try:
                    ctx = glv.context()
                    if ctx is not None:
                        f = ctx.functions()
                        if f is not None and hasattr(f, "glFinish"):
                            f.glFinish()
                except Exception:
                    pass

            except Exception:
                return

            finally:
                try:
                    if hasattr(glv, "doneCurrent"):
                        glv.doneCurrent()
                except Exception:
                    pass

            if image is None or image.isNull():
                return

            # output thumbnail size (1:1)
            OUT_W = 1024
            OUT_H = 1024

            w = int(image.width())
            h = int(image.height())
            if w <= 0 or h <= 0:
                return

            # centered square crop (top/bottom if tall, left/right if wide)
            side = min(w, h)
            x = max(0, (w - side) // 2)
            y = max(0, (h - side) // 2)
            cropped = image.copy(x, y, side, side)
            if cropped.isNull():
                return

            # scale to fixed thumbnail size, no letterbox
            out = cropped.scaled(
                OUT_W, OUT_H,
                QtCore.Qt.IgnoreAspectRatio,
                QtCore.Qt.SmoothTransformation
            )

            scene = self.scene()
            scene_path = getattr(scene, "_filename", None) if scene is not None else None

            # parent can be missing depending on how/when capture() runs
            parent = _top_level_parent_for_dialog()
            workflow_path = getattr(parent, "_current_path", None) if parent is not None else None
            workflow_path = workflow_path or scene_path

            if not workflow_path:
                return

            base_dir = Path(workflow_path).parent
            snapshots_dir = base_dir / "snapshots"
            try:
                snapshots_dir.mkdir(exist_ok=True, parents=True)
            except Exception:
                return

            p = str(Path(path).expanduser())
            try:
                ap = str(Path(p).resolve())
            except Exception:
                ap = os.path.abspath(p)

            # include file stamp so changes produce a new thumb automatically
            try:
                st = os.stat(ap)
                stamp = f"{int(st.st_mtime)}|{int(st.st_size)}"
            except Exception:
                stamp = "nostat"

            # include texture too for .obj if you want different thumbs per texture
            tex = (self._param_value("texture") or "").strip()
            tex_key = ""
            if tex:
                try:
                    tex_key = str(Path(tex).expanduser().resolve())
                except Exception:
                    tex_key = os.path.abspath(tex)

            key_src = f"{ap}|{stamp}|{tex_key}".encode("utf-8", errors="ignore")
            key = hashlib.sha1(key_src).hexdigest()[:10]

            model_filename = Path(ap).stem
            snap_stamp = time.strftime("%Y%m%d_%H%M%S")

            # NEW: save into a per-asset folder, but keep the same filename
            snapshots_dir = snapshots_dir / f"{model_filename}_{key}"
            try:
                snapshots_dir.mkdir(exist_ok=True, parents=True)
            except Exception:
                return

            image_path = snapshots_dir / f"{model_filename}_{key}_{snap_stamp}.png"

            
            ok = False
            try:
                ok = out.save(str(image_path))
            except Exception as exc:
                print("[SNAP] out.save exception:", exc, flush=True)
                ok = False

            if not ok:
                print("[SNAP] out.save FAILED ->", str(image_path), flush=True)
                print("[SNAP] snapshots_dir exists:", snapshots_dir.exists(), "dir:", str(snapshots_dir), flush=True)
                return

            thumb = str(Path(image_path).resolve())
            print("[SNAP] saved ->", thumb, flush=True)

            try:
                QtGui.QPixmapCache.remove(thumb)
            except Exception:
                pass

            self._set_param_value("thumbnail", thumb)
            print("[SNAP] param thumbnail set ->", self._param_value("thumbnail"), flush=True)

            # NEW: user-facing selected snapshot "version" (stores the filename)
            try:
                from pathlib import Path as _Path
                self._set_param_value("thumbnail_choice", _Path(thumb).name)
            except Exception:
                self._set_param_value("thumbnail_choice", "")

            # keep this for now (internal cache-bust)
            self._set_param_value("thumbnail_rev", str(time.time()))

            try:
                self.update()                 # repaint this node item
            except Exception:
                pass
            try:
                s = self.scene()
                if s is not None and hasattr(s, "update"):
                    s.update()
            except Exception:
                pass
            # save camera state beside the thumbnail: same name, .json
            try:
                parent = _top_level_parent_for_dialog()
                glv = getattr(parent, "gl_view", None) if parent is not None else None

                def _cam_provider(v):
                    if v is None:
                        return None
                    # common places the mixin/renderer might live
                    for cand in (
                        v,
                        getattr(v, "_mgl_renderer", None),
                        getattr(v, "mgl_renderer", None),
                        getattr(v, "renderer", None),
                        getattr(v, "_renderer", None),
                        getattr(v, "gl_view", None),  # if parent.gl_view is a container
                    ):
                        if cand is not None and hasattr(cand, "_mgl_get_camera_state"):
                            return cand
                    return None

                prov = _cam_provider(glv)
                cam_path = Path(thumb).with_suffix(".json")

                if prov is None:
                    print("[SNAP] scene cam provider not found, NOT saving:", str(cam_path), flush=True)
                else:
                    cam = prov._mgl_get_camera_state()
                    with open(cam_path, "w", encoding="utf-8") as f:
                        json.dump(cam, f, indent=2)
                    print("[SNAP] scene cam saved ->", str(cam_path), flush=True)

            except Exception as exc:
                print("[SNAP] scene cam save failed:", exc, flush=True)
            # self._schedule_rebuild()


        QtCore.QTimer.singleShot(30, capture)

    def _on_scene_screengrab_clicked(self):
        assets = self._collect_scene_assets()
        if not assets:
            return

        parent = _top_level_parent_for_dialog()
        if parent is None:
            return

        gl_view = getattr(parent, "gl_view", None)
        if gl_view is None or not hasattr(gl_view, "grabFramebuffer"):
            return

        # Ensure the 3D view is actually visible before grabbing
        # Do not change the UI mode. Only grab if the GL view is already visible.
        try:
            if hasattr(gl_view, "isVisible") and not gl_view.isVisible():
                print("[SNAP] gl_view not visible, skipping capture", flush=True)
                return
        except Exception:
            pass

        print("[SNAP] scene screengrab clicked ->", getattr(self.model, "name", ""), flush=True)

        def capture():
            print("[SNAP] scene capture start", flush=True)
            glv = gl_view
            if glv is None:
                return

            try:
                if hasattr(glv, "makeCurrent"):
                    glv.makeCurrent()

                image = glv.grabFramebuffer()

                try:
                    ctx = glv.context()
                    if ctx is not None:
                        f = ctx.functions()
                        if f is not None and hasattr(f, "glFinish"):
                            f.glFinish()
                except Exception:
                    pass

            except Exception:
                return

            finally:
                try:
                    if hasattr(glv, "doneCurrent"):
                        glv.doneCurrent()
                except Exception:
                    pass

            if image is None or image.isNull():
                return

            # output thumbnail size (1:1)
            OUT_W = 1024
            OUT_H = 1024

            w = int(image.width())
            h = int(image.height())
            if w <= 0 or h <= 0:
                return

            # centered square crop (top/bottom if tall, left/right if wide)
            side = min(w, h)
            x = max(0, (w - side) // 2)
            y = max(0, (h - side) // 2)
            cropped = image.copy(x, y, side, side)
            if cropped.isNull():
                return

            # scale to fixed thumbnail size, no letterbox
            out = cropped.scaled(
                OUT_W, OUT_H,
                QtCore.Qt.IgnoreAspectRatio,
                QtCore.Qt.SmoothTransformation
            )

            scene = self.scene()
            scene_path = getattr(scene, "_filename", None) if scene is not None else None

            # parent can be missing depending on how/when capture() runs
            parent = _top_level_parent_for_dialog()
            workflow_path = getattr(parent, "_current_path", None) if parent is not None else None
            workflow_path = workflow_path or scene_path

            if not workflow_path:
                return

            base_dir = Path(workflow_path).parent
            snapshots_dir = base_dir / "snapshots"
            try:
                snapshots_dir.mkdir(exist_ok=True, parents=True)
            except Exception:
                return

            key_parts = []
            for asset in assets:
                path = (asset.get("path") or "").strip()
                if not path:
                    continue
                p = str(Path(path).expanduser())
                try:
                    ap = str(Path(p).resolve())
                except Exception:
                    ap = os.path.abspath(p)
                try:
                    st = os.stat(ap)
                    stamp = f"{int(st.st_mtime)}|{int(st.st_size)}"
                except Exception:
                    stamp = "nostat"
                tex = (asset.get("texture") or "").strip()
                tex_key = ""
                if tex:
                    try:
                        tex_key = str(Path(tex).expanduser().resolve())
                    except Exception:
                        tex_key = os.path.abspath(tex)
                key_parts.append(f"{ap}|{stamp}|{tex_key}")

            key_parts.sort()
            if key_parts:
                key_src = "|".join(key_parts).encode("utf-8", errors="ignore")
                key = hashlib.sha1(key_src).hexdigest()[:10]
            else:
                key = "scene"

            scene_name = (getattr(self.model, "name", "") or "scene").strip()
            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", scene_name).strip("_") or "scene"
            snap_stamp = time.strftime("%Y%m%d_%H%M%S")
            
            # save into a per-scene folder, but keep the same filename
            snapshots_dir = snapshots_dir / f"scene_{safe_name}_{key}"
            try:
                snapshots_dir.mkdir(exist_ok=True, parents=True)
            except Exception:
                return

            image_path = snapshots_dir / f"scene_{safe_name}_{key}_{snap_stamp}.png"
                    
            ok = False
            try:
                ok = out.save(str(image_path))
            except Exception as exc:
                print("[SNAP] out.save exception:", exc, flush=True)
                ok = False

            if not ok:
                print("[SNAP] out.save FAILED ->", str(image_path), flush=True)
                print("[SNAP] snapshots_dir exists:", snapshots_dir.exists(), "dir:", str(snapshots_dir), flush=True)
                return

            thumb = str(Path(image_path).resolve())
            print("[SNAP] scene saved ->", thumb, flush=True)

            try:
                QtGui.QPixmapCache.remove(thumb)
            except Exception:
                pass

            self._set_param_value("thumbnail", thumb, rebuild=False)
            print("[SNAP] param thumbnail set ->", self._param_value("thumbnail"), flush=True)

            self._set_param_value("thumbnail_choice", Path(thumb).name, rebuild=False)

            # keep this for now (cache-bust / UI refresh)
            self._set_param_value("thumbnail_rev", str(time.time()), rebuild=False)

            # keep runtime selection consistent
            try:
                self._scene_selected_snapshot = Path(thumb).name
            except Exception:
                pass

            # update UI; if widgets don't exist yet (folder was empty / thumb missing), rebuild once
            try:
                need_rebuild = (getattr(self, "_scene_thumb_label", None) is None) or (getattr(self, "_scene_snap_combo", None) is None)
                if need_rebuild and hasattr(self, "_schedule_rebuild"):
                    self._schedule_rebuild()

                def _ui_refresh():
                    try:
                        self._update_scene_thumb_label(thumb)
                        self._refresh_scene_snap_combo()
                        combo = getattr(self, "_scene_snap_combo", None)
                        if combo is not None:
                            try:
                                idx = combo.findData(Path(thumb).name)
                                if idx >= 0:
                                    combo.blockSignals(True)
                                    combo.setCurrentIndex(idx)
                                    combo.blockSignals(False)
                                else:
                                    if hasattr(self, "_schedule_rebuild"):
                                        self._schedule_rebuild()
                            except Exception:
                                pass
                    except Exception:
                        pass

                QtCore.QTimer.singleShot(0, _ui_refresh)
            except Exception:
                pass


            # Capture per-asset transforms from this scene node (prefer live GL values).
            scene_xf_mesh = {}
            scene_xf_splat = {}
            try:
                node_xforms = getattr(getattr(self, "model", None), "_scene_xforms", None)
                if not isinstance(node_xforms, dict):
                    node_xforms = {}

                def _find_owner_key(d, owner):
                    if not isinstance(d, dict):
                        return None
                    if owner in d:
                        return owner
                    lo = str(owner).strip().lower()
                    for k in d.keys():
                        try:
                            if str(k).strip().lower() == lo:
                                return k
                        except Exception:
                            continue
                    return None

                splat_map = getattr(gl_view, "_mgl_scene_splats", None) or {}
                splat_world = getattr(gl_view, "_mgl_scene_splats_world", None) or {}
                mesh_bounds = getattr(gl_view, "_mgl_scene_mesh_bounds_by_owner", None)
                if not isinstance(mesh_bounds, dict):
                    mesh_bounds = getattr(gl_view, "_mgl_scene_bounds_by_owner", None) or {}

                splat_xforms = getattr(gl_view, "_mgl_scene_splat_xforms_by_owner", None) or {}
                mesh_xforms = getattr(gl_view, "_mgl_scene_xforms_by_owner", None) or {}

                for asset in assets or []:
                    owner = (asset.get("node") or "").strip()
                    if not owner:
                        p = (asset.get("path") or "").strip()
                        if p:
                            owner = Path(p).name
                    if not owner:
                        continue
                    ext = str(asset.get("ext") or Path(asset.get("path") or "").suffix).lower()
                    is_splat = (ext == ".ply")

                    xf = None
                    if is_splat:
                        if _find_owner_key(splat_map, owner) or _find_owner_key(splat_world, owner):
                            kx = _find_owner_key(splat_xforms, owner)
                            if kx is not None:
                                xf = splat_xforms.get(kx)
                    else:
                        if _find_owner_key(mesh_bounds, owner):
                            kx = _find_owner_key(mesh_xforms, owner)
                            if kx is not None:
                                xf = mesh_xforms.get(kx)

                    if not isinstance(xf, dict):
                        xf = node_xforms.get(owner)
                        if xf is None:
                            lo = str(owner).strip().lower()
                            for k, v in node_xforms.items():
                                if str(k).strip().lower() == lo:
                                    xf = v
                                    break

                    if not isinstance(xf, dict):
                        continue

                    try:
                        pos = [float(v) for v in xf.get("pos", (0.0, 0.0, 0.0))]
                        rot = [float(v) for v in xf.get("rot", (0.0, 0.0, 0.0))]
                        scl = [float(v) for v in xf.get("scl", (1.0, 1.0, 1.0))]
                    except Exception:
                        continue

                    entry = {"pos": pos, "rot": rot, "scl": scl}
                    if is_splat:
                        scene_xf_splat[str(owner)] = entry
                    else:
                        scene_xf_mesh[str(owner)] = entry
                    node_xforms[str(owner)] = entry

                try:
                    if getattr(self, "model", None) is not None:
                        setattr(self.model, "_scene_xforms", node_xforms)
                except Exception:
                    pass
            except Exception:
                scene_xf_mesh = {}
                scene_xf_splat = {}

            # save camera state beside the thumbnail: same name, .json
            try:
                if hasattr(gl_view, "_mgl_get_camera_state"):
                    cam = gl_view._mgl_get_camera_state()
                    if scene_xf_mesh or scene_xf_splat:
                        cam["scene_xforms"] = {"mesh": scene_xf_mesh, "splat": scene_xf_splat}
                    cam_path = Path(thumb).with_suffix(".json")
                    with open(cam_path, "w", encoding="utf-8") as f:
                        json.dump(cam, f, indent=2)
                    print("[SNAP] scene cam saved ->", str(cam_path), flush=True)
                else:
                    print("[SNAP] scene cam provider missing _mgl_get_camera_state", flush=True)
            except Exception as exc:
                print("[SNAP] scene cam save failed:", exc, flush=True)


        QtCore.QTimer.singleShot(30, capture)

    def _file_detail_for_path(self, path: str) -> tuple[str, bool]:
        path = (path or "").strip()
        if not path:
            return "No file selected", False

        name = os.path.basename(path) or path

        def _soft_wrap_filename(s: str, chunk: int = 48) -> str:
            if not s:
                return s
            # allow wraps after separators
            s = re.sub(r"([/\\\\_.-])", lambda m: m.group(1) + "\u200b", s)
            # allow wraps inside long alphanumeric runs
            s = re.sub(rf"([A-Za-z0-9]{{{chunk}}})(?=[A-Za-z0-9])", lambda m: m.group(1) + "\u200b", s)
            return s

        name_wrapped = _soft_wrap_filename(name)

        if os.path.exists(path):
            try:
                stat = os.stat(path)
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
                detail = f"{name_wrapped}\nUpdated: {mtime}"
            except Exception:
                detail = name_wrapped
            return detail, True

        return f"{name_wrapped}\n(Missing file)", False


    def _read_html_text(self, path: str) -> str:
        path = (path or "").strip()
        if not path:
            return ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            except Exception:
                return ""

    def _create_html_preview_widget(self, path: str):
        html = self._read_html_text(path)
        if not html:
            return None
        if WebEngine is not None:
            view = WebEngine.QWebEngineView()
            view.setObjectName("HtmlPreviewView")
            try:
                view.setZoomFactor(0.9)
            except Exception:
                pass
            view.setHtml(html, QtCore.QUrl.fromLocalFile(path))
            return view

        browser = QtWidgets.QTextBrowser()
        browser.setObjectName("HtmlPreviewFallback")
        browser.setStyleSheet(
            "QTextBrowser{background:#0f1216;color:#e6edf3;"
            "border:1px solid #3c4450;border-radius:6px;padding:6px;}"
        )
        browser.setHtml(html)
        browser.setOpenExternalLinks(True)
        return browser

    @staticmethod
    def _read_plaintext_file(path: str) -> str:
        path = (path or "").strip()
        if not path:
            return ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            except Exception:
                return ""

    @staticmethod
    def _read_pdf_text(path: str) -> str:
        if not _HAS_PYPDF:
            return ""
        path = (path or "").strip()
        if not path:
            return ""
        try:
            reader = PdfReader(path)
            chunks = []
            for page in reader.pages:
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                if txt.strip():
                    chunks.append(txt.strip())
            return "\n\n".join(chunks)
        except Exception:
            return ""

    @staticmethod
    def read_import_text(path: str) -> str:
        path = (path or "").strip()
        if not path:
            return ""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return NodeItem._read_pdf_text(path)
        return NodeItem._read_plaintext_file(path)

    def _browse_import_file(self, current: str):
        start = current or os.path.expanduser("~")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            _top_level_parent_for_dialog(),
            "Select File",
            start,
            "3D Models (*.fbx *.obj *.gltf *.glb *.ply);;"
            "Documents (*.html *.htm *.txt *.md *.json *.py *.pdf);;"
            "All Files (*.*)",
        )
        if file_path:
            QtCore.QTimer.singleShot(0, lambda p=file_path: self._set_param_value("path", p))

    def _browse_import_texture(self, current: str):
        start = current or os.path.expanduser("~")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            _top_level_parent_for_dialog(),
            "Select Texture",
            start,
            "Image files (*.png *.jpg *.jpeg *.bmp *.tga);;All Files (*.*)",
        )
        if file_path:
            QtCore.QTimer.singleShot(0, lambda p=file_path: self._set_param_value("texture", p))

    def _open_import_preview(self, path: str):
        path = (path or "").strip()
        if not path:
            QtWidgets.QMessageBox.information(_top_level_parent_for_dialog(), "Import", "No file selected.")
            return

        ext = os.path.splitext(path)[1].lower()
        if self._open_import_model(path, ext):
            return

        if ext == ".pdf":
            text = self.read_import_text(path)
            if not text:
                msg = "Install 'pypdf' to enable PDF previews." if not _HAS_PYPDF else "Failed to extract text from PDF."
                QtWidgets.QMessageBox.warning(_top_level_parent_for_dialog(), "Import", msg)
                return
            dlg = BigTextEditDialog(_top_level_parent_for_dialog(), title=f"Preview: {os.path.basename(path)}", initial=text)
            dlg.edit.setReadOnly(True)
            self._show_modeless_dialog(dlg)
            return

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(_top_level_parent_for_dialog(), "Import", f"Failed to open file:\n{exc}")
            return

        html = self._highlight_html_content(text, path)
        if html:
            dlg = QtWidgets.QDialog(_top_level_parent_for_dialog())
            dlg.setWindowTitle(f"Preview: {os.path.basename(path)}")
            dlg.resize(760, 540)
            layout = QtWidgets.QVBoxLayout(dlg)
            view = QtWidgets.QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setHtml(html)
            layout.addWidget(view, 1)
            bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            bb.rejected.connect(dlg.close)
            bb.accepted.connect(dlg.close)
            layout.addWidget(bb)
            self._show_modeless_dialog(dlg)
            return

        dlg = BigTextEditDialog(_top_level_parent_for_dialog(), title=f"Preview: {os.path.basename(path)}", initial=text)
        dlg.edit.setReadOnly(True)
        self._show_modeless_dialog(dlg)

    @staticmethod
    def _is_3d_model_ext(ext: str) -> bool:
        return ext in (".fbx", ".obj", ".gltf", ".glb", ".ply")

    def _open_import_model(self, path: str, ext: str) -> bool:
        if not self._is_3d_model_ext(ext):
            return False
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(_top_level_parent_for_dialog(), "Import", "3D file not found.")
            return True
        texture = ""
        if ext == ".obj":
            texture = (self._param_value("texture") or "").strip()
        parent = _top_level_parent_for_dialog()
        if parent is None:
            QtWidgets.QMessageBox.warning(_top_level_parent_for_dialog(), "Import", "3D view is not available.")
            return True
            
        handler = getattr(parent, "open_3d_model", None)
        if callable(handler):
            try:
                handler(path, texture if texture else None)

                # restore camera from selected snapshot sidecar json (apply after load settles)
                try:
                    thumb_base = (self._param_value("thumbnail") or "").strip()
                    if thumb_base:
                        base_path = Path(thumb_base)
                        folder = base_path.parent

                        # prefer UI selection from dropdown, else fall back to stored param
                        sel = (getattr(self, "_import_selected_snapshot", "") or "").strip()
                        if not sel:
                            sel = (self._param_value("thumbnail_choice") or "").strip()

                        snap_png = (folder / sel) if sel else base_path
                        if not snap_png.exists():
                            snap_png = base_path

                        cam_path = snap_png.with_suffix(".json")
                        if cam_path.exists():
                            with open(cam_path, "r", encoding="utf-8") as f:
                                cam = json.load(f)

                            glv = getattr(parent, "gl_view", None)

                            def _apply_cam():
                                try:
                                    if glv is None:
                                        return
                                    if hasattr(glv, "_mgl_queue_camera_state"):
                                        glv._mgl_queue_camera_state(cam)
                                    elif hasattr(glv, "_mgl_apply_camera_state"):
                                        glv._mgl_apply_camera_state(cam)
                                except Exception:
                                    pass

                            QtCore.QTimer.singleShot(0, _apply_cam)
                            QtCore.QTimer.singleShot(250, _apply_cam)
                            QtCore.QTimer.singleShot(900, _apply_cam)

                except Exception as exc2:
                    print("[IMPORT] camera restore failed:", exc2, flush=True)

                return True
            except Exception as exc:
                import traceback
                print("[IMPORT] open_3d_model failed:", exc, flush=True)
                print(traceback.format_exc(), flush=True)

        QtWidgets.QMessageBox.warning(_top_level_parent_for_dialog(), "Import", "3D view is not available.")
        return True

    def _highlight_html_content(self, text: str, filename: str) -> str | None:
        if not _HAS_PYGMENTS:
            return None
        try:
            lexer = get_lexer_for_filename(filename, stripall=True)
        except Exception:
            try:
                lexer = guess_lexer(text)
            except Exception:
                lexer = TextLexer()

        try:
            formatter = HtmlFormatter(style="monokai", noclasses=True, nowrap=True)
            colored = highlight(text, lexer, formatter)
        except Exception:
            return None

        return (
            "<html><head><meta charset='utf-8'></head>"
            "<body style='margin:0;background:#272822;color:#f8f8f2;'>"
            "<pre style='margin:0;padding:12px;font-family:\"Fira Code\",\"Consolas\",\"Courier New\",monospace;"
            "font-size:13px;line-height:1.4;white-space:pre-wrap;'>"
            f"{colored}"
            "</pre></body></html>"
        )

    def _show_modeless_dialog(self, dlg: QtWidgets.QDialog):
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dlg.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, True)
        dlg.setWindowFlag(QtCore.Qt.WindowMinMaxButtonsHint, True)
        dlg.setSizeGripEnabled(True)
        dlg.setWindowModality(QtCore.Qt.NonModal)
        self._live_dialogs.add(dlg)
        def _cleanup(*_):
            self._live_dialogs.discard(dlg)
        dlg.finished.connect(_cleanup)
        dlg.destroyed.connect(lambda *_: self._live_dialogs.discard(dlg))
        dlg.show()


    def _schedule_rebuild(self):
        # Coalesce multiple toggles/edits into a single rebuild on the next event loop tick
        if getattr(self, "_rebuild_pending", False):
            return
        self._rebuild_pending = True
        QtCore.QTimer.singleShot(0, self._do_rebuild)

    def _do_rebuild(self):
        self._rebuild_pending = False
        # Recompute height first, then rebuild
        self._recompute_height()
        self._build_widgets()
        if (self.model.kind or "").lower() in ("import", "html_preview"):
            self._import_path_committed = (self._param_value("path") or "").strip()

    def _note_resize_available(self) -> bool:
        kind = (self.model.kind or "").lower()
        if kind not in ("note", "chatbot", "chat bot", "chat_bot"):
            return False
        if self._note_resize_mode:
            return True
        return bool(self.isSelected())

    def _note_hit_test(self, pos: QtCore.QPointF) -> str | None:
        if not self._note_resize_available():
            return None
        margin = 8.0
        r = QtCore.QRectF(0.0, 0.0, float(self.width), float(self.height))
        x = float(pos.x())
        y = float(pos.y())
        near_left = abs(x - r.left()) <= margin
        near_right = abs(x - r.right()) <= margin
        near_top = abs(y - r.top()) <= margin
        near_bottom = abs(y - r.bottom()) <= margin

        if near_left and near_top:
            return "top-left"
        if near_right and near_top:
            return "top-right"
        if near_left and near_bottom:
            return "bottom-left"
        if near_right and near_bottom:
            return "bottom-right"
        if near_left:
            return "left"
        if near_right:
            return "right"
        if near_top:
            return "top"
        if near_bottom:
            return "bottom"
        return None

    def _note_cursor_for_mode(self, mode: str | None):
        if not mode:
            return None
        if mode in ("left", "right"):
            return QtCore.Qt.SizeHorCursor
        if mode in ("top", "bottom"):
            return QtCore.Qt.SizeVerCursor
        if mode in ("top-left", "bottom-right"):
            return QtCore.Qt.SizeFDiagCursor
        if mode in ("top-right", "bottom-left"):
            return QtCore.Qt.SizeBDiagCursor
        return None

    def _begin_note_resize(self, mode: str, pos: QtCore.QPointF):
        self._note_resize_mode = mode
        self._note_resizing = True
        self._note_resize_start = QtCore.QPointF(pos)
        try:
            self._note_scene_start = QtCore.QPointF(self.mapToScene(pos))
        except Exception:
            self._note_scene_start = QtCore.QPointF()
        self._note_initial_rect = QtCore.QRectF(0.0, 0.0, float(self.width), float(self.height))
        self._note_initial_pos = QtCore.QPointF(self.pos())

    def _apply_note_resize(self, pos: QtCore.QPointF):
        if not self._note_resize_mode:
            return
        rect = QtCore.QRectF(self._note_initial_rect)
        try:
            scene_pos = self.mapToScene(pos)
            delta_scene = scene_pos - self._note_scene_start
            delta = QtCore.QPointF(delta_scene.x(), delta_scene.y())
        except Exception:
            delta = pos - self._note_resize_start
        new_rect = QtCore.QRectF(rect)
        new_pos = QtCore.QPointF(self._note_initial_pos)
        kind = (self.model.kind or "").lower()
        if kind in ("chatbot", "chat bot", "chat_bot"):
            min_w = float(getattr(self, "_chatbot_min_w", self._BASE_W))
            min_h = float(getattr(self, "_chatbot_min_h", self._BASE_H))
        else:
            min_w = float(self._BASE_W)
            min_h = float(self._BASE_H)
        mode = self._note_resize_mode

        if "right" in mode:
            new_rect.setWidth(max(min_w, rect.width() + delta.x()))
        if "bottom" in mode:
            new_rect.setHeight(max(min_h, rect.height() + delta.y()))
        if "left" in mode:
            dx = delta.x()
            max_dx = rect.width() - min_w
            dx = min(max_dx, dx)
            new_rect.setWidth(max(min_w, rect.width() - dx))
            new_pos.setX(self._note_initial_pos.x() + dx)
        if "top" in mode:
            dy = delta.y()
            max_dy = rect.height() - min_h
            dy = min(max_dy, dy)
            new_rect.setHeight(max(min_h, rect.height() - dy))
            new_pos.setY(self._note_initial_pos.y() + dy)

        new_rect.setWidth(max(min_w, new_rect.width()))
        new_rect.setHeight(max(min_h, new_rect.height()))

        changed = (
            abs(new_rect.width() - float(self.width)) > 0.25
            or abs(new_rect.height() - float(self.height)) > 0.25
            or (new_pos != self.pos())
        )
        if not changed:
            return

        try:
            self.prepareGeometryChange()
        except Exception:
            pass
        self.width = float(new_rect.width())
        self.height = float(new_rect.height())
        if new_pos != self.pos():
            self.setPos(new_pos)
        try:
            if kind == "note":
                self.model._note_size = (float(self.width), float(self.height))
            elif kind in ("chatbot", "chat bot", "chat_bot"):
                self.model._chatbot_size = (float(self.width), float(self.height))
        except Exception:
            pass
        try:
            self._build_widgets()
        except Exception:
            pass
        sc = self.scene()
        if sc:
            try:
                for edge in getattr(sc, "_edges", []):
                    if edge.src is self or edge.dst is self:
                        edge.updatePath()
            except Exception:
                pass
        self.update()

    def _finish_note_resize(self):
        if self._note_resize_mode:
            self._note_resize_mode = None
            try:
                kind = (self.model.kind or "").lower()
                if kind == "note":
                    self.model._note_size = (float(self.width), float(self.height))
                elif kind in ("chatbot", "chat bot", "chat_bot"):
                    self.model._chatbot_size = (float(self.width), float(self.height))
            except Exception:
                pass
            self._schedule_rebuild()
        self._note_resizing = False
        self._note_scene_start = QtCore.QPointF()

    def _switch_label_text(self):
        n = len(self.model.switch_inputs)
        idx = max(0, min(self.model.switch_index, max(0, n - 1)))
        return f"Branch {idx+1}/{max(1, n)}"

    def _on_switch_slider(self, v, label_widget):
        self.model.switch_index = int(v)
        if isinstance(label_widget, QtWidgets.QLabel):
            label_widget.setText(self._switch_label_text())
        self.switchIndexChanged.emit(self, self.model.switch_index)

    def _on_param_changed(self, idx, txt):
        try:
            self.model.params[idx]["value"] = txt
        except Exception:
            pass

        sc = self.scene()
        if sc and hasattr(sc, "paramChanged"):
            try:
                sc.paramChanged.emit(self.model.name, list(self.model.params))
            except Exception:
                pass

        if (self.model.kind or "").lower() == "llm":
            try:
                name = (self.model.params[idx]["name"] or "").lower()
            except Exception:
                name = ""
            if name in ("url", "address", "endpoint"):
                norm = self._normalize_url(txt)
                if self._llm_sampler:
                    try:
                        self._llm_sampler.set_url(QtCore.QUrl(norm))
                    except Exception:
                        pass

    # (Deliberately NO NodeItem.eventFilter override — avoids accidental second path.)

    def _open_big_param_editor(self, title: str, initial_text: str, apply_to_lineedit: QtWidgets.QLineEdit):
        parent = _top_level_parent_for_dialog()
        dlg = BigTextEditDialog(parent, title=title, initial=initial_text)
        try:
            dlg.setWindowFlags(
                QtCore.Qt.Dialog
                | QtCore.Qt.CustomizeWindowHint
                | QtCore.Qt.WindowTitleHint
                | QtCore.Qt.WindowCloseButtonHint
            )
        except Exception:
            pass
        try:
            dlg.setWindowModality(QtCore.Qt.WindowModal if parent is not None else QtCore.Qt.NonModal)
        except Exception:
            pass
        try:
            sz = dlg.sizeHint()
            w = max(560, int(sz.width()  or 560))
            h = max(360, int(sz.height() or 360))
            dlg.resize(w, h)
        except Exception:
            pass
        try:
            cp = QtGui.QCursor.pos()
            screen = QtGui.QGuiApplication.screenAt(cp) or QtWidgets.QApplication.primaryScreen()
            sgeom = screen.availableGeometry() if screen else QtCore.QRect(100, 100, 1200, 800)
            x = cp.x() - dlg.width() // 2
            y = cp.y() - dlg.height() // 2
            x = max(sgeom.left() + 8,  min(x, sgeom.right()  - dlg.width()  - 8))
            y = max(sgeom.top()  + 8,  min(y, sgeom.bottom() - dlg.height() - 8))
            dlg.move(x, y)
        except Exception:
            pass
        try:
            fw = QtWidgets.QApplication.focusWidget()
            if fw and isinstance(fw, QtWidgets.QWidget):
                fw.clearFocus()
        except Exception:
            pass
        try:
            dlg.show(); dlg.raise_(); dlg.activateWindow()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass
        if _qexec(dlg) == QtWidgets.QDialog.Accepted:
            apply_to_lineedit.setText(dlg.text())         # triggers textChanged → updates model
            try:
                apply_to_lineedit.editingFinished.emit()  # optional: keep downstream listeners consistent
            except Exception:
                pass

    def boundingRect(self):
        m = 6
        extra_top = 0.0
        try:
            kind_lower = (self.model.kind or "").lower()
            if kind_lower in (
                "database",
                "llm",
                "llm_prompt",
                "append",
                "note",
                "librarian",
                "import",
                "output",
                "python",
                "switch",
                "chatbot",
                "chat bot",
                "chat_bot",
                "scene",
                "scene_assembly",
                "scene_outliner",
            ):
                # Allow space for floating icon above the bar
                extra_top = 80.0
        except Exception:
            pass
        return QtCore.QRectF(-m, -m - extra_top, self.width + 2 * m, self.height + 2 * m + extra_top)

    def shape(self):
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(0, 0, self.width, self.height), self.radius, self.radius)
        return path

    def paint(self, p: QtGui.QPainter, opt: QtWidgets.QStyleOptionGraphicsItem, w: QtWidgets.QWidget | None = None):
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # --- Base body ---
        r = QtCore.QRectF(0, 0, self.width, self.height)
        body = QtGui.QColor("#262930" if not self._hover else "#2f343c")
        try:
            if getattr(self, "_transparent_body", False):
                p.setBrush(QtCore.Qt.NoBrush)
                p.setPen(self.pen)
                p.drawRoundedRect(r, self.radius, self.radius)
            else:
                p.setBrush(QtGui.QBrush(body))
                p.setPen(self.pen)  # outline pen set in __init__
                p.drawRoundedRect(r, self.radius, self.radius)
        except Exception as e:
            print("[EchoGraph][paint] body fail:", e)

        # --- Selection outline ---
        try:
            if self.isSelected():
                sel_pen = QtGui.QPen(QtGui.QColor("#93c5fd"), 1.4)
                sel_pen.setCosmetic(True)
                p.setPen(sel_pen)
                p.setBrush(QtCore.Qt.NoBrush)
                grow = 0.45
                rect = r.adjusted(-grow, -grow, grow, grow)
                radius = max(0.0, self.radius + 0.15)
                p.drawRoundedRect(rect, radius, radius)
        except Exception:
            pass
        finally:
            p.setPen(QtCore.Qt.NoPen)

        if self._busy:
            try:
                pulse = QtGui.QColor(249, 115, 22)
                pulse.setAlpha(180 if self._busy_flash_on else 90)
                p.save()
                busy_pen = QtGui.QPen(pulse, 3.0)
                busy_pen.setCosmetic(True)
                p.setPen(busy_pen)
                p.setBrush(QtCore.Qt.NoBrush)
                glow_rect = r.adjusted(-2.0, -2.0, 2.0, 2.0)
                p.drawRoundedRect(glow_rect, self.radius + 2.0, self.radius + 2.0)
                p.restore()
            except Exception:
                p.setPen(QtCore.Qt.NoPen)

        # --- Stripe color (from registry or default) ---
        stripe_hex = _spec_stripe_color((self.model.kind or "node").lower())

        # --- Top stripe ---
        try:
            stripe_h = 12.0
            full_rect = QtCore.QRectF(0.0, 0.0, self.width, self.height)
            clip_rect = QtCore.QRectF(0.0, 0.0, self.width, stripe_h)
            clip_path = QtGui.QPainterPath()
            clip_path.addRoundedRect(full_rect, self.radius, self.radius)
            p.save()
            p.setClipPath(clip_path)
            p.setClipRect(clip_rect, QtCore.Qt.IntersectClip)
            p.setBrush(QtGui.QColor(stripe_hex))
            p.setPen(QtCore.Qt.NoPen)
            p.drawRect(clip_rect)
            p.restore()
        except Exception as e:
            print("[EchoGraph][paint] stripe fail:", e)

        # --- Title (node name) ---
        try:
            # In NodeItem.paint()
            p.setPen(self.titlePen)
            fm = QtGui.QFontMetrics(p.font())
            name_txt = self.model.name or "<Unnamed>"
            p.drawText(
                QtCore.QPointF(10, 28),
                fm.elidedText(name_txt, QtCore.Qt.ElideRight, int(self.width - 16)),
            )
        except Exception as e:
            print("[EchoGraph][paint] title fail:", e)

        # --- Optional icon for specific node kinds ---
        try:
            kind_lower = (self.model.kind or "").lower()
            icon_pm = None
            if kind_lower == "database":
                icon_pm = node_icons._db_icon()
            elif kind_lower == "llm":
                icon_pm = node_icons._llm_server_icon()
            elif kind_lower in ("llm_prompt",):
                icon_pm = node_icons._llm_icon()
            elif kind_lower == "append":
                icon_pm = node_icons._append_icon()
            elif kind_lower == "note":
                icon_pm = node_icons._note_icon()
            elif kind_lower == "librarian":
                icon_pm = node_icons._librarian_icon()
            elif kind_lower == "import":
                icon_pm = getattr(self, "_import_icon_pm", None) or node_icons._import_icon()
            elif kind_lower == "switch":
                icon_pm = node_icons._switch_icon()
            elif kind_lower in ("chatbot", "chat bot", "chat_bot"):
                icon_pm = node_icons._chatbot_icon()
            elif kind_lower in ("scene", "scene_assembly", "scene_outliner"):
                icon_pm = node_icons._scene_icon()
            elif kind_lower == "output":
                icon_pm = node_icons._output_icon()
            elif kind_lower == "python":
                icon_pm = node_icons._python_icon()
            if icon_pm and not icon_pm.isNull():
                scale = 1.0
                try:
                    view = self.scene().views()[0] if self.scene() and self.scene().views() else None
                    if view:
                        scale = float(view.transform().m11())
                except Exception:
                    scale = 1.0
                # Grow when zoomed out; clamp with larger max
                size = int(max(40, min(128, 38 / max(scale, 0.001))))
                if kind_lower in ("chatbot", "chat bot", "chat_bot"):
                    size = int(size * 1.13)
                pm_scaled = icon_pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                x = (self.width - pm_scaled.width()) / 2.0
                # float above the top bar
                y = -pm_scaled.height() * 0.6
                if kind_lower in ("chatbot", "chat bot", "chat_bot"):
                    y = -pm_scaled.height() * 0.7
                p.drawPixmap(QtCore.QPointF(x, y), pm_scaled)
        except Exception:
            pass

        # --- Kind badge (type pill) ---
        try:
            kb_y = 38  # under the title line
            kb = QtCore.QRectF(10, kb_y, 80, 16)
            p.setBrush(QtGui.QBrush(QtGui.QColor("#d1d5db")))
            p.setPen(QtCore.Qt.NoPen)
            r = 8.0
            rl = 1.0
            path = QtGui.QPainterPath()
            path.moveTo(kb.left() + rl, kb.top())
            path.lineTo(kb.right() - r, kb.top())
            path.quadTo(kb.right(), kb.top(), kb.right(), kb.top() + r)
            path.lineTo(kb.right(), kb.bottom() - r)
            path.quadTo(kb.right(), kb.bottom(), kb.right() - r, kb.bottom())
            path.lineTo(kb.left() + rl, kb.bottom())
            path.quadTo(kb.left(), kb.bottom(), kb.left(), kb.bottom() - rl)
            path.lineTo(kb.left(), kb.top() + rl)
            path.quadTo(kb.left(), kb.top(), kb.left() + rl, kb.top())
            path.closeSubpath()
            p.drawPath(path)
            badge_font = p.font()
            badge_font.setBold(True)
            p.setFont(badge_font)
            p.setPen(QtGui.QPen(QtGui.QColor("#111111")))
            badge = (self.model.kind or "node").upper()
            p.drawText(kb.adjusted(6, 1, -6, -2), QtCore.Qt.AlignCenter, badge)
        except Exception as e:
            print("[EchoGraph][paint] badge fail:", e)

        # --- IO sockets ---
        try:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#cbd5e1"))
            p.drawEllipse(QtCore.QRectF(self.width - 4, self._BASE_H / 2.0 - 4, 8, 8))
            entries = list(getattr(self, "_input_port_pos", {}).items())
            wired = self._wired_named_inputs()
            if entries:
                for key, (pos, _) in entries:
                    color = QtGui.QColor("#facc15" if key in wired else "#cbd5e1")
                    p.setBrush(color)
                    p.drawEllipse(QtCore.QRectF(float(pos.x()) - 4.0, float(pos.y()) - 4.0, 8.0, 8.0))
            else:
                p.drawEllipse(QtCore.QRectF(-4, self._BASE_H / 2.0 - 4, 8, 8))
        except Exception as e:
            print("[EchoGraph][paint] sockets fail:", e)


    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()
        self.unsetCursor()

    def hoverMoveEvent(self, e):
        if self._note_resize_available():
            mode = self._note_hit_test(e.pos())
            cursor = self._note_cursor_for_mode(mode)
            if cursor:
                self.setCursor(cursor)
            else:
                self.unsetCursor()
        else:
            self.unsetCursor()
        super().hoverMoveEvent(e)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange:
            if getattr(self, "_note_resizing", False):
                return super().itemChange(change, value)
            sc = self.scene()
            if (
                sc
                and getattr(sc, "_group_drag_active", False)
                and not getattr(sc, "_group_move_lock", False)
                and self.isSelected()
                and isinstance(value, QtCore.QPointF)
            ):
                selected_items = list(sc.selectedItems())
                selected_nodes = [it for it in selected_items if isinstance(it, NodeItem)]
                selected_pins = [
                    it for it in selected_items
                    if getattr(it, "__class__", type(it)).__name__ == "EdgePin"
                ]
                if (len(selected_nodes) + len(selected_pins)) > 1:
                    delta = value - self.pos()
                    if isinstance(delta, QtCore.QPointF) and delta.manhattanLength() > 0:
                        sc._group_move_lock = True
                        try:
                            for it in selected_nodes:
                                if it is self:
                                    continue
                                it.setPos(it.pos() + delta)
                            for it in selected_pins:
                                try:
                                    it.setPos(it.pos() + delta)
                                except Exception:
                                    pass
                        finally:
                            sc._group_move_lock = False

        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            sc = self.scene()
            suppress_model = bool(sc and getattr(sc, "_suppress_node_model_updates", False))
            if not suppress_model:
                # Write both: new model (pos_xy) + legacy (pos) for compatibility
                try:
                    if isinstance(value, QtCore.QPointF):
                        try:
                            self.model.pos_xy = (float(value.x()), float(value.y()))
                        except Exception:
                            pass
                        try:
                            # Keep legacy QPointF field alive until all code switches to pos_xy
                            self.model.pos = value
                        except Exception:
                            pass
                except Exception:
                    pass
            if sc and not suppress_model:
                # Keep connected edges updated
                for edge in getattr(sc, "_edges", []):
                    if edge.src is self or edge.dst is self:
                        try:
                            edge.updatePath()
                        except Exception:
                            pass
                # Let the scene auto-grow to fit nodes
                if hasattr(sc, "_reframe_to_nodes"):
                    try:
                        sc._reframe_to_nodes(margin=8000.0)
                    except Exception:
                        pass
                if hasattr(sc, "_update_comment_membership_for_node"):
                    try:
                        sc._update_comment_membership_for_node(self)
                    except Exception:
                        pass

        return super().itemChange(change, value)


    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._bring_to_front()
            scene = self.scene()
            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & QtCore.Qt.ControlModifier)
            if scene and not shift:
                keep_edge_highlight = False
                if self.isSelected():
                    try:
                        for it in list(scene.selectedItems()):
                            name = getattr(it, "__class__", type(it)).__name__
                            if name == "EdgePin":
                                keep_edge_highlight = True
                                break
                    except Exception:
                        keep_edge_highlight = False
                if not keep_edge_highlight and hasattr(scene, "_clear_edge_click_highlight"):
                    try:
                        scene._clear_edge_click_highlight()
                    except Exception:
                        pass
            if self._note_resize_available():
                mode = self._note_hit_test(e.pos())
                if mode:
                    self._begin_note_resize(mode, e.pos())
                    e.accept()
                    return
            self._lmb_press_scene = self.mapToScene(e.pos())
            self._lmb_started_wire = False
            if scene:
                if shift:
                    snapshot = getattr(scene, "_shift_select_snapshot", None)
                    if snapshot is None:
                        try:
                            snapshot = list(scene.selectedItems())
                        except Exception:
                            snapshot = []
                    was_selected = False
                    for it in snapshot:
                        if it is self:
                            was_selected = True
                            break
                    self.setSelected(not was_selected)
                    for it in snapshot:
                        if it is self:
                            continue
                        name = getattr(it, "__class__", type(it)).__name__
                        if isinstance(it, NodeItem) or name in ("CommentGroup", "EdgePin"):
                            try:
                                it.setSelected(True)
                            except Exception:
                                pass
                    try:
                        scene._shift_select_snapshot = None
                    except Exception:
                        pass
                    try:
                        scene._group_drag_active = False
                    except Exception:
                        pass
                    self._skip_release_super = True
                    self._lmb_press_scene = None
                    self._lmb_started_wire = False
                    e.accept()
                    return
                elif ctrl:
                    self.setSelected(not self.isSelected())
                    try:
                        scene._group_drag_active = False
                    except Exception:
                        pass
                    self._skip_release_super = True
                    self._lmb_press_scene = None
                    self._lmb_started_wire = False
                    e.accept()
                    return
                else:
                    if not self.isSelected():
                        for it in list(scene.selectedItems()):
                            name = getattr(it, "__class__", type(it)).__name__
                            if it is self:
                                continue
                            if isinstance(it, NodeItem):
                                it.setSelected(False)
                            elif name in ("CommentGroup", "EdgePin"):
                                it.setSelected(False)
                        self.setSelected(True)

            on_right_socket = (self.width - 12 <= e.pos().x() <= self.width + 6) and (0 <= e.pos().y() <= self._BASE_H)
            if on_right_socket:
                if scene:
                    scene._group_drag_active = False
                try:
                    self.startWireDrag.emit(self)
                    self._lmb_started_wire = True
                except Exception:
                    pass
            else:
                if scene:
                    selected_items = list(scene.selectedItems())
                    selected_nodes = [it for it in selected_items if isinstance(it, NodeItem)]
                    selected_pins = [
                        it for it in selected_items
                        if getattr(it, "__class__", type(it)).__name__ == "EdgePin"
                    ]
                    scene._group_drag_active = (len(selected_nodes) + len(selected_pins)) > 1
                try:
                    self.clicked.emit(self.model)
                except Exception:
                    pass
            super().mousePressEvent(e)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._note_resize_mode:
            self._apply_note_resize(e.pos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            if self._note_resize_mode:
                self._apply_note_resize(e.pos())
                self._finish_note_resize()
                e.accept()
                return
            scene = self.scene()
            if getattr(self, "_skip_release_super", False):
                self._skip_release_super = False
                if scene:
                    scene._group_drag_active = False
                self._lmb_press_scene = None
                self._lmb_started_wire = False
                e.accept()
                return
            if scene:
                scene._group_drag_active = False
            try:
                press_scene = getattr(self, "_lmb_press_scene", None)
                if press_scene is not None:
                    rel_scene = self.mapToScene(e.pos())
                    if (rel_scene - press_scene).manhattanLength() <= 4 and not getattr(self, "_lmb_started_wire", False):
                        self.clicked.emit(self.model)
            finally:
                self._lmb_press_scene = None
                self._lmb_started_wire = False
            super().mouseReleaseEvent(e)
            e.accept()
            return
        super().mouseReleaseEvent(e)


def _open_in_explorer(path_str: str):
    if not path_str:
        return
    try:
        path = Path(path_str).expanduser().resolve()
    except Exception:
        return
    if not path.exists():
        return
    try:
        import subprocess
        if QtCore.QSysInfo.productType().lower().startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif QtCore.QSysInfo.productType().lower() == "osx":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])
    except Exception:
        pass


