# ==== echograph (single-file shelf script; runs on click) ======================
# Maya + Houdini PySide2 node-graph demo with info cards, Output path highlight,
# multi-input Switch with slider, JSON Open/Save/Export, node params, wiring, and code editor.
# Nav: Left-click = select / wire-drag; Middle-mouse = pan; Right-drag = zoom.
# Alt+LeftClick a link to delete it. Badge (type bubble) is below the name.
# Quick-create: right-click empty canvas (no drag) to open Create Node.
# Delete/Backspace removes selected nodes with their links.
# Clicking an Output node auto-fills Info pane with ordered branch cards (start → output).

import sys, re, json, math, os, time, subprocess
from pathlib import Path
from typing import List, Dict, Any

from echograph.ui.infocard import InfoCard
from echograph.ui.graph_items import _gi_flag, EdgeItem, TempWire
from echograph.ui.view import GraphView
from echograph.ui.node_item import NodeItem
from echograph import persistence
from echograph.model import GraphNode

from echograph.qt_compat import (
    QtCore, QtGui, QtWidgets,
    QAction, QShortcut, QKeySequence,
    wrapInstance, QT_IS_6, _qexec
)
from echograph.constants import (
    APP_TITLE, APP_ICON, KEY_BIGEDIT,
    LLM_URL, LLM_NODE_W_BASE, LLM_NODE_H_BASE,
    DEFAULT_STRIPE_HEX, LLM_SCALE_DEFAULT,
    script_dir
)

# Librarian IPC glue (redirect old private helpers to the new service)
from echograph.services.librarian_ipc import (
    outbox_dir,
    enqueue,
    load_output_text_by_ts,
    ensure_running,
)

from echograph.ui.dialogs import (
    CodeEditorDialog,
    ParamEditorDialog,
    BigTextEditDialog,
    CreateNodeDialog,
    RecentGraphsDialog,
)

# Keep a local mutable scale (slider edits this)
LLM_SCALE = float(LLM_SCALE_DEFAULT)
def _llm_dims():
    return int(LLM_NODE_W_BASE * LLM_SCALE), int(LLM_NODE_H_BASE * LLM_SCALE)

# Derived dims used by node sizing
LLM_NODE_W, LLM_NODE_H = _llm_dims()

# Recent file tracking
_RECENT_GRAPHS_PATH = script_dir() / "recent_graphs.json"
_RECENT_GRAPHS_LIMIT = 10

