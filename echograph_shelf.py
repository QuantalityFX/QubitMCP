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
from echograph.ui.gl_view import GraphGLView
from echograph.ui.node_item import NodeItem
from echograph import persistence
from echograph.model import GraphNode
from echograph.ui import hotkeys
from echograph.ui import actions
from echograph.ui import hotkeys_config


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
    CommentGroupDialog,
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

DEFAULT_COMMENT_COLOR = "#1f2933"
COMMENT_MEMBER_PAD = 16.0

def _normalize_comment_color(value: str = None) -> str:
    qc = QtGui.QColor(value if value is not None else DEFAULT_COMMENT_COLOR)
    if not qc.isValid():
        qc = QtGui.QColor(DEFAULT_COMMENT_COLOR)
    try:
        return qc.name(QtGui.QColor.HexRgb)
    except Exception:
        return qc.name()

class CommentGroup(QtWidgets.QGraphicsObject):
    Type = QtWidgets.QGraphicsItem.UserType + 5201

    def __init__(
        self,
        scene: 'GraphScene',
        title: str,
        body: str,
        members: List[str],
        rect: QtCore.QRectF,
        color: str = DEFAULT_COMMENT_COLOR,
    ):
        super().__init__()
        self._scene_ref = scene
        self._title = (title or "").strip() or "Comment"
        self._body = (body or "").strip()
        self._members = [str(m) for m in (members or []) if m]
        self._color_hex = _normalize_comment_color(color)
        try:
            self.setData(0xC0DE, "comment_group")
        except Exception:
            pass

        rect = QtCore.QRectF(rect)
        if rect.width() < 160:
            rect.setWidth(160)
        if rect.height() < 100:
            rect.setHeight(100)
        self._rect = QtCore.QRectF(0.0, 0.0, rect.width(), rect.height())
        self.setPos(rect.topLeft())

        self.setZValue(0.2)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._resize_mode = None
        self._resize_start_pos = QtCore.QPointF()
        self._initial_rect = QtCore.QRectF(self._rect)
        self._initial_pos = QtCore.QPointF(self.pos())
        self._suspend_member_move = False
        self._dragging_header = False
        self._drag_start_scene = QtCore.QPointF()
        self._drag_start_pos = QtCore.QPointF()
        self._drag_peer_starts: List[tuple['CommentGroup', QtCore.QPointF]] = []
        
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
            "color": self._color_hex,
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
        base_color = QtGui.QColor(self._color_hex)
        if not base_color.isValid():
            base_color = QtGui.QColor(DEFAULT_COMMENT_COLOR)
        bg = QtGui.QColor(base_color)
        bg.setAlpha(70)
        frame = QtGui.QColor(base_color.lighter(150))
        frame.setAlpha(80)
        if self.isSelected():
            frame = QtGui.QColor(base_color.lighter(180))
            frame.setAlpha(200)
        pen = QtGui.QPen(frame, 2.1 if not self.isSelected() else 2.8, QtCore.Qt.SolidLine)
        pen.setCosmetic(True)
        p.setBrush(QtGui.QBrush(bg))
        p.setPen(pen)
        p.drawRoundedRect(rect, 14, 14)

        # Header bar (drag handle) with rounded top corners and square bottom edge
        header_h = 32.0
        hx, hy, hw = rect.x(), rect.y(), rect.width()
        radius = 14.0
        header_color = QtGui.QColor(base_color.lighter(140 if not self.isSelected() else 180))
        header_color.setAlpha(120)
        header_path = QtGui.QPainterPath()
        header_path.moveTo(hx, hy + header_h)
        header_path.lineTo(hx, hy + radius)
        header_path.quadTo(hx, hy, hx + radius, hy)
        header_path.lineTo(hx + hw - radius, hy)
        header_path.quadTo(hx + hw, hy, hx + hw, hy + radius)
        header_path.lineTo(hx + hw, hy + header_h)
        header_path.closeSubpath()
        p.fillPath(header_path, header_color)

        title_rect = QtCore.QRectF(rect.x() + padding, rect.y() + 6.0, rect.width() - 2 * padding, header_h - 10.0)
        title_color = QtGui.QColor("#e2e8f0")
        p.setPen(QtGui.QPen(title_color))
        font = p.font()
        font.setBold(True)
        base_pt = font.pointSizeF()
        if base_pt > 0:
            font.setPointSizeF(base_pt * 1.15)
        else:
            px = font.pixelSize()
            if px > 0:
                font.setPixelSize(px + 2)
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
            if self._header_rect().contains(e.pos()):
                self._dragging_header = True
                self._drag_start_scene = QtCore.QPointF(e.scenePos())
                self._drag_start_pos = QtCore.QPointF(self.pos())
                self._drag_peer_starts = []
                sc = self.scene()
                if sc:
                    try:
                        sc._group_drag_active = False
                    except Exception:
                        pass
                    try:
                        sc._comment_drag_active = True
                        sc._comment_drag_seen_tick = None
                        sc._comment_drag_moved_nodes = set()
                        sc._comment_drag_moved_pins = set()
                        sc._comment_drag_moved_groups = set()
                        all_pins = []
                        for edge in list(getattr(sc, "_edges", [])):
                            for pin in list(getattr(edge, "_pins", [])):
                                if pin is None:
                                    continue
                                all_pins.append(pin)
                        pin_map = {}
                        for cg in list(getattr(sc, "_comment_groups", [])):
                            try:
                                rect = cg.mapRectToScene(cg._rect)
                            except Exception:
                                continue
                            pin_ids = set()
                            for pin in all_pins:
                                try:
                                    if rect.contains(pin.scenePos()):
                                        pin_ids.add(id(pin))
                                except Exception:
                                    pass
                            pin_map[cg] = pin_ids
                        sc._comment_drag_pin_ids_by_group = pin_map
                    except Exception:
                        pass
                if sc:
                    peers = [
                        it for it in sc.selectedItems()
                        if isinstance(it, CommentGroup)
                    ]
                    if not peers:
                        peers = [self]
                    elif self in peers:
                        peers = [self] + [it for it in peers if it is not self]
                else:
                    peers = [self]
                for peer in peers:
                    self._drag_peer_starts.append((peer, QtCore.QPointF(peer.pos())))
                if not (e.modifiers() & QtCore.Qt.ControlModifier):
                    self._select_members()
                e.accept()
                return
        # Let clicks in the body fall through so rubber-band selection can start inside the wrapper.
        e.ignore()

    def mouseMoveEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._resize_mode:
            self._apply_resize(e.pos())
            e.accept()
            return
        if self._dragging_header:
            sc = self.scene()
            if sc:
                try:
                    sc._comment_drag_tick = int(getattr(sc, "_comment_drag_tick", 0)) + 1
                except Exception:
                    sc._comment_drag_tick = 1
            delta = QtCore.QPointF(e.scenePos()) - self._drag_start_scene
            for peer, start_pos in self._drag_peer_starts or [(self, self._drag_start_pos)]:
                peer.setPos(start_pos + delta)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._resize_mode and e.button() == QtCore.Qt.LeftButton:
            self._resize_mode = None
            e.accept()
            return
        if self._dragging_header and e.button() == QtCore.Qt.LeftButton:
            self._dragging_header = False
            self._drag_peer_starts = []
            sc = self.scene()
            if sc:
                try:
                    sc._comment_drag_active = False
                    sc._comment_drag_seen_tick = None
                    sc._comment_drag_moved_nodes = set()
                    sc._comment_drag_moved_pins = set()
                    sc._comment_drag_moved_groups = set()
                    sc._comment_drag_pin_ids_by_group = {}
                except Exception:
                    pass
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            parent = None
            sc = self.scene() or getattr(self, "_scene_ref", None)
            try:
                parent = sc.views()[0] if sc and sc.views() else None
            except Exception:
                parent = None
            dlg = CommentGroupDialog(
                parent,
                title=self._title,
                body=self._body,
                color=self._color_hex,
            )
            if _qexec(dlg) == QtWidgets.QDialog.Accepted:
                payload = dlg.result_payload()
                self._title = (payload.get("title") or self._title).strip() or self._title
                self._body = (payload.get("body") or "").strip()
                self._color_hex = _normalize_comment_color(payload.get("color"))
                self.update()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def hoverMoveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent):
        mode = self._hit_test_resize(e.pos())
        cursor = self._cursor_for_mode(mode)
        if cursor:
            self.setCursor(cursor)
        elif self._header_rect().contains(e.pos()):
            self.setCursor(QtCore.Qt.OpenHandCursor)
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
        group_rect = self.mapRectToScene(self._rect)
        for edge in getattr(scene, "_edges", []):
            for pin in list(getattr(edge, "_pins", [])):
                if pin is None:
                    continue
                try:
                    if group_rect.contains(pin.scenePos()):
                        pin.setSelected(True)
                except Exception:
                    pass

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

    def _header_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._rect.x(), self._rect.y(), self._rect.width(), 32.0)

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
        self._suppress_node_model_updates = False

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

    def set_node_params(self, name: str, params: list, rebuild: bool = True, emit: bool = True):
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

        # IMPORTANT: avoid nuking QGraphicsProxyWidget during combo interaction
        if rebuild:
            self.refresh_node_widget(name)
        else:
            it = self._node_items.get(name)
            if it:
                it.update()  # repaint only, keep proxy alive

        try:
            if emit:
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
        # Keep 3D viewport scene ownership in sync with node renames
        try:
            gv = (
                getattr(self, "gl_view", None)
                or getattr(self, "_gl_view", None)
                or getattr(self, "glView", None)
                or getattr(self, "_glView", None)
            )
            if gv is not None and hasattr(gv, "rename_scene_asset_owner"):
                gv.rename_scene_asset_owner(old_name, new_name)
        except Exception:
            pass

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

        # keep scene node outliner hidden sets in sync
        try:
            for it in self._node_items.values():
                model = getattr(it, "model", None)
                if model is None:
                    continue
                if (model.kind or "").strip().lower() not in ("scene", "scene_assembly", "scene_outliner"):
                    continue
                hidden = getattr(model, "_scene_hidden", None)
                if isinstance(hidden, set) and old_name in hidden:
                    hidden.remove(old_name)
                    hidden.add(new_name)
                elif isinstance(hidden, list) and old_name in hidden:
                    hidden = [new_name if n == old_name else n for n in hidden]
                    setattr(model, "_scene_hidden", hidden)
        except Exception:
            pass

        try:
            self.linksChanged.emit()
        except Exception:
            pass

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
        try:
            if hasattr(edge, "clear_pins"):
                edge.clear_pins()
        except Exception:
            pass

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
            nsize = getattr(node, "_note_size", None)
            if isinstance(nsize, (list, tuple)) and len(nsize) >= 2:
                try:
                    nw = float(nsize[0])
                    nh = float(nsize[1])
                    snap["note_size"] = [nw, nh]
                except Exception:
                    pass
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
                nsize = entry.get("note_size")
                if isinstance(nsize, (list, tuple)) and len(nsize) >= 2:
                    try:
                        nw = float(nsize[0])
                        nh = float(nsize[1])
                        setattr(node, "_note_size", (nw, nh))
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
        if getattr(self, "_comment_drag_active", False) and not getattr(group, "_dragging_header", False):
            return
        self._moving_comment_group = True
        try:
            base_groups = [group]
            try:
                if getattr(group, "_dragging_header", False):
                    peers = [peer for peer, _start in getattr(group, "_drag_peer_starts", []) or []]
                    if peers:
                        base_groups = []
                        for peer in peers:
                            if peer not in base_groups:
                                base_groups.append(peer)
            except Exception:
                pass

            if getattr(self, "_comment_drag_active", False):
                tick = getattr(self, "_comment_drag_tick", 0)
                if getattr(self, "_comment_drag_seen_tick", None) != tick:
                    try:
                        self._comment_drag_seen_tick = tick
                        self._comment_drag_moved_nodes = set()
                        self._comment_drag_moved_pins = set()
                        self._comment_drag_moved_groups = set()
                    except Exception:
                        pass
                moved_nodes = getattr(self, "_comment_drag_moved_nodes", set())
                moved_pins = getattr(self, "_comment_drag_moved_pins", set())
                moved_groups = getattr(self, "_comment_drag_moved_groups", set())
            else:
                moved_nodes = set()
                moved_pins = set()
                moved_groups = set()

            def _move_nodes_for_group(grp):
                for name in grp.members():
                    if name in moved_nodes:
                        continue
                    it = self._node_items.get(name)
                    if it:
                        it.setPos(it.pos() + delta)
                        moved_nodes.add(name)

            def _move_pins_for_rect(rect, allow_ids):
                for edge in getattr(self, "_edges", []):
                    for pin in list(getattr(edge, "_pins", [])):
                        if pin is None:
                            continue
                        pid = id(pin)
                        if pid in moved_pins:
                            continue
                        try:
                            if allow_ids is not None:
                                if pid not in allow_ids:
                                    continue
                            else:
                                if rect is None or not rect.contains(pin.scenePos()):
                                    continue
                            pin.setPos(pin.pos() + delta)
                            moved_pins.add(pid)
                        except Exception:
                            pass

            base_rects = []
            for grp in base_groups:
                try:
                    rect = grp.mapRectToScene(grp._rect)
                except Exception:
                    rect = None
                base_rects.append((grp, rect))

            for grp, rect in base_rects:
                if grp in moved_groups:
                    continue
                moved_groups.add(grp)
                allow_ids = None
                if getattr(self, "_comment_drag_active", False):
                    try:
                        allow_ids = getattr(self, "_comment_drag_pin_ids_by_group", {}).get(grp)
                    except Exception:
                        allow_ids = None
                _move_nodes_for_group(grp)
                _move_pins_for_rect(rect, allow_ids)

            nested_groups = []
            for cg in list(getattr(self, "_comment_groups", [])):
                if cg in base_groups:
                    continue
                try:
                    child_rect = cg.mapRectToScene(cg._rect)
                except Exception:
                    continue
                for _grp, rect in base_rects:
                    if rect is not None and rect.contains(child_rect):
                        nested_groups.append((cg, child_rect))
                        break

            for cg, child_rect in nested_groups:
                if cg in moved_groups:
                    continue
                moved_groups.add(cg)
                allow_ids = None
                if getattr(self, "_comment_drag_active", False):
                    try:
                        allow_ids = getattr(self, "_comment_drag_pin_ids_by_group", {}).get(cg)
                    except Exception:
                        allow_ids = None
                try:
                    cg.setPos(cg.pos() + delta)
                except Exception:
                    pass
                _move_nodes_for_group(cg)
                _move_pins_for_rect(child_rect, allow_ids)
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
        parent = self.views()[0] if self.views() else None
        dlg = CommentGroupDialog(
            parent,
            title="Comment",
            body="",
            color=DEFAULT_COMMENT_COLOR,
        )
        if _qexec(dlg) != QtWidgets.QDialog.Accepted:
            return
        payload = dlg.result_payload()
        group = CommentGroup(
            self,
            payload.get("title", "Comment"),
            payload.get("body", ""),
            [it.model.name for it in selected],
            rect,
            _normalize_comment_color(payload.get("color")),
        )
        self.addItem(group)
        self._comment_groups.append(group)

    def _refresh_comment_group_membership(self, group: CommentGroup):
        if group not in self._comment_groups:
            return
        group_rect = group.mapRectToScene(group._rect)
        member_rect = group_rect.adjusted(
            -COMMENT_MEMBER_PAD, -COMMENT_MEMBER_PAD, COMMENT_MEMBER_PAD, COMMENT_MEMBER_PAD
        )
        members = []
        for name, node in self._node_items.items():
            rect = node.sceneBoundingRect()
            if member_rect.contains(rect.center()):
                members.append(name)
        group._members = members

    def _update_comment_membership_for_node(self, node_item: NodeItem):
        if getattr(self, "_moving_comment_group", False):
            return
        name = node_item.model.name
        node_rect = node_item.sceneBoundingRect()
        changed = False
        for cg in list(self._comment_groups):
            group_rect = cg.mapRectToScene(cg._rect)
            member_rect = group_rect.adjusted(
                -COMMENT_MEMBER_PAD, -COMMENT_MEMBER_PAD, COMMENT_MEMBER_PAD, COMMENT_MEMBER_PAD
            )
            if member_rect.contains(node_rect.center()):
                if name not in cg.members():
                    cg._members.append(name)
                    changed = True
            else:
                if name in cg.members():
                    cg.remove_member(name)
                    changed = True
        if changed:
            self.update()

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
            _normalize_comment_color(data.get("color")),
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

    def _clear_edge_click_highlight(self):
        for e in self._edges:
            try:
                e.setClickHighlighted(False)
            except Exception:
                pass

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