def _load_recent_graphs() -> List[str]:
    try:
        data = json.loads(_RECENT_GRAPHS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            out = []
            for entry in data:
                if isinstance(entry, str):
                    entry = entry.strip()
                    if entry:
                        out.append(entry)
                if len(out) >= _RECENT_GRAPHS_LIMIT:
                    break
            return out
    except Exception:
        pass
    return []

def _save_recent_graphs(paths: List[str]) -> None:
    try:
        _RECENT_GRAPHS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_GRAPHS_PATH.write_text(
            json.dumps(list(paths), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

class CommentGroup(QtWidgets.QGraphicsObject):
    Type = QtWidgets.QGraphicsItem.UserType + 5201

    def __init__(self, scene: 'GraphScene', title: str, body: str, members: List[str], rect: QtCore.QRectF):
        super().__init__()
        self._scene_ref = scene
        self._title = (title or "").strip() or "Comment"
        self._body = (body or "").strip()
        self._members = [str(m) for m in (members or []) if m]

        rect = QtCore.QRectF(rect)
        if rect.width() < 160:
            rect.setWidth(160)
        if rect.height() < 100:
            rect.setHeight(100)
        self._rect = QtCore.QRectF(0.0, 0.0, rect.width(), rect.height())
        self.setPos(rect.topLeft())

        self.setZValue(0.2)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._resize_mode = None
        self._resize_start_pos = QtCore.QPointF()
        self._initial_rect = QtCore.QRectF(self._rect)
        self._initial_pos = QtCore.QPointF(self.pos())
        self._suspend_member_move = False

    # --- data helpers --------------------------------------------------------
    def members(self) -> List[str]:
        return list(self._members)

    def remove_member(self, name: str):
        if name in self._members:
            self._members = [m for m in self._members if m != name]

    def replace_member(self, old: str, new: str):
        self._members = [new if m == old else m for m in self._members]

    def to_dict(self) -> dict:
        pos = self.scenePos()
        return {
            "title": self._title,
            "body": self._body,
            "members": list(self._members),
            "rect": [float(pos.x()), float(pos.y()), float(self._rect.width()), float(self._rect.height())],
        }

    # --- graphics ------------------------------------------------------------
    def boundingRect(self) -> QtCore.QRectF:
        return self._rect.adjusted(-8, -28, 8, 8)

    def shape(self) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        path.addRoundedRect(self._rect, 14, 14)
        return path

    def paint(self, p: QtGui.QPainter, option, widget=None):
        rect = QtCore.QRectF(self._rect)
        padding = 14.0
        bg = QtGui.QColor("#1f2933")
        bg.setAlphaF(0.45)
        pen = QtGui.QPen(QtGui.QColor("#94a3b8"), 1.2, QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        p.setBrush(QtGui.QBrush(bg))
        p.setPen(pen)
        p.drawRoundedRect(rect, 14, 14)

        title_rect = QtCore.QRectF(rect.x() + padding, rect.y() + padding, rect.width() - 2 * padding, 24)
        title_color = QtGui.QColor("#e2e8f0")
        p.setPen(QtGui.QPen(title_color))
        font = p.font()
        font.setBold(True)
        p.setFont(font)
        p.drawText(title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._title)

        if self._body:
            body_rect = QtCore.QRectF(title_rect.x(), title_rect.bottom() + 4, title_rect.width(), rect.height() - 2 * padding - 28)
            font.setBold(False)
            p.setFont(font)
            body_color = QtGui.QColor("#94a3b8")
            p.setPen(QtGui.QPen(body_color))
            p.drawText(body_rect, QtCore.Qt.TextWordWrap, self._body)

    # --- behavior ------------------------------------------------------------
    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and self.scene():
            old_pos = self.pos()
            new_pos = value if isinstance(value, QtCore.QPointF) else QtCore.QPointF(value)
            delta = new_pos - old_pos
            if delta.manhattanLength() > 0:
                scene = self.scene()
                if (
                    scene
                    and hasattr(scene, "_move_comment_members")
                    and not getattr(self, "_suspend_member_move", False)
                ):
                    scene._move_comment_members(self, delta)
        return super().itemChange(change, value)

    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.AltModifier):
            e.ignore()
            return
        if e.button() == QtCore.Qt.LeftButton:
            mode = self._hit_test_resize(e.pos())
            if mode:
                self._resize_mode = mode
                self._resize_start_pos = QtCore.QPointF(e.pos())
                self._initial_rect = QtCore.QRectF(self._rect)
                self._initial_pos = QtCore.QPointF(self.pos())
                e.accept()
                return
        if e.button() == QtCore.Qt.LeftButton and not (e.modifiers() & QtCore.Qt.ControlModifier):
            self._select_members()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._resize_mode:
            self._apply_resize(e.pos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._resize_mode and e.button() == QtCore.Qt.LeftButton:
            self._resize_mode = None
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            new_title, ok = QtWidgets.QInputDialog.getText(
                None,
                "Edit Comment Title",
                "Title:",
                text=self._title,
            )
            if ok:
                self._title = (new_title or "").strip() or self._title
            new_body, ok = QtWidgets.QInputDialog.getMultiLineText(
                None,
                "Edit Comment Body",
                "Body:",
                self._body,
            )
            if ok:
                self._body = (new_body or "").strip()
            self.update()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def hoverMoveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent):
        mode = self._hit_test_resize(e.pos())
        cursor = self._cursor_for_mode(mode)
        if cursor:
            self.setCursor(cursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent):
        self.unsetCursor()
        super().hoverLeaveEvent(e)

    def _select_members(self):
        scene = self.scene()
        if not scene:
            return
        for it in scene.selectedItems():
            if not isinstance(it, CommentGroup):
                it.setSelected(False)
        for name in self._members:
            node = scene._node_items.get(name)
            if node:
                node.setSelected(True)

    def _hit_test_resize(self, pos: QtCore.QPointF) -> str | None:
        margin = 8.0
        r = self._rect
        x = pos.x()
        y = pos.y()
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

    def _cursor_for_mode(self, mode: str | None):
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

    def _apply_resize(self, pos: QtCore.QPointF):
        if not self._resize_mode:
            return
        min_w, min_h = 160.0, 100.0
        rect = QtCore.QRectF(self._initial_rect)
        pos_delta = pos - self._resize_start_pos
        new_rect = QtCore.QRectF(rect)
        new_pos = QtCore.QPointF(self._initial_pos)
        mode = self._resize_mode

        if "right" in mode:
            new_rect.setWidth(max(min_w, rect.width() + pos_delta.x()))
        if "bottom" in mode:
            new_rect.setHeight(max(min_h, rect.height() + pos_delta.y()))
        if "left" in mode:
            dx = pos_delta.x()
            max_dx = rect.width() - min_w
            dx = min(max_dx, dx)
            new_rect.setWidth(max(min_w, rect.width() - dx))
            new_pos.setX(self._initial_pos.x() + dx)
        if "top" in mode:
            dy = pos_delta.y()
            max_dy = rect.height() - min_h
            dy = min(max_dy, dy)
            new_rect.setHeight(max(min_h, rect.height() - dy))
            new_pos.setY(self._initial_pos.y() + dy)

        new_rect.setWidth(max(min_w, new_rect.width()))
        new_rect.setHeight(max(min_h, new_rect.height()))

        if new_rect == self._rect and new_pos == self.pos():
            return

        self.prepareGeometryChange()
        self._rect = new_rect
        if new_pos != self.pos():
            self._suspend_member_move = True
            try:
                self.setPos(new_pos)
            finally:
                self._suspend_member_move = False
        self.update()

        scene = self.scene()
        if scene and hasattr(scene, "_refresh_comment_group_membership"):
            scene._refresh_comment_group_membership(self)

# --- WebEngine (for embedding Gradio UI) ---
try:
    from PySide6 import QtWebEngineWidgets as WebEngine
except Exception:
    try:
        from PySide2 import QtWebEngineWidgets as WebEngine
    except Exception:
        WebEngine = None

def set_global_llm_scale(new_scale: float, scene=None):
    global LLM_SCALE, LLM_NODE_W, LLM_NODE_H
    try:
        s = float(new_scale)
    except Exception:
        return
    s = max(0.25, min(1.75, s))
    if abs(s - LLM_SCALE) < 1e-6 and (scene is None or getattr(scene, "_llm_scale", None) == s):
        return

    LLM_SCALE = s
    LLM_NODE_W, LLM_NODE_H = _llm_dims()

    if scene is not None:
        scene._llm_scale = s   # ← important so NodeItem uses the fresh value

        # Rebuild LLM nodes (unchanged)
        for item in list(getattr(scene, "_node_items", {}).values()):
            try:
                kind = (item.model.kind or "").lower()
                if kind in ("llm", "html_preview"):
                    item._recompute_height()
                    item._build_widgets()
            except Exception:
                pass

        for e in list(getattr(scene, "_edges", [])):
            try: e.updatePath()
            except Exception: pass

        try:
            if hasattr(scene, "_reframe_to_nodes"):
                scene._reframe_to_nodes()
        except Exception:
            pass

# script_dir() and APP_ICON now come from echograph.constants

# --- Import path bootstrap (must come before importing nodes.core) ---
_BASE_DIR = script_dir()
_NODES_DIR = _BASE_DIR / "nodes"
for _p in (str(_BASE_DIR), str(_NODES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --- Node-kind registry (plugin hooks) ---
import nodes.core as core
from nodes.loader import bootstrap_plugins

core.register_defaults()

# --- host detection (Maya / Houdini / standalone) ---
HOST = "standalone"
_SKIP_RECENT_DIALOG = "--skip-recent" in sys.argv
maya_cmds = None
omui = None
hou_mod = None

try:
    from maya import cmds as maya_cmds
    from maya import OpenMayaUI as omui
    HOST = "maya"
except Exception:
    try:
        import hou as hou_mod
        HOST = "houdini"
    except Exception:
        pass

def _main_window():
    if HOST == "maya" and omui:
        try:
            ptr = omui.MQtUtil.mainWindow()
            if ptr and wrapInstance:
                return wrapInstance(int(ptr), QtWidgets.QWidget)
        except Exception:
            return None
    if HOST == "houdini" and hou_mod:
        try:
            return hou_mod.qt.mainWindow()
        except Exception:
            return None
    return None

WS_CTRL = "EchoGraphWorkspaceControl"  # Maya only

def _as_pointf(pt):
    if isinstance(pt, QtCore.QPointF):
        return pt
    try:
        # accept (x, y) tuples/lists, or any sequence of 2 numbers
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            return QtCore.QPointF(float(pt[0]), float(pt[1]))
    except Exception:
        pass
    return QtCore.QPointF(0.0, 0.0)


# scene/view/graph ops
class GraphScene(QtWidgets.QGraphicsScene):
    nodeDeleted = QtCore.Signal(str)
    paramChanged = QtCore.Signal(str, list)  # (node_name, params)
    linksChanged = QtCore.Signal()

    def __init__(self, on_info=None, on_branch=None):
        try:
            super().__init__()
        except TypeError:
            super(GraphScene, self).__init__()

        self.on_info = on_info
        self.on_branch = on_branch

        self._nodes_by_name = {}
        self._node_items = {}
        self._edges = []
        self._comment_groups = []
        self._moving_comment_group = False
        self._drag_src_item = None
        self._temp_wire = None
        self._current_output_name = None
        self._last_paste_jitter = QtCore.QPointF(0.0, 0.0)
        self._group_drag_active = False
        self._group_move_lock = False

        try:
            self.setItemIndexMethod(QtWidgets.QGraphicsScene.NoIndex)
        except AttributeError:
            try:
                self.setItemIndexMethod(QtWidgets.QGraphicsScene.ItemIndexMethod.NoIndex)
            except Exception:
                self.setItemIndexMethod(0)

        self.setBackgroundBrush(QtGui.QColor("#1a1f24"))
        self.setSceneRect(QtCore.QRectF(-20000, -20000, 40000, 40000))

    def refresh_node_widget(self, name: str):
        it = self._node_items.get(name)
        if not it:
            return
        it._recompute_height()
        it._build_widgets()
        for e in self._edges:
            if e.src is it or e.dst is it:
                e.updatePath()

    def set_node_params(self, name: str, params: list):
        node = self._nodes_by_name.get(name)
        if not node:
            return False
        clean = []
        for p in (params or []):
            nm = str(p.get("name", "")).strip()
            val = str(p.get("value", ""))
            if nm:
                clean.append({"name": nm, "value": val})
        node.params = clean
        self.refresh_node_widget(name)

        # broadcast so open InfoCards update immediately
        try:
            self.paramChanged.emit(name, list(node.params))
        except Exception:
            pass

        return True


    def _unique_node_name(self, requested: str, kind: str) -> str:
        """
        Return a unique node name:
        - If 'requested' is non-empty: use it, or append 1..N if taken.
        - If empty: use the node 'kind' (lowercased, spaces->underscores),
            or append 1..N if that base is taken.
        """
        existing = set(self._nodes_by_name.keys() or [])
        req = (requested or "").strip()
        knd = (kind or "node").strip() or "node"

        if req:
            base = req
        else:
            import re
            base = re.sub(r"\s+", "_", knd.lower())

        # If base is free, use it
        if base not in existing:
            return base

        # Otherwise append 1..N
        i = 1
        while True:
            cand = f"{base}{i}"
            if cand not in existing:
                return cand
            i += 1

    def rename_node(self, old_name: str, new_name: str):
        new_name = (new_name or "").strip()
        if not new_name or new_name == old_name:
            return False, "No change."
        if new_name in self._nodes_by_name:
            return False, f"A node named '{new_name}' already exists."

        item = self._node_items.get(old_name)
        node = self._nodes_by_name.get(old_name)
        if not item or not node:
            return False, f"Node '{old_name}' not found."

        # Update registries
        self._node_items[new_name] = self._node_items.pop(old_name)
        self._nodes_by_name[new_name] = self._nodes_by_name.pop(old_name)
        node.name = new_name
        item.model.name = new_name
        item.update()

        # Update any nodes that reference the old name (e.g., Switch/Append nodes)
        for it in self._node_items.values():
            if (it.model.kind or "").lower() in ("switch", "append"):
                if old_name in it.model.switch_inputs:
                    it.model.switch_inputs = [
                        (new_name if n == old_name else n) for n in it.model.switch_inputs
                    ]
                    self.refresh_node_widget(it.model.name)

        for cg in self._comment_groups:
            cg.replace_member(old_name, new_name)

        if self._current_output_name == old_name:
            self._current_output_name = new_name

        if self._current_output_name:
            self.recompute_active_path(self._current_output_name)
        else:
            self._clear_path_highlight()

        return True, ""
    
    def upstream_of(self, dst_name: str):
        dst = self._node_items.get(dst_name)
        if not dst: return []
        srcs = []
        for e in self._edges:
            if e.dst is dst:
                srcs.append(e.src)
        return srcs

    def _coerce_node_item(self, ref):
        if isinstance(ref, NodeItem):
            return ref
        if isinstance(ref, GraphNode):
            return self._node_items.get(ref.name)
        if isinstance(ref, str):
            return self._node_items.get(ref)
        if hasattr(ref, "model"):
            return ref
        return None

    def _direct_param_segments(self, node_item):
        item = self._coerce_node_item(node_item)
        if not item or not getattr(item, "model", None):
            return []
        model = item.model
        kind = (model.kind or "").lower()
        segments = []

        params = list(model.params or [])
        for idx, p in enumerate(params):
            val = (p.get("value") or "").strip()
            if not val:
                continue
            name = (p.get("name") or "").strip()
            if not name:
                name = f"param{idx+1}"
            segments.append({"node": model.name, "param": name, "text": val})

        if kind in ("import", "html_preview"):
            path = ""
            for p in params:
                if (p.get("name") or "").strip().lower() == "path":
                    path = (p.get("value") or "").strip()
                    break
            if path:
                text = NodeItem.read_import_text(path)
                if text:
                    segments.append({"node": model.name, "param": "file", "text": text})

        if not segments:
            info = (model.info or "").strip()
            if info:
                segments.append({"node": model.name, "param": "info", "text": info})
        return segments

    def _flatten_text_segments(self, node_item):
        item = self._coerce_node_item(node_item)
        if not item or not getattr(item, "model", None):
            return []
        kind = (item.model.kind or "").lower()

        if kind == "append":
            segs = [dict(seg) for seg in self._direct_param_segments(item)]
            try:
                in_edges = self._ordered_in_edges(item)
            except Exception:
                in_edges = self._in_edges(item)
            for e in in_edges:
                for seg in self._flatten_text_segments(e.src):
                    segs.append(dict(seg))
            return segs

        if kind == "switch":
            try:
                in_edges = self._ordered_in_edges(item)
            except Exception:
                in_edges = self._in_edges(item)
            if in_edges:
                return self._flatten_text_segments(in_edges[0].src)
            return [dict(seg) for seg in self._direct_param_segments(item)]

        if kind in ("import", "html_preview"):
            return [dict(seg) for seg in self._direct_param_segments(item)]

        segs = self._direct_param_segments(item)
        if segs:
            return [dict(seg) for seg in segs]
        return []

    def resolve_text_value(self, node_item) -> str:
        segments = self._flatten_text_segments(node_item)
        texts = [seg.get("text", "") for seg in segments if seg.get("text")]
        return "\n\n".join(texts)

    def resolve_text_label(self, node_item) -> str:
        segments = self._flatten_text_segments(node_item)
        if segments:
            return segments[0].get("param", "")
        return ""

    def text_source_info(self, node_ref):
        segments = self._flatten_text_segments(node_ref)
        if not segments:
            return {"text": "", "parameter": ""}
        first = segments[0]
        return {"text": first.get("text", ""), "parameter": first.get("param", "")}

    def describe_append_inputs(self, append_ref):
        item = self._coerce_node_item(append_ref)
        if not item or (item.model.kind or "").lower() != "append":
            return []

        rows = []
        for seg in self._direct_param_segments(item):
            entry = dict(seg)
            entry["is_local"] = True
            rows.append(entry)

        try:
            in_edges = self._ordered_in_edges(item)
        except Exception:
            in_edges = self._in_edges(item)
        for e in in_edges:
            for seg in self._flatten_text_segments(e.src):
                entry = dict(seg)
                entry["is_local"] = False
                rows.append(entry)
        return rows


    def _nodes_bbox(self):
        rect = None
        for it in self._node_items.values():
            r = it.mapRectToScene(it.boundingRect())
            rect = r if rect is None else rect.united(r)
        return rect

    def _reframe_to_nodes(self, margin: float = 8000.0, min_half_extent: float = 20000.0):
        bbox = self._nodes_bbox()
        if bbox is None:
            self.setSceneRect(QtCore.QRectF(-min_half_extent, -min_half_extent,
                                            2*min_half_extent, 2*min_half_extent))
            return
        newr = bbox.adjusted(-margin, -margin, margin, margin)
        cx = newr.center().x()
        cy = newr.center().y()
        minr = QtCore.QRectF(cx - min_half_extent, cy - min_half_extent,
                             2*min_half_extent, 2*min_half_extent)
        final = newr.united(minr)
        if final != self.sceneRect():
            self.setSceneRect(final)

    def _ensure_space(self, pt: QtCore.QPointF, margin: float = 8000.0):
        pt = _as_pointf(pt)   # <-- add this
        r = self.sceneRect()
        safe = QtCore.QRectF(r.left() + margin, r.top() + margin,
                            r.width() - 2*margin, r.height() - 2*margin)
        if safe.contains(pt):
            return
        left   = min(r.left(),   pt.x() - margin)
        top    = min(r.top(),    pt.y() - margin)
        right  = max(r.right(),  pt.x() + margin)
        bottom = max(r.bottom(), pt.y() + margin)
        self.setSceneRect(QtCore.QRectF(QtCore.QPointF(left, top),
                                        QtCore.QPointF(right, bottom)))


    def show_create_dialog_at(self, scene_pos: QtCore.QPointF):
        self._ensure_space(scene_pos)
        hits = self.items(scene_pos)
        for it in hits:
            if isinstance(it, (NodeItem, EdgeItem)):
                return
        parent = self.views()[0] if self.views() else None
        dlg = CreateNodeDialog(parent, existing_names=list(self._nodes_by_name.keys()))
        if _qexec(dlg) != QtWidgets.QDialog.Accepted: return
        data = dlg.result_payload()
        # Finalize the name (handles empty or conflicting names)
        final_name = self._unique_node_name(data.get("name", ""), data.get("kind", "node"))
        data["name"] = final_name

        node = GraphNode(final_name, kind=data["kind"], info="",
                        params=data["params"], code=data.get("code"))
                
        # Check if it's a Librarian node
        if (data["kind"] or "").lower() == "librarian":
            node.info = "Librarian node created"
            ensure_params = [
                ("query", ""),
                ("docs_dir", ""),
                ("mode", "tree_summarize"),
                ("top_k", "5"),
                ("action", ""),
            ]
            names = {(p.get("name") or "").strip().lower() for p in (node.params or [])}
            for pname, default in ensure_params:
                if pname not in names:
                    node.params = list(node.params or [])
                    node.params.append({"name": pname, "value": default})
                    names.add(pname)

        if (data["kind"] or "").lower() in ("import", "html_preview"):
            names = {(p.get("name") or "").strip().lower() for p in (node.params or [])}
            if "path" not in names:
                node.params = list(node.params or [])
                node.params.append({"name": "path", "value": ""})

        item = self.add_node(node, scene_pos)
        item.setPos(scene_pos - QtCore.QPointF(item.width/2.0, item.height/2.0))
        if callable(self.on_info): self.on_info(node)

    # --- in GraphScene.to_dict(self) ---
    def to_dict(self):
        # Single source of truth lives in echograph.persistence
        return persistence.serialize_scene(self)

    # --- in GraphScene.from_dict(self, data) ---
    def from_dict(self, data):
        # delegate; pass your ctor + scale setter
        persistence.deserialize_scene(
            self,
            data,
            GraphNode_ctor=GraphNode,
            set_scale_cb=lambda s: set_global_llm_scale(s, self),
        )

    def clear_scene(self):
        for e in list(self._edges):
            try: self.removeItem(e)
            except Exception: pass
        self._edges.clear()
        for cg in list(self._comment_groups):
            try: self.removeItem(cg)
            except Exception:
                pass
        self._comment_groups.clear()
        for it in list(self._node_items.values()):
            try: self.removeItem(it)
            except Exception: pass
        self._node_items.clear(); self._nodes_by_name.clear()
        self._current_output_name = None
        self.setSceneRect(QtCore.QRectF(-20000, -20000, 40000, 40000))

    def add_node(self, node: GraphNode, pos):
        # If the model already has a stored position (e.g., after load), prefer it.
        try:
            x, y = node.pos_xy  # may raise if not set yet
            pos = QtCore.QPointF(float(x), float(y))
        except Exception:
            pos = _as_pointf(pos)

        self._ensure_space(pos)

        item = NodeItem(node)
        item.setPos(pos)

        # Write both the legacy QPointF and the new tuple field for persistence/model.
        try:
            node.pos = pos
        except Exception:
            pass
        try:
            node.pos_xy = (float(pos.x()), float(pos.y()))
        except Exception:
            pass

        item.clicked.connect(self._on_node_clicked)
        item.requestCenter.connect(self.center_on_name)
        item.startWireDrag.connect(self._on_start_wire_drag)
        item.switchIndexChanged.connect(self._on_switch_index_changed)

        self.addItem(item)
        self._nodes_by_name[node.name] = node
        self._node_items[node.name] = item
        self._reframe_to_nodes(margin=8000.0)
        return item

    def add_edge(self, src_name, dst_name, dst_port_name=None):
        return self._add_edge_and_update_switch(src_name, dst_name, dst_port_name=dst_port_name)

    def _add_edge_and_update_switch(self, src_name, dst_name, dst_port_name=None):
        src = self._node_items[src_name]; dst = self._node_items[dst_name]
        edge = EdgeItem(src, dst, dst_port_name=dst_port_name)
        self._edges.append(edge); self.addItem(edge)

        dst_kind = (dst.model.kind or "").lower()
        if dst_kind in ("append", "switch"):
            if src.model.name not in dst.model.switch_inputs:
                dst.model.switch_inputs.append(src.model.name)
            if dst_kind == "switch":
                dst.model.switch_index = max(0, min(dst.model.switch_index,
                                                    max(0, len(dst.model.switch_inputs)-1)))
            self.refresh_node_widget(dst.model.name)

            # Also refresh the Append card UI if the destination is an Append/Switch node with a list
            try:
                win = self.views()[0].window() if self.views() else None
                if win:
                    card = getattr(win, "_card_by_node", {}).get(dst.model.name)
                    if card and hasattr(card, "refresh_append_ui_from_model"):
                        card.refresh_append_ui_from_model()
            except Exception:
                pass

        # NEW: keep Info panel + preview live when graph changes
        if self._current_output_name:
            try:
                self.recompute_active_path(self._current_output_name)
                if self.views() and hasattr(self.views()[0].window(), "populate_branch_info"):
                    win = self.views()[0].window()
                    seq = self.ordered_upstream_items(self._current_output_name)
                    win.populate_branch_info([it.model for it in seq])
                    # refresh merged preview on the Output card too
                    card = getattr(win, "_card_by_node", {}).get(self._current_output_name)
                    if card and hasattr(card, "apply_append_preview_if_output"):
                        card.apply_append_preview_if_output()
            except Exception:
                pass

        try:
            self.linksChanged.emit()
        except Exception:
            pass
        self.refresh_node_widget(dst.model.name)
        return edge

    def _on_edge_removed(self, edge: 'EdgeItem'):
        dst = edge.dst; src = edge.src
        dst_kind = (dst.model.kind or "").lower()

        if dst_kind in ("append", "switch"):
            try:
                if src.model.name in dst.model.switch_inputs:
                    dst.model.switch_inputs.remove(src.model.name)
            except Exception:
                pass
            if dst_kind == "switch":
                dst.model.switch_index = max(
                    0,
                    min(dst.model.switch_index, max(0, len(dst.model.switch_inputs) - 1))
                )
            self.refresh_node_widget(dst.model.name)

            # Also refresh the Append card UI if there’s a live card
            try:
                win = self.views()[0].window() if self.views() else None
                if win:
                    card = getattr(win, "_card_by_node", {}).get(dst.model.name)
                    if card and hasattr(card, "refresh_append_ui_from_model"):
                        card.refresh_append_ui_from_model()
            except Exception:
                pass

        if self._current_output_name:
            try:
                self.recompute_active_path(self._current_output_name)
                if self.views() and hasattr(self.views()[0].window(), "populate_branch_info"):
                    win = self.views()[0].window()
                    seq = self.ordered_upstream_items(self._current_output_name)
                    win.populate_branch_info([it.model for it in seq])
                    # refresh merged preview on the Output card too
                    card = getattr(win, "_card_by_node", {}).get(self._current_output_name)
                    if card and hasattr(card, "apply_append_preview_if_output"):
                        card.apply_append_preview_if_output()
            except Exception:
                pass

        # ← emit here as well
        try:
            self.linksChanged.emit()
        except Exception:
            pass
        self.refresh_node_widget(dst.model.name)
        self.refresh_node_widget(dst.model.name)


    def delete_node_by_name(self, name: str):
        item = self._node_items.get(name)
        if not item: return
        for e in list(self._edges):
            if e.src is item or e.dst is item:
                try: self.removeItem(e)
                except Exception: pass
                try:
                    if e in self._edges:
                        self._edges.remove(e)
                except Exception:
                    pass
                try: self._on_edge_removed(e)
                except Exception: pass
        try: self.removeItem(item)
        except Exception: pass
        self._node_items.pop(name, None)
        self._nodes_by_name.pop(name, None)

        for cg in list(self._comment_groups):
            cg.remove_member(name)
            if not cg.members():
                self.delete_comment_group(cg)

        if self._current_output_name == name:
            self._current_output_name = None
            self._clear_path_highlight()
        else:
            if self._current_output_name:
                self.recompute_active_path(self._current_output_name)
        self.nodeDeleted.emit(name)
        self._reframe_to_nodes(margin=8000.0)

    def delete_selected_nodes(self):
        for it in list(self.selectedItems()):
            if isinstance(it, NodeItem):
                self.delete_node_by_name(it.model.name)
            elif isinstance(it, CommentGroup):
                self.delete_comment_group(it)

    # --- copy/paste helpers ---------------------------------------------------
    def _node_payload_for_clipboard(self, node: GraphNode) -> dict:
        try:
            x, y = node.pos_xy
        except Exception:
            try:
                x = float(node.pos.x())
                y = float(node.pos.y())
            except Exception:
                x = y = 0.0
        snap = {
            "name": node.name,
            "kind": node.kind,
            "info": node.info or "",
            "code": node.code if node.code is not None else None,
            "params": [{"name": p.get("name",""), "value": p.get("value","")} for p in (node.params or [])],
            "switch_inputs": list(node.switch_inputs or []),
            "switch_index": int(getattr(node, "switch_index", 0) or 0),
            "pos": [float(x), float(y)],
        }
        if (node.kind or "").lower() == "note":
            feat = getattr(node, "_featured_params", None)
            if isinstance(feat, set):
                snap["featured_params"] = sorted(feat)
            fheights = getattr(node, "_featured_heights", None)
            if isinstance(fheights, dict):
                clean = {}
                for name, val in fheights.items():
                    try:
                        hv = float(val)
                    except Exception:
                        continue
                    if hv > 0:
                        clean[str(name)] = hv
                if clean:
                    snap["featured_heights"] = clean
        return snap

    def copy_selection_to_clipboard(self) -> bool:
        items = [it for it in self.selectedItems() if isinstance(it, NodeItem)]
        if not items:
            return False

        nodes_data = []
        selected_names = set()
        positions = []
        for it in items:
            node = it.model
            nodes_data.append(self._node_payload_for_clipboard(node))
            selected_names.add(node.name)
            try:
                positions.append(tuple(map(float, node.pos_xy)))
            except Exception:
                try:
                    positions.append((float(node.pos.x()), float(node.pos.y())))
                except Exception:
                    positions.append((0.0, 0.0))

        if not positions:
            return False

        centroid = [
            sum(p[0] for p in positions) / len(positions),
            sum(p[1] for p in positions) / len(positions),
        ]

        edges_data = []
        for e in self._edges:
            src_name = getattr(getattr(e, "src", None), "model", None)
            dst_name = getattr(getattr(e, "dst", None), "model", None)
            src_name = getattr(src_name, "name", None)
            dst_name = getattr(dst_name, "name", None)
            if src_name in selected_names and dst_name in selected_names:
                entry = {"src": src_name, "dst": dst_name}
                if getattr(e, "dst_port_name", None):
                    entry["dst_port"] = e.dst_port_name
                if getattr(e, "src_port_name", None):
                    entry["src_port"] = e.src_port_name
                edges_data.append(entry)

        payload = {
            "format": "EchoGraphClipboard",
            "version": 1,
            "nodes": nodes_data,
            "edges": edges_data,
            "centroid": centroid,
        }

        QtWidgets.QApplication.clipboard().setText(json.dumps(payload, ensure_ascii=False))
        return True

    def paste_from_clipboard(self) -> bool:
        payload = None
        text = QtWidgets.QApplication.clipboard().text()
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("format") == "EchoGraphClipboard":
                    payload = data
            except Exception:
                payload = None
        if not payload:
            return False

        nodes_data = payload.get("nodes") or []
        if not nodes_data:
            return False

        centroid = payload.get("centroid") or [0.0, 0.0]
        try:
            cx = float(centroid[0])
            cy = float(centroid[1])
        except Exception:
            cx = cy = 0.0

        if self.views():
            view = self.views()[0]
            anchor = view.mapToScene(view.viewport().rect().center())
        else:
            anchor = QtCore.QPointF(0.0, 0.0)

        jitter = getattr(self, "_last_paste_jitter", QtCore.QPointF(0.0, 0.0))
        anchor = anchor + jitter
        jitter += QtCore.QPointF(24.0, 24.0)
        if abs(jitter.x()) > 240 or abs(jitter.y()) > 240:
            jitter = QtCore.QPointF(0.0, 0.0)
        self._last_paste_jitter = jitter

        name_map = {}
        new_items = []

        # Clear current selection so pasted nodes become the new selection
        for it in self.selectedItems():
            it.setSelected(False)

        for entry in nodes_data:
            orig_name = entry.get("name", "node")
            kind = entry.get("kind", "node")
            new_name = self._unique_node_name(orig_name, kind)

            node = GraphNode(
                new_name,
                kind=kind,
                info=entry.get("info", ""),
                code=entry.get("code"),
                params=[{"name": p.get("name",""), "value": p.get("value","")} for p in (entry.get("params") or [])],
                switch_inputs=list(entry.get("switch_inputs") or []),
                switch_index=int(entry.get("switch_index", 0) or 0),
            )

            if (kind or "").lower() == "note":
                if entry.get("featured_params"):
                    try:
                        setattr(node, "_featured_params", {str(x) for x in entry["featured_params"] if x})
                    except Exception:
                        setattr(node, "_featured_params", set())
                fheights = entry.get("featured_heights")
                if isinstance(fheights, dict):
                    clean = {}
                    for name, val in fheights.items():
                        try:
                            hv = float(val)
                        except Exception:
                            continue
                        if hv > 0:
                            clean[str(name)] = hv
                    if clean:
                        try:
                            setattr(node, "_featured_heights", clean)
                        except Exception:
                            pass

            pos = entry.get("pos") or [0.0, 0.0]
            try:
                ox = float(pos[0])
                oy = float(pos[1])
            except Exception:
                ox = oy = 0.0

            new_x = anchor.x() + (ox - cx)
            new_y = anchor.y() + (oy - cy)
            node.pos_xy = (new_x, new_y)

            item = self.add_node(node, (new_x, new_y))
            if hasattr(node, "_featured_params"):
                setattr(item.model, "_featured_params", getattr(node, "_featured_params"))
            if hasattr(node, "_featured_heights"):
                setattr(item.model, "_featured_heights", getattr(node, "_featured_heights"))
            new_items.append(item)
            name_map[orig_name] = new_name

        for item in new_items:
            item.setSelected(True)

        for edge in payload.get("edges", []):
            src_old = edge.get("src")
            dst_old = edge.get("dst")
            if src_old in name_map and dst_old in name_map:
                try:
                    self._add_edge_and_update_switch(
                        name_map[src_old],
                        name_map[dst_old],
                        dst_port_name=edge.get("dst_port"),
                    )
                except Exception:
                    pass

        return True

    # --- comment groups ------------------------------------------------------
    def _move_comment_members(self, group: CommentGroup, delta: QtCore.QPointF):
        if self._moving_comment_group:
            return
        if delta.manhattanLength() <= 0.0:
            return
        self._moving_comment_group = True
        try:
            for name in group.members():
                it = self._node_items.get(name)
                if it:
                    it.setPos(it.pos() + delta)
        finally:
            self._moving_comment_group = False

    def create_comment_group_from_selection(self):
        selected = [it for it in self.selectedItems() if isinstance(it, NodeItem)]
        if not selected:
            QtWidgets.QMessageBox.information(None, APP_TITLE, "Select at least one node before wrapping it.")
            return
        rect = QtCore.QRectF(selected[0].sceneBoundingRect())
        for it in selected[1:]:
            rect = rect.united(it.sceneBoundingRect())
        padding = 40.0
        rect = rect.adjusted(-padding, -padding, padding, padding)
        title, ok = QtWidgets.QInputDialog.getText(None, "Comment Title", "Title:", text="Comment")
        if not ok:
            return
        body, ok = QtWidgets.QInputDialog.getMultiLineText(None, "Comment Body", "Body (optional):", "")
        if not ok:
            return
        group = CommentGroup(self, title, body, [it.model.name for it in selected], rect)
        self.addItem(group)
        self._comment_groups.append(group)

    def _refresh_comment_group_membership(self, group: CommentGroup):
        if group not in self._comment_groups:
            return
        group_rect = group.mapRectToScene(group._rect)
        members = []
        for name, node in self._node_items.items():
            rect = node.sceneBoundingRect()
            if group_rect.contains(rect):
                members.append(name)
        group._members = members
        if not members:
            self.delete_comment_group(group)

    def _update_comment_membership_for_node(self, node_item: NodeItem):
        if getattr(self, "_moving_comment_group", False):
            return
        name = node_item.model.name
        node_rect = node_item.sceneBoundingRect()
        changed = False
        for cg in list(self._comment_groups):
            group_rect = cg.mapRectToScene(cg._rect)
            if group_rect.contains(node_rect):
                if name not in cg.members():
                    cg._members.append(name)
                    changed = True
            else:
                if name in cg.members():
                    cg.remove_member(name)
                    changed = True
                    if not cg.members():
                        self.delete_comment_group(cg)
        if changed:
            self.update()
        group.setSelected(True)

    def delete_comment_group(self, group: CommentGroup):
        if group in self._comment_groups:
            try:
                self.removeItem(group)
            except Exception:
                pass
            self._comment_groups.remove(group)

    def comment_groups_data(self) -> List[Dict[str, Any]]:
        out = []
        for cg in self._comment_groups:
            out.append(cg.to_dict())
        return out

    def _add_comment_group_from_data(self, data: dict):
        if not isinstance(data, dict):
            return
        rect = data.get("rect") or [0.0, 0.0, 200.0, 120.0]
        try:
            rectf = QtCore.QRectF(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
        except Exception:
            rectf = QtCore.QRectF(0.0, 0.0, 200.0, 120.0)
        group = CommentGroup(
            self,
            data.get("title", "Comment"),
            data.get("body", ""),
            data.get("members", []),
            rectf,
        )
        self.addItem(group)
        self._comment_groups.append(group)

    def _refresh_all_switch_widgets(self):
        for it in self._node_items.values():
            if (it.model.kind or "").lower()=="switch":
                self._refresh_switch_widget(it)
            self.refresh_node_widget(it.model.name)

    def _refresh_switch_widget(self, switch_item: 'NodeItem'):
        switch_item._recompute_height()
        switch_item._build_widgets()
        for e in self._edges:
            if e.src is switch_item or e.dst is switch_item:
                e.updatePath()

    def _on_switch_index_changed(self, item: 'NodeItem', idx: int):
        if self._current_output_name:
            self.recompute_active_path(self._current_output_name)

    def _on_node_clicked(self, model: GraphNode):
        if (model.kind or "").lower() == "output":
            self._current_output_name = model.name
            ordered_nodes = self.recompute_active_path(model.name)
            if not ordered_nodes:
                ordered_nodes = [model]
            if callable(self.on_info):
                try: self.on_info(model)
                except Exception: pass
            if callable(self.on_branch):
                try: self.on_branch(ordered_nodes)
                except Exception: pass
        else:
            if callable(self.on_info): self.on_info(model)

    def center_on_name(self, name:str):
        name = QtCore.QUrl.fromPercentEncoding(name.encode())
        if name in self._node_items:
            item=self._node_items[name]; views=self.views()
            if views: views[0].centerOn(item)

    def _on_start_wire_drag(self, src_item: NodeItem):
        self._cancel_temp_wire()
        self._drag_src_item = src_item
        start = src_item.scenePos() + QtCore.QPointF(src_item.width, src_item._BASE_H/2)
        self._temp_wire = TempWire(start)
        self.addItem(self._temp_wire)

    def mouseMoveEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._temp_wire is not None:
            self._temp_wire.updateTo(e.scenePos()); e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._temp_wire is not None and self._drag_src_item is not None:
            target, dst_port = self._node_at_left_socket(e.scenePos())
            if target and (target is not self._drag_src_item):
                try:
                    self._add_edge_and_update_switch(
                        self._drag_src_item.model.name,
                        target.model.name,
                        dst_port_name=dst_port,
                    )
                except Exception:
                    pass
            self._cancel_temp_wire(); e.accept(); return
        super().mouseReleaseEvent(e)

    def _node_at_left_socket(self, scene_pos: QtCore.QPointF):
        items = self.items(scene_pos)
        for it in items:
            if isinstance(it, NodeItem):
                lp = it.mapFromScene(scene_pos)
                h = getattr(it, "height", getattr(it, "_BASE_H", 0))
                port_name = None
                if hasattr(it, "input_port_hit"):
                    port_name = it.input_port_hit(lp)
                if port_name:
                    return it, port_name
                if -12 <= lp.x() <= 24 and 0 <= lp.y() <= h:
                    return it, None
        return None, None

    def _cancel_temp_wire(self):
        if self._temp_wire:
            self.removeItem(self._temp_wire)
            self._temp_wire = None
        self._drag_src_item = None

    def _clear_path_highlight(self):
        for e in self._edges:
            e.setHighlighted(False)

    def _highlighted_edges(self):
        return [e for e in self._edges if getattr(e, "_highlight", False)]

    def recompute_active_path(self, output_name: str):
        """Return upstream nodes ordered so that:
        - SWITCH picks only the active branch (by slider).
        - APPEND expands inputs in the user-defined switch_inputs order.
        - Other nodes expand all inputs (left→right fallback).
        Also highlights the participating edges.
        """
        self._clear_path_highlight()
        out_item = self._node_items.get(output_name)
        if not out_item:
            return []

        # --- helpers -------------------------------------------------------------
        def _in_edges(it):
            return [e for e in self._edges if e.dst is it]

        def _ordered_in_edges(it):
            kind = (it.model.kind or "").lower()
            in_edges = _in_edges(it)

            if kind == "append":
                # map by src name; use switch_inputs order
                byname = {e.src.model.name: e for e in in_edges}
                ordered = [byname[n] for n in it.model.switch_inputs if n in byname]
                # append any stray inputs not in switch_inputs (stable)
                extras = [e for e in in_edges if e.src.model.name not in it.model.switch_inputs]
                return ordered + extras

            if kind == "switch":
                # maintain current behavior: only the active one
                if not in_edges:
                    return []
                # build ordered list following switch_inputs, then fall back
                byname = {e.src.model.name: e for e in in_edges}
                ordered = [byname[n] for n in it.model.switch_inputs if n in byname]
                for e in in_edges:
                    if e not in ordered:
                        ordered.append(e)
                idx = max(0, min(it.model.switch_index, len(ordered) - 1))
                return [ordered[idx]]

            # default: sort left→right for stability
            return sorted(in_edges, key=lambda e: (float(e.src.scenePos().x()), e.src.model.name))

        # Depth-first expansion that honors Append order
        active_edges = set()
        seen_items = set()

        def _collect(it, out_list):
            if it in seen_items:
                return
            seen_items.add(it)

            # visit inputs in node-specific order
            for e in _ordered_in_edges(it):
                active_edges.add(e)
                _collect(e.src, out_list)

            # do not push 'it' itself now; we want a pure upstream list.
            # The caller will place the output at the end.

        # build ordered upstream
        ordered_items = []
        _collect(out_item, ordered_items)

        # highlight edges used
        for e in active_edges:
            e.setHighlighted(True)

        # ensure unique, keep order (DFS already unique via seen_items gate)
        # Add the output node at the end so the Info panel shows sources → … → Output
        if out_item not in ordered_items:
            ordered_items.append(out_item)

        return [it.model for it in ordered_items]

    # --- helpers to honor Append order everywhere -------------------------------
    def _in_edges(self, it):
        return [e for e in self._edges if e.dst is it]

    def _ordered_in_edges(self, it):
        kind = (it.model.kind or "").lower()
        in_edges = self._in_edges(it)

        if kind == "append":
            byname = {e.src.model.name: e for e in in_edges}
            ordered = [byname[n] for n in it.model.switch_inputs if n in byname]
            extras = [e for e in in_edges if e.src.model.name not in it.model.switch_inputs]
            return ordered + extras

        if kind == "switch":
            if not in_edges:
                return []
            byname = {e.src.model.name: e for e in in_edges}
            ordered = [byname[n] for n in it.model.switch_inputs if n in byname]
            for e in in_edges:
                if e not in ordered:
                    ordered.append(e)
            idx = max(0, min(it.model.switch_index, len(ordered) - 1))
            return [ordered[idx]]

        # default: stable left→right
        return sorted(in_edges, key=lambda e: (float(e.src.scenePos().x()), e.src.model.name))

    def ordered_upstream_items(self, output_name: str):
        """Return NodeItems upstream of 'output' in correct visual/render order,
        honoring Append order and Switch selection. Last element is the output itself.
        """
        out_item = self._node_items.get(output_name)
        if not out_item:
            return []

        active_edges = set()
        seen = set()
        ordered = []

        def _visit(it):
            if it in seen:
                return
            seen.add(it)
            for e in self._ordered_in_edges(it):
                active_edges.add(e)
                _visit(e.src)
            # push after inputs so ordered becomes [sources..., output] when we finish at root
            ordered.append(it)

        _visit(out_item)

        # highlight currently-used edges
        for e in active_edges:
            e.setHighlighted(True)

        return ordered

    # In class GraphScene, put this after ordered_upstream_items(...)
    def merged_text_for_output(self, output_name: str):
        """
        Return [(node_name, text_value), ...] in the exact order declared on the
        closest upstream Append node (top row first). If no Append exists anywhere
        on the active path, fall back to ordered_upstream_items().
        """
        out_item = self._node_items.get(output_name)
        if not out_item:
            return []

        # --- helpers -------------------------------------------------------------
        def _collect_active_path_nodes(root_item):
            """Active path honoring Switch and Append order, as NodeItems (sources→...→root)."""
            seen = set()
            ordered = []

            def _visit(it):
                if it in seen: return
                seen.add(it)
                for e in self._ordered_in_edges(it):  # already honors Append/Switch semantics
                    _visit(e.src)
                ordered.append(it)

            _visit(root_item)
            return ordered  # [sources..., root]

        def _closest_append_in_path(items):
            """Return the Append NodeItem that is closest to the output (last in items before root)."""
            # items is sources→...→output; scan from the end backwards, stop at first Append
            for it in reversed(items[:-1]):  # skip the output itself
                if (it.model.kind or "").lower() == "append":
                    return it
            return None

        path_items = _collect_active_path_nodes(out_item)
        append_item = _closest_append_in_path(path_items)

        out = []

        if append_item:
            # Map upstream edges by src name so order is *exactly* switch_inputs
            in_edges = self._in_edges(append_item)
            byname = {e.src.model.name: e for e in in_edges}

            # STRICT: use the UI list as the single source of truth (top to bottom)
            order = list(append_item.model.switch_inputs or [])
            for nm in order:
                e = byname.get(nm)
                if not e:
                    continue  # silently skip missing/disconnected entries
                t = self.resolve_text_value(e.src)
                if t:
                    out.append((e.src.model.name, t))

            # If there are stray edges not in switch_inputs (rare), append them **after** in stable order
            for e in in_edges:
                if e.src.model.name not in order:
                    t = self.resolve_text_value(e.src)
                    if t:
                        out.append((e.src.model.name, t))
            return out

        # Fallback: no Append found on path → use stable upstream order
        for it in path_items:
            t = self.resolve_text_value(it)
            if t:
                out.append((it.model.name, t))
        return out

# App-level Ctrl+B catcher (focus-only)
class _CtrlBEventFilter(QtCore.QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win  # EchoGraphWindow

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress):
            if isinstance(ev, QtGui.QKeyEvent) and ev.key() == QtCore.Qt.Key_B and (ev.modifiers() & QtCore.Qt.ControlModifier):
                fw = QtWidgets.QApplication.focusWidget()
                w = fw
                target = None
                for _ in range(6):
                    if w in self.win._bigedit_registry:
                        target = w
                        break
                    w = w.parent() if isinstance(w, QtWidgets.QWidget) else None
                    if w is None:
                        break
                if target:
                    node_item, param_name = self.win._bigedit_registry[target]
                    node_item._open_big_param_editor(f"Edit: {param_name}", target.text(), target)
                    ev.accept()
                    return True
        return False

# main window
class EchoGraphWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

        bootstrap_plugins()  # ← register core + optional plugin specs before scene builds

        self.setObjectName("EchoGraphWindow")
        self.setWindowTitle(APP_TITLE)

        try:
            self.setWindowIcon(APP_ICON)
        except Exception:
            pass
        self.resize(1100, 720)

        self._current_path = None
        self._card_by_node = {}
        self._bigedit_registry = {}
        self._recent_files = _load_recent_graphs()

        central = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        topbar = self._build_topbar()
        v.addWidget(topbar, 0)

        # Scene/View
        self.scene = GraphScene(on_info=self.add_info_card, on_branch=self.populate_branch_info)
        try:
            self.scene.nodeDeleted.connect(self._on_node_deleted)
        except Exception:
            pass

        # keep Info cards in sync with param edits
        try:
            self.scene.paramChanged.connect(self._on_params_changed)
        except Exception:
            pass

        set_global_llm_scale(0.5, self.scene)  # ← apply global LLM scale here
        
        self.view = GraphView(self.scene)

        self.view.setMinimumSize(400, 300)
        v.addWidget(self.view, 1)

        self.setCentralWidget(central)

        self._init_info_dock()

        # App-level Ctrl+B filter (focus-only)
        self._ctrlb_filter = _CtrlBEventFilter(self)
        QtWidgets.QApplication.instance().installEventFilter(self._ctrlb_filter)

        # Global shortcuts
        try:
            self._shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
            self._shortcut_save.setContext(QtCore.Qt.ApplicationShortcut)
            self._shortcut_save.activated.connect(self._save_graph)
        except Exception:
            self._shortcut_save = None

        if HOST == "standalone":
            if not _SKIP_RECENT_DIALOG:
                QtCore.QTimer.singleShot(0, self._maybe_show_recent_dialog)
            QtCore.QTimer.singleShot(0, self.showMaximized)

    def _register_bigedit_target(self, lineedit: QtWidgets.QLineEdit, node_item: 'NodeItem', param_name: str):
        self._bigedit_registry[lineedit] = (node_item, param_name)

    def _remember_recent(self, path: str):
        path = (path or "").strip()
        if not path:
            return
        try:
            path = os.path.abspath(path)
        except Exception:
            pass
        entries = []
        seen = set()
        for cand in [path] + list(getattr(self, "_recent_files", [])):
            cand = (cand or "").strip()
            if not cand:
                continue
            try:
                norm = os.path.abspath(cand)
            except Exception:
                norm = cand
            if norm in seen:
                continue
            entries.append(norm)
            seen.add(norm)
            if len(entries) >= _RECENT_GRAPHS_LIMIT:
                break
        self._recent_files = entries
        _save_recent_graphs(entries)

    def _forget_recent(self, path: str):
        if not path:
            return
        try:
            target = os.path.abspath(path)
        except Exception:
            target = path
        new_list = []
        changed = False
        for cand in getattr(self, "_recent_files", []):
            try:
                norm = os.path.abspath(cand)
            except Exception:
                norm = cand
            if norm == target:
                changed = True
                continue
            new_list.append(cand)
        if changed:
            self._recent_files = new_list
            _save_recent_graphs(new_list)

    def _maybe_show_recent_dialog(self):
        recents = [p for p in getattr(self, "_recent_files", []) if p]
        if not recents:
            return
        dlg = RecentGraphsDialog(self, recents)
        if _qexec(dlg) == QtWidgets.QDialog.Accepted:
            action = getattr(dlg, "result_action", lambda: "open")()
            if action == "new":
                return
            path = dlg.selected_path()
            if path:
                if not self._load_graph_file(path):
                    self._forget_recent(path)

    def _launch_new_instance(self):
        script = Path(__file__).resolve().parent / "echograph_app.py"
        python = sys.executable or "python"
        if not script.exists():
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Unable to find launcher:\n{script}")
            return
        try:
            subprocess.Popen([python, str(script), "--skip-recent"])
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to launch:\n{exc}")

    def _build_topbar(self):
        bar = QtWidgets.QFrame(); bar.setObjectName("TopBar")
        bar.setStyleSheet("#TopBar{background:#20242b;border-bottom:1px solid #333;} PushButton{padding:6px 12px;font-weight:600;}")
        bar.setFixedHeight(36)
        h = QtWidgets.QHBoxLayout(bar); h.setContentsMargins(8,4,8,4); h.setSpacing(8)

        btn_new = QtWidgets.QPushButton("New Workflow", bar)
        btn_new.setToolTip("Launch a fresh EchoGraph window")
        btn_new.clicked.connect(self._launch_new_instance)
        h.addWidget(btn_new, 0)

        btn_create = QtWidgets.QPushButton("Create Node", bar)
        btn_create.setToolTip("Create a new node with type & params")
        btn_create.clicked.connect(self._create_node_interactive)
        h.addWidget(btn_create, 0)

        btn_open = QtWidgets.QPushButton("Open", bar)
        btn_open.setToolTip("Load a graph from a .json file")
        btn_open.clicked.connect(self._open_graph)
        h.addWidget(btn_open, 0)

        btn_save = QtWidgets.QPushButton("Save", bar)
        btn_save.setToolTip("Save to the last opened/exported .json (Save)")
        btn_save.clicked.connect(self._save_graph)
        h.addWidget(btn_save, 0)

        btn_export = QtWidgets.QPushButton("Export", bar)
        btn_export.setToolTip("Export current graph to a new .json (Save As)")
        btn_export.clicked.connect(self._export_graph)
        h.addWidget(btn_export, 0)

        btn_logs = QtWidgets.QPushButton("Logs")
        btn_logs.setToolTip("Open EchoGraph log folder")
        btn_logs.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(__import__("os").path.join(__import__("tempfile").gettempdir(), "EchoGraph"))
        ))
        h.addWidget(btn_logs, 0)
                
        # --- LLM Scale slider ---
        # LLM Scale (LEFT side)
        # h.addStretch(1)  # ← move content that follows to the right

        self._llm_value_lbl = QtWidgets.QLabel(f"{int(round(LLM_SCALE*100))}%")
        self._llm_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._llm_slider.setMinimum(25); self._llm_slider.setMaximum(175)
        self._llm_slider.setSingleStep(1); self._llm_slider.setPageStep(5)
        self._llm_slider.setFixedWidth(140)
        self._llm_slider.setValue(int(round(LLM_SCALE * 100)))
        self._llm_slider.valueChanged.connect(
            lambda v: (set_global_llm_scale(max(0.25, min(1.75, v/100.0)), self.scene),
                    self._llm_value_lbl.setText(f"{v}%"))
        )
        h.addWidget(QtWidgets.QLabel("LLM Scale"))
        h.addWidget(self._llm_slider)
        h.addWidget(self._llm_value_lbl)

        h.addStretch(1)   # ← stretch AFTER the slider block to keep it left
        return bar

    def _create_node_interactive(self):
        dlg = CreateNodeDialog(self, existing_names=list(self.scene._nodes_by_name.keys()))
        if _qexec(dlg) != QtWidgets.QDialog.Accepted:
            return

        data = dlg.result_payload()

        # Finalize a unique name (empty -> kind; conflicts -> numbered)
        final_name = self.scene._unique_node_name(data.get("name", ""), data.get("kind", "node"))
        data["name"] = final_name

        # Build the node
        node = GraphNode(
            data["name"],
            kind=data["kind"],
            info="",
            params=data["params"],
            code=data.get("code"),
        )

        # Librarian nicety (same as right-click path)
        if (data["kind"] or "").lower() == "librarian":
            node.info = "Librarian node created"
            ensure_params = [
                ("query", ""),
                ("docs_dir", ""),
                ("mode", "tree_summarize"),
                ("top_k", "5"),
                ("action", ""),
            ]
            names = {(p.get("name") or "").strip().lower() for p in (node.params or [])}
            for pname, default in ensure_params:
                if pname not in names:
                    node.params = list(node.params or [])
                    node.params.append({"name": pname, "value": default})
                    names.add(pname)

        # Drop it at the center of the view
        v = self.view
        try:
            center_vp = v.viewport().rect().center()
            scene_pos = v.mapToScene(center_vp)
        except Exception:
            scene_pos = QtCore.QPointF(0, 0)

        item = self.scene.add_node(node, scene_pos)
        item.setPos(scene_pos - QtCore.QPointF(item.width / 2.0, item.height / 2.0))

        # Pop an Info card immediately (nice feedback)
        if callable(self.add_info_card):
            self.add_info_card(node)

    def _load_graph_file(self, path: str) -> bool:
        path = (path or "").strip()
        if not path:
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to open:\n{e}")
            return False
        try:
            self.scene.from_dict(data)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to open:\n{e}")
            return False

        try:
            s = float(data.get("llm_scale", LLM_SCALE))
        except Exception as e:
            s = LLM_SCALE

        set_global_llm_scale(s, self.scene)
        if hasattr(self, "_llm_slider"):
            self._llm_slider.blockSignals(True)
            self._llm_slider.setValue(int(round(s * 100)))
            if hasattr(self, "_llm_value_lbl"):
                self._llm_value_lbl.setText(f"{int(round(s*100))}%")
            self._llm_slider.blockSignals(False)

        self._current_path = path
        self._remember_recent(path)
        if self.scene._node_items:
            first = next(iter(self.scene._node_items.values()))
            self.view.centerOn(first)
        return True

    def _open_graph(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Graph (.json)", "", "JSON Files (*.json)")
        if not path:
            return
        self._load_graph_file(path)

    def _save_graph(self):
        if not self._current_path:
            return self._export_graph()
        try:
            data = self.scene.to_dict()
            with open(self._current_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._remember_recent(self._current_path)
            try:
                QtWidgets.QToolTip.showText(
                    QtGui.QCursor.pos(),
                    f"Saved:\n{self._current_path}",
                    self, self.rect(), 1500
                )
            except Exception:
                pass
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to save:\n{e}")


    def _export_graph(self):
        suggested = self._current_path if self._current_path else "graph.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Graph (.json)", suggested, "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            data = self.scene.to_dict()

            # Optional: Append-aware preview block
            try:
                out_name = getattr(self.scene, "_current_output_name", "") or ""
                if out_name and hasattr(self.scene, "merged_text_for_output"):
                    pairs = self.scene.merged_text_for_output(out_name)  # [(node_name, text)]
                    data.setdefault("preview", {})
                    data["preview"]["output"] = out_name
                    data["preview"]["ordered_pairs"] = [{"node": n, "text": t} for (n, t) in pairs]
                    data["preview"]["merged_text"] = "\n\n".join(t for _, t in pairs)
            except Exception:
                pass  # don’t block export if preview assembly hiccups

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            self._current_path = path
            self._remember_recent(path)
            try:
                QtWidgets.QToolTip.showText(
                    QtGui.QCursor.pos(),
                    f"Exported:\n{path}",
                    self, self.rect(), 1500
                )
            except Exception:
                pass

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to export:\n{e}")

    def _init_info_dock(self):
        self.infoDock = QtWidgets.QDockWidget("Info", self)
        self.infoDock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.infoDock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable)
        self.infoDock.setFloating(False)
        self.infoDock.setMinimumWidth(10)
        self.infoDock.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)

        self._cardsContainer = QtWidgets.QWidget()
        self._cardsLayout = QtWidgets.QVBoxLayout(self._cardsContainer)
        self._cardsLayout.setContentsMargins(8,8,8,8)
        self._cardsLayout.setSpacing(8)
        self._cardsLayout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(self._cardsContainer)

        self.infoDock.setWidget(scroll)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.infoDock)

        self.infoDock.setStyleSheet(
            "QDockWidget{background:#1a1f24;color:#e6edf3;}"
            "QDockWidget::title{background:#20242b;color:#e6edf3;padding:4px 8px;}"
        )
        scroll.setStyleSheet(
            "QScrollArea{background:#1a1f24;border:none;}"
            "QScrollArea>Viewport{background:#1a1f24;}"
        )
        self._cardsContainer.setStyleSheet("QWidget{background:#1a1f24;color:#e6edf3;}")

        try:
            self.resizeDocks([self.infoDock], [380], QtCore.Qt.Horizontal)
        except Exception:
            pass

    def add_info_card(self, node: GraphNode):
        existing = self._card_by_node.get(node.name)
        if existing and isinstance(existing, QtWidgets.QWidget):
            idx = self._cardsLayout.indexOf(existing)
            if idx != -1:
                self._cardsLayout.removeWidget(existing)
                self._cardsLayout.insertWidget(0, existing)
            existing.show()
            return
        card = InfoCard(node)
        card.requestJump.connect(self.scene.center_on_name)
        card.closedForNode.connect(self._on_card_closed)
        card.attach_scene(self.scene)
        
        # Fill Append-ordered preview if this is an Output card
        try:
            card.apply_append_preview_if_output()
        except Exception:
            pass

        self._cardsLayout.insertWidget(0, card)
        self._card_by_node[node.name] = card
        self._trim_cards()

    def populate_branch_info(self, ordered_nodes):
        # ignore incoming ordered_nodes; rebuild with Append-aware order
        if not getattr(self, 'scene', None) or not getattr(self.scene, '_current_output_name', None):
            return

        try:
            self.infoDock.setVisible(True); self.infoDock.raise_()
        except Exception:
            pass

        # clear all existing cards (keep trailing stretch)
        for i in reversed(range(self._cardsLayout.count()-1)):
            w = self._cardsLayout.itemAt(i).widget()
            if w: w.deleteLater()
        self._card_by_node.clear()

        seq = self.scene.ordered_upstream_items(self.scene._current_output_name)  # ← Append-aware
        if not seq:
            return
        total = len(seq)
        for idx, item in enumerate(seq, start=1):
            node = item.model
            card = InfoCard(node, order_index=idx, order_total=total)
            card.requestJump.connect(self.scene.center_on_name)
            card.closedForNode.connect(self._on_card_closed)
            card.attach_scene(self.scene)  # sets _graph_scene and connects linksChanged

            try:
                card.apply_append_preview_if_output()
            except Exception:
                pass

            self._cardsLayout.insertWidget(self._cardsLayout.count()-1, card)

    def _on_params_changed(self, node_name: str, params: list):
        # 1) keep the edited node's InfoCard table in sync
        card = self._card_by_node.get(node_name)
        if card and hasattr(card, "refresh_params_from_model"):
            try:
                card.refresh_params_from_model()
            except Exception:
                pass

        # 2) If an Output is active, rebuild the branch card stack (Append order aware)
        try:
            sc = getattr(self, "scene", None)
            if sc and getattr(sc, "_current_output_name", None):
                # Recompute path + repopulate cards so top→bottom order + values are current
                sc.recompute_active_path(sc._current_output_name)
                seq = sc.ordered_upstream_items(sc._current_output_name)
                self.populate_branch_info([it.model for it in seq])  # ignores arg and rebuilds correctly

                # 3) Also refresh the merged preview text on the Output card itself
                out_card = self._card_by_node.get(sc._current_output_name)
                if out_card and hasattr(out_card, "apply_append_preview_if_output"):
                    out_card.apply_append_preview_if_output()
        except Exception:
            pass

    def _on_node_deleted(self, name: str):
        w = self._card_by_node.pop(name, None)
        if w:
            try:
                if hasattr(w, "_poll_timer") and w._poll_timer is not None:
                    w._poll_timer.stop()
                    w._poll_timer.deleteLater()
            except Exception:
                pass
            try: w.deleteLater()
            except Exception: pass
        for i in reversed(range(self._cardsLayout.count()-1)):
            w = self._cardsLayout.itemAt(i).widget()
            if hasattr(w, "_node_name") and getattr(w, "_node_name", None) == name:
                try:
                    if hasattr(w, "_poll_timer") and w._poll_timer is not None:
                        w._poll_timer.stop()
                        w._poll_timer.deleteLater()
                except Exception:
                    pass
                try: w.deleteLater()
                except Exception: pass

    def _on_card_closed(self, node_name: str):
        self._card_by_node.pop(node_name, None)

    def _trim_cards(self):
        MAX_CARDS = 6
        widgets = []
        for i in range(self._cardsLayout.count()-1):
            w = self._cardsLayout.itemAt(i).widget()
            if isinstance(w, QtWidgets.QWidget): widgets.append(w)
        for excess in widgets[MAX_CARDS:][::-1]:
            for k, v in list(self._card_by_node.items()):
                if v is excess:
                    self._card_by_node.pop(k, None)
                    break
            excess.deleteLater()

# launch (single call)
_WINDOW = None
def _launch():
    global _WINDOW
    if HOST == "maya" and maya_cmds and omui:
        try:
            if maya_cmds.workspaceControl(WS_CTRL, q=True, exists=True):
                maya_cmds.deleteUI(WS_CTRL)
        except Exception:
            pass
        ctrl = maya_cmds.workspaceControl(WS_CTRL, label=APP_TITLE, retain=False, iw=1100, ih=700)
        ptr = omui.MQtUtil.findControl(ctrl)
        host_widget = wrapInstance(int(ptr), QtWidgets.QWidget)

        _WINDOW = EchoGraphWindow(host_widget)
        _WINDOW.setParent(host_widget)
        _WINDOW.setWindowFlags(QtCore.Qt.Widget)

        _WINDOW.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        _WINDOW.view.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        lay = host_widget.layout() or QtWidgets.QVBoxLayout(host_widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_WINDOW)

        try:
            maya_cmds.workspaceControl(WS_CTRL, e=True, vis=True)
            maya_cmds.workspaceControl(WS_CTRL, e=True, r=True)
        except Exception:
            pass

        _WINDOW.show()
        QtWidgets.QApplication.processEvents()
        return

    parent = _main_window() if HOST == "houdini" else None
    _WINDOW = EchoGraphWindow(parent)

    if HOST == "standalone":
        try:
            _WINDOW.setWindowFlag(QtCore.Qt.Window, True)
        except Exception:
            _WINDOW.setWindowFlags(QtCore.Qt.Window)

    QtWidgets.QApplication.instance().setQuitOnLastWindowClosed(True)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    QtWidgets.QApplication.processEvents()

# run immediately
_launch()