def _matches_hotkey(ev: QtGui.QKeyEvent, seq: str) -> bool:
    try:
        wanted = QtGui.QKeySequence(seq)
        if hasattr(ev, "keyCombination"):
            pressed = QtGui.QKeySequence(ev.keyCombination())
            return pressed.matches(wanted) == QtGui.QKeySequence.ExactMatch
        return False
    except Exception:
        return False

# App-level Big Editor catcher (focus-only)
class _BigEditEventFilter(QtCore.QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def eventFilter(self, obj, ev):
        try:
            et = ev.type()
            seq = hotkeys_config.keyseq("big_editor", "Ctrl+B")

            # Swallow override so we don't double-trigger
            if et == QtCore.QEvent.ShortcutOverride:
                if isinstance(ev, QtGui.QKeyEvent) and _matches_hotkey(ev, seq):
                    ev.accept()
                    return True
                return False

            # Perform action only on KeyPress (once)
            if et == QtCore.QEvent.KeyPress:
                if isinstance(ev, QtGui.QKeyEvent):
                    if ev.isAutoRepeat():
                        return False

                    if _matches_hotkey(ev, seq):
                        if actions.open_big_editor_from_window(self.win):
                            ev.accept()
                            return True

            return False
        except Exception:
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
        self.gl_view = GraphGLView(self.scene)
        self.view.setMinimumSize(400, 300)
        self.gl_view.setMinimumSize(400, 300)
        self._view_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._view_splitter.setObjectName("ViewSplitter")
        self._view_splitter.setHandleWidth(6)
        self._view_splitter.addWidget(self.gl_view)
        self._view_splitter.addWidget(self.view)
        self._view_splitter.setStretchFactor(0, 1)
        self._view_splitter.setStretchFactor(1, 1)
        self.gl_view.hide()
        try:
            self._apply_pan_settings_to_gl_view()
        except Exception:
            pass
        self._view_mode = "2d"
        self._frame_margin_x = 400.0
        self._frame_margin_y = 125.0
        self._split_framed_once = False
        v.addWidget(self._view_splitter, 1)

        self.setCentralWidget(central)

        self._init_info_dock()
        self._set_view_mode("2d")

        # App-level Big Editor hotkey catcher (focus-only)
        self._bigedit_filter = _BigEditEventFilter(self)
        QtWidgets.QApplication.instance().installEventFilter(self._bigedit_filter)

        # Global shortcuts
        try:
            self._shortcut_save = hotkeys.add_shortcut(
                self,
                "app_save",
                "Ctrl+S",
                self._save_graph,
                context=QtCore.Qt.ApplicationShortcut,
            )
        except Exception:
            self._shortcut_save = None

        # GraphView-only shortcuts (avoid firing while typing in param line edits)
        try:
            self._shortcut_comment_group = hotkeys.add_shortcut(
                self.view,
                "comment_group",
                "C",
                lambda: actions.create_comment_group_from_window(self),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_comment_group = None

        try:
            self._shortcut_node_menu = hotkeys.add_shortcut(
                self.view,
                "node_menu",
                "Tab",
                self._open_create_menu_from_hotkey,
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_node_menu = None

        try:
            self._shortcut_view_mode_2d = hotkeys.add_shortcut(
                self,
                "view_mode_2d",
                "1",
                lambda: self._set_view_mode_from_hotkey("2d"),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_view_mode_2d = None

        try:
            self._shortcut_view_mode_split = hotkeys.add_shortcut(
                self,
                "view_mode_split",
                "2",
                lambda: self._set_view_mode_from_hotkey("split"),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_view_mode_split = None

        try:
            self._shortcut_view_mode_3d = hotkeys.add_shortcut(
                self,
                "view_mode_3d",
                "3",
                lambda: self._set_view_mode_from_hotkey("3d"),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_view_mode_3d = None

        try:
            self._shortcut_gl_frame = hotkeys.add_shortcut(
                self,
                "gl_frame",
                "F",
                self._frame_from_hotkey,
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_gl_frame = None

        try:
            self._shortcut_node_delete = hotkeys.add_shortcut(
                self.view,
                "node_delete",
                "Del",
                lambda: actions.delete_selected_nodes_from_window(self),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_node_delete = None

        try:
            self._shortcut_node_copy = hotkeys.add_shortcut(
                self.view,
                "node_copy",
                "Ctrl+C",
                lambda: actions.copy_selected_nodes_from_window(self),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_node_copy = None

        try:
            self._shortcut_node_paste = hotkeys.add_shortcut(
                self.view,
                "node_paste",
                "Ctrl+V",
                lambda: actions.paste_nodes_from_window(self),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
        except Exception:
            self._shortcut_node_paste = None

        if HOST == "standalone":
            if not _SKIP_RECENT_DIALOG:
                QtCore.QTimer.singleShot(0, self._maybe_show_recent_dialog)
            QtCore.QTimer.singleShot(0, self.showMaximized)

    def _register_bigedit_target(self, edit: QtWidgets.QWidget, node_item, param_name: str):
        if not hasattr(self, "_bigedit_registry"):
            self._bigedit_registry = {}
        self._bigedit_registry[edit] = (node_item, param_name)

        # set a safe default target immediately
        self._bigedit_last = edit

        # Track last focused edit reliably (GraphicsView focus is usually GraphView)
        try:
            orig = getattr(edit, "focusInEvent", None)
            if orig and not getattr(edit, "_bigedit_focus_hooked", False):
                def _focus_in_hook(ev, _orig=orig, _w=edit, _self=self):
                    _self._bigedit_last = _w
                    return _orig(ev)
                edit.focusInEvent = _focus_in_hook
                edit._bigedit_focus_hooked = True
        except Exception:
            pass


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

    def _frame_all_nodes(self, margin: float | None = None, v_margin: float | None = None):
        sc = getattr(self, "scene", None)
        v = getattr(self, "view", None)
        if not sc or not v:
            return
        if margin is None:
            margin = float(getattr(self, "_frame_margin_x", 400.0))
        if v_margin is None:
            v_margin = float(getattr(self, "_frame_margin_y", max(96.0, margin * 0.48)))
        try:
            bbox = sc._nodes_bbox()
        except Exception:
            bbox = None
        if bbox is None:
            return
        rect = bbox.adjusted(-margin, -v_margin, margin, v_margin)
        try:
            v.fitInView(rect, QtCore.Qt.KeepAspectRatio)
        except Exception:
            pass

    def _cycle_view_mode(self) -> None:
        mode = getattr(self, "_view_mode", "2d")
        if mode == "2d":
            next_mode = "3d"
        elif mode == "3d":
            next_mode = "split"
        else:
            next_mode = "2d"
        self._set_view_mode(next_mode)

    def _set_view_mode(self, mode: str) -> None:
        mode = (mode or "2d").lower()
        if mode not in {"2d", "3d", "split"}:
            mode = "2d"
        self._view_mode = mode
        view = getattr(self, "view", None)
        gl_view = getattr(self, "gl_view", None)
        splitter = getattr(self, "_view_splitter", None)
        if gl_view and mode in {"3d", "split"}:
            try:
                sc = getattr(self, "scene", None)
                gl_view.set_scene(sc)

                # Only refresh when the scene object changes (prevents slow toggle stalls)
                if getattr(gl_view, "_last_refresh_scene_obj", None) is not sc:
                    gl_view._last_refresh_scene_obj = sc
                    gl_view.refresh_from_scene()
            except Exception:
                pass
        if view and hasattr(view, "set_3d_mode"):
            try:
                view.set_3d_mode(False)
            except Exception:
                pass
        if mode == "2d":
            if gl_view:
                try:
                    gl_view.hide()
                except Exception:
                    pass
            if view:
                try:
                    view.show()
                except Exception:
                    pass
            if splitter:
                try:
                    splitter.setSizes([0, 1])
                except Exception:
                    pass
        elif mode == "3d":
            if view:
                try:
                    view.hide()
                except Exception:
                    pass
            if gl_view:
                try:
                    gl_view.show()
                except Exception:
                    pass
            if splitter:
                try:
                    splitter.setSizes([1, 0])
                except Exception:
                    pass
        else:
            if view:
                try:
                    view.show()
                except Exception:
                    pass
            if gl_view:
                try:
                    gl_view.show()
                except Exception:
                    pass
            if splitter:
                try:
                    total = max(2, splitter.width())
                    left = total // 2
                    splitter.setSizes([left, total - left])
                except Exception:
                    pass
            if not getattr(self, "_split_framed_once", False):
                self._split_framed_once = True
                try:
                    QtCore.QTimer.singleShot(0, self._frame_all_nodes)
                except Exception:
                    self._frame_all_nodes()
        self._update_view_mode_button()

    def _update_view_mode_button(self) -> None:
        btn = getattr(self, "_btn_3d", None)
        if btn is None:
            return
        mode = getattr(self, "_view_mode", "2d")
        if mode == "2d":
            btn.setText("2D View")
            btn.setToolTip("2D view active")
        elif mode == "3d":
            btn.setText("3D View")
            btn.setToolTip("3D view active")
        else:
            btn.setText("2D/3D View")
            btn.setToolTip("Split view active")

    def open_3d_model(self, path: str, texture_path: str | None = None) -> None:
        import traceback

        path = (path or "").strip()
        if not path:
            print("[open_3d_model] empty path", flush=True)
            return

        try:
            if not os.path.exists(path):
                print(f"[open_3d_model] missing file: {path}", flush=True)
                return
        except Exception as exc:
            print(f"[open_3d_model] exists check failed: {exc}", flush=True)
            return

        ext = os.path.splitext(path)[1].lower()
        print(f"[open_3d_model] path={path} ext={ext} texture={texture_path}", flush=True)

        # switch to split view unless the user is already in full 3D
        mode = getattr(self, "_view_mode", "2d")
        if mode == "3d":
            self._set_view_mode("3d")
        else:
            self._set_view_mode("split")

        # splat PLY path
        if ext == ".ply":
            try:
                self.open_splat_model(path)
            except Exception:
                print("[open_3d_model] open_splat_model failed:\n" + traceback.format_exc(), flush=True)
            return

        # regular mesh path
        gl_view = getattr(self, "gl_view", None)
        if gl_view is None:
            print("[open_3d_model] gl_view is None", flush=True)
            return

        # When loading a mesh, hide splats so they don't cover the mesh render
        try:
            gl_view._mgl_render_splats = False
            gl_view._mgl_splat_count = 0

            # cancel pending splat upload (otherwise it can stomp center/zoom mid-frame)
            gl_view._mgl_pending_splats = None
            gl_view._mgl_splats15_cpu = None

            gl_view._mgl_splatq_vao = None
            gl_view._mgl_splatq_vbo = None
        except Exception:
            pass

        # ensure mesh rendering is enabled when opening a mesh
        try:
            gl_view._render_scene_models = True
        except Exception:
            pass

        loader = getattr(gl_view, "load_model_path", None)
        if not callable(loader):
            print("[open_3d_model] gl_view.load_model_path missing or not callable", flush=True)
            return

        try:
            loader(path, texture_path)
            print("[open_3d_model] loader finished", flush=True)

            # frame after loading so zoom/center are sane
            try:
                if hasattr(gl_view, "_on_frame_clicked"):
                    gl_view._on_frame_clicked()
            except Exception:
                pass

        except Exception:
            print("[open_3d_model] loader error:\n" + traceback.format_exc(), flush=True)

    def open_scene_assets(self, assets) -> None:
        import traceback
        import time

        assets = list(assets or [])
        if not assets:
            print("[open_scene_assets] no assets", flush=True)
            return

        # Ensure outliner selection/gizmo start cleared on scene open
        try:
            if hasattr(self, "clear_scene_asset_selection"):
                self.clear_scene_asset_selection()
        except Exception:
            pass

        clean = []
        visibility_map = {}
        for entry in assets:
            if not isinstance(entry, dict):
                continue
            path = (entry.get("path") or "").strip()
            if not path:
                continue
            try:
                if not os.path.exists(path):
                    continue
            except Exception:
                continue
            node_name = (entry.get("node") or "").strip()
            raw_visible = entry.get("visible")
            visible = True if raw_visible is None else bool(raw_visible)
            if node_name:
                visibility_map[node_name] = visible
            clean.append(
                {
                    "path": path,
                    "texture": entry.get("texture") or None,
                    "node": node_name,
                    "ext": entry.get("ext"),
                    "visible": visible,
                    "xform": entry.get("xform"),
                }
            )

        if not clean:
            print("[open_scene_assets] no valid asset paths", flush=True)
            return

        # Debounce duplicate loads (prevents repeated reload loops)
        try:
            sig = []
            for entry in clean:
                xf = entry.get("xform") or {}
                def _round3(vals, default):
                    try:
                        return tuple(round(float(v), 6) for v in (vals or default))
                    except Exception:
                        return tuple(default)
                sig.append(
                    (
                        str(entry.get("path") or ""),
                        str(entry.get("node") or ""),
                        bool(entry.get("visible", True)),
                        _round3((xf or {}).get("pos"), (0.0, 0.0, 0.0)),
                        _round3((xf or {}).get("rot"), (0.0, 0.0, 0.0)),
                        _round3((xf or {}).get("scl"), (1.0, 1.0, 1.0)),
                    )
                )
            sig = tuple(sorted(sig))
            now = time.time()
            last_sig = getattr(self, "_scene_assets_sig", None)
            last_ts = float(getattr(self, "_scene_assets_ts", 0.0) or 0.0)
            if sig == last_sig and (now - last_ts) < 0.5:
                return
            self._scene_assets_sig = sig
            self._scene_assets_ts = now
        except Exception:
            pass

        mode = getattr(self, "_view_mode", "2d")
        if mode == "3d":
            self._set_view_mode("3d")
        else:
            self._set_view_mode("split")

        gl_view = getattr(self, "gl_view", None)
        if gl_view is None:
            print("[open_scene_assets] gl_view is None", flush=True)
            return

        try:
            gl_view._mgl_scene_visibility = dict(visibility_map)
        except Exception:
            pass

        try:
            gl_view._render_scene_models = True
        except Exception:
            pass

        loader = getattr(gl_view, "load_scene_assets", None)
        if callable(loader):
            try:
                loader(clean, frame=True)
                return
            except Exception:
                print("[open_scene_assets] loader error:\n" + traceback.format_exc(), flush=True)

        # fallback: load first asset only
        first = clean[0]
        try:
            gl_view.load_model_path(first["path"], first.get("texture"))
            if hasattr(gl_view, "_on_frame_clicked"):
                gl_view._on_frame_clicked()
        except Exception:
            print("[open_scene_assets] fallback failed:\n" + traceback.format_exc(), flush=True)

    def set_scene_asset_visible(self, owner: str, visible: bool) -> None:
        gl_view = getattr(self, "gl_view", None)
        if gl_view is None:
            return
        handler = getattr(gl_view, "set_scene_asset_visible", None)
        if callable(handler):
            handler(owner, visible)

    def rename_scene_asset_owner(self, old_name: str, new_name: str) -> None:
        gl_view = getattr(self, "gl_view", None)
        if gl_view is None:
            return
        handler = getattr(gl_view, "rename_scene_asset_owner", None)
        if callable(handler):
            handler(old_name, new_name)

    def select_scene_asset(self, owner: str) -> None:
        owner = (owner or "").strip()
        if not owner:
            return

        for card in (getattr(self, "_card_by_node", {}) or {}).values():
            outliner = getattr(card, "_scene_outliner_widget", None)
            if outliner is None:
                continue
            try:
                for i in range(outliner.count()):
                    it = outliner.item(i)
                    if it is None:
                        continue
                    if (it.data(QtCore.Qt.UserRole) or "") == owner:
                        outliner.setCurrentRow(i)
                        outliner.scrollToItem(it)
                        return
            except Exception:
                pass

    def clear_scene_asset_selection(self) -> None:
        try:
            gl_view = getattr(self, "gl_view", None)
            if gl_view is not None:
                gl_view._mgl_log("scene: clear outliner selection")
                # Reset gizmo to world origin when nothing is selected.
                gl_view._xform_gizmo_owner = None
                gl_view._xform_gizmo_owner_kind = None
                gl_view._xform_gizmo_pos_locked = False
                gl_view._xform_gizmo_pos = (0.0, 0.0, 0.0)
                try:
                    gl_view.update()
                except Exception:
                    pass
        except Exception:
            pass
        for card in (getattr(self, "_card_by_node", {}) or {}).values():
            outliner = getattr(card, "_scene_outliner_widget", None)
            if outliner is None:
                continue
            try:
                outliner.blockSignals(True)
                outliner.setCurrentRow(-1)
                outliner.clearSelection()
            except Exception:
                pass
            finally:
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
            try:
                card._scene_selected_owner = None
            except Exception:
                pass
            try:
                card._scene_outliner_user_selected = False
            except Exception:
                pass
            try:
                panel = getattr(card, "_xform_panel", None)
                if panel is not None:
                    panel.setEnabled(False)
            except Exception:
                pass

    def update_scene_asset_xform(self, owner: str) -> None:
        owner = (owner or "").strip()
        if not owner:
            return
        gl_view = getattr(self, "gl_view", None)
        xf = None
        try:
            if gl_view is not None:
                is_splat = False
                try:
                    splat_map = getattr(gl_view, "_mgl_scene_splats", None)
                    if isinstance(splat_map, dict) and owner in splat_map:
                        is_splat = True
                except Exception:
                    is_splat = False
                getf = (
                    getattr(gl_view, "_mgl_get_scene_splat_xform", None)
                    if is_splat
                    else getattr(gl_view, "_mgl_get_scene_asset_xform", None)
                )
                if callable(getf):
                    xf = getf(owner)
        except Exception:
            xf = None
        for card in (getattr(self, "_card_by_node", {}) or {}).values():
            if getattr(card, "_scene_selected_owner", None) != owner:
                # still allow persistence update if the card contains this owner
                try:
                    outliner = getattr(card, "_scene_outliner_widget", None)
                    if outliner is None:
                        continue
                    found = False
                    for i in range(outliner.count()):
                        it = outliner.item(i)
                        if it is None:
                            continue
                        if (it.data(QtCore.Qt.UserRole) or "") == owner:
                            found = True
                            break
                    if not found:
                        continue
                except Exception:
                    continue
            try:
                fn = getattr(card, "_scene_xform_refresh", None)
                if callable(fn):
                    fn(owner)
            except Exception:
                pass
            # Persist xform to scene node model for workflow save/load
            try:
                node = getattr(card, "_node_ref", None)
                if node is None:
                    continue
                if xf is None or not isinstance(xf, dict):
                    continue
                xforms = getattr(node, "_scene_xforms", None)
                if not isinstance(xforms, dict):
                    xforms = {}
                xforms[str(owner)] = {
                    "pos": list(xf.get("pos", (0.0, 0.0, 0.0))),
                    "rot": list(xf.get("rot", (0.0, 0.0, 0.0))),
                    "scl": list(xf.get("scl", (1.0, 1.0, 1.0))),
                }
                setattr(node, "_scene_xforms", xforms)
            except Exception:
                pass

    def open_splat_model(self, ply_path: str) -> None:
        import traceback
        try:
            gv = getattr(self, "gl_view", None)
            dbg = bool(getattr(gv, "_mgl_debug", False)) if gv is not None else False
            if dbg:
                print("[SPLAT] open:", ply_path, flush=True)

            # When loading splats, hide any mesh so splats don't "mask" it later
            try:
                gv = getattr(self, "gl_view", None)
                if gv is not None:
                    try:
                        if hasattr(gv, "_clear_scene_asset_state"):
                            gv._clear_scene_asset_state()
                    except Exception:
                        pass
                    gv._render_scene_models = True
                    gv._meshes.clear()
                    gv._mesh_colors.clear()
                    gv._mesh_transforms.clear()
                    gv._mesh_meta.clear()
                    gv._manual_model_path = None
                    gv._model_load_pending = False
                    try:
                        scene = getattr(gv, "_mgl_scene", None)
                        if scene is not None:
                            # manual single-model tags
                            scene.remove_by_tag("model")
                            scene.remove_by_tag("model-wire")

                            # scene-assembly tags (this is what was sticking around)
                            scene.remove_by_tag("scene-model")
                            scene.remove_by_tag("scene-wire")

                    except Exception:
                        pass
                    gv._mgl_vao = None
                    gv._mgl_submeshes = []
                    gv._mgl_mesh_vbos = []
                    gv._mgl_index_buffer = None
                    gv._mgl_mesh = None
                    gv._mgl_mesh_vertex_count = 0
                    gv._mgl_mesh_path = ""
            except Exception:
                pass

            from echograph.util.splats_io import load_splats_ply
            splats = load_splats_ply(ply_path, n=200_000)
            if dbg:
                print("[SPLAT] loaded:", splats.shape, splats.dtype, flush=True)

            self.gl_view.set_splats(splats)
            if dbg:
                print("[SPLAT] set_splats done", flush=True)
        except Exception:
            print("[SPLAT] ERROR:\n", traceback.format_exc(), flush=True)

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

        btn_export = QtWidgets.QPushButton("Save As", bar)
        btn_export.setToolTip("Save current graph to a new .json (Save As)")
        btn_export.clicked.connect(self._export_graph)
        h.addWidget(btn_export, 0)

        btn_frame = QtWidgets.QPushButton("Frame", bar)
        btn_frame.setToolTip("Fit view to all nodes")
        btn_frame.clicked.connect(self._frame_all_nodes)
        h.addWidget(btn_frame, 0)

        self._btn_3d = QtWidgets.QPushButton("3D View", bar)
        self._btn_3d.setToolTip("Switch to 3D viewport")
        self._btn_3d.clicked.connect(self._cycle_view_mode)
        h.addWidget(self._btn_3d, 0)

        btn_logs = QtWidgets.QPushButton("Logs")
        btn_logs.setToolTip("Open EchoGraph log folder")
        btn_logs.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(__import__("os").path.join(__import__("tempfile").gettempdir(), "EchoGraph"))
        ))
        h.addWidget(btn_logs, 0)

        settings_btn = QtWidgets.QToolButton(bar)
        settings_btn.setObjectName("SettingsButton")
        settings_btn.setText("Settings")
        settings_btn.setCursor(QtCore.Qt.PointingHandCursor)
        settings_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        settings_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        settings_btn.setFixedHeight(22)
        settings_btn.setStyleSheet(
            "QToolButton#SettingsButton{color:#ffffff;background:#2a2f36;border:1px solid #3a3f46;"
            "border-radius:4px;padding:1px 10px;}"
            "QToolButton#SettingsButton:hover{background:#353b45;}"
            "QToolButton#SettingsButton[active=\"true\"]{background:#1f7a45;border-color:#2a8a52;}"
        )

        settings_menu = QtWidgets.QMenu(settings_btn)
        settings_menu.setObjectName("SettingsMenu")
        settings_menu.setStyleSheet(
            "#SettingsMenu{background:#1b2026;border:1px solid #333;padding:0px;}"
        )

        panel = QtWidgets.QFrame(settings_menu)
        panel.setObjectName("SettingsPanel")
        panel.setStyleSheet(
            "#SettingsPanel{background:#1b2026;border:0px;border-radius:6px;}"
            "#SettingsPanel QLabel{color:#e5e7eb;}"
            "#SettingsPanel QToolButton{background:transparent;border:0px;padding:0px;}"
            "#SettingsPanel QToolButton:hover{background:#2b313a;}"
        )
        grid = QtWidgets.QGridLayout(panel)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self._llm_value_lbl = QtWidgets.QLabel(f"{int(round(LLM_SCALE*100))}%")
        self._llm_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._llm_slider.setMinimum(25); self._llm_slider.setMaximum(175)
        self._llm_slider.setSingleStep(1); self._llm_slider.setPageStep(5)
        self._llm_slider.setFixedWidth(160)
        self._llm_slider.setValue(int(round(LLM_SCALE * 100)))
        self._llm_slider.valueChanged.connect(
            lambda v: (set_global_llm_scale(max(0.25, min(1.75, v/100.0)), self.scene),
                    self._llm_value_lbl.setText(f"{v}%"))
        )
        grid.addWidget(QtWidgets.QLabel("LLM Scale"), 0, 0)
        grid.addWidget(self._llm_slider, 0, 1)
        grid.addWidget(self._llm_value_lbl, 0, 2)
        refresh_btn = QtWidgets.QToolButton(panel)
        refresh_btn.setToolTip("Reset settings to defaults")
        refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        refresh_btn.setAutoRaise(True)
        refresh_btn.setIconSize(QtCore.QSize(14, 14))
        refresh_icon = QtGui.QIcon(str(script_dir() / "icons" / "Refresh_Icon.png"))
        if not refresh_icon.isNull():
            refresh_btn.setIcon(refresh_icon)
        else:
            refresh_btn.setText("Reset")
        refresh_btn.clicked.connect(self._reset_settings_to_defaults)
        grid.addWidget(refresh_btn, 0, 3)

        self._pan_base = float(getattr(self, "_pan_base", 0.01))
        self._pan_exp = float(getattr(self, "_pan_exp", 1.2))
        self._pan_boost = float(getattr(self, "_pan_boost", 10.0))
        self._gizmo_zoom_scale = float(getattr(self, "_gizmo_zoom_scale", 0.02))

        self._pan_base_value_lbl = QtWidgets.QLabel(f"{self._pan_base:.3f}")
        self._pan_base_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._pan_base_slider.setMinimum(1); self._pan_base_slider.setMaximum(500)
        self._pan_base_slider.setSingleStep(1); self._pan_base_slider.setPageStep(10)
        self._pan_base_slider.setFixedWidth(160)
        self._pan_base_slider.setValue(int(round(self._pan_base * 1000.0)))
        self._pan_base_slider.valueChanged.connect(self._on_pan_base_changed)
        grid.addWidget(QtWidgets.QLabel("Pan Base"), 1, 0)
        grid.addWidget(self._pan_base_slider, 1, 1)
        grid.addWidget(self._pan_base_value_lbl, 1, 2)

        self._pan_exp_value_lbl = QtWidgets.QLabel(f"{self._pan_exp:.2f}")
        self._pan_exp_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._pan_exp_slider.setMinimum(50); self._pan_exp_slider.setMaximum(300)
        self._pan_exp_slider.setSingleStep(1); self._pan_exp_slider.setPageStep(10)
        self._pan_exp_slider.setFixedWidth(160)
        self._pan_exp_slider.setValue(int(round(self._pan_exp * 100.0)))
        self._pan_exp_slider.valueChanged.connect(self._on_pan_exp_changed)
        grid.addWidget(QtWidgets.QLabel("Pan Exp"), 2, 0)
        grid.addWidget(self._pan_exp_slider, 2, 1)
        grid.addWidget(self._pan_exp_value_lbl, 2, 2)

        self._pan_boost_value_lbl = QtWidgets.QLabel(f"{self._pan_boost:.1f}")
        self._pan_boost_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._pan_boost_slider.setMinimum(1); self._pan_boost_slider.setMaximum(50)
        self._pan_boost_slider.setSingleStep(1); self._pan_boost_slider.setPageStep(5)
        self._pan_boost_slider.setFixedWidth(160)
        self._pan_boost_slider.setValue(int(round(self._pan_boost)))
        self._pan_boost_slider.valueChanged.connect(self._on_pan_boost_changed)
        grid.addWidget(QtWidgets.QLabel("Pan Boost"), 3, 0)
        grid.addWidget(self._pan_boost_slider, 3, 1)
        grid.addWidget(self._pan_boost_value_lbl, 3, 2)

        self._gizmo_zoom_value_lbl = QtWidgets.QLabel(f"{self._gizmo_zoom_scale:.3f}")
        self._gizmo_zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._gizmo_zoom_slider.setMinimum(1); self._gizmo_zoom_slider.setMaximum(1000)
        self._gizmo_zoom_slider.setSingleStep(1); self._gizmo_zoom_slider.setPageStep(10)
        self._gizmo_zoom_slider.setFixedWidth(160)
        self._gizmo_zoom_slider.setValue(int(round(self._gizmo_zoom_scale * 1000.0)))
        self._gizmo_zoom_slider.valueChanged.connect(self._on_gizmo_zoom_scale_changed)
        grid.addWidget(QtWidgets.QLabel("Gizmo Zoom"), 4, 0)
        grid.addWidget(self._gizmo_zoom_slider, 4, 1)
        grid.addWidget(self._gizmo_zoom_value_lbl, 4, 2)

        panel_action = QtWidgets.QWidgetAction(settings_menu)
        panel_action.setDefaultWidget(panel)
        settings_menu.addAction(panel_action)

        settings_menu.aboutToShow.connect(lambda: self._set_settings_menu_active(True))
        settings_menu.aboutToHide.connect(lambda: self._set_settings_menu_active(False))
        settings_btn.setMenu(settings_menu)
        self._settings_btn = settings_btn
        h.addWidget(settings_btn, 0)

        h.addStretch(1)   # ← stretch AFTER the settings block to keep it left
        return bar

    def _set_settings_menu_active(self, active: bool) -> None:
        btn = getattr(self, "_settings_btn", None)
        if btn is None:
            return
        try:
            btn.setProperty("active", bool(active))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def _reset_settings_to_defaults(self) -> None:
        try:
            default_llm = float(LLM_SCALE_DEFAULT)
        except Exception:
            default_llm = 0.5
        try:
            set_global_llm_scale(default_llm, self.scene)
        except Exception:
            pass
        if hasattr(self, "_llm_slider"):
            try:
                self._llm_slider.blockSignals(True)
                self._llm_slider.setValue(int(round(default_llm * 100)))
                self._llm_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_llm_value_lbl"):
            try:
                self._llm_value_lbl.setText(f"{int(round(default_llm * 100))}%")
            except Exception:
                pass

        self._pan_base = 0.01
        self._pan_exp = 1.2
        self._pan_boost = 10.0
        self._gizmo_zoom_scale = 0.02
        if hasattr(self, "_pan_base_slider"):
            try:
                self._pan_base_slider.blockSignals(True)
                self._pan_base_slider.setValue(int(round(self._pan_base * 1000.0)))
                self._pan_base_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_pan_exp_slider"):
            try:
                self._pan_exp_slider.blockSignals(True)
                self._pan_exp_slider.setValue(int(round(self._pan_exp * 100.0)))
                self._pan_exp_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_pan_boost_slider"):
            try:
                self._pan_boost_slider.blockSignals(True)
                self._pan_boost_slider.setValue(int(round(self._pan_boost)))
                self._pan_boost_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_gizmo_zoom_slider"):
            try:
                self._gizmo_zoom_slider.blockSignals(True)
                self._gizmo_zoom_slider.setValue(int(round(self._gizmo_zoom_scale * 1000.0)))
                self._gizmo_zoom_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_pan_base_value_lbl"):
            try:
                self._pan_base_value_lbl.setText(f"{self._pan_base:.3f}")
            except Exception:
                pass
        if hasattr(self, "_pan_exp_value_lbl"):
            try:
                self._pan_exp_value_lbl.setText(f"{self._pan_exp:.2f}")
            except Exception:
                pass
        if hasattr(self, "_pan_boost_value_lbl"):
            try:
                self._pan_boost_value_lbl.setText(f"{self._pan_boost:.1f}")
            except Exception:
                pass
        if hasattr(self, "_gizmo_zoom_value_lbl"):
            try:
                self._gizmo_zoom_value_lbl.setText(f"{self._gizmo_zoom_scale:.3f}")
            except Exception:
                pass
        self._apply_pan_settings_to_gl_view()

    def _apply_pan_settings_to_gl_view(self) -> None:
        gv = getattr(self, "gl_view", None)
        if gv is None:
            return
        try:
            gv._mgl_pan_base = float(getattr(self, "_pan_base", 0.01))
            gv._mgl_pan_zoom_exp_out = float(getattr(self, "_pan_exp", 1.2))
            gv._mgl_pan_zoom_boost = float(getattr(self, "_pan_boost", 10.0))
            gv._mgl_zoom_pan_scale = float(getattr(self, "_gizmo_zoom_scale", 0.02))
            gv._mgl_pan_ref_zoom = None
        except Exception:
            pass
        sc = getattr(self, "scene", None)
        if sc is not None:
            try:
                settings = getattr(sc, "_view_settings", None)
                if not isinstance(settings, dict):
                    settings = {}
                settings = dict(settings)
                settings["pan_base"] = float(getattr(self, "_pan_base", 0.01))
                settings["pan_exp"] = float(getattr(self, "_pan_exp", 1.2))
                settings["pan_boost"] = float(getattr(self, "_pan_boost", 10.0))
                settings["gizmo_zoom_scale"] = float(getattr(self, "_gizmo_zoom_scale", 0.02))
                sc._view_settings = settings
            except Exception:
                pass

    def _on_pan_base_changed(self, value: int) -> None:
        try:
            base = max(0.0001, float(value) / 1000.0)
        except Exception:
            base = 0.01
        self._pan_base = base
        lbl = getattr(self, "_pan_base_value_lbl", None)
        if lbl is not None:
            lbl.setText(f"{base:.3f}")
        self._apply_pan_settings_to_gl_view()

    def _on_pan_exp_changed(self, value: int) -> None:
        try:
            exp = max(0.1, float(value) / 100.0)
        except Exception:
            exp = 1.2
        self._pan_exp = exp
        lbl = getattr(self, "_pan_exp_value_lbl", None)
        if lbl is not None:
            lbl.setText(f"{exp:.2f}")
        self._apply_pan_settings_to_gl_view()

    def _on_pan_boost_changed(self, value: int) -> None:
        try:
            boost = max(1.0, float(value))
        except Exception:
            boost = 10.0
        self._pan_boost = boost
        lbl = getattr(self, "_pan_boost_value_lbl", None)
        if lbl is not None:
            lbl.setText(f"{boost:.1f}")
        self._apply_pan_settings_to_gl_view()

    def _on_gizmo_zoom_scale_changed(self, value: int) -> None:
        try:
            scale = max(0.0001, float(value) / 1000.0)
        except Exception:
            scale = 0.02
        self._gizmo_zoom_scale = scale
        lbl = getattr(self, "_gizmo_zoom_value_lbl", None)
        if lbl is not None:
            lbl.setText(f"{scale:.3f}")
        self._apply_pan_settings_to_gl_view()

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

    def _open_create_menu_from_hotkey(self):
        try:
            if actions._focus_is_text_input():
                return
        except Exception:
            pass
        v = getattr(self, "view", None)
        sc = getattr(self, "scene", None)
        if v is None or sc is None or not hasattr(sc, "show_create_dialog_at"):
            return
        try:
            vp = v.viewport()
            cursor = QtGui.QCursor.pos()
            local = vp.mapFromGlobal(cursor) if vp is not None else v.mapFromGlobal(cursor)
            if vp is not None and not vp.rect().contains(local):
                local = vp.rect().center()
            scene_pos = v.mapToScene(local)
        except Exception:
            scene_pos = QtCore.QPointF(0, 0)
        try:
            sc.show_create_dialog_at(scene_pos)
        except Exception:
            pass

    def _set_view_mode_from_hotkey(self, mode: str) -> None:
        try:
            if actions._focus_is_text_input():
                return
        except Exception:
            pass
        try:
            self._set_view_mode(mode)
        except Exception:
            pass

    def _frame_from_hotkey(self) -> None:
        try:
            if actions._focus_is_text_input():
                return
        except Exception:
            pass
        cursor = QtGui.QCursor.pos()
        gl_view = getattr(self, "gl_view", None)
        if gl_view is not None:
            try:
                if gl_view.isVisible():
                    local = gl_view.mapFromGlobal(cursor)
                    if gl_view.rect().contains(local):
                        handler = getattr(gl_view, "_on_frame_clicked", None)
                        if callable(handler):
                            handler()
                            return
            except Exception:
                pass

        view = getattr(self, "view", None)
        if view is not None:
            try:
                if view.isVisible():
                    vp = view.viewport()
                    local = vp.mapFromGlobal(cursor) if vp is not None else view.mapFromGlobal(cursor)
                    if (vp is not None and vp.rect().contains(local)) or (vp is None and view.rect().contains(local)):
                        self._frame_all_nodes()
                        return
            except Exception:
                pass

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
        settings = data.get("settings", {}) if isinstance(data, dict) else {}
        try:
            pan_base = float(settings.get("pan_base", getattr(self, "_pan_base", 0.01)))
        except Exception:
            pan_base = getattr(self, "_pan_base", 0.01)
        try:
            pan_exp = float(settings.get("pan_exp", getattr(self, "_pan_exp", 1.2)))
        except Exception:
            pan_exp = getattr(self, "_pan_exp", 1.2)
        try:
            pan_boost = float(settings.get("pan_boost", getattr(self, "_pan_boost", 10.0)))
        except Exception:
            pan_boost = getattr(self, "_pan_boost", 10.0)
        try:
            gizmo_zoom = float(settings.get("gizmo_zoom_scale", getattr(self, "_gizmo_zoom_scale", 0.02)))
        except Exception:
            gizmo_zoom = getattr(self, "_gizmo_zoom_scale", 0.02)
        self._pan_base = pan_base
        self._pan_exp = pan_exp
        self._pan_boost = pan_boost
        self._gizmo_zoom_scale = gizmo_zoom
        if hasattr(self, "_pan_base_slider"):
            self._pan_base_slider.blockSignals(True)
            self._pan_base_slider.setValue(int(round(pan_base * 1000.0)))
            self._pan_base_slider.blockSignals(False)
        if hasattr(self, "_pan_exp_slider"):
            self._pan_exp_slider.blockSignals(True)
            self._pan_exp_slider.setValue(int(round(pan_exp * 100.0)))
            self._pan_exp_slider.blockSignals(False)
        if hasattr(self, "_pan_boost_slider"):
            self._pan_boost_slider.blockSignals(True)
            self._pan_boost_slider.setValue(int(round(pan_boost)))
            self._pan_boost_slider.blockSignals(False)
        if hasattr(self, "_gizmo_zoom_slider"):
            self._gizmo_zoom_slider.blockSignals(True)
            self._gizmo_zoom_slider.setValue(int(round(gizmo_zoom * 1000.0)))
            self._gizmo_zoom_slider.blockSignals(False)
        if hasattr(self, "_pan_base_value_lbl"):
            self._pan_base_value_lbl.setText(f"{pan_base:.3f}")
        if hasattr(self, "_pan_exp_value_lbl"):
            self._pan_exp_value_lbl.setText(f"{pan_exp:.2f}")
        if hasattr(self, "_pan_boost_value_lbl"):
            self._pan_boost_value_lbl.setText(f"{pan_boost:.1f}")
        if hasattr(self, "_gizmo_zoom_value_lbl"):
            self._gizmo_zoom_value_lbl.setText(f"{gizmo_zoom:.3f}")
        try:
            self._apply_pan_settings_to_gl_view()
        except Exception:
            pass

        self._current_path = path
        self._remember_recent(path)
        self._frame_all_nodes()
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
        self._info_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(self._cardsContainer)

        self.infoDock.setWidget(scroll)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.infoDock)

        self.infoDock.setStyleSheet(
            "QDockWidget{background:#1a1f24;color:#e6edf3;}"
            "QDockWidget::title{background:#20242b;color:#e6edf3;padding:4px 8px;}"
        )
        try:
            self.infoDock.setMinimumWidth(360)
        except Exception:
            pass
        scroll.setStyleSheet(
            "QScrollArea{background:#1a1f24;border:none;}"
            "QScrollArea>Viewport{background:#1a1f24;}"
        )
        self._cardsContainer.setStyleSheet("QWidget{background:#1a1f24;color:#e6edf3;}")

        try:
            self.resizeDocks([self.infoDock], [400], QtCore.Qt.Horizontal)
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
        vpos = None
        hpos = None
        try:
            if getattr(self, "_info_scroll", None):
                vpos = int(self._info_scroll.verticalScrollBar().value())
                hpos = int(self._info_scroll.horizontalScrollBar().value())
        except Exception:
            pass

        # ignore incoming ordered_nodes; rebuild with Append-aware order
        if not getattr(self, "scene", None) or not getattr(self.scene, "_current_output_name", None):
            seq = []
        else:
            seq = self.scene.ordered_upstream_items(self.scene._current_output_name) or []

        try:
            self.infoDock.setVisible(True)
            self.infoDock.raise_()
        except Exception:
            pass

        # clear all existing cards (keep trailing stretch)
        for i in reversed(range(self._cardsLayout.count() - 1)):
            w = self._cardsLayout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._card_by_node.clear()

        total = len(seq)
        for idx, item in enumerate(seq, start=1):
            node = item.model
            card = InfoCard(node, order_index=idx, order_total=total)
            card.requestJump.connect(self.scene.center_on_name)
            card.closedForNode.connect(self._on_card_closed)
            card.attach_scene(self.scene)

            try:
                card.apply_append_preview_if_output()
            except Exception:
                pass

            self._cardsLayout.insertWidget(self._cardsLayout.count() - 1, card)

        # restore scroll after layout has stabilized (two ticks)
        try:
            if getattr(self, "_info_scroll", None) and vpos is not None:
                vs = self._info_scroll.verticalScrollBar()
                hs = self._info_scroll.horizontalScrollBar()

                QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, lambda v=vpos, b=vs: b.setValue(v)))
                QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, lambda v=hpos, b=hs: b.setValue(v)))
        except Exception:
            pass

    def _on_params_changed(self, node_name: str, params: list):
        # Refresh just the affected card (no full branch rebuild)
        try:
            card = self._card_by_node.get(node_name)
            if card and hasattr(card, "refresh_params_from_model"):
                card.refresh_params_from_model()
        except Exception:
            pass

        # If an Output is active, refresh only the Output preview text (no branch rebuild)
        try:
            sc = getattr(self, "scene", None)
            if sc and getattr(sc, "_current_output_name", None):
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
