# ==== echograph (single-file shelf script; runs on click) ======================
# Maya + Houdini PySide2 node-graph demo with info cards, Output path highlight,
# multi-input Switch with slider, JSON Open/Save/Export, node params, wiring, and code editor.
# Nav: Left-click = select / wire-drag; Middle-mouse = pan; Right-drag = zoom.
# Alt+LeftClick a link to delete it. Badge (type bubble) is below the name.
# Quick-create: right-click empty canvas (no drag) to open Create Node.
# Delete/Backspace removes selected nodes with their links.
# Clicking an Output node auto-fills Info pane with ordered branch cards (start → output).

import sys, re, json, math, os, time, tempfile, subprocess
from datetime import datetime, timezone
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
from echograph.ui.timeline_controller import TimelineController
from echograph.ui.timeline_menu import build_timeline_panels_menu
from echograph.ui.profiler_controller import ProfilerController
from echograph.services.profiler import profiled, profile_scope
from echograph.services import runtime_logging


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

try:
    import speech_recognition as _speech_recognition  # type: ignore
except Exception:
    _speech_recognition = None

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
_APP_SETTINGS_PATH = script_dir() / "app_settings.json"
_DEFAULT_PANEL_LAYOUT_PRESET = {"timeline": False, "audio": False, "profiler": False}
_VOICE_AUDIO_MODE_BILATERAL = "bilateral"
_VOICE_AUDIO_MODE_TURN_TAKING = "turn_taking"
_VOICE_AUDIO_MODE_DEFAULT = _VOICE_AUDIO_MODE_TURN_TAKING
_VOICE_MIC_DEVICE_DEFAULT = None
_SHADOW_QUALITY_DEFAULT = "high"
_AMBIENT_LIGHT_STRENGTH_DEFAULT = 0.10
_SCENE_SKELETON_JOINT_NAMES_DEFAULT = False
_SHADOW_QUALITY_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "ultra": "Ultra",
}

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


def _coerce_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n"}:
            return False
    return bool(default)


def _normalize_panel_layout_preset(value, fallback=None) -> Dict[str, bool]:
    base = dict(_DEFAULT_PANEL_LAYOUT_PRESET)
    if isinstance(fallback, dict):
        if "timeline" in fallback:
            base["timeline"] = _coerce_bool(fallback.get("timeline"), base["timeline"])
        if "audio" in fallback:
            base["audio"] = _coerce_bool(fallback.get("audio"), base["audio"])
        if "profiler" in fallback:
            base["profiler"] = _coerce_bool(fallback.get("profiler"), base["profiler"])
    if isinstance(value, dict):
        if "timeline" in value:
            base["timeline"] = _coerce_bool(value.get("timeline"), base["timeline"])
        if "audio" in value:
            base["audio"] = _coerce_bool(value.get("audio"), base["audio"])
        if "profiler" in value:
            base["profiler"] = _coerce_bool(value.get("profiler"), base["profiler"])
    if base["audio"] and not base["timeline"]:
        base["timeline"] = True
    if not base["timeline"]:
        base["audio"] = False
    return {
        "timeline": bool(base["timeline"]),
        "audio": bool(base["audio"]),
        "profiler": bool(base["profiler"]),
    }


def _normalize_view_mode_preset(value, fallback: str | None = "2d") -> str | None:
    text = str(value or "").strip().lower()
    text = text.replace("\\", "/").replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    compact = text.replace(" ", "")
    if compact in {"2d", "2donly", "2dview"}:
        return "2d"
    if compact in {"3d", "3donly", "3dview"}:
        return "3d"
    if compact in {"split", "2d/3d", "2d+3d", "2d3d", "2dand3d", "both", "dual"}:
        return "split"
    return fallback


def _normalize_voice_audio_mode(value, fallback: str = _VOICE_AUDIO_MODE_DEFAULT) -> str:
    text = str(value or "").strip().lower()
    if text in {"bilateral", "duplex", "full_duplex", "simultaneous"}:
        return _VOICE_AUDIO_MODE_BILATERAL
    if text in {"turn_taking", "turn-taking", "turntaking", "single", "single_talk"}:
        return _VOICE_AUDIO_MODE_TURN_TAKING
    fb = str(fallback or "").strip().lower()
    if fb in {"bilateral", "duplex", "full_duplex", "simultaneous"}:
        return _VOICE_AUDIO_MODE_BILATERAL
    if fb in {"turn_taking", "turn-taking", "turntaking", "single", "single_talk"}:
        return _VOICE_AUDIO_MODE_TURN_TAKING
    return _VOICE_AUDIO_MODE_DEFAULT


def _normalize_voice_mic_device_index(value, fallback=_VOICE_MIC_DEVICE_DEFAULT):
    if value is None:
        if fallback is None:
            return _VOICE_MIC_DEVICE_DEFAULT
        return _normalize_voice_mic_device_index(fallback, _VOICE_MIC_DEVICE_DEFAULT)
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        idx = int(value)
        return idx if idx >= 0 else _VOICE_MIC_DEVICE_DEFAULT
    text = str(value or "").strip()
    if not text:
        return _VOICE_MIC_DEVICE_DEFAULT
    low = text.lower()
    if low in {"default", "system", "auto", "none", "-1"}:
        return _VOICE_MIC_DEVICE_DEFAULT
    try:
        idx = int(text)
        return idx if idx >= 0 else _VOICE_MIC_DEVICE_DEFAULT
    except Exception:
        pass
    if fallback is not None and fallback != value:
        return _normalize_voice_mic_device_index(fallback, _VOICE_MIC_DEVICE_DEFAULT)
    return _VOICE_MIC_DEVICE_DEFAULT


def _normalize_shadow_quality(value, fallback: str = _SHADOW_QUALITY_DEFAULT) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "l": "low",
        "lo": "low",
        "low": "low",
        "m": "medium",
        "med": "medium",
        "medium": "medium",
        "h": "high",
        "hi": "high",
        "high": "high",
        "u": "ultra",
        "ultra": "ultra",
        "max": "ultra",
    }
    if text in aliases:
        return aliases[text]
    fb = str(fallback or "").strip().lower()
    return aliases.get(fb, _SHADOW_QUALITY_DEFAULT)


def _normalize_ambient_light_strength(value, fallback: float = _AMBIENT_LIGHT_STRENGTH_DEFAULT) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        pass
    try:
        return max(0.0, min(1.0, float(fallback)))
    except Exception:
        return _AMBIENT_LIGHT_STRENGTH_DEFAULT


def _list_available_microphone_options() -> List[Dict[str, Any]]:
    options = [{"device_index": _VOICE_MIC_DEVICE_DEFAULT, "name": "System Default"}]
    sr_mod = _speech_recognition
    if sr_mod is None:
        return options
    mic_cls = getattr(sr_mod, "Microphone", None)
    if mic_cls is None:
        return options
    output_tokens = ("speaker", "speakers", "headphone", "headphones", "output", "line out", "monitor", "loopback")
    input_tokens = ("mic", "microphone", "input", "headset", "array", "record", "recording")

    # Prefer PyAudio device metadata so we can filter to input-capable devices.
    pyaudio_mod = None
    get_pyaudio = getattr(mic_cls, "get_pyaudio", None)
    if callable(get_pyaudio):
        try:
            pyaudio_mod = get_pyaudio()
        except Exception:
            pyaudio_mod = None
    if pyaudio_mod is not None:
        pa = None
        try:
            pa = pyaudio_mod.PyAudio()
            count = int(pa.get_device_count() or 0)
            for idx in range(count):
                try:
                    info = pa.get_device_info_by_index(idx) or {}
                except Exception:
                    continue
                try:
                    max_input = int(info.get("maxInputChannels", 0) or 0)
                except Exception:
                    max_input = 0
                if max_input <= 0:
                    continue
                name = str(info.get("name", "") or "").strip() or f"Microphone {idx}"
                key = name.lower()
                if any(tok in key for tok in output_tokens) and not any(tok in key for tok in input_tokens):
                    continue
                options.append({"device_index": int(idx), "name": f"{idx}: {name}"})
        except Exception:
            pass
        finally:
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass
        if len(options) > 1:
            return options

    # Fallback when PyAudio metadata is unavailable.
    if not hasattr(mic_cls, "list_microphone_names"):
        return options
    try:
        names = list(mic_cls.list_microphone_names() or [])
    except Exception:
        names = []
    for idx, raw_name in enumerate(names):
        name = str(raw_name or "").strip() or f"Microphone {idx}"
        key = name.lower()
        if any(tok in key for tok in output_tokens) and not any(tok in key for tok in input_tokens):
            continue
        options.append({"device_index": int(idx), "name": f"{idx}: {name}"})
    return options


def _load_app_settings() -> Dict[str, Any]:
    raw = {}
    try:
        data = json.loads(_APP_SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data
    except Exception:
        raw = {}
    panel_layout = _normalize_panel_layout_preset(raw.get("panel_layout"), _DEFAULT_PANEL_LAYOUT_PRESET)
    view_mode = _normalize_view_mode_preset(raw.get("view_mode"), "2d")
    save_layout = _coerce_bool(raw.get("save_layout"), True)
    voice_audio_mode = _normalize_voice_audio_mode(raw.get("voice_audio_mode"), _VOICE_AUDIO_MODE_DEFAULT)
    voice_mic_device_index = _normalize_voice_mic_device_index(
        raw.get("voice_mic_device_index"),
        _VOICE_MIC_DEVICE_DEFAULT,
    )
    shadow_quality = _normalize_shadow_quality(raw.get("shadow_quality"), _SHADOW_QUALITY_DEFAULT)
    cast_shadows = _coerce_bool(raw.get("cast_shadows"), True)
    self_shadows = _coerce_bool(raw.get("self_shadows"), True)
    two_sided_shadows = _coerce_bool(raw.get("two_sided_shadows"), True)
    ambient_light = _coerce_bool(raw.get("ambient_light"), True)
    ambient_light_strength = _normalize_ambient_light_strength(
        raw.get("ambient_light_strength"),
        _AMBIENT_LIGHT_STRENGTH_DEFAULT,
    )
    scene_skeleton_joint_names = _coerce_bool(
        raw.get("scene_skeleton_joint_names"),
        _SCENE_SKELETON_JOINT_NAMES_DEFAULT,
    )
    return {
        "save_layout": bool(save_layout),
        "panel_layout": panel_layout,
        "view_mode": view_mode or "2d",
        "voice_audio_mode": voice_audio_mode,
        "voice_mic_device_index": voice_mic_device_index,
        "shadow_quality": shadow_quality,
        "cast_shadows": bool(cast_shadows),
        "self_shadows": bool(self_shadows),
        "two_sided_shadows": bool(two_sided_shadows),
        "ambient_light": bool(ambient_light),
        "ambient_light_strength": float(ambient_light_strength),
        "scene_skeleton_joint_names": bool(scene_skeleton_joint_names),
    }


def _save_app_settings(settings: Dict[str, Any]) -> None:
    payload = {
        "save_layout": _coerce_bool((settings or {}).get("save_layout"), True),
        "panel_layout": _normalize_panel_layout_preset((settings or {}).get("panel_layout"), _DEFAULT_PANEL_LAYOUT_PRESET),
        "view_mode": _normalize_view_mode_preset((settings or {}).get("view_mode"), "2d") or "2d",
        "voice_audio_mode": _normalize_voice_audio_mode((settings or {}).get("voice_audio_mode"), _VOICE_AUDIO_MODE_DEFAULT),
        "voice_mic_device_index": _normalize_voice_mic_device_index(
            (settings or {}).get("voice_mic_device_index"),
            _VOICE_MIC_DEVICE_DEFAULT,
        ),
        "shadow_quality": _normalize_shadow_quality(
            (settings or {}).get("shadow_quality"),
            _SHADOW_QUALITY_DEFAULT,
        ),
        "cast_shadows": _coerce_bool((settings or {}).get("cast_shadows"), True),
        "self_shadows": _coerce_bool((settings or {}).get("self_shadows"), True),
        "two_sided_shadows": _coerce_bool((settings or {}).get("two_sided_shadows"), True),
        "ambient_light": _coerce_bool((settings or {}).get("ambient_light"), True),
        "ambient_light_strength": _normalize_ambient_light_strength(
            (settings or {}).get("ambient_light_strength"),
            _AMBIENT_LIGHT_STRENGTH_DEFAULT,
        ),
        "scene_skeleton_joint_names": _coerce_bool(
            (settings or {}).get("scene_skeleton_joint_names"),
            _SCENE_SKELETON_JOINT_NAMES_DEFAULT,
        ),
    }
    try:
        _APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _APP_SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


_WORKFLOW_LOAD_PROFILE_PATH = Path(tempfile.gettempdir()) / "EchoGraph" / "workflow_load_profile.jsonl"


def _workflow_load_profile_enabled() -> bool:
    raw = str(os.environ.get("ECHOGRAPH_LOAD_PROFILE", "0") or "").strip().lower()
    if not raw:
        return False
    return raw in ("1", "true", "yes", "on", "y")


def _workflow_load_log(event: str, **fields) -> None:
    if not _workflow_load_profile_enabled():
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": str(event or ""),
    }
    record.update(fields or {})
    try:
        _WORKFLOW_LOAD_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _WORKFLOW_LOAD_PROFILE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
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
        self._resize_start_scene_pos = QtCore.QPointF()
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
                self._resize_start_scene_pos = QtCore.QPointF(e.scenePos())
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
            self._apply_resize(e.scenePos())
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

    def _apply_resize(self, scene_pos: QtCore.QPointF):
        if not self._resize_mode:
            return
        min_w, min_h = 160.0, 100.0
        rect = QtCore.QRectF(self._initial_rect)
        # Top/left resizing moves the item, so measure against fixed scene coords.
        pos_delta = scene_pos - self._resize_start_scene_pos
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
                if kind in ("llm", "local_server", "local server", "localserver", "html_preview"):
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
        self._edge_index_by_dst = {}
        self._edge_index_dirty = True
        self._edge_index_version = 0
        self._scene_asset_revision = 0
        self._comment_groups = []
        self._moving_comment_group = False
        self._drag_src_item = None
        self._temp_wire = None
        self._current_output_name = None
        self._last_paste_jitter = QtCore.QPointF(0.0, 0.0)
        self._group_drag_active = False
        self._group_move_lock = False
        self._suppress_node_model_updates = False
        self._bulk_loading = False

        try:
            self.setItemIndexMethod(QtWidgets.QGraphicsScene.NoIndex)
        except AttributeError:
            try:
                self.setItemIndexMethod(QtWidgets.QGraphicsScene.ItemIndexMethod.NoIndex)
            except Exception:
                self.setItemIndexMethod(0)

        self.setBackgroundBrush(QtGui.QColor("#1a1f24"))
        self.setSceneRect(QtCore.QRectF(-20000, -20000, 40000, 40000))
        try:
            self.linksChanged.connect(self._bump_scene_asset_revision)
        except Exception:
            pass
        try:
            self.paramChanged.connect(self._bump_scene_asset_revision)
        except Exception:
            pass

    def refresh_node_widget(self, name: str):
        with profile_scope("graph.refresh_node_widget"):
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
            kind_key = knd.lower().replace(" ", "_")
            if kind_key in ("fx", "fx_trail"):
                base = "fx_bullet_time"
            elif kind_key in ("fx_music_effects", "music_effects", "musiceffects"):
                base = "fx_music_visualizer"
            else:
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
        # Keep 3D viewport scene ownership in sync with node renames.
        rename_forwarded = False
        try:
            gv = (
                getattr(self, "gl_view", None)
                or getattr(self, "_gl_view", None)
                or getattr(self, "glView", None)
                or getattr(self, "_glView", None)
            )
            if gv is None:
                try:
                    for view in (self.views() or []):
                        if view is None:
                            continue
                        win = view.window()
                        gv = getattr(win, "gl_view", None) if win is not None else None
                        if gv is not None:
                            break
                except Exception:
                    gv = None
            if gv is not None and hasattr(gv, "rename_scene_asset_owner"):
                gv.rename_scene_asset_owner(old_name, new_name)
                rename_forwarded = True
        except Exception:
            pass
        try:
            win = None
            for view in (self.views() or []):
                if view is None:
                    continue
                cand = view.window()
                if cand is not None:
                    win = cand
                    break
            if win is not None:
                if not rename_forwarded:
                    handler = getattr(win, "rename_scene_asset_owner", None)
                    if callable(handler):
                        handler(old_name, new_name)
                try:
                    card_map = getattr(win, "_card_by_node", None)
                    if isinstance(card_map, dict) and old_name in card_map:
                        card = card_map.pop(old_name)
                        card_map[new_name] = card
                        try:
                            setattr(card, "_node_name", new_name)
                        except Exception:
                            pass
                        try:
                            setattr(card, "_node_ref", node)
                        except Exception:
                            pass
                        title_edit = getattr(card, "_title_edit", None)
                        if title_edit is not None:
                            try:
                                title_edit.blockSignals(True)
                                title_edit.setText(new_name)
                            finally:
                                try:
                                    title_edit.blockSignals(False)
                                except Exception:
                                    pass
                except Exception:
                    pass
                try:
                    ctl = getattr(win, "_timeline_controller", None)
                    if ctl is not None:
                        ctl.sync_timeline_context()
                except Exception:
                    pass
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
                xforms = getattr(model, "_scene_xforms", None)
                if isinstance(xforms, dict) and old_name in xforms:
                    xforms[new_name] = xforms.pop(old_name)
                    setattr(model, "_scene_xforms", xforms)
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
        if kind in ("gantt_chart", "gantt chart", "gant_chart", "gant chart"):
            csv_text = ""
            try:
                from nodes.gantt_chart import spec as _gantt_spec
                exporter = getattr(_gantt_spec, "export_csv_text", None)
                if callable(exporter):
                    csv_text = str(exporter(item) or "").strip()
            except Exception:
                csv_text = ""
            if csv_text:
                return [{"node": model.name, "param": "csv", "text": csv_text}]
            return []
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

        if (data["kind"] or "").lower() in (
            "qubit_deck_controller",
            "qubit deck controller",
            "qubitdeckcontroller",
        ):
            ensure_params = [
                ("api_base", "http://127.0.0.1:8765"),
                ("button", ""),
                ("button_name", ""),
                ("button_slot", ""),
                ("action", "invoke"),
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
        return persistence.deserialize_scene(
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
        self._edge_index_by_dst = {}
        self._mark_edge_index_dirty()
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
        try:
            requested_name = str(getattr(node, "name", "") or "").strip()
            kind = str(getattr(node, "kind", "") or "node")
            unique_name = self._unique_node_name(requested_name, kind)
            if unique_name != requested_name:
                node.name = unique_name
        except Exception:
            pass

        # If the model already has a stored position (e.g., after load), prefer it.
        try:
            x, y = node.pos_xy  # may raise if not set yet
            pos = QtCore.QPointF(float(x), float(y))
        except Exception:
            pos = _as_pointf(pos)

        self._ensure_space(pos)

        # InfoCard footer plugins need scene access during card construction.
        # Attach the owner scene on the model before any card is built.
        try:
            node._graph_scene = self
        except Exception:
            pass

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
        if not bool(getattr(self, "_bulk_loading", False)):
            self._reframe_to_nodes(margin=8000.0)
        return item

    def add_edge(self, src_name, dst_name, dst_port_name=None):
        return self._add_edge_and_update_switch(src_name, dst_name, dst_port_name=dst_port_name)

    @profiled("graph.connect_edge")
    def _add_edge_and_update_switch(self, src_name, dst_name, dst_port_name=None):
        bulk = bool(getattr(self, "_bulk_loading", False))
        src = self._node_items[src_name]; dst = self._node_items[dst_name]
        edge = EdgeItem(src, dst, dst_port_name=dst_port_name)
        self._edges.append(edge); self.addItem(edge)
        self._mark_edge_index_dirty()

        dst_kind = (dst.model.kind or "").lower()
        if dst_kind in ("append", "switch"):
            if src.model.name not in dst.model.switch_inputs:
                dst.model.switch_inputs.append(src.model.name)
            if dst_kind == "switch":
                dst.model.switch_index = max(0, min(dst.model.switch_index,
                                                    max(0, len(dst.model.switch_inputs)-1)))
            if not bulk:
                self.refresh_node_widget(dst.model.name)

            # Also refresh the Append card UI if the destination is an Append/Switch node with a list
            if not bulk:
                try:
                    win = self.views()[0].window() if self.views() else None
                    if win:
                        card = getattr(win, "_card_by_node", {}).get(dst.model.name)
                        if card and hasattr(card, "refresh_append_ui_from_model"):
                            card.refresh_append_ui_from_model()
                except Exception:
                    pass

        # NEW: keep Info panel + preview live when graph changes
        if (not bulk) and self._current_output_name:
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

        if not bulk:
            try:
                self.linksChanged.emit()
            except Exception:
                pass
            self.refresh_node_widget(dst.model.name)
        return edge

    @profiled("graph.disconnect_edge")
    def _on_edge_removed(self, edge: 'EdgeItem'):
        self._mark_edge_index_dirty()
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
                        self._mark_edge_index_dirty()
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

    def delete_pin_item(self, pin):
        if pin is None:
            return
        edge = getattr(pin, "_edge", None)
        if edge is not None and hasattr(edge, "remove_pin"):
            try:
                edge.remove_pin(pin)
                return
            except Exception:
                pass
        try:
            self.removeItem(pin)
        except Exception:
            pass

    def delete_selected_nodes(self):
        selected = list(self.selectedItems())
        selected_pins = []
        for it in selected:
            if isinstance(it, NodeItem):
                self.delete_node_by_name(it.model.name)
            elif isinstance(it, CommentGroup):
                self.delete_comment_group(it)
            elif getattr(it, "__class__", type(it)).__name__ == "EdgePin":
                selected_pins.append(it)

        for it in selected_pins:
            try:
                if it.scene() is not self:
                    continue
            except Exception:
                continue
            self.delete_pin_item(it)
        try:
            self._clear_edge_click_highlight()
        except Exception:
            pass
        try:
            self._group_drag_active = False
        except Exception:
            pass

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
        if (node.kind or "").lower() in ("gantt_chart", "gantt chart", "gant_chart", "gant chart"):
            gsize = getattr(node, "_gantt_chart_size", None)
            if isinstance(gsize, (list, tuple)) and len(gsize) >= 2:
                try:
                    gw = float(gsize[0])
                    gh = float(gsize[1])
                    snap["gantt_chart_size"] = [gw, gh]
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
            if (kind or "").lower() in ("gantt_chart", "gantt chart", "gant_chart", "gant chart"):
                gsize = entry.get("gantt_chart_size")
                if isinstance(gsize, (list, tuple)) and len(gsize) >= 2:
                    try:
                        gw = float(gsize[0])
                        gh = float(gsize[1])
                        setattr(node, "_gantt_chart_size", (gw, gh))
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
            kind = (it.model.kind or "").lower()
            if kind == "switch":
                self._refresh_switch_widget(it)
            if kind in ("switch", "append", "scene", "scene_assembly", "scene_outliner"):
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
            if e.button() == QtCore.Qt.LeftButton:
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
                    self._cancel_temp_wire()
                # If no valid target, keep the temp wire attached to cursor.
                super().mouseReleaseEvent(e)
                e.accept()
                return
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
            return self._in_edges(it)

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
    def _mark_edge_index_dirty(self):
        self._edge_index_dirty = True
        try:
            self._edge_index_version = int(getattr(self, "_edge_index_version", 0)) + 1
        except Exception:
            self._edge_index_version = 0
        self._bump_scene_asset_revision()

    def _bump_scene_asset_revision(self, *_args):
        try:
            self._scene_asset_revision = int(getattr(self, "_scene_asset_revision", 0)) + 1
        except Exception:
            self._scene_asset_revision = 0

    def _rebuild_edge_index(self):
        if not bool(getattr(self, "_edge_index_dirty", True)):
            return
        by_dst = {}
        for e in getattr(self, "_edges", []):
            dst = getattr(e, "dst", None)
            if dst is None:
                continue
            if dst in by_dst:
                by_dst[dst].append(e)
            else:
                by_dst[dst] = [e]
        self._edge_index_by_dst = by_dst
        self._edge_index_dirty = False

    def _in_edges(self, it):
        self._rebuild_edge_index()
        return self._edge_index_by_dst.get(it, [])

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
        self._hotkey_shortcuts = {}
        self._workflow_load_in_progress = False
        self._update_window_title()
        self._timeline_controller = TimelineController(self)
        self._profiler_controller = ProfilerController(self)
        self._suspend_panel_layout_persist = False
        app_settings = _load_app_settings()
        self._save_layout_enabled = _coerce_bool(app_settings.get("save_layout"), True)
        self._panel_layout_master_preset = _normalize_panel_layout_preset(
            app_settings.get("panel_layout"),
            _DEFAULT_PANEL_LAYOUT_PRESET,
        )
        self._view_mode_master_preset = _normalize_view_mode_preset(
            app_settings.get("view_mode"),
            "2d",
        ) or "2d"
        self._voice_audio_mode = _normalize_voice_audio_mode(
            app_settings.get("voice_audio_mode"),
            _VOICE_AUDIO_MODE_DEFAULT,
        )
        self._voice_mic_device_index = _normalize_voice_mic_device_index(
            app_settings.get("voice_mic_device_index"),
            _VOICE_MIC_DEVICE_DEFAULT,
        )
        self._shadow_quality = _normalize_shadow_quality(
            app_settings.get("shadow_quality"),
            _SHADOW_QUALITY_DEFAULT,
        )
        self._cast_shadows_enabled = _coerce_bool(app_settings.get("cast_shadows"), True)
        self._self_shadows_enabled = _coerce_bool(app_settings.get("self_shadows"), True)
        self._two_sided_shadows_enabled = _coerce_bool(app_settings.get("two_sided_shadows"), True)
        self._ambient_light_enabled = _coerce_bool(app_settings.get("ambient_light"), True)
        self._ambient_light_strength = _normalize_ambient_light_strength(
            app_settings.get("ambient_light_strength"),
            _AMBIENT_LIGHT_STRENGTH_DEFAULT,
        )
        self._scene_skeleton_joint_names_enabled = _coerce_bool(
            app_settings.get("scene_skeleton_joint_names"),
            _SCENE_SKELETON_JOINT_NAMES_DEFAULT,
        )

        central = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        topbar = self._build_topbar()
        self._topbar = topbar
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
        self._active_scene_refresh_node = ""
        self._active_scene_refresh_timer = QtCore.QTimer(self)
        self._active_scene_refresh_timer.setSingleShot(True)
        self._active_scene_refresh_timer.setInterval(180)
        self._active_scene_refresh_timer.timeout.connect(self._refresh_active_scene_from_graph)
        self._apply_voice_audio_mode_to_scene(sync_view_settings=True)
        self._apply_voice_microphone_to_scene(sync_view_settings=True)

        set_global_llm_scale(0.5, self.scene)  # ← apply global LLM scale here
        try:
            actions.init_history_log()
        except Exception:
            pass
        
        self.view = GraphView(self.scene)
        self.gl_view = GraphGLView(self.scene)
        self._profiler_controller.attach_panel_to_view()
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
        self._fullscreen_enabled = False
        self._fullscreen_prev_state = None
        self._fullscreen_prev_geom = None
        self._fullscreen_prev_view_mode = None
        self._fullscreen_prev_split_sizes = None
        self._fullscreen_prev_info_visible = None
        self._fullscreen_prev_topbar_visible = None
        self._fullscreen_target = None
        self._fullscreen_exit_pending = False
        self._fullscreen_prev_was_max = False
        v.addWidget(self._view_splitter, 1)

        self.setCentralWidget(central)

        self._init_info_dock()
        self._set_view_mode("2d")
        try:
            self._timeline_controller.sync_timeline_context()
            self._timeline_controller.sync_timeline_menu_state()
        except Exception:
            pass
        try:
            if bool(getattr(self, "_save_layout_enabled", True)):
                self._apply_panel_layout_preset(
                    getattr(self, "_panel_layout_master_preset", None),
                    persist_global=False,
                )
                self._set_view_mode(
                    _normalize_view_mode_preset(
                        getattr(self, "_view_mode_master_preset", None),
                        "2d",
                    ) or "2d"
                )
        except Exception:
            pass

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
            self._register_shortcut("app_save", self._shortcut_save)
        except Exception:
            self._shortcut_save = None

        try:
            self._shortcut_undo = hotkeys.add_shortcut(
                self,
                "app_undo",
                "Ctrl+Z",
                lambda: actions.undo_scene_xform(self),
                context=QtCore.Qt.ApplicationShortcut,
            )
            self._register_shortcut("app_undo", self._shortcut_undo)
        except Exception:
            self._shortcut_undo = None

        try:
            self._shortcut_redo = hotkeys.add_shortcut(
                self,
                "app_redo",
                "Ctrl+Y",
                lambda: actions.redo_scene_xform(self),
                context=QtCore.Qt.ApplicationShortcut,
            )
            self._register_shortcut("app_redo", self._shortcut_redo)
        except Exception:
            self._shortcut_redo = None

        # GraphView-only shortcuts (avoid firing while typing in param line edits)
        try:
            self._shortcut_comment_group = hotkeys.add_shortcut(
                self.view,
                "comment_group",
                "C",
                lambda: actions.create_comment_group_from_window(self),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("comment_group", self._shortcut_comment_group)
        except Exception:
            self._shortcut_comment_group = None

        try:
            self._shortcut_node_view = hotkeys.add_shortcut(
                self.view,
                "node_view",
                "V",
                self._open_selected_node_view,
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("node_view", self._shortcut_node_view)
        except Exception:
            self._shortcut_node_view = None

        try:
            self._shortcut_node_menu = hotkeys.add_shortcut(
                self.view,
                "node_menu",
                "Tab",
                self._open_create_menu_from_hotkey,
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("node_menu", self._shortcut_node_menu)
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
            self._register_shortcut("view_mode_2d", self._shortcut_view_mode_2d)
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
            self._register_shortcut("view_mode_split", self._shortcut_view_mode_split)
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
            self._register_shortcut("view_mode_3d", self._shortcut_view_mode_3d)
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
            self._register_shortcut("gl_frame", self._shortcut_gl_frame)
        except Exception:
            self._shortcut_gl_frame = None

        try:
            self._shortcut_gl_reset = hotkeys.add_shortcut(
                self.gl_view,
                "gl_reset_view",
                "Shift+R",
                self._reset_gl_view,
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("gl_reset_view", self._shortcut_gl_reset)
        except Exception:
            self._shortcut_gl_reset = None

        try:
            self._shortcut_gl_wireframe = hotkeys.add_shortcut(
                self,
                "gl_wireframe_toggle",
                "W",
                self._shortcut_gl_wireframe_toggle,
                context=QtCore.Qt.ApplicationShortcut,
            )
            self._register_shortcut("gl_wireframe_toggle", self._shortcut_gl_wireframe)
        except Exception:
            self._shortcut_gl_wireframe = None

        try:
            self._shortcut_gl_grid = hotkeys.add_shortcut(
                self,
                "gl_grid_toggle",
                "G",
                self._shortcut_gl_grid_toggle,
                context=QtCore.Qt.ApplicationShortcut,
            )
            self._register_shortcut("gl_grid_toggle", self._shortcut_gl_grid)
        except Exception:
            self._shortcut_gl_grid = None

        try:
            self._shortcut_timeline_play_toggle = hotkeys.add_shortcut(
                self,
                "timeline_play_toggle",
                "Space",
                self._timeline_controller.shortcut_timeline_play_toggle_action,
                context=QtCore.Qt.ApplicationShortcut,
            )
            try:
                self._shortcut_timeline_play_toggle.setAutoRepeat(False)
            except Exception:
                pass
            self._register_shortcut("timeline_play_toggle", self._shortcut_timeline_play_toggle)
        except Exception:
            self._shortcut_timeline_play_toggle = None

        try:
            self._shortcut_timeline_set_key = hotkeys.add_shortcut(
                self,
                "timeline_set_key",
                "K",
                self._timeline_controller.shortcut_timeline_set_key_action,
                context=QtCore.Qt.ApplicationShortcut,
            )
            try:
                self._shortcut_timeline_set_key.setAutoRepeat(False)
            except Exception:
                pass
            self._register_shortcut("timeline_set_key", self._shortcut_timeline_set_key)
        except Exception:
            self._shortcut_timeline_set_key = None

        try:
            self._shortcut_fullscreen = hotkeys.add_shortcut(
                self,
                "app_fullscreen",
                "F11",
                self._toggle_fullscreen,
                context=QtCore.Qt.ApplicationShortcut,
            )
            self._register_shortcut("app_fullscreen", self._shortcut_fullscreen)
        except Exception:
            self._shortcut_fullscreen = None

        try:
            self._shortcut_gizmo_translate = hotkeys.add_shortcut(
                self.gl_view,
                "gizmo_translate",
                "T",
                lambda: self._set_gizmo_mode("translate"),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("gizmo_translate", self._shortcut_gizmo_translate)
        except Exception:
            self._shortcut_gizmo_translate = None

        try:
            self._shortcut_gizmo_rotate = hotkeys.add_shortcut(
                self.gl_view,
                "gizmo_rotate",
                "R",
                lambda: self._set_gizmo_mode("rotate"),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("gizmo_rotate", self._shortcut_gizmo_rotate)
        except Exception:
            self._shortcut_gizmo_rotate = None

        try:
            self._shortcut_gizmo_scale = hotkeys.add_shortcut(
                self.gl_view,
                "gizmo_scale",
                "E",
                lambda: self._set_gizmo_mode("scale"),
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("gizmo_scale", self._shortcut_gizmo_scale)
        except Exception:
            self._shortcut_gizmo_scale = None

        try:
            if self.gl_view is not None:
                self.gl_view._gizmo_hotkeys_active = True
        except Exception:
            pass

        try:
            self._shortcut_node_delete = hotkeys.add_shortcut(
                self.view,
                "node_delete",
                "Del",
                self._shortcut_delete_selected_action,
                context=QtCore.Qt.WidgetWithChildrenShortcut,
            )
            self._register_shortcut("node_delete", self._shortcut_node_delete)
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
            self._register_shortcut("node_copy", self._shortcut_node_copy)
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
            self._register_shortcut("node_paste", self._shortcut_node_paste)
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
            next_mode = "split"
        elif mode == "split":
            next_mode = "3d"
        else:
            next_mode = "2d"
        self._set_view_mode(next_mode)
        self._auto_save_current_view_mode_preset()

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
                if not bool(getattr(self, "_scene_view_open_in_progress", False)):
                    self._timeline_controller.sync_timeline_context()

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
        try:
            self._timeline_controller.sync_timeline_menu_state()
        except Exception:
            pass

    def _update_view_mode_button(self) -> None:
        btn = getattr(self, "_btn_3d", None)
        if btn is None:
            return
        try:
            if not hasattr(self, "_view_mode_btn_w"):
                fm = QtGui.QFontMetrics(btn.font())
                labels = ["2D View", "3D View", "2D / 3D"]
                max_w = max(fm.horizontalAdvance(t) for t in labels)
                self._view_mode_btn_w = int(max_w + 20)
            btn.setFixedWidth(int(self._view_mode_btn_w))
        except Exception:
            pass
        mode = getattr(self, "_view_mode", "2d")
        if mode == "2d":
            btn.setText("2D View")
            btn.setToolTip("2D view active")
            color = "#22c55e"
        elif mode == "3d":
            btn.setText("3D View")
            btn.setToolTip("3D view active")
            color = "#facc15"
        else:
            btn.setText("2D / 3D")
            btn.setToolTip("Split view active")
            color = "#fb923c"
        try:
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:0px;padding:6px 10px;font-weight:600;"
                "text-align:left;"
                "color:" + str(color) + ";}"
                "QPushButton:hover{background:#2b313a;}"
            )
        except Exception:
            pass

    def _set_gizmo_mode(self, mode: str) -> None:
        gv = getattr(self, "gl_view", None)
        if gv is None:
            return
        try:
            gv._xform_gizmo_mode = str(mode or "translate")
        except Exception:
            pass
        try:
            gv.update()
        except Exception:
            pass

    def _reset_gl_view(self) -> None:
        gv = getattr(self, "gl_view", None)
        if gv is None:
            return
        try:
            gv._reset_camera()
        except Exception:
            pass
        try:
            gv.update()
        except Exception:
            pass

    def _viewport_hotkey_ok(self) -> bool:
        gv = getattr(self, "gl_view", None)
        if gv is None:
            return False
        try:
            if not gv.isVisible() or not gv.isEnabled():
                return False
        except Exception:
            return False
        try:
            w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
        except Exception:
            w = None
        if w is None:
            return False
        try:
            if isinstance(
                w,
                (
                    QtWidgets.QLineEdit,
                    QtWidgets.QTextEdit,
                    QtWidgets.QPlainTextEdit,
                    QtWidgets.QSpinBox,
                    QtWidgets.QDoubleSpinBox,
                ),
            ):
                return False
        except Exception:
            pass
        while w is not None:
            if w is gv:
                return True
            w = w.parentWidget()
        return False

    def _shortcut_gl_wireframe_toggle(self) -> None:
        if not self._viewport_hotkey_ok():
            return
        gv = getattr(self, "gl_view", None)
        if gv is None:
            return
        try:
            toggle = getattr(gv, "_mgl_wireframe_toggle", None)
            if toggle is not None:
                toggle.setChecked(not bool(toggle.isChecked()))
                return
        except Exception:
            pass
        try:
            gv._on_mgl_wireframe_toggled(not bool(getattr(gv, "_mgl_wireframe", False)))
        except Exception:
            pass

    def _shortcut_gl_grid_toggle(self) -> None:
        if not self._viewport_hotkey_ok():
            return
        gv = getattr(self, "gl_view", None)
        if gv is None:
            return
        try:
            checked = not bool(getattr(gv, "_mgl_grid_visible", False))
            btn = getattr(gv, "_grid_btn", None)
            if btn is not None:
                btn.setChecked(bool(checked))
            gv._on_grid_button_toggled(bool(checked))
        except Exception:
            pass

    def _shortcut_delete_selected_action(self) -> None:
        try:
            if bool(self._timeline_controller.timeline_delete_selected_keys_action()):
                return
        except Exception:
            pass
        try:
            actions.delete_selected_nodes_from_window(self)
        except Exception:
            pass

    def _register_shortcut(self, action_id: str, shortcut) -> None:
        try:
            if shortcut is None:
                return
            self._hotkey_shortcuts[action_id] = shortcut
        except Exception:
            pass

    def _apply_hotkey_map(self, mapping: dict) -> None:
        try:
            hotkeys_config._KEYMAP_CACHE = dict(mapping or {})
        except Exception:
            pass
        for action_id, sc in (getattr(self, "_hotkey_shortcuts", {}) or {}).items():
            try:
                seq = mapping.get(action_id, hotkeys_config.DEFAULT_KEYMAP.get(action_id, ""))
                sc.setKey(QtGui.QKeySequence(str(seq or "")))
            except Exception:
                pass

    def _open_hotkeys_dialog(self) -> None:
        try:
            mapping = hotkeys_config.load_keymap()
        except Exception:
            mapping = dict(hotkeys_config.DEFAULT_KEYMAP)
        keys = list(hotkeys_config.DEFAULT_KEYMAP.keys())
        for k in mapping.keys():
            if k not in keys:
                keys.append(k)
        keys = sorted(keys)

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Hotkeys")
        dlg.resize(500, 730)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        table = QtWidgets.QTableWidget(len(keys), 2, dlg)
        table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setColumnWidth(0, 220)

        wire_actions = {"wire_add_pin", "wire_remove"}

        def _mods_to_text(mods) -> str:
            bits = []
            if mods & QtCore.Qt.ControlModifier:
                bits.append("Ctrl")
            if mods & QtCore.Qt.AltModifier:
                bits.append("Alt")
            if mods & QtCore.Qt.ShiftModifier:
                bits.append("Shift")
            if mods & QtCore.Qt.MetaModifier:
                bits.append("Meta")
            return "+".join(bits)

        def _normalize_mod_text(text: str) -> str:
            s = str(text or "").lower()
            mods = QtCore.Qt.KeyboardModifiers()
            if "ctrl" in s or "control" in s:
                mods |= QtCore.Qt.ControlModifier
            if "alt" in s:
                mods |= QtCore.Qt.AltModifier
            if "shift" in s:
                mods |= QtCore.Qt.ShiftModifier
            if "meta" in s or "cmd" in s or "command" in s:
                mods |= QtCore.Qt.MetaModifier
            return _mods_to_text(mods)

        class _MouseHotkeyEditor(QtWidgets.QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                layout = QtWidgets.QHBoxLayout(self)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(6)

                self.key_edit = QtWidgets.QLineEdit(self)
                self.key_edit.setPlaceholderText("Ctrl/Alt/Shift")
                self.key_edit.setToolTip("Modifiers only (Ctrl/Alt/Shift/Meta).")
                self.key_edit.setMaximumWidth(140)
                layout.addWidget(self.key_edit, 1)

                self.btn_combo = QtWidgets.QComboBox(self)
                self.btn_combo.addItem("Left Click", "LeftClick")
                self.btn_combo.addItem("Right Click", "RightClick")
                self.btn_combo.addItem("Middle Click", "MiddleClick")
                self.btn_combo.setMaximumWidth(120)
                layout.addWidget(self.btn_combo, 0)

            def set_value(self, spec: str) -> None:
                mods, btn = hotkeys_config.parse_mouse_binding(spec)
                self.key_edit.setText(_mods_to_text(mods))
                if btn == QtCore.Qt.RightButton:
                    self.btn_combo.setCurrentIndex(1)
                elif btn == QtCore.Qt.MiddleButton:
                    self.btn_combo.setCurrentIndex(2)
                else:
                    self.btn_combo.setCurrentIndex(0)

            def value(self) -> str:
                mod_text = _normalize_mod_text(self.key_edit.text())
                btn_text = str(self.btn_combo.currentData() or "LeftClick")
                return f"{mod_text}+{btn_text}" if mod_text else btn_text

        editors = {}
        for row, action_id in enumerate(keys):
            item = QtWidgets.QTableWidgetItem(action_id)
            table.setItem(row, 0, item)
            seq = mapping.get(action_id, hotkeys_config.DEFAULT_KEYMAP.get(action_id, ""))
            if action_id in wire_actions:
                editor = _MouseHotkeyEditor(table)
                editor.set_value(str(seq or ""))
                table.setCellWidget(row, 1, editor)
                editors[action_id] = ("mouse", editor)
            else:
                editor = QtWidgets.QKeySequenceEdit(table)
                try:
                    editor.setKeySequence(QtGui.QKeySequence(str(seq or "")))
                except Exception:
                    pass
                table.setCellWidget(row, 1, editor)
                editors[action_id] = ("key", editor)

        lay.addWidget(table, 1)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=dlg
        )
        lay.addWidget(btns, 0)

        def _save():
            new_map = {}
            for action_id, data in editors.items():
                kind, editor = data
                if kind == "mouse":
                    try:
                        seq = editor.value()
                    except Exception:
                        seq = ""
                else:
                    try:
                        seq = editor.keySequence().toString()
                    except Exception:
                        try:
                            seq = str(editor.text()).strip()
                        except Exception:
                            seq = ""
                new_map[action_id] = str(seq or "")
            try:
                path = hotkeys_config.keymap_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(new_map, indent=2), encoding="utf-8")
            except Exception:
                pass
            self._apply_hotkey_map(new_map)
            dlg.accept()

        btns.accepted.connect(_save)
        btns.rejected.connect(dlg.reject)
        dlg.exec()

    def open_3d_model(self, path: str, texture_path: str | None = None, frame: bool = True) -> None:
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
        try:
            if hasattr(gl_view, "set_gizmo_visible"):
                gl_view.set_gizmo_visible(False)
        except Exception:
            pass

        loader = getattr(gl_view, "load_model_path", None)
        if not callable(loader):
            print("[open_3d_model] gl_view.load_model_path missing or not callable", flush=True)
            return

        try:
            loader(path, texture_path, frame=frame)
            print("[open_3d_model] loader finished", flush=True)

            if frame:
                # frame after loading so zoom/center are sane
                try:
                    if hasattr(gl_view, "_on_frame_clicked"):
                        gl_view._on_frame_clicked()
                except Exception:
                    pass

        except Exception:
            print("[open_3d_model] loader error:\n" + traceback.format_exc(), flush=True)

    def open_scene_assets(self, assets, frame: bool = True) -> bool:
        import traceback
        import time

        assets = list(assets or [])
        scene_debug_enabled = bool(os.environ.get("ECHOGRAPH_SCENE_DEBUG_VERBOSE"))
        if not scene_debug_enabled:
            try:
                scene_debug_enabled = any(
                    isinstance(entry, dict) and bool(entry.get("debug_log", False))
                    for entry in assets
                )
            except Exception:
                scene_debug_enabled = False

        def _scene_log(msg: str) -> None:
            if not scene_debug_enabled:
                return
            try:
                root = Path(__file__).resolve().parent
                log_dir = root / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                with (log_dir / "scene_assets_debug.log").open("a", encoding="utf-8") as f:
                    f.write(f"{ts} {msg}\n")
            except Exception:
                pass
        fx_log_enabled = scene_debug_enabled

        def _fx_log(msg: str) -> None:
            if not fx_log_enabled:
                return
            try:
                root = Path(__file__).resolve().parent
                log_dir = root / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                with (log_dir / "fx_trail_debug.log").open("a", encoding="utf-8") as f:
                    f.write(f"{ts} {msg}\n")
            except Exception:
                pass
        try:
            cur_path = getattr(self, "_current_path", None)
        except Exception:
            cur_path = None
        try:
            glv_for_frame = getattr(self, "gl_view", None)
            current_timeline_frame = int(glv_for_frame._timeline_current_frame()) if glv_for_frame is not None else 0
        except Exception:
            current_timeline_frame = 0
        _scene_log("")
        _scene_log(
            "=== open_scene_assets start "
            + f"raw_count={len(assets)} frame_arg={bool(frame)} timeline_frame={current_timeline_frame} "
            + f"current_path={cur_path} ==="
        )
        if not assets:
            _scene_log("open_scene_assets: no assets")
            print("[open_scene_assets] no assets", flush=True)
            return False

        preview_context = None
        try:
            for entry in assets:
                if not isinstance(entry, dict):
                    continue
                ctx = entry.get("preview_context") or entry.get("__preview_context")
                if isinstance(ctx, dict):
                    preview_context = dict(ctx)
                    break
        except Exception:
            preview_context = None
        scene_source_open = False
        try:
            scene_source_open = bool(getattr(self, "_opening_scene_assets_from_scene_node", False))
        except Exception:
            scene_source_open = False
        if preview_context is None and bool(frame) and not scene_source_open:
            try:
                first = next((entry for entry in assets if isinstance(entry, dict)), {})
            except Exception:
                first = {}
            try:
                node_label = str((first or {}).get("node") or "").strip()
                kind_label = str((first or {}).get("kind") or "").strip()
                path_label = str((first or {}).get("path") or "").strip()
                if not node_label and path_label:
                    node_label = Path(path_label).stem
                raw_name = "_".join(part for part in ("preview", kind_label, node_label) if part)
                safe_name = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in raw_name).strip("_")
                preview_context = {
                    "kind": kind_label or "node_preview",
                    "node": node_label,
                    "scene_name": safe_name or "node_preview",
                }
            except Exception:
                preview_context = {"kind": "node_preview", "node": "", "scene_name": "node_preview"}
        if isinstance(preview_context, dict):
            try:
                self._active_scene_preview_context = preview_context
                self._active_scene_node = None
                timer = getattr(self, "_active_scene_refresh_timer", None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        else:
            try:
                active = getattr(self, "_active_scene_node", None)
                active_kind = str(getattr(active, "kind", "") or "").strip().lower() if active is not None else ""
                if scene_source_open or active_kind in {"scene", "scene_assembly", "scene_outliner", "modeler"} or (bool(frame) and active is None):
                    self._active_scene_preview_context = None
            except Exception:
                pass

        _scene_log(
            "open_scene_assets: pre_clean "
            + f"preview={bool(isinstance(preview_context, dict))} "
            + f"scene_source={bool(scene_source_open)}"
        )

        # Only clear selection on full scene opens. Playback refreshes (`frame=False`)
        # must preserve outliner/timeline owner context across frame swaps. Do not
        # sync the timeline here; the scene has not been loaded into the viewport yet.
        try:
            if bool(frame) and hasattr(self, "clear_scene_asset_selection"):
                _scene_log("open_scene_assets: clear_selection begin")
                self.clear_scene_asset_selection(sync_timeline=False)
                _scene_log("open_scene_assets: clear_selection done")
        except Exception:
            _scene_log("open_scene_assets: clear_selection error\n" + traceback.format_exc())
            pass

        clean = []
        visibility_map = {}
        base_dir = None
        try:
            if cur_path:
                base_dir = Path(str(cur_path)).expanduser().resolve().parent
        except Exception:
            base_dir = None
        _scene_log(f"open_scene_assets: base_dir={base_dir}")
        for idx, entry in enumerate(assets):
            if not isinstance(entry, dict):
                _scene_log(f"raw[{idx}] skip: not dict type={type(entry)}")
                continue
            node_name = (entry.get("node") or "").strip()
            path = (entry.get("path") or "").strip()
            kind = str(entry.get("kind") or "").strip().lower()
            ext_hint = str(entry.get("ext") or "").strip().lower()
            is_camera = (kind == "camera") or (ext_hint == ".camera")
            is_light = (kind == "light") or (ext_hint == ".light")
            is_fx_trail = kind == "fx_trail"
            is_anim_retarget_preview = kind == "anim_retarget_preview"
            is_groom_guides = kind in {
                "groom_guides",
                "groom guides",
                "hair_guides",
                "hair guides",
                "groom_deform",
                "groom deform",
                "groomdeform",
                "hair_deform",
                "hair deform",
            }
            is_curve = kind in {"curve", "curve_primitive", "primitive_curve"}
            target_owner = str(entry.get("target_owner") or "").strip()
            _scene_log(
                f"raw[{idx}] node={node_name!r} path={path!r} kind={kind!r} "
                f"ext={entry.get('ext')!r} visible={entry.get('visible')!r}"
            )
            if (
                not path
                and not is_camera
                and not is_light
                and not is_fx_trail
                and not is_anim_retarget_preview
                and not is_groom_guides
                and not is_curve
            ):
                _scene_log(f"raw[{idx}] drop: missing path node={node_name!r}")
                continue
            if is_fx_trail and not target_owner:
                _scene_log(f"raw[{idx}] drop: fx_trail missing target_owner node={node_name!r}")
                if bool(entry.get("debug_log", False)):
                    _fx_log(f"[shelf] drop invalid fx_trail node={node_name!r} asset={entry!r}")
                continue
            if path:
                try:
                    if not os.path.exists(path):
                        if base_dir is not None:
                            try:
                                alt = (base_dir / path).resolve()
                                _scene_log(f"raw[{idx}] resolve: path={path!r} alt={str(alt)!r} exists={alt.exists()}")
                                if alt.exists():
                                    path = str(alt)
                                else:
                                    _scene_log(f"raw[{idx}] drop: path not found after resolve")
                                    continue
                            except Exception as exc:
                                _scene_log(f"raw[{idx}] drop: resolve exception={exc!r}")
                                continue
                        else:
                            _scene_log(f"raw[{idx}] drop: path not found and no base_dir")
                            continue
                except Exception as exc:
                    _scene_log(f"raw[{idx}] drop: exists check failed err={exc!r}")
                    continue
            node_name = (entry.get("node") or "").strip()
            raw_visible = entry.get("visible")
            visible = True if raw_visible is None else bool(raw_visible)
            if node_name:
                visibility_map[node_name] = visible
            clean_entry = {
                "path": path,
                "texture": entry.get("texture") or None,
                "node": node_name,
                "kind": kind or ("camera" if is_camera else ("light" if is_light else "")),
                "ext": entry.get("ext"),
                "visible": visible,
                "xform": entry.get("xform"),
            }
            if "source_kind" in entry:
                clean_entry["source_kind"] = str(entry.get("source_kind") or "").strip()
            if isinstance(entry.get("render_proxy"), dict):
                clean_entry["render_proxy"] = dict(entry.get("render_proxy") or {})
            if "xform_offset" in entry:
                clean_entry["xform_offset"] = bool(entry.get("xform_offset"))
            if "splat_zero_pivot" in entry:
                clean_entry["splat_zero_pivot"] = bool(entry.get("splat_zero_pivot"))
            if "retime_percent" in entry or "speed_percent" in entry:
                raw_speed = entry.get("retime_percent", entry.get("speed_percent"))
                try:
                    clean_entry["retime_percent"] = max(1.0, min(1000.0, float(raw_speed)))
                except Exception:
                    pass
            if "wire_only" in entry:
                clean_entry["wire_only"] = bool(entry.get("wire_only"))
            if "volume" in entry:
                clean_entry["volume"] = bool(entry.get("volume"))
            if "fov" in entry:
                clean_entry["fov"] = entry.get("fov")
            if isinstance(entry.get("light"), dict):
                clean_entry["light"] = dict(entry.get("light") or {})
            if "aspect_width" in entry:
                clean_entry["aspect_width"] = entry.get("aspect_width")
            if "aspect_height" in entry:
                clean_entry["aspect_height"] = entry.get("aspect_height")
            if "material" in entry and isinstance(entry.get("material"), dict):
                clean_entry["material"] = dict(entry.get("material") or {})
            if isinstance(entry.get("fbx_rig_context"), dict):
                clean_entry["fbx_rig_context"] = dict(entry.get("fbx_rig_context") or {})
            if "fbx_sample_owner" in entry:
                clean_entry["fbx_sample_owner"] = str(entry.get("fbx_sample_owner") or "").strip()
            if "hidden_submeshes" in entry:
                clean_entry["hidden_submeshes"] = [
                    str(name).strip()
                    for name in (entry.get("hidden_submeshes") or [])
                    if str(name).strip()
                ]
            if isinstance(entry.get("copy_to_points"), dict):
                clean_entry["copy_to_points"] = dict(entry.get("copy_to_points") or {})
            if isinstance(entry.get("music_effects"), dict):
                clean_entry["music_effects"] = dict(entry.get("music_effects") or {})
            if isinstance(entry.get("preview_context"), dict):
                clean_entry["preview_context"] = dict(entry.get("preview_context") or {})
            elif isinstance(entry.get("__preview_context"), dict):
                clean_entry["preview_context"] = dict(entry.get("__preview_context") or {})
            if "debug_log" in entry:
                clean_entry["debug_log"] = bool(entry.get("debug_log"))
            tex_provider = entry.get("texture_provider")
            if tex_provider is not None:
                clean_entry["texture_provider"] = tex_provider
            if is_fx_trail:
                clean_entry["target_owner"] = target_owner
                clean_entry["target_owner_aliases"] = list(entry.get("target_owner_aliases") or [])
                clean_entry["instance_path"] = str(entry.get("instance_path") or "").strip()
                clean_entry["instance_source_name"] = str(entry.get("instance_source_name") or "").strip()
                if isinstance(entry.get("instance_material"), dict):
                    clean_entry["instance_material"] = dict(entry.get("instance_material") or {})
                clean_entry["instance_texture"] = str(entry.get("instance_texture") or "").strip()
                if entry.get("instance_texture_provider") is not None:
                    clean_entry["instance_texture_provider"] = entry.get("instance_texture_provider")
                if isinstance(entry.get("instance_xform"), dict):
                    clean_entry["instance_xform"] = dict(entry.get("instance_xform") or {})
                clean_entry["enabled"] = bool(entry.get("enabled", True))
                clean_entry["global_space"] = bool(entry.get("global_space", False))
                clean_entry["samples"] = int(entry.get("samples", 28) or 28)
                clean_entry["frame_step"] = int(entry.get("frame_step", 1) or 1)
                clean_entry["spawn_rate"] = float(entry.get("spawn_rate", entry.get("frame_step", 1.0)) or 1.0)
                clean_entry["substeps"] = int(entry.get("substeps", 1) or 1)
                clean_entry["lifespan"] = int(entry.get("lifespan", max(1, int(entry.get("samples", 28) or 28) * int(entry.get("frame_step", 1) or 1))) or 1)
                clean_entry["repeats"] = int(entry.get("repeats", 1) or 1)
                clean_entry["radius"] = float(entry.get("radius", 0.35) or 0.35)
                clean_entry["sides"] = int(entry.get("sides", 28) or 28)
                clean_entry["color"] = entry.get("color")
                clean_entry["line_width"] = float(entry.get("line_width", 2.0) or 2.0)
                clean_entry["profile_points"] = list(entry.get("profile_points") or [])
                clean_entry["age_scale_min"] = float(entry.get("age_scale_min", 1.0) or 1.0)
                clean_entry["age_scale_max"] = float(entry.get("age_scale_max", 1.0) or 1.0)
                clean_entry["age_scale_points"] = list(entry.get("age_scale_points") or [])
            if is_anim_retarget_preview:
                clean_entry["source_owner"] = str(entry.get("source_owner") or "").strip()
                clean_entry["target_owner"] = str(entry.get("target_owner") or "").strip()
                clean_entry["retarget_node_item"] = entry.get("retarget_node_item")
                clean_entry["retarget_node_model"] = entry.get("retarget_node_model")
                clean_entry["joint_map"] = str(entry.get("joint_map") or "{}")
                clean_entry["source_handles"] = list(entry.get("source_handles") or [])
                clean_entry["target_handles"] = list(entry.get("target_handles") or [])
                clean_entry["handle_radius"] = float(entry.get("handle_radius", 0.008) or 0.008)
                clean_entry["curve_thickness"] = float(entry.get("curve_thickness", 2.4) or 2.4)
            if is_groom_guides:
                clean_entry["guides_path"] = str(entry.get("guides_path") or "").strip()
                clean_entry["pose_cache_path"] = str(entry.get("pose_cache_path") or "").strip()
                clean_entry["sim_cache_path"] = str(entry.get("sim_cache_path") or "").strip()
                clean_entry["source_path"] = str(entry.get("source_path") or "").strip()
                clean_entry["source_owner"] = str(entry.get("source_owner") or "").strip()
                for key in ("rig_owner", "deformer_owner", "sample_owner"):
                    if key in entry:
                        clean_entry[key] = str(entry.get(key) or "").strip()
                if "sample_owner_candidates" in entry:
                    clean_entry["sample_owner_candidates"] = [
                        str(value).strip()
                        for value in (entry.get("sample_owner_candidates") or [])
                        if str(value).strip()
                    ]
                try:
                    clean_entry["guide_count"] = int(entry.get("guide_count", 0) or 0)
                except Exception:
                    clean_entry["guide_count"] = 0
                try:
                    clean_entry["points_per_curve"] = int(entry.get("points_per_curve", 0) or 0)
                except Exception:
                    clean_entry["points_per_curve"] = 0
                try:
                    clean_entry["length"] = float(entry.get("length", 0.0) or 0.0)
                except Exception:
                    clean_entry["length"] = 0.0
                def _clean_index_list(values):
                    cleaned = []
                    for idx in values or []:
                        try:
                            cleaned.append(int(idx))
                        except Exception:
                            continue
                    return cleaned
                clean_entry["root_indices"] = _clean_index_list(entry.get("root_indices") or [])
                if isinstance(entry.get("point_groups"), dict):
                    clean_entry["point_groups"] = {
                        str(group_name): _clean_index_list(indices or [])
                        for group_name, indices in (entry.get("point_groups") or {}).items()
                    }
                clean_entry["curves"] = list(entry.get("curves") or [])
                clean_entry["line_points"] = list(entry.get("line_points") or [])
                clean_entry["debug"] = dict(entry.get("debug") or {}) if isinstance(entry.get("debug"), dict) else {}
                clean_entry["guide_bindings"] = list(entry.get("guide_bindings") or [])
                clean_entry["start_curves"] = list(entry.get("start_curves") or [])
                clean_entry["bind_curves"] = list(entry.get("bind_curves") or [])
                if isinstance(entry.get("deform_rig_context"), dict):
                    clean_entry["deform_rig_context"] = dict(entry.get("deform_rig_context") or {})
                if "groom_deform_mode" in entry:
                    clean_entry["groom_deform_mode"] = str(entry.get("groom_deform_mode") or "").strip()
                if isinstance(entry.get("groom_deform"), dict):
                    clean_entry["groom_deform"] = dict(entry.get("groom_deform") or {})
                elif isinstance(entry.get("groom_deform_info"), dict):
                    clean_entry["groom_deform_info"] = dict(entry.get("groom_deform_info") or {})
                if isinstance(entry.get("groom_guide_pose"), dict):
                    clean_entry["groom_guide_pose"] = dict(entry.get("groom_guide_pose") or {})
                if isinstance(entry.get("groom_guide_sim"), dict):
                    clean_entry["groom_guide_sim"] = dict(entry.get("groom_guide_sim") or {})
                # The renderer builds animated mesh collisions from this
                # context.  Do not discard it while sanitizing Scene assets.
                if isinstance(entry.get("groom_collider"), dict):
                    clean_entry["groom_collider"] = dict(entry.get("groom_collider") or {})
                if isinstance(entry.get("groom_guide_sim_settings"), dict):
                    clean_entry["groom_guide_sim_settings"] = dict(entry.get("groom_guide_sim_settings") or {})
            if is_curve:
                clean_entry["curve_type"] = str(entry.get("curve_type") or "line").strip().lower() or "line"
                clean_entry["points"] = list(entry.get("points") or [])
                clean_entry["line_points"] = list(entry.get("line_points") or [])
                try:
                    clean_entry["point_count"] = int(entry.get("point_count", 0) or 0)
                except Exception:
                    clean_entry["point_count"] = 0
                try:
                    clean_entry["line_segment_count"] = int(entry.get("line_segment_count", 0) or 0)
                except Exception:
                    clean_entry["line_segment_count"] = 0
                try:
                    clean_entry["line_width"] = float(entry.get("line_width", 3.0) or 3.0)
                except Exception:
                    clean_entry["line_width"] = 3.0
                if "color" in entry:
                    clean_entry["color"] = entry.get("color")
            clean.append(clean_entry)
            _scene_log(f"clean[{len(clean)-1}] node={node_name!r} path={path!r} visible={visible}")
            if is_fx_trail and bool(entry.get("debug_log", False)):
                _fx_log(
                    f"[shelf] pass fx_trail node={node_name or '<none>'} target_owner={target_owner} "
                    f"instance={str(entry.get('instance_source_name') or '').strip() or str(entry.get('instance_path') or '').strip() or '<rings>'} "
                    f"global={bool(entry.get('global_space', False))} "
                    f"spawn_rate={float(entry.get('spawn_rate', entry.get('frame_step', 1.0)) or 1.0):.3f} "
                    f"substeps={int(entry.get('substeps', 1) or 1)} repeats={int(entry.get('repeats', 1) or 1)} "
                    f"aliases={list(entry.get('target_owner_aliases') or [])!r} visible={visible}"
                )

        if not clean:
            _scene_log("open_scene_assets: no valid scene assets")
            print("[open_scene_assets] no valid scene assets", flush=True)
            return False
        _scene_log(f"open_scene_assets: clean_count={len(clean)}")

        # Debounce duplicate loads (prevents repeated reload loops)
        try:
            sig = []
            for entry in clean:
                xf = entry.get("xform") or {}
                rig_ctx = entry.get("fbx_rig_context")
                collider_cfg = entry.get("groom_collider") if isinstance(entry.get("groom_collider"), dict) else {}
                collider_rig_ctx = collider_cfg.get("fbx_rig_context") if isinstance(collider_cfg, dict) else {}
                def _round3(vals, default):
                    try:
                        return tuple(round(float(v), 6) for v in (vals or default))
                    except Exception:
                        return tuple(default)
                sig.append(
                    (
                        bool(frame),
                        int(current_timeline_frame),
                        str(entry.get("kind") or ""),
                        str(entry.get("source_kind") or ""),
                        str(entry.get("ext") or ""),
                        str(entry.get("path") or ""),
                        str(entry.get("node") or ""),
                        bool(entry.get("debug_log", False)),
                        str(entry.get("source_owner") or ""),
                        str(entry.get("fbx_sample_owner") or ""),
                        str(entry.get("guides_path") or ""),
                        int(entry.get("guide_count", 0) or 0),
                        int(entry.get("points_per_curve", 0) or 0),
                        round(float(entry.get("length", 0.0) or 0.0), 6),
                        bool(isinstance(entry.get("groom_deform"), dict)),
                        str(entry.get("groom_deform_mode") or ""),
                        str(entry.get("rig_owner") or ""),
                        str(entry.get("deformer_owner") or ""),
                        str(entry.get("sample_owner") or ""),
                        repr(list(entry.get("sample_owner_candidates") or [])),
                        len(list(entry.get("guide_bindings") or [])),
                        len(list(entry.get("start_curves") or [])),
                        len(list(entry.get("bind_curves") or [])),
                        repr(
                            dict((entry.get("groom_guide_sim") or {}).get("settings") or {})
                            if isinstance(entry.get("groom_guide_sim"), dict)
                            else {}
                        ),
                        str(collider_cfg.get("node") or ""),
                        str(((collider_cfg.get("volume_mesh") or {}).get("manifest")) or ""),
                        id((collider_rig_ctx or {}).get("skeleton")) if isinstance(collider_rig_ctx, dict) else None,
                        id((collider_rig_ctx or {}).get("clip")) if isinstance(collider_rig_ctx, dict) else None,
                        bool(isinstance(entry.get("deform_rig_context"), dict)),
                        len(list(entry.get("root_indices") or [])),
                        len(list(entry.get("curves") or [])),
                        len(list(entry.get("line_points") or [])),
                        str(entry.get("curve_type") or ""),
                        int(entry.get("point_count", 0) or 0),
                        int(entry.get("line_segment_count", 0) or 0),
                        str(entry.get("target_owner") or ""),
                        str(entry.get("joint_map") or ""),
                        len(list(entry.get("source_handles") or [])),
                        len(list(entry.get("target_handles") or [])),
                        round(float(entry.get("handle_radius", 0.0) or 0.0), 6),
                        round(float(entry.get("curve_thickness", 0.0) or 0.0), 6),
                        str(entry.get("instance_path") or ""),
                        str(entry.get("instance_source_name") or ""),
                        repr(dict(entry.get("instance_material") or {})),
                        str(entry.get("instance_texture") or ""),
                        id(entry.get("instance_texture_provider")) if entry.get("instance_texture_provider") is not None else None,
                        id((rig_ctx or {}).get("skeleton")) if isinstance(rig_ctx, dict) else None,
                        id((rig_ctx or {}).get("clip")) if isinstance(rig_ctx, dict) else None,
                        repr(dict(entry.get("instance_xform") or {})),
                        repr(list(entry.get("target_owner_aliases") or [])),
                        bool(entry.get("visible", True)),
                        bool(entry.get("global_space", False)),
                        _round3((xf or {}).get("pos"), (0.0, 0.0, 0.0)),
                        _round3((xf or {}).get("rot"), (0.0, 0.0, 0.0)),
                        _round3((xf or {}).get("scl"), (1.0, 1.0, 1.0)),
                        bool(entry.get("xform_offset", False)),
                        bool(entry.get("splat_zero_pivot", False)),
                        round(float(entry.get("retime_percent", entry.get("speed_percent", 100.0)) or 100.0), 6),
                        bool(entry.get("enabled", True)),
                        int(entry.get("samples", 28) or 28),
                        int(entry.get("frame_step", 1) or 1),
                        round(float(entry.get("spawn_rate", entry.get("frame_step", 1.0)) or 1.0), 6),
                        int(entry.get("substeps", 1) or 1),
                        int(entry.get("lifespan", max(1, int(entry.get("samples", 28) or 28) * int(entry.get("frame_step", 1) or 1))) or 1),
                        int(entry.get("repeats", 1) or 1),
                        round(float(entry.get("radius", 0.35) or 0.35), 6),
                        int(entry.get("sides", 28) or 28),
                        round(float(entry.get("line_width", 2.0) or 2.0), 6),
                        repr(list(entry.get("profile_points") or [])),
                        round(float(entry.get("age_scale_min", 1.0) or 1.0), 6),
                        round(float(entry.get("age_scale_max", 1.0) or 1.0), 6),
                        repr(list(entry.get("age_scale_points") or [])),
                        repr(dict(entry.get("render_proxy") or {})),
                        repr(dict(entry.get("copy_to_points") or {})),
                        repr(dict(entry.get("music_effects") or {})),
                        repr(dict(entry.get("preview_context") or {})),
                        repr(list(entry.get("hidden_submeshes") or [])),
                        repr(dict(entry.get("light") or {})),
                        repr(entry.get("fov", None)),
                        repr(entry.get("aspect_width", None)),
                        repr(entry.get("aspect_height", None)),
                    )
                )
            sig = tuple(sorted(sig))
            now = time.time()
            last_sig = getattr(self, "_scene_assets_sig", None)
            last_ts = float(getattr(self, "_scene_assets_ts", 0.0) or 0.0)
            if sig == last_sig and (now - last_ts) < 0.5:
                _scene_log("open_scene_assets: duplicate signature skipped")
                return True
            self._scene_assets_sig = sig
            self._scene_assets_ts = now
        except Exception:
            pass

        mode = getattr(self, "_view_mode", "2d")
        try:
            target_mode = "3d" if str(mode or "").strip().lower() == "3d" else "split"
            if str(mode or "").strip().lower() == target_mode:
                _scene_log(f"open_scene_assets: set_view_mode skip already={target_mode!r}")
            else:
                _scene_log(f"open_scene_assets: set_view_mode begin mode={mode!r} target={target_mode!r}")
                self._scene_view_open_in_progress = True
                try:
                    self._set_view_mode(target_mode)
                finally:
                    self._scene_view_open_in_progress = False
                _scene_log("open_scene_assets: set_view_mode done")
        except Exception:
            try:
                self._scene_view_open_in_progress = False
            except Exception:
                pass
            _scene_log("open_scene_assets: set_view_mode error\n" + traceback.format_exc())
            raise

        gl_view = getattr(self, "gl_view", None)
        if gl_view is None:
            print("[open_scene_assets] gl_view is None", flush=True)
            return False

        def _defer_timeline_context_sync(reason: str) -> None:
            if not bool(frame):
                return

            def _sync_selected_scene_camera_view() -> None:
                glv = getattr(self, "gl_view", None)
                if glv is None:
                    return
                try:
                    selected = str(getattr(glv, "_camera_select_mode", "default") or "default").strip()
                except Exception:
                    selected = "default"
                if not selected or selected.lower() == "default":
                    return
                try:
                    cur_frame = int(glv._timeline_current_frame()) if hasattr(glv, "_timeline_current_frame") else 0
                except Exception:
                    cur_frame = 0
                try:
                    apply_selected = getattr(glv, "_timeline_apply_selected_camera_owner_frame", None)
                    if callable(apply_selected):
                        apply_selected(cur_frame)
                except Exception:
                    pass
                try:
                    sync_from_owner = getattr(glv, "_timeline_sync_selected_camera_view_from_owner", None)
                    if callable(sync_from_owner):
                        sync_from_owner(
                            selected,
                            reason=f"scene_load_{reason}",
                            frame=cur_frame,
                        )
                        return
                except Exception:
                    pass
                try:
                    sync_view = getattr(glv, "_sync_selected_scene_camera_view", None)
                    if callable(sync_view):
                        sync_view(selected)
                except Exception:
                    pass

            def _run_sync() -> None:
                try:
                    _scene_log(f"open_scene_assets: sync_timeline_context {reason} begin")
                    self._timeline_controller.sync_timeline_context(apply_current_frame=False, load_audio=False)
                    _scene_log(f"open_scene_assets: sync_timeline_context {reason} done")
                except Exception:
                    _scene_log("open_scene_assets: sync_timeline_context error\n" + traceback.format_exc())
                for delay in (0, 120, 420):
                    try:
                        QtCore.QTimer.singleShot(delay, _sync_selected_scene_camera_view)
                    except Exception:
                        _sync_selected_scene_camera_view()

            try:
                QtCore.QTimer.singleShot(0, _run_sync)
            except Exception:
                _run_sync()

        if isinstance(preview_context, dict):
            try:
                gl_view._camera_select_mode = "default"
                gl_view._camera_select_lock_enabled = False
                gl_view._camera_select_saved_default_state = None
                gl_view._scene_camera_entries = []
                gl_view._scene_camera_fov_by_owner = {}
                gl_view._scene_camera_aspect_by_owner = {}
                refresh_cameras = getattr(gl_view, "_refresh_camera_selector_dropdown", None)
                if callable(refresh_cameras):
                    refresh_cameras()
            except Exception:
                pass
            try:
                scene_name = str(preview_context.get("scene_name") or preview_context.get("name") or "").strip()
                set_ctx = getattr(gl_view, "set_timeline_scene_context", None)
                if callable(set_ctx):
                    set_ctx(
                        scene_name=scene_name or "node_preview",
                        project_path=str(cur_path) if cur_path else None,
                        owner_name=None,
                    )
            except Exception:
                pass
        preserve_owner = ""
        if not bool(frame):
            # Playback refresh: preserve current scene-owner selection/gizmo binding.
            try:
                active_scene = getattr(self, "_active_scene_node", None)
                cards = getattr(self, "_card_by_node", None)
                if isinstance(cards, dict):
                    for card in cards.values():
                        if getattr(card, "_node_ref", None) is not active_scene:
                            continue
                        if not bool(getattr(card, "_scene_outliner_user_selected", False)):
                            continue
                        preserve_owner = str(getattr(card, "_scene_selected_owner", "") or "").strip()
                        if preserve_owner:
                            break
            except Exception:
                preserve_owner = ""
            if not preserve_owner:
                try:
                    preserve_owner = str(getattr(gl_view, "_xform_gizmo_owner", "") or "").strip()
                except Exception:
                    preserve_owner = ""
        # Sync timeline context after scene load. Doing it inline here can apply
        # saved camera/owner keys while the viewport is switching scenes.
        try:
            if hasattr(gl_view, "set_gizmo_visible"):
                gl_view.set_gizmo_visible(True)
        except Exception:
            pass

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
                _scene_log(f"open_scene_assets: load_scene_assets count={len(clean)} frame={frame}")
                loader(clean, frame=frame)
                _scene_log("open_scene_assets: load_scene_assets done")
                _defer_timeline_context_sync("post_load")
                if (not bool(frame)) and preserve_owner:
                    # Re-assert selected owner after frame-swap reloads.
                    def _restore_owner_selection(owner_name=str(preserve_owner)):
                        try:
                            self.select_scene_asset(owner_name)
                        except Exception:
                            pass
                        try:
                            self._timeline_controller.sync_timeline_context()
                        except Exception:
                            pass
                    try:
                        QtCore.QTimer.singleShot(0, _restore_owner_selection)
                    except Exception:
                        _restore_owner_selection()
                return True
            except Exception:
                print("[open_scene_assets] loader error:\n" + traceback.format_exc(), flush=True)

        # fallback: load first path-backed asset only
        first = next((e for e in clean if (e.get("path") or "").strip()), None)
        if first is None:
            _scene_log("open_scene_assets: no fallback path-backed asset")
            return False
        try:
            gl_view.load_model_path(first["path"], first.get("texture"), frame=frame)
            _defer_timeline_context_sync("fallback_post_load")
            if frame and hasattr(gl_view, "_on_frame_clicked"):
                gl_view._on_frame_clicked()
            return True
        except Exception:
            print("[open_scene_assets] fallback failed:\n" + traceback.format_exc(), flush=True)
        return False

    def set_scene_asset_visible(self, owner: str, visible: bool) -> None:
        gl_view = getattr(self, "gl_view", None)
        if gl_view is None:
            return
        handler = getattr(gl_view, "set_scene_asset_visible", None)
        if callable(handler):
            handler(owner, visible)

    def rename_scene_asset_owner(self, old_name: str, new_name: str) -> None:
        gl_view = getattr(self, "gl_view", None)
        if gl_view is not None:
            handler = getattr(gl_view, "rename_scene_asset_owner", None)
            if callable(handler):
                handler(old_name, new_name)
        try:
            self._timeline_controller.sync_timeline_context()
            self._timeline_controller.sync_timeline_menu_state()
        except Exception:
            pass

    def select_scene_asset(self, owner: str) -> None:
        owner = (owner or "").strip()
        if not owner:
            return
        owner_l = owner.lower()

        for card in (getattr(self, "_card_by_node", {}) or {}).values():
            outliner = getattr(card, "_scene_outliner_widget", None)
            if outliner is None:
                continue
            try:
                for i in range(outliner.count()):
                    it = outliner.item(i)
                    if it is None:
                        continue
                    item_owner = str(it.data(QtCore.Qt.UserRole) or "").strip()
                    if item_owner.lower() == owner_l:
                        outliner.setCurrentRow(i)
                        outliner.scrollToItem(it)
                        # Keep logical owner selection in sync even if row-change
                        # signals were temporarily blocked during a refresh.
                        try:
                            card._scene_selected_owner = item_owner or owner
                            card._scene_selected_kind = str(it.data(QtCore.Qt.UserRole + 1) or "").strip().lower() or None
                            card._scene_outliner_user_selected = True
                        except Exception:
                            pass
                        try:
                            panel = getattr(card, "_xform_panel", None)
                            if panel is not None:
                                panel.setEnabled(True)
                        except Exception:
                            pass
                        try:
                            self._timeline_controller.sync_timeline_context()
                        except Exception:
                            pass
                        return
            except Exception:
                pass

    def clear_scene_asset_selection(self, *, sync_timeline: bool = True) -> None:
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
                    if hasattr(gl_view, "set_scene_asset_uv_overlay"):
                        gl_view.set_scene_asset_uv_overlay(None)
                except Exception:
                    pass
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
        if bool(sync_timeline):
            try:
                self._timeline_controller.sync_timeline_context()
            except Exception:
                pass

    def _reset_viewport_scene_state_for_workflow_load(self) -> None:
        # Prevent per-owner xforms/gizmo state from leaking across workflow files.
        try:
            self.clear_scene_asset_selection(sync_timeline=False)
        except Exception:
            pass
        try:
            self._active_scene_node = None
            self._active_scene_preview_context = None
            timer = getattr(self, "_active_scene_refresh_timer", None)
            if timer is not None:
                timer.stop()
        except Exception:
            pass
        try:
            self._scene_assets_sig = None
            self._scene_assets_ts = 0.0
        except Exception:
            pass
        try:
            gl_view = getattr(self, "gl_view", None)
            if gl_view is None:
                return
            clear_fn = getattr(gl_view, "_clear_scene_asset_state", None)
            if callable(clear_fn):
                clear_fn()
            else:
                # Fallback for older GL view variants.
                for name in (
                    "_mgl_scene_visibility",
                    "_mgl_scene_splats",
                    "_mgl_scene_bounds_by_owner",
                    "_mgl_scene_mesh_bounds_by_owner",
                    "_mgl_scene_splats_world",
                    "_mgl_scene_splats_bounds_local",
                    "_mgl_scene_splat_bounds_by_owner",
                    "_mgl_scene_xforms_by_owner",
                    "_mgl_scene_splat_xforms_by_owner",
                    "_mgl_scene_xform_offset_by_owner",
                    "_mgl_scene_pivot_local_by_owner",
                ):
                    try:
                        setattr(gl_view, name, {})
                    except Exception:
                        pass
                try:
                    gl_view._xform_gizmo_owner = None
                    gl_view._xform_gizmo_owner_kind = None
                    gl_view._xform_gizmo_pos_locked = False
                    gl_view._xform_gizmo_pos = (0.0, 0.0, 0.0)
                except Exception:
                    pass
            try:
                gl_view.update()
            except Exception:
                pass
        except Exception:
            pass

    def update_scene_asset_xform(self, owner: str) -> None:
        owner = (owner or "").strip()
        if not owner:
            return
        gl_view = getattr(self, "gl_view", None)
        xf = None
        xf_from_explicit_cache = False

        def _map_lookup_casefold(mapping, key: str):
            if not isinstance(mapping, dict):
                return None
            if key in mapping:
                return mapping.get(key)
            lk = str(key or "").strip().lower()
            for k, v in mapping.items():
                try:
                    if str(k).strip().lower() == lk:
                        return v
                except Exception:
                    continue
            return None

        def _map_has_casefold(mapping, key: str) -> bool:
            if not isinstance(mapping, dict):
                return False
            if key in mapping:
                return True
            lk = str(key or "").strip().lower()
            for k in mapping.keys():
                try:
                    if str(k).strip().lower() == lk:
                        return True
                except Exception:
                    continue
            return False

        def _norm_triplet(values, default):
            seq = values if isinstance(values, (list, tuple)) else default
            try:
                return (
                    float(seq[0]),
                    float(seq[1]),
                    float(seq[2]),
                )
            except Exception:
                return (
                    float(default[0]),
                    float(default[1]),
                    float(default[2]),
                )

        def _normalize_xf(raw):
            if not isinstance(raw, dict):
                return None
            return {
                "pos": _norm_triplet(raw.get("pos", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
                "rot": _norm_triplet(raw.get("rot", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
                "scl": _norm_triplet(raw.get("scl", (1.0, 1.0, 1.0)), (1.0, 1.0, 1.0)),
            }

        def _xf_is_identity(raw) -> bool:
            norm = _normalize_xf(raw)
            if not isinstance(norm, dict):
                return False
            try:
                pos = norm.get("pos", (0.0, 0.0, 0.0))
                rot = norm.get("rot", (0.0, 0.0, 0.0))
                scl = norm.get("scl", (1.0, 1.0, 1.0))
                return (
                    all(abs(float(v)) < 1e-6 for v in pos)
                    and all(abs(float(v)) < 1e-6 for v in rot)
                    and all(abs(float(v) - 1.0) < 1e-6 for v in scl)
                )
            except Exception:
                return False

        try:
            if gl_view is not None:
                renderer = getattr(gl_view, "_mgl_renderer", None) or gl_view
                sources = (renderer, gl_view)

                def _first_dict_attr(attr_name: str):
                    for src in sources:
                        try:
                            m = getattr(src, attr_name, None)
                        except Exception:
                            m = None
                        if isinstance(m, dict):
                            return m
                    return None

                splat_live_map = _first_dict_attr("_mgl_scene_splats")
                splat_world_map = _first_dict_attr("_mgl_scene_splats_world")
                is_splat = _map_has_casefold(splat_live_map, owner) or _map_has_casefold(splat_world_map, owner)

                explicit_splat_xf = _map_lookup_casefold(_first_dict_attr("_mgl_scene_splat_xforms_by_owner"), owner)
                explicit_mesh_xf = _map_lookup_casefold(_first_dict_attr("_mgl_scene_xforms_by_owner"), owner)
                chosen_xf = None
                if is_splat and isinstance(explicit_splat_xf, dict):
                    chosen_xf = explicit_splat_xf
                elif (not is_splat) and isinstance(explicit_mesh_xf, dict):
                    chosen_xf = explicit_mesh_xf
                elif isinstance(explicit_splat_xf, dict) and not isinstance(explicit_mesh_xf, dict):
                    chosen_xf = explicit_splat_xf
                    is_splat = True
                elif isinstance(explicit_mesh_xf, dict):
                    chosen_xf = explicit_mesh_xf
                elif isinstance(explicit_splat_xf, dict):
                    chosen_xf = explicit_splat_xf
                    is_splat = True

                if isinstance(chosen_xf, dict):
                    xf = _normalize_xf(chosen_xf)
                    xf_from_explicit_cache = True

                getf = None
                if xf is None:
                    getf = (
                        getattr(renderer, "_mgl_get_scene_splat_xform", None)
                        if is_splat
                        else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                    )
                if callable(getf):
                    xf = _normalize_xf(getf(owner))
        except Exception:
            xf = None
            xf_from_explicit_cache = False
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
                        item_owner = str(it.data(QtCore.Qt.UserRole) or "").strip()
                        if item_owner.lower() == owner_l:
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
                store_key = str(owner)
                prev_saved_xf = None
                owner_l = owner.lower()
                for k, v in xforms.items():
                    try:
                        if str(k).strip().lower() == owner_l:
                            store_key = str(k)
                            prev_saved_xf = v
                            break
                    except Exception:
                        continue
                if prev_saved_xf is None:
                    prev_saved_xf = xforms.get(store_key)
                # Avoid clobbering a good saved xform with transient identity from getter fallback.
                if (
                    _xf_is_identity(xf)
                    and not bool(xf_from_explicit_cache)
                    and isinstance(prev_saved_xf, dict)
                    and (not _xf_is_identity(prev_saved_xf))
                ):
                    continue
                xforms[store_key] = {
                    "pos": list(xf.get("pos", (0.0, 0.0, 0.0))),
                    "rot": list(xf.get("rot", (0.0, 0.0, 0.0))),
                    "scl": list(xf.get("scl", (1.0, 1.0, 1.0))),
                }
                setattr(node, "_scene_xforms", xforms)
                try:
                    selected_owner = str(getattr(card, "_scene_selected_owner", "") or "").strip()
                    if selected_owner.lower() == owner_l:
                        prev_updating = bool(getattr(card, "_xform_updating", False))
                        card._xform_updating = True
                        try:
                            for attr_name, values in (
                                ("_xform_pos", xf.get("pos", (0.0, 0.0, 0.0))),
                                ("_xform_rot", xf.get("rot", (0.0, 0.0, 0.0))),
                                ("_xform_scl", xf.get("scl", (1.0, 1.0, 1.0))),
                            ):
                                spins = getattr(card, attr_name, None)
                                if not isinstance(spins, (list, tuple)):
                                    continue
                                for spin, value in zip(spins, values):
                                    if spin is None:
                                        continue
                                    try:
                                        spin.blockSignals(True)
                                        spin.setValue(float(value))
                                    finally:
                                        try:
                                            spin.blockSignals(False)
                                        except Exception:
                                            pass
                        finally:
                            card._xform_updating = prev_updating
                except Exception:
                    pass
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
                            scene.remove_by_tag("scene-volume")
                            scene.remove_by_tag("scene-camera")
                            scene.remove_by_tag("scene-light")

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

            try:
                if hasattr(self.gl_view, "set_gizmo_visible"):
                    self.gl_view.set_gizmo_visible(False)
            except Exception:
                pass
            self.gl_view.set_splats(splats)
            if dbg:
                print("[SPLAT] set_splats done", flush=True)
        except Exception:
            print("[SPLAT] ERROR:\n", traceback.format_exc(), flush=True)

    def _maybe_show_recent_dialog(self):
        recents = [p for p in getattr(self, "_recent_files", []) if p]
        if not recents:
            return
        self._open_recent_dialog()

    def _open_recent_dialog(self):
        recents = [p for p in getattr(self, "_recent_files", []) if p]
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
        bar.setStyleSheet(
            "#TopBar{background:#20242b;border-bottom:1px solid #333;}"
            "QPushButton{background:transparent;border:0px;padding:1px 6px;font-weight:600;color:#e5e7eb;border-radius:2px;}"
            "QPushButton:hover{background:#2b313a;}"
            "QToolButton{background:transparent;border:0px;padding:1px 6px;color:#e5e7eb;border-radius:2px;}"
            "QToolButton:hover{background:#2b313a;}"
            "QToolButton::menu-indicator{image:none;width:0px;height:0px;}"
            "QToolButton[active=\"true\"]{background:#1f7a45;}"
            "QToolButton#DebugButton[debugActive=\"true\"]{background:#7f1d1d;color:#fecaca;}"
            "QToolButton#DebugButton[debugActive=\"true\"]:hover{background:#991b1b;}"
        )
        bar.setFixedHeight(36)
        h = QtWidgets.QHBoxLayout(bar); h.setContentsMargins(8,4,8,4); h.setSpacing(1)

        file_btn = QtWidgets.QToolButton(bar)
        file_btn.setObjectName("FileButton")
        file_btn.setText("File")
        file_btn.setCursor(QtCore.Qt.PointingHandCursor)
        file_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        file_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        file_btn.setFixedHeight(22)
        file_btn.setStyleSheet(
            "QToolButton#FileButton{border-radius:2px;text-align:center;}"
        )

        file_menu = QtWidgets.QMenu(file_btn)
        file_menu.setObjectName("FileMenu")
        file_menu.setStyleSheet(
            "#FileMenu{background:#1b2026;border:1px solid #333;padding:0px;}"
        )

        file_panel = QtWidgets.QFrame(file_menu)
        file_panel.setObjectName("FilePanel")
        file_panel.setFixedWidth(101)
        file_panel.setStyleSheet(
            "#FilePanel{background:#1b2026;border:0px;border-radius:6px;}"
            "#FilePanel QPushButton{color:#e5e7eb;background:transparent;border:0px;padding:0px 6px;text-align:left;}"
            "#FilePanel QPushButton:hover{color:#e5e7eb;background:#1f7a45;}"
        )
        file_layout = QtWidgets.QVBoxLayout(file_panel)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(0)

        file_new = QtWidgets.QPushButton("New", file_panel)
        file_new.setToolTip("Launch a fresh EchoGraph window")
        file_new.setFixedHeight(22)
        file_new.setCursor(QtCore.Qt.PointingHandCursor)
        file_new.setFlat(True)
        file_new.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_new.clicked.connect(lambda: self._run_menu_action(self._launch_new_instance, "_file_btn"))
        file_layout.addWidget(file_new, 0)

        file_open = QtWidgets.QPushButton("Open", file_panel)
        file_open.setToolTip("Load a graph from a .json file")
        file_open.setFixedHeight(22)
        file_open.setCursor(QtCore.Qt.PointingHandCursor)
        file_open.setFlat(True)
        file_open.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_open.clicked.connect(lambda: self._run_menu_action(self._open_graph, "_file_btn"))
        file_layout.addWidget(file_open, 0)

        file_recent = QtWidgets.QPushButton("Open Recent", file_panel)
        file_recent.setToolTip("Open a recent workflow")
        file_recent.setFixedHeight(22)
        file_recent.setCursor(QtCore.Qt.PointingHandCursor)
        file_recent.setFlat(True)
        file_recent.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_recent.clicked.connect(lambda: self._run_menu_action(self._open_recent_dialog, "_file_btn"))
        file_layout.addWidget(file_recent, 0)

        file_save = QtWidgets.QPushButton("Save", file_panel)
        file_save.setToolTip("Save to the last opened/exported .json (Save)")
        file_save.setFixedHeight(22)
        file_save.setCursor(QtCore.Qt.PointingHandCursor)
        file_save.setFlat(True)
        file_save.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_save.clicked.connect(lambda: self._run_menu_action(self._save_graph, "_file_btn"))
        file_layout.addWidget(file_save, 0)

        file_export = QtWidgets.QPushButton("Save As", file_panel)
        file_export.setToolTip("Save current graph to a new .json (Save As)")
        file_export.setFixedHeight(22)
        file_export.setCursor(QtCore.Qt.PointingHandCursor)
        file_export.setFlat(True)
        file_export.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_export.clicked.connect(lambda: self._run_menu_action(self._export_graph, "_file_btn"))
        file_layout.addWidget(file_export, 0)

        file_action = QtWidgets.QWidgetAction(file_menu)
        file_action.setDefaultWidget(file_panel)
        file_menu.addAction(file_action)

        file_menu.aboutToShow.connect(lambda: self._set_file_menu_active(True))
        file_menu.aboutToHide.connect(lambda: self._set_file_menu_active(False))
        file_btn.setMenu(file_menu)
        h.addWidget(file_btn, 0)
        self._file_btn = file_btn

        create_btn = QtWidgets.QToolButton(bar)
        create_btn.setObjectName("CreateButton")
        create_btn.setText("Create")
        create_btn.setCursor(QtCore.Qt.PointingHandCursor)
        create_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        create_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        create_btn.setFixedHeight(22)
        create_btn.setStyleSheet(
            "QToolButton#CreateButton{border-radius:2px;text-align:center;}"
        )

        create_menu = QtWidgets.QMenu(create_btn)
        create_menu.setObjectName("CreateMenu")
        create_menu.setStyleSheet(
            "#CreateMenu{background:#1b2026;border:1px solid #333;padding:0px;}"
        )

        create_panel = QtWidgets.QFrame(create_menu)
        create_panel.setObjectName("CreatePanel")
        create_panel.setFixedWidth(84)
        create_panel.setStyleSheet(
            "#CreatePanel{background:#1b2026;border:0px;border-radius:6px;}"
            "#CreatePanel QPushButton{color:#e5e7eb;background:transparent;border:0px;padding:0px 6px;text-align:left;}"
            "#CreatePanel QPushButton:hover{color:#e5e7eb;background:#1f7a45;}"
        )
        create_layout = QtWidgets.QVBoxLayout(create_panel)
        create_layout.setContentsMargins(0, 0, 0, 0)
        create_layout.setSpacing(0)

        create_node = QtWidgets.QPushButton("Node", create_panel)
        create_node.setToolTip("Create a new node with type & params")
        create_node.setFixedHeight(22)
        create_node.setCursor(QtCore.Qt.PointingHandCursor)
        create_node.setFlat(True)
        create_node.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        create_node.clicked.connect(self._create_node_interactive)
        create_layout.addWidget(create_node, 0)

        create_action = QtWidgets.QWidgetAction(create_menu)
        create_action.setDefaultWidget(create_panel)
        create_menu.addAction(create_action)

        create_menu.aboutToShow.connect(lambda: self._set_create_menu_active(True))
        create_menu.aboutToHide.connect(lambda: self._set_create_menu_active(False))
        create_btn.setMenu(create_menu)
        h.addWidget(create_btn, 0)
        self._create_btn = create_btn

        build_timeline_panels_menu(self, bar, h)

        settings_btn = QtWidgets.QToolButton(bar)
        settings_btn.setObjectName("SettingsButton")
        settings_btn.setText("Settings")
        settings_btn.setCursor(QtCore.Qt.PointingHandCursor)
        settings_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        settings_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        settings_btn.setFixedHeight(22)
        settings_btn.setStyleSheet(
            "QToolButton#SettingsButton{border-radius:2px;text-align:center;}"
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
            "#SettingsPanel QPushButton#WireframeColorSwatch{border:1px solid #4b5563;border-radius:3px;padding:0px;min-width:18px;max-width:18px;min-height:18px;max-height:18px;}"
            "#SettingsPanel QPushButton#WireframeColorSwatch:hover{border-color:#cbd5e1;}"
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
        grid.addWidget(QtWidgets.QLabel("Local Server Scale"), 0, 0)
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
        self._fly_speed_mult = float(getattr(self, "_fly_speed_mult", 1.0))

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

        self._fly_speed_value_lbl = QtWidgets.QLabel(f"{self._fly_speed_mult:.2f}x")
        self._fly_speed_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._fly_speed_slider.setMinimum(10); self._fly_speed_slider.setMaximum(1000)
        self._fly_speed_slider.setSingleStep(1); self._fly_speed_slider.setPageStep(10)
        self._fly_speed_slider.setFixedWidth(160)
        self._fly_speed_slider.setValue(int(round(self._fly_speed_mult * 100.0)))
        self._fly_speed_slider.valueChanged.connect(self._on_fly_speed_mult_changed)
        grid.addWidget(QtWidgets.QLabel("Fly Speed"), 5, 0)
        grid.addWidget(self._fly_speed_slider, 5, 1)
        grid.addWidget(self._fly_speed_value_lbl, 5, 2)

        wire_color_label = QtWidgets.QLabel("Wire Color")
        self._wireframe_color_btn = QtWidgets.QPushButton(panel)
        self._wireframe_color_btn.setObjectName("WireframeColorSwatch")
        self._wireframe_color_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._wireframe_color_btn.setToolTip("Pick wireframe color")
        self._wireframe_color_btn.clicked.connect(self._pick_wireframe_color)
        grid.addWidget(wire_color_label, 6, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._wireframe_color_btn, 6, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._update_wireframe_color_swatch()

        self._shadow_quality = _normalize_shadow_quality(getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT))
        shadow_quality_label = QtWidgets.QLabel("Shadow Quality")
        self._shadow_quality_combo = QtWidgets.QComboBox()
        self._shadow_quality_combo.setMinimumWidth(120)
        self._shadow_quality_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:2px 8px;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        for key in ("low", "medium", "high", "ultra"):
            self._shadow_quality_combo.addItem(_SHADOW_QUALITY_LABELS.get(key, key.title()), key)
        qidx = self._shadow_quality_combo.findData(self._shadow_quality)
        self._shadow_quality_combo.setCurrentIndex(qidx if qidx >= 0 else 0)
        self._shadow_quality_combo.currentIndexChanged.connect(self._on_shadow_quality_changed)
        grid.addWidget(shadow_quality_label, 7, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._shadow_quality_combo, 7, 1, 1, 1, QtCore.Qt.AlignVCenter)

        cast_shadows_label = QtWidgets.QLabel("Cast Shadows")
        self._cast_shadows_toggle = QtWidgets.QCheckBox()
        self._cast_shadows_toggle.setChecked(bool(getattr(self, "_cast_shadows_enabled", True)))
        self._cast_shadows_toggle.setToolTip("Enable meshes and splats casting shadows into the scene.")
        self._cast_shadows_toggle.toggled.connect(self._on_cast_shadows_toggled)
        grid.addWidget(cast_shadows_label, 8, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._cast_shadows_toggle, 8, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        self_shadows_label = QtWidgets.QLabel("Self Shadows")
        self._self_shadows_toggle = QtWidgets.QCheckBox()
        self._self_shadows_toggle.setChecked(bool(getattr(self, "_self_shadows_enabled", True)))
        self._self_shadows_toggle.setToolTip(
            "Allow a mesh to receive its own shadow. Turn off to keep shadows from other meshes while removing self-shadowing."
        )
        self._self_shadows_toggle.toggled.connect(self._on_self_shadows_toggled)
        grid.addWidget(self_shadows_label, 9, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._self_shadows_toggle, 9, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        ambient_light_label = QtWidgets.QLabel("Ambient Light")
        self._ambient_light_toggle = QtWidgets.QCheckBox()
        self._ambient_light_toggle.setChecked(bool(getattr(self, "_ambient_light_enabled", True)))
        self._ambient_light_toggle.setToolTip(
            "Enable the renderer's base fill light. Turn off to see directional light and shadows without ambient lift."
        )
        self._ambient_light_toggle.toggled.connect(self._on_ambient_light_toggled)
        grid.addWidget(ambient_light_label, 10, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._ambient_light_toggle, 10, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        two_sided_shadows_label = QtWidgets.QLabel("2-Sided Shadows")
        self._two_sided_shadows_toggle = QtWidgets.QCheckBox()
        self._two_sided_shadows_toggle.setChecked(bool(getattr(self, "_two_sided_shadows_enabled", True)))
        self._two_sided_shadows_toggle.setToolTip(
            "Cast shadows from both front and back faces so closed or thin geometry blocks directional light."
        )
        self._two_sided_shadows_toggle.toggled.connect(self._on_two_sided_shadows_toggled)
        grid.addWidget(two_sided_shadows_label, 11, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(
            self._two_sided_shadows_toggle,
            11,
            1,
            1,
            1,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
        )

        self._splat_log_enabled = bool(getattr(self, "_splat_log_enabled", False))
        splat_log_label = QtWidgets.QLabel("Debug Log")
        self._splat_log_toggle = QtWidgets.QCheckBox()
        self._splat_log_toggle.setChecked(self._splat_log_enabled)
        self._splat_log_toggle.toggled.connect(self._on_splat_log_toggled)
        grid.addWidget(splat_log_label, 12, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._splat_log_toggle, 12, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        joint_names_label = QtWidgets.QLabel("Joint Names")
        self._scene_skeleton_joint_names_toggle = QtWidgets.QCheckBox()
        self._scene_skeleton_joint_names_toggle.setChecked(
            bool(getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT))
        )
        self._scene_skeleton_joint_names_toggle.setToolTip("Show selected skeleton joint names in the 3D view")
        self._scene_skeleton_joint_names_toggle.toggled.connect(self._on_scene_skeleton_joint_names_toggled)
        grid.addWidget(joint_names_label, 13, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(
            self._scene_skeleton_joint_names_toggle,
            13,
            1,
            1,
            1,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
        )

        save_layout_label = QtWidgets.QLabel("Auto Save Layout")
        self._save_layout_toggle = QtWidgets.QCheckBox()
        self._save_layout_toggle.setChecked(bool(getattr(self, "_save_layout_enabled", True)))
        self._save_layout_toggle.setToolTip("Automatically save panel visibility and view mode as the global default layout")
        self._save_layout_toggle.toggled.connect(self._on_save_layout_toggled)
        grid.addWidget(save_layout_label, 14, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._save_layout_toggle, 14, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        voice_audio_label = QtWidgets.QLabel("Turn-Taking Audio")
        self._voice_audio_toggle = QtWidgets.QCheckBox()
        self._voice_audio_toggle.setChecked(self._voice_audio_mode_is_turn_taking())
        self._voice_audio_toggle.setToolTip(
            "On = turn-taking (pause mic while another actor speaks). Off = bilateral mic+speaker."
        )
        self._voice_audio_toggle.toggled.connect(self._on_voice_audio_mode_toggled)
        grid.addWidget(voice_audio_label, 15, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._voice_audio_toggle, 15, 1, 1, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        mic_label = QtWidgets.QLabel("Microphone")
        self._voice_mic_combo = QtWidgets.QComboBox()
        self._voice_mic_combo.setMinimumWidth(220)
        self._voice_mic_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:2px 8px;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#1e3a8a;}"
        )
        self._voice_mic_combo.currentIndexChanged.connect(self._on_voice_microphone_changed)
        grid.addWidget(mic_label, 16, 0, 1, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(self._voice_mic_combo, 16, 1, 1, 2, QtCore.Qt.AlignVCenter)
        self._refresh_voice_microphone_options()

        panel_action = QtWidgets.QWidgetAction(settings_menu)
        panel_action.setDefaultWidget(panel)
        settings_menu.addAction(panel_action)

        settings_menu.aboutToShow.connect(lambda: self._set_settings_menu_active(True))
        settings_menu.aboutToHide.connect(lambda: self._set_settings_menu_active(False))
        settings_btn.setMenu(settings_menu)
        self._settings_btn = settings_btn
        h.addWidget(settings_btn, 0)

        hotkeys_btn = QtWidgets.QToolButton(bar)
        hotkeys_btn.setObjectName("HotkeysButton")
        hotkeys_btn.setText("Hotkeys")
        hotkeys_btn.setCursor(QtCore.Qt.PointingHandCursor)
        hotkeys_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        hotkeys_btn.setFixedHeight(22)
        hotkeys_btn.setStyleSheet(
            "QToolButton#HotkeysButton{border-radius:2px;text-align:center;}"
        )
        hotkeys_btn.clicked.connect(self._open_hotkeys_dialog)
        h.addWidget(hotkeys_btn, 0)

        help_btn = QtWidgets.QPushButton("Help", bar)
        help_btn.setToolTip("Open the Qubit manual")
        help_btn.setFixedHeight(22)
        help_btn.clicked.connect(self._open_help_docs)
        h.addWidget(help_btn, 0)

        btn_frame = QtWidgets.QPushButton(bar)
        btn_frame.setToolTip("Fit view to all nodes")
        btn_frame.setFixedHeight(22)
        try:
            frame_icon = QtGui.QIcon(str(script_dir() / "icons" / "Frame_Icon.png"))
            if not frame_icon.isNull():
                btn_frame.setIcon(frame_icon)
                btn_frame.setIconSize(QtCore.QSize(16, 16))
                btn_frame.setText("")
            else:
                btn_frame.setText("Frame")
        except Exception:
            btn_frame.setText("Frame")
        btn_frame.clicked.connect(self._frame_all_nodes)
        h.addWidget(btn_frame, 0)

        debug_btn = self._build_topbar_debug_button(bar)
        h.addWidget(debug_btn, 0)

        self._btn_3d = QtWidgets.QPushButton("3D View", bar)
        self._btn_3d.setToolTip("Switch to 3D viewport")
        self._btn_3d.setFixedHeight(22)
        self._btn_3d.clicked.connect(self._cycle_view_mode)
        h.addWidget(self._btn_3d, 0)

        h.addStretch(1)   # ← stretch AFTER the settings block to keep it left
        return bar

    def _debug_icon(self, active: bool = False) -> QtGui.QIcon:
        cache_name = "_debug_icon_active" if active else "_debug_icon_normal"
        cached = getattr(self, cache_name, None)
        if isinstance(cached, QtGui.QIcon) and not cached.isNull():
            return cached
        try:
            path = script_dir() / "icons" / "debug_002_Icon_s.png"
            pix = QtGui.QPixmap(str(path))
            if pix.isNull():
                return QtGui.QIcon()
            if active:
                image = pix.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
                for y in range(image.height()):
                    for x in range(image.width()):
                        color = image.pixelColor(x, y)
                        if color.alpha() <= 0:
                            continue
                        color.setRed(239)
                        color.setGreen(68)
                        color.setBlue(68)
                        image.setPixelColor(x, y, color)
                icon = QtGui.QIcon(QtGui.QPixmap.fromImage(image))
            else:
                icon = QtGui.QIcon(pix)
            setattr(self, cache_name, icon)
            return icon
        except Exception:
            return QtGui.QIcon()

    def _build_topbar_debug_button(self, parent) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton(parent)
        btn.setObjectName("DebugButton")
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        btn.setFixedHeight(22)
        btn.setIconSize(QtCore.QSize(16, 16))
        btn.setStyleSheet(
            "QToolButton#DebugButton{background:transparent;border:0px;padding:1px 6px;color:#e5e7eb;border-radius:2px;text-align:center;}"
            "QToolButton#DebugButton:hover{background:#2b313a;}"
            "QToolButton#DebugButton::menu-indicator{image:none;width:0px;height:0px;}"
            "QToolButton#DebugButton[active=\"true\"]{background:#1f7a45;}"
            "QToolButton#DebugButton[debugActive=\"true\"]{background:#7f1d1d;color:#fecaca;}"
            "QToolButton#DebugButton[debugActive=\"true\"]:hover{background:#991b1b;}"
        )

        menu = QtWidgets.QMenu(btn)
        menu.setObjectName("DebugMenu")
        menu.setStyleSheet(
            "#DebugMenu{background:#1b2026;color:#e5e7eb;border:1px solid #333;padding:4px;}"
            "#DebugMenu::item{background:transparent;color:#e5e7eb;padding:5px 22px 5px 24px;}"
            "#DebugMenu::item:selected{background:#14532d;color:#dcfce7;}"
            "#DebugMenu::item:checked{color:#bbf7d0;}"
            "#DebugMenu::indicator{width:13px;height:13px;}"
        )

        open_action = QAction("Debug Path Location", menu)
        open_action.triggered.connect(lambda _checked=False: self._run_menu_action(self._open_debug_path_location, "_debug_btn"))
        menu.addAction(open_action)

        echo_action = QAction("EchoGraph Log", menu)
        echo_action.setCheckable(True)
        echo_action.triggered.connect(self._on_echo_log_action_triggered)
        menu.addAction(echo_action)

        viewport_action = QAction("Enable Viewport Debug", menu)
        viewport_action.setCheckable(True)
        viewport_action.triggered.connect(self._on_viewport_debug_action_triggered)
        menu.addAction(viewport_action)

        menu.aboutToShow.connect(self._sync_debug_menu_state)
        menu.aboutToShow.connect(lambda: self._set_debug_menu_active(True))
        menu.aboutToHide.connect(lambda: self._set_debug_menu_active(False))
        btn.setMenu(menu)

        self._debug_btn = btn
        self._debug_open_path_action = open_action
        self._echo_log_action = echo_action
        self._viewport_debug_action = viewport_action
        self._sync_debug_menu_state()
        return btn

    def _sync_debug_menu_state(self) -> None:
        echo_enabled = bool(runtime_logging.echo_log_enabled())
        viewport_enabled = bool(runtime_logging.viewport_render_debug_enabled())
        echo_action = getattr(self, "_echo_log_action", None)
        if echo_action is not None:
            try:
                echo_action.blockSignals(True)
                echo_action.setChecked(echo_enabled)
                echo_action.setText("EchoGraph Log: On" if echo_enabled else "EchoGraph Log: Off")
            finally:
                try:
                    echo_action.blockSignals(False)
                except Exception:
                    pass
        viewport_action = getattr(self, "_viewport_debug_action", None)
        if viewport_action is not None:
            try:
                viewport_action.blockSignals(True)
                viewport_action.setChecked(viewport_enabled)
                viewport_action.setText("Disable Viewport Debug" if viewport_enabled else "Enable Viewport Debug")
            finally:
                try:
                    viewport_action.blockSignals(False)
                except Exception:
                    pass
        self._update_debug_button_state()

    def _update_debug_button_state(self) -> None:
        btn = getattr(self, "_debug_btn", None)
        if btn is None:
            return
        viewport_enabled = bool(runtime_logging.viewport_render_debug_enabled())
        try:
            icon = self._debug_icon(active=viewport_enabled)
            btn.setIcon(icon)
            if icon.isNull():
                btn.setText("Debug")
                btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            else:
                btn.setText("")
                btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            btn.setToolTip("Viewport debug logging is on" if viewport_enabled else "Debug logs")
            btn.setProperty("debugActive", bool(viewport_enabled))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def _set_debug_menu_active(self, active: bool) -> None:
        btn = getattr(self, "_debug_btn", None)
        if btn is None:
            return
        try:
            if not runtime_logging.viewport_render_debug_enabled():
                btn.setProperty("active", bool(active))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def _open_debug_path_location(self) -> None:
        try:
            path = runtime_logging.debug_log_dir()
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        except Exception:
            pass

    def _on_echo_log_action_triggered(self, checked: bool) -> None:
        runtime_logging.set_echo_log_enabled(bool(checked))
        self._sync_debug_menu_state()

    def _on_viewport_debug_action_triggered(self, checked: bool) -> None:
        runtime_logging.set_viewport_render_debug_enabled(bool(checked))
        self._apply_pan_settings_to_gl_view()
        try:
            gv = getattr(self, "gl_view", None)
            if gv is not None:
                setattr(gv, "_viewport_render_debug_enabled", bool(checked))
                gv.update()
        except Exception:
            pass
        self._sync_debug_menu_state()

    def _open_help_docs(self) -> None:
        try:
            doc_path = script_dir() / "Doc" / "index.html"
        except Exception:
            doc_path = None
        if not doc_path or not doc_path.exists():
            try:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, "Help docs not found.")
            except Exception:
                pass
            return
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(doc_path)))
        except Exception:
            pass

    def _set_settings_menu_active(self, active: bool) -> None:
        btn = getattr(self, "_settings_btn", None)
        if btn is None:
            return
        if bool(active):
            try:
                self._refresh_voice_microphone_options()
            except Exception:
                pass
        try:
            btn.setProperty("active", bool(active))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def _set_file_menu_active(self, active: bool) -> None:
        btn = getattr(self, "_file_btn", None)
        if btn is None:
            return
        try:
            btn.setProperty("active", bool(active))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def _set_create_menu_active(self, active: bool) -> None:
        btn = getattr(self, "_create_btn", None)
        if btn is None:
            return
        try:
            btn.setProperty("active", bool(active))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def _close_menu_for_button(self, button_attr: str) -> None:
        btn = getattr(self, button_attr, None)
        if btn is None:
            return
        try:
            menu = btn.menu()
        except Exception:
            menu = None
        if menu is None:
            return
        try:
            menu.close()
        except Exception:
            try:
                menu.hide()
            except Exception:
                pass

    def _reset_ui_cursor_arrow(self) -> None:
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                while app.overrideCursor() is not None:
                    app.restoreOverrideCursor()
        except Exception:
            pass
        for obj in (self, getattr(self, "view", None), getattr(self, "gl_view", None)):
            if obj is None:
                continue
            try:
                obj.setCursor(QtCore.Qt.ArrowCursor)
            except Exception:
                pass
            try:
                vp = obj.viewport() if hasattr(obj, "viewport") else None
            except Exception:
                vp = None
            if vp is not None:
                try:
                    vp.setCursor(QtCore.Qt.ArrowCursor)
                except Exception:
                    pass

    def _run_menu_action(self, callback, button_attr: str = "") -> None:
        if button_attr:
            self._close_menu_for_button(button_attr)
        try:
            if callable(callback):
                callback()
        finally:
            try:
                QtCore.QTimer.singleShot(0, self._reset_ui_cursor_arrow)
            except Exception:
                self._reset_ui_cursor_arrow()

    def _update_window_title(self) -> None:
        try:
            path = (self._current_path or "").strip()
        except Exception:
            path = ""
        title = f"{path} - {APP_TITLE}" if path else APP_TITLE
        try:
            self.setWindowTitle(title)
        except Exception:
            pass

    def _workflow_project_dir(self) -> str:
        try:
            path = str(self._current_path or "").strip()
        except Exception:
            path = ""
        if path:
            try:
                current = Path(path).expanduser()
                base_dir = current if current.is_dir() else current.parent
                return str(base_dir.resolve())
            except Exception:
                pass
        try:
            return str(script_dir().resolve())
        except Exception:
            return str(script_dir())

    def _current_panel_layout_preset(self) -> Dict[str, bool]:
        timeline_on = False
        audio_on = False
        profiler_on = False
        ctl = getattr(self, "_timeline_controller", None)
        if ctl is not None:
            try:
                timeline_on = bool(ctl.timeline_panel_enabled())
            except Exception:
                timeline_on = False
            try:
                audio_on = bool(ctl.audio_panel_enabled())
            except Exception:
                audio_on = False
        pctl = getattr(self, "_profiler_controller", None)
        if pctl is not None:
            try:
                profiler_on = bool(pctl.profiler_panel_enabled())
            except Exception:
                profiler_on = False
        preset = _normalize_panel_layout_preset(
            {"timeline": timeline_on, "audio": audio_on, "profiler": profiler_on},
            _DEFAULT_PANEL_LAYOUT_PRESET,
        )
        return dict(preset)

    def _persist_app_layout_settings(self) -> None:
        payload = {
            "save_layout": bool(getattr(self, "_save_layout_enabled", True)),
            "panel_layout": _normalize_panel_layout_preset(
                getattr(self, "_panel_layout_master_preset", None),
                _DEFAULT_PANEL_LAYOUT_PRESET,
            ),
            "view_mode": _normalize_view_mode_preset(
                getattr(self, "_view_mode_master_preset", getattr(self, "_view_mode", "2d")),
                "2d",
            ) or "2d",
            "voice_audio_mode": _normalize_voice_audio_mode(
                getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT),
                _VOICE_AUDIO_MODE_DEFAULT,
            ),
            "voice_mic_device_index": _normalize_voice_mic_device_index(
                getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
                _VOICE_MIC_DEVICE_DEFAULT,
            ),
            "shadow_quality": _normalize_shadow_quality(
                getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT),
                _SHADOW_QUALITY_DEFAULT,
            ),
            "cast_shadows": bool(getattr(self, "_cast_shadows_enabled", True)),
            "self_shadows": bool(getattr(self, "_self_shadows_enabled", True)),
            "two_sided_shadows": bool(getattr(self, "_two_sided_shadows_enabled", True)),
            "ambient_light": bool(getattr(self, "_ambient_light_enabled", True)),
            "ambient_light_strength": _normalize_ambient_light_strength(
                getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
                _AMBIENT_LIGHT_STRENGTH_DEFAULT,
            ),
            "scene_skeleton_joint_names": bool(
                getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT)
            ),
        }
        _save_app_settings(payload)

    def _current_layout_view_mode_preset(self) -> str:
        return _normalize_view_mode_preset(getattr(self, "_view_mode", "2d"), "2d") or "2d"

    def _auto_save_current_view_mode_preset(self) -> None:
        if bool(getattr(self, "_suspend_panel_layout_persist", False)):
            return
        if not bool(getattr(self, "_save_layout_enabled", True)):
            return
        self._view_mode_master_preset = self._current_layout_view_mode_preset()
        self._persist_app_layout_settings()

    def _voice_audio_mode_is_bilateral(self) -> bool:
        mode = _normalize_voice_audio_mode(
            getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT),
            _VOICE_AUDIO_MODE_DEFAULT,
        )
        return mode == _VOICE_AUDIO_MODE_BILATERAL

    def _voice_audio_mode_is_turn_taking(self) -> bool:
        return not self._voice_audio_mode_is_bilateral()

    def _apply_voice_audio_mode_to_scene(self, *, sync_view_settings: bool = True) -> None:
        mode = _normalize_voice_audio_mode(
            getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT),
            _VOICE_AUDIO_MODE_DEFAULT,
        )
        self._voice_audio_mode = mode
        sc = getattr(self, "scene", None)
        if sc is None:
            return
        try:
            setattr(sc, "_voice_actor_audio_mode", mode)
        except Exception:
            pass
        if not sync_view_settings:
            return
        try:
            settings = getattr(sc, "_view_settings", None)
            if not isinstance(settings, dict):
                settings = {}
            settings = dict(settings)
            settings["voice_audio_mode"] = mode
            sc._view_settings = settings
        except Exception:
            pass

    def _apply_voice_microphone_to_scene(self, *, sync_view_settings: bool = True) -> None:
        mic_index = _normalize_voice_mic_device_index(
            getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
            _VOICE_MIC_DEVICE_DEFAULT,
        )
        self._voice_mic_device_index = mic_index
        sc = getattr(self, "scene", None)
        if sc is None:
            return
        try:
            setattr(sc, "_voice_actor_mic_device_index", mic_index)
        except Exception:
            pass
        if not sync_view_settings:
            return
        try:
            settings = getattr(sc, "_view_settings", None)
            if not isinstance(settings, dict):
                settings = {}
            settings = dict(settings)
            settings["voice_mic_device_index"] = mic_index
            sc._view_settings = settings
        except Exception:
            pass

    def _refresh_voice_microphone_options(self) -> None:
        combo = getattr(self, "_voice_mic_combo", None)
        if combo is None:
            return
        selected = _normalize_voice_mic_device_index(
            getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
            _VOICE_MIC_DEVICE_DEFAULT,
        )
        options = _list_available_microphone_options()
        has_selected = any(
            _normalize_voice_mic_device_index(opt.get("device_index"), _VOICE_MIC_DEVICE_DEFAULT) == selected
            for opt in options
        )
        if not has_selected and selected is not None:
            options.append({"device_index": selected, "name": f"{selected}: (Unavailable)"})
        combo.blockSignals(True)
        try:
            combo.clear()
            selected_index = 0
            for idx, opt in enumerate(options):
                opt_index = _normalize_voice_mic_device_index(opt.get("device_index"), _VOICE_MIC_DEVICE_DEFAULT)
                combo.addItem(str(opt.get("name", "") or ""), opt_index)
                if opt_index == selected:
                    selected_index = idx
            combo.setCurrentIndex(selected_index)
        finally:
            combo.blockSignals(False)
        if _speech_recognition is None:
            combo.setToolTip("Install speech_recognition + pyaudio for microphone device selection.")
        else:
            combo.setToolTip("Select the microphone device for Voice Actor listen mode.")

    def _on_voice_microphone_changed(self, _index: int) -> None:
        combo = getattr(self, "_voice_mic_combo", None)
        if combo is None:
            return
        selected = _normalize_voice_mic_device_index(
            combo.currentData(),
            _VOICE_MIC_DEVICE_DEFAULT,
        )
        if selected == _normalize_voice_mic_device_index(
            getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
            _VOICE_MIC_DEVICE_DEFAULT,
        ):
            return
        self._voice_mic_device_index = selected
        self._apply_voice_microphone_to_scene(sync_view_settings=True)
        self._persist_app_layout_settings()

    def _on_voice_audio_mode_toggled(self, checked: bool) -> None:
        self._voice_audio_mode = (
            _VOICE_AUDIO_MODE_TURN_TAKING if bool(checked) else _VOICE_AUDIO_MODE_BILATERAL
        )
        self._apply_voice_audio_mode_to_scene(sync_view_settings=True)
        self._persist_app_layout_settings()

    def _on_panel_layout_changed(self) -> None:
        if bool(getattr(self, "_suspend_panel_layout_persist", False)):
            return
        current = self._current_panel_layout_preset()
        if bool(getattr(self, "_save_layout_enabled", True)):
            self._panel_layout_master_preset = dict(current)
            self._view_mode_master_preset = self._current_layout_view_mode_preset()
        self._persist_app_layout_settings()

    def _on_save_layout_toggled(self, checked: bool) -> None:
        self._save_layout_enabled = bool(checked)
        if self._save_layout_enabled:
            self._panel_layout_master_preset = self._current_panel_layout_preset()
            self._view_mode_master_preset = self._current_layout_view_mode_preset()
        self._persist_app_layout_settings()

    def _save_current_layout_as_global_preset(self) -> None:
        self._panel_layout_master_preset = self._current_panel_layout_preset()
        self._view_mode_master_preset = self._current_layout_view_mode_preset()
        self._save_layout_enabled = True
        if hasattr(self, "_save_layout_toggle"):
            try:
                self._save_layout_toggle.blockSignals(True)
                self._save_layout_toggle.setChecked(True)
                self._save_layout_toggle.blockSignals(False)
            except Exception:
                pass
        self._persist_app_layout_settings()
        try:
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(),
                "Saved layout preset",
                self,
                self.rect(),
                1200,
            )
        except Exception:
            pass

    def _apply_panel_layout_preset(self, preset, *, persist_global: bool = False) -> None:
        normalized = _normalize_panel_layout_preset(
            preset,
            self._current_panel_layout_preset(),
        )
        self._suspend_panel_layout_persist = True
        try:
            ctl = getattr(self, "_timeline_controller", None)
            if bool(normalized.get("timeline")) and str(getattr(self, "_view_mode", "2d")).lower() == "2d":
                try:
                    self._set_view_mode("split")
                except Exception:
                    pass
            if ctl is not None:
                try:
                    ctl.sync_timeline_context()
                except Exception:
                    pass
            gv = getattr(self, "gl_view", None)
            if gv is not None:
                try:
                    set_timeline_visible = getattr(gv, "set_timeline_visible", None)
                    if callable(set_timeline_visible):
                        set_timeline_visible(bool(normalized.get("timeline")))
                except Exception:
                    pass
                try:
                    set_audio_visible = getattr(gv, "set_timeline_audio_visible", None)
                    if callable(set_audio_visible):
                        set_audio_visible(bool(normalized.get("audio")))
                except Exception:
                    pass
            pctl = getattr(self, "_profiler_controller", None)
            view = getattr(self, "view", None)
            if pctl is not None:
                try:
                    pctl.attach_panel_to_view()
                except Exception:
                    pass
            if view is not None:
                try:
                    set_profiler_visible = getattr(view, "set_profiler_visible", None)
                    if callable(set_profiler_visible):
                        set_profiler_visible(bool(normalized.get("profiler")))
                except Exception:
                    pass
            if ctl is not None:
                try:
                    ctl.sync_timeline_menu_state()
                except Exception:
                    pass
            if pctl is not None:
                try:
                    pctl.sync_profiler_menu_state()
                except Exception:
                    pass
        finally:
            self._suspend_panel_layout_persist = False

        if persist_global and bool(getattr(self, "_save_layout_enabled", True)):
            self._panel_layout_master_preset = dict(normalized)
            self._view_mode_master_preset = self._current_layout_view_mode_preset()
            self._persist_app_layout_settings()

    @staticmethod
    def _workflow_panel_layout_from_settings(settings) -> Dict[str, bool] | None:
        if not isinstance(settings, dict):
            return None
        raw = settings.get("panel_layout", None)
        if not isinstance(raw, dict):
            return None
        if "timeline" not in raw and "audio" not in raw and "profiler" not in raw:
            return None
        return _normalize_panel_layout_preset(raw, _DEFAULT_PANEL_LAYOUT_PRESET)

    @staticmethod
    def _normalize_workflow_view_mode(value, fallback: str | None = None) -> str | None:
        return _normalize_view_mode_preset(value, fallback)

    @classmethod
    def _workflow_view_mode_from_settings(cls, settings) -> str | None:
        if not isinstance(settings, dict):
            return None
        return cls._normalize_workflow_view_mode(settings.get("view_mode"), None)

    @staticmethod
    def _workflow_scene_restore_name_from_settings(settings) -> str | None:
        if not isinstance(settings, dict):
            return None
        raw = settings.get("scene_restore", None)
        if isinstance(raw, dict):
            name = str(raw.get("active_scene_node", "") or "").strip()
            if name:
                return name
        legacy = str(settings.get("active_scene_node", "") or "").strip()
        return legacy or None

    def _inject_panel_layout_into_workflow_data(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        settings = data.get("settings", None)
        if not isinstance(settings, dict):
            settings = {}
        settings = dict(settings)
        settings["panel_layout"] = self._current_panel_layout_preset()
        settings["view_mode"] = self._normalize_workflow_view_mode(
            getattr(self, "_view_mode", "2d"),
            "2d",
        )
        settings["voice_audio_mode"] = _normalize_voice_audio_mode(
            getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT),
            _VOICE_AUDIO_MODE_DEFAULT,
        )
        settings["voice_mic_device_index"] = _normalize_voice_mic_device_index(
            getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
            _VOICE_MIC_DEVICE_DEFAULT,
        )
        settings["shadow_quality"] = _normalize_shadow_quality(
            getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT),
            _SHADOW_QUALITY_DEFAULT,
        )
        settings["cast_shadows"] = bool(getattr(self, "_cast_shadows_enabled", True))
        settings["self_shadows"] = bool(getattr(self, "_self_shadows_enabled", True))
        settings["two_sided_shadows"] = bool(getattr(self, "_two_sided_shadows_enabled", True))
        settings["ambient_light"] = bool(getattr(self, "_ambient_light_enabled", True))
        settings["ambient_light_strength"] = _normalize_ambient_light_strength(
            getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
            _AMBIENT_LIGHT_STRENGTH_DEFAULT,
        )
        settings["scene_skeleton_joint_names"] = bool(
            getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT)
        )
        data["settings"] = settings

    def _inject_scene_restore_into_workflow_data(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        settings = data.get("settings", None)
        if not isinstance(settings, dict):
            settings = {}
        settings = dict(settings)
        try:
            active = getattr(self, "_active_scene_node", None)
            active_name = str(getattr(active, "name", "") or "").strip() if active is not None else ""
        except Exception:
            active_name = ""
        if active_name:
            scene_restore = settings.get("scene_restore", None)
            if not isinstance(scene_restore, dict):
                scene_restore = {}
            scene_restore = dict(scene_restore)
            scene_restore["active_scene_node"] = active_name
            settings["scene_restore"] = scene_restore
            settings.pop("active_scene_node", None)
        else:
            scene_restore = settings.get("scene_restore", None)
            if isinstance(scene_restore, dict):
                scene_restore = dict(scene_restore)
                scene_restore.pop("active_scene_node", None)
                if scene_restore:
                    settings["scene_restore"] = scene_restore
                else:
                    settings.pop("scene_restore", None)
            else:
                settings.pop("scene_restore", None)
            settings.pop("active_scene_node", None)
        data["settings"] = settings

    def _restore_workflow_active_scene(self, scene_name: str | None) -> bool:
        target = str(scene_name or "").strip()
        if not target:
            return False
        target_norm = target.lower()
        sc = getattr(self, "scene", None)
        node_items = getattr(sc, "_node_items", None)
        if not isinstance(node_items, dict):
            return False
        item = node_items.get(target)
        if not isinstance(item, NodeItem):
            try:
                for name, cand in list(node_items.items()):
                    if not isinstance(cand, NodeItem):
                        continue
                    if str(name or "").strip().lower() == target_norm:
                        item = cand
                        break
            except Exception:
                item = None
        if not isinstance(item, NodeItem):
            scene_nodes = []
            try:
                for cand in list(node_items.values()):
                    if not isinstance(cand, NodeItem):
                        continue
                    kind_c = (getattr(getattr(cand, "model", None), "kind", "") or "").strip().lower()
                    if kind_c in {"scene", "scene_assembly", "scene_outliner"}:
                        scene_nodes.append(cand)
            except Exception:
                scene_nodes = []
            if len(scene_nodes) == 1:
                item = scene_nodes[0]
        if not isinstance(item, NodeItem):
            return False
        kind = (getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()
        assets = []
        if kind == "modeler":
            try:
                from nodes.modeler import spec as _modeler_spec  # type: ignore

                collect = getattr(_modeler_spec, "_modeler_scene_assets", None)
                if callable(collect):
                    assets = list(collect(sc, item) or [])
            except Exception:
                assets = []
        elif kind in {"scene", "scene_assembly", "scene_outliner"}:
            try:
                collect = getattr(item, "_collect_scene_assets", None)
                if callable(collect):
                    assets = list(collect() or [])
            except Exception:
                assets = []
        else:
            return False
        if not assets:
            return False
        try:
            if str(getattr(self, "_view_mode", "2d") or "").strip().lower() == "2d":
                self._set_view_mode("split")
        except Exception:
            pass
        glv = getattr(self, "gl_view", None)
        if glv is None:
            return False
        try:
            if bool(getattr(glv, "_use_moderngl", False)):
                if getattr(glv, "_mgl_ctx", None) is None:
                    return False
                if getattr(glv, "_mgl_scene", None) is None:
                    return False
        except Exception:
            pass
        try:
            self._active_scene_node = getattr(item, "model", None)
            self._active_scene_preview_context = None
        except Exception:
            pass
        loaded = False
        try:
            self._opening_scene_assets_from_scene_node = True
            loaded = bool(self.open_scene_assets(assets, frame=True))
        except Exception:
            loaded = False
        finally:
            try:
                self._opening_scene_assets_from_scene_node = False
            except Exception:
                pass
        if not loaded:
            return False
        try:
            err = str(getattr(glv, "_mgl_error", "") or "").strip().lower()
            if "context not ready" in err or "scene assembly not ready" in err:
                return False
        except Exception:
            pass
        try:
            glv = getattr(self, "gl_view", None)
            if glv is not None:
                raw = ""
                getter = getattr(item, "_param_value", None)
                if callable(getter):
                    raw = str(getter("splat_depth_test") or "").strip()
                glv._mgl_splat_depth_test = raw
        except Exception:
            pass
        try:
            getter = getattr(item, "_param_value", None)
            thumb_base = str(getter("thumbnail") or "").strip() if callable(getter) else ""
            if thumb_base:
                base_path = Path(thumb_base).expanduser()
                if not base_path.is_absolute():
                    try:
                        cur = str(getattr(self, "_current_path", "") or "").strip()
                        if cur:
                            base_path = (Path(cur).expanduser().resolve().parent / base_path).resolve()
                    except Exception:
                        pass
                folder = base_path.parent
                sel = str(getattr(item, "_scene_selected_snapshot", "") or "").strip()
                if not sel and callable(getter):
                    sel = str(getter("thumbnail_choice") or "").strip()
                snap_png = base_path
                if sel:
                    try:
                        sel_path = Path(sel).expanduser()
                        snap_png = sel_path if sel_path.is_absolute() else (folder / sel_path)
                    except Exception:
                        snap_png = folder / sel
                if not snap_png.exists():
                    snap_png = base_path
                cam_path = snap_png.with_suffix(".json")
                if cam_path.exists():
                    with open(cam_path, "r", encoding="utf-8") as f:
                        cam = json.load(f)
                    if isinstance(cam, dict):
                        cam = dict(cam)
                        cam.pop("scene_xforms", None)
                        cam["_apply_scene_xforms"] = False
                    glv = getattr(self, "gl_view", None)

                    def _apply_cam() -> None:
                        try:
                            if glv is None:
                                return
                            selected_camera = str(getattr(glv, "_camera_select_mode", "default") or "default").strip()
                            if selected_camera and selected_camera.lower() != "default":
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
                    QtCore.QTimer.singleShot(1500, _apply_cam)
                    QtCore.QTimer.singleShot(2400, _apply_cam)
        except Exception:
            pass
        try:
            if sc is not None:
                sc.clearSelection()
            item.setSelected(True)
            item.clicked.emit(item.model)
        except Exception:
            pass
        return True

    def _restore_workflow_active_scene_deferred(
        self,
        scene_name: str | None,
        *,
        workflow_path: str | None = None,
        delay_ms: int = 260,
        max_attempts: int = 24,
    ) -> None:
        target = str(scene_name or "").strip()
        if not target:
            return
        expected_path = str(workflow_path or getattr(self, "_current_path", "") or "").strip()
        attempts = {"count": 0}

        def _apply() -> None:
            try:
                if expected_path:
                    current = str(getattr(self, "_current_path", "") or "").strip()
                    if current != expected_path:
                        return
            except Exception:
                return
            try:
                if bool(getattr(self, "_workflow_load_in_progress", False)):
                    attempts["count"] = int(attempts.get("count", 0)) + 1
                    if int(attempts["count"]) >= int(max(1, int(max_attempts))):
                        return
                    QtCore.QTimer.singleShot(max(80, int(delay_ms)), _apply)
                    return
            except Exception:
                pass
            restored = False
            try:
                restored = bool(self._restore_workflow_active_scene(target))
            except Exception:
                restored = False
            if restored:
                return
            attempts["count"] = int(attempts.get("count", 0)) + 1
            if int(attempts["count"]) >= int(max(1, int(max_attempts))):
                return
            try:
                QtCore.QTimer.singleShot(max(80, int(delay_ms)), _apply)
            except Exception:
                pass

        try:
            QtCore.QTimer.singleShot(max(0, int(delay_ms)), _apply)
        except Exception:
            _apply()

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
        self._fly_speed_mult = 1.0
        self._shadow_quality = _SHADOW_QUALITY_DEFAULT
        self._cast_shadows_enabled = True
        self._self_shadows_enabled = True
        self._two_sided_shadows_enabled = True
        self._ambient_light_enabled = True
        self._ambient_light_strength = _AMBIENT_LIGHT_STRENGTH_DEFAULT
        self._splat_log_enabled = False
        self._scene_skeleton_joint_names_enabled = _SCENE_SKELETON_JOINT_NAMES_DEFAULT
        self._save_layout_enabled = True
        self._panel_layout_master_preset = dict(_DEFAULT_PANEL_LAYOUT_PRESET)
        self._view_mode_master_preset = "2d"
        self._voice_audio_mode = _VOICE_AUDIO_MODE_DEFAULT
        self._voice_mic_device_index = _VOICE_MIC_DEVICE_DEFAULT
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
        if hasattr(self, "_fly_speed_slider"):
            try:
                self._fly_speed_slider.blockSignals(True)
                self._fly_speed_slider.setValue(int(round(self._fly_speed_mult * 100.0)))
                self._fly_speed_slider.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_shadow_quality_combo"):
            try:
                idx = self._shadow_quality_combo.findData(self._shadow_quality)
                self._shadow_quality_combo.blockSignals(True)
                self._shadow_quality_combo.setCurrentIndex(idx if idx >= 0 else 0)
                self._shadow_quality_combo.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_cast_shadows_toggle"):
            try:
                self._cast_shadows_toggle.blockSignals(True)
                self._cast_shadows_toggle.setChecked(bool(self._cast_shadows_enabled))
                self._cast_shadows_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_self_shadows_toggle"):
            try:
                self._self_shadows_toggle.blockSignals(True)
                self._self_shadows_toggle.setChecked(bool(self._self_shadows_enabled))
                self._self_shadows_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_ambient_light_toggle"):
            try:
                self._ambient_light_toggle.blockSignals(True)
                self._ambient_light_toggle.setChecked(bool(self._ambient_light_enabled))
                self._ambient_light_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_two_sided_shadows_toggle"):
            try:
                self._two_sided_shadows_toggle.blockSignals(True)
                self._two_sided_shadows_toggle.setChecked(bool(self._two_sided_shadows_enabled))
                self._two_sided_shadows_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_splat_log_toggle"):
            try:
                self._splat_log_toggle.blockSignals(True)
                self._splat_log_toggle.setChecked(bool(self._splat_log_enabled))
                self._splat_log_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_scene_skeleton_joint_names_toggle"):
            try:
                self._scene_skeleton_joint_names_toggle.blockSignals(True)
                self._scene_skeleton_joint_names_toggle.setChecked(bool(self._scene_skeleton_joint_names_enabled))
                self._scene_skeleton_joint_names_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_save_layout_toggle"):
            try:
                self._save_layout_toggle.blockSignals(True)
                self._save_layout_toggle.setChecked(bool(self._save_layout_enabled))
                self._save_layout_toggle.blockSignals(False)
            except Exception:
                pass
        if hasattr(self, "_voice_audio_toggle"):
            try:
                self._voice_audio_toggle.blockSignals(True)
                self._voice_audio_toggle.setChecked(self._voice_audio_mode_is_turn_taking())
                self._voice_audio_toggle.blockSignals(False)
            except Exception:
                pass
        self._refresh_voice_microphone_options()
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
        if hasattr(self, "_fly_speed_value_lbl"):
            try:
                self._fly_speed_value_lbl.setText(f"{self._fly_speed_mult:.2f}x")
            except Exception:
                pass
        self._apply_pan_settings_to_gl_view()
        self._apply_voice_audio_mode_to_scene(sync_view_settings=True)
        self._apply_voice_microphone_to_scene(sync_view_settings=True)
        self._apply_wireframe_color(self._default_wireframe_color(), sync_scene=True)
        self._persist_app_layout_settings()

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
            gv._mgl_splat_log = bool(getattr(self, "_splat_log_enabled", False))
            gv._viewport_render_debug_enabled = bool(runtime_logging.viewport_render_debug_enabled())
            gv._mgl_scene_skeleton_show_joint_names = bool(
                getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT)
            )
            fly_mult = float(getattr(self, "_fly_speed_mult", 1.0))
            if hasattr(gv, "_apply_fly_speed_multiplier"):
                gv._apply_fly_speed_multiplier(fly_mult, sync_ui=False, sync_scene=False)
            else:
                gv._fly_speed_mult = fly_mult
            shadow_quality = _normalize_shadow_quality(
                getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT),
                _SHADOW_QUALITY_DEFAULT,
            )
            cast_shadows = bool(getattr(self, "_cast_shadows_enabled", True))
            self_shadows = bool(getattr(self, "_self_shadows_enabled", True))
            two_sided_shadows = bool(getattr(self, "_two_sided_shadows_enabled", True))
            ambient_light = bool(getattr(self, "_ambient_light_enabled", True))
            ambient_strength = _normalize_ambient_light_strength(
                getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
                _AMBIENT_LIGHT_STRENGTH_DEFAULT,
            )
            if hasattr(gv, "_apply_mgl_ambient_settings"):
                gv._apply_mgl_ambient_settings(
                    enabled=ambient_light,
                    strength=ambient_strength,
                    sync_ui=True,
                    sync_scene=False,
                )
            else:
                gv._mgl_ambient_light_enabled = ambient_light
                gv._mgl_ambient_light_strength = ambient_strength
            if hasattr(gv, "_apply_mgl_shadow_settings"):
                gv._apply_mgl_shadow_settings(
                    enabled=cast_shadows,
                    quality=shadow_quality,
                    self_shadows=self_shadows,
                    two_sided_shadows=two_sided_shadows,
                    sync_scene=False,
                )
            else:
                gv._mgl_shadows_enabled = cast_shadows
                gv._mgl_shadow_quality = shadow_quality
                gv._mgl_self_shadows_enabled = self_shadows
                gv._mgl_two_sided_shadows_enabled = two_sided_shadows
                gv._mgl_shadow_dirty = True
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
                settings["splat_log"] = bool(getattr(self, "_splat_log_enabled", False))
                settings["scene_skeleton_joint_names"] = bool(
                    getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT)
                )
                settings["fly_speed_mult"] = float(getattr(self, "_fly_speed_mult", 1.0))
                settings["shadow_quality"] = _normalize_shadow_quality(
                    getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT),
                    _SHADOW_QUALITY_DEFAULT,
                )
                settings["cast_shadows"] = bool(getattr(self, "_cast_shadows_enabled", True))
                settings["self_shadows"] = bool(getattr(self, "_self_shadows_enabled", True))
                settings["two_sided_shadows"] = bool(getattr(self, "_two_sided_shadows_enabled", True))
                settings["ambient_light"] = bool(getattr(self, "_ambient_light_enabled", True))
                settings["ambient_light_strength"] = _normalize_ambient_light_strength(
                    getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
                    _AMBIENT_LIGHT_STRENGTH_DEFAULT,
                )
                settings["voice_audio_mode"] = _normalize_voice_audio_mode(
                    getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT),
                    _VOICE_AUDIO_MODE_DEFAULT,
                )
                settings["voice_mic_device_index"] = _normalize_voice_mic_device_index(
                    getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
                    _VOICE_MIC_DEVICE_DEFAULT,
                )
                sc._view_settings = settings
            except Exception:
                pass

    @staticmethod
    def _default_wireframe_color():
        return (0.25, 0.25, 0.25, 1.0)

    @classmethod
    def _normalize_wireframe_color(cls, color):
        rgba = cls._default_wireframe_color()
        try:
            if isinstance(color, QtGui.QColor):
                if color.isValid():
                    rgba = (
                        float(color.redF()),
                        float(color.greenF()),
                        float(color.blueF()),
                        float(color.alphaF()),
                    )
            elif isinstance(color, (list, tuple)):
                vals = [float(v) for v in color[:4]]
                if len(vals) >= 3:
                    if len(vals) < 4:
                        vals.append(1.0)
                    rgba = tuple(min(1.0, max(0.0, float(v))) for v in vals[:4])
        except Exception:
            rgba = cls._default_wireframe_color()
        return rgba

    def _update_wireframe_color_swatch(self) -> None:
        btn = getattr(self, "_wireframe_color_btn", None)
        if btn is None:
            return
        gv = getattr(self, "gl_view", None)
        rgba = self._default_wireframe_color()
        if gv is not None:
            try:
                rgba = self._normalize_wireframe_color(getattr(gv, "_mgl_wire_color", rgba))
            except Exception:
                rgba = self._default_wireframe_color()
        qcolor = QtGui.QColor.fromRgbF(float(rgba[0]), float(rgba[1]), float(rgba[2]), 1.0)
        hex_color = str(qcolor.name() or "#404040")
        try:
            btn.setStyleSheet(
                "QPushButton#WireframeColorSwatch{"
                f"background:{hex_color};"
                "border:1px solid #4b5563;border-radius:3px;padding:0px;"
                "min-width:18px;max-width:18px;min-height:18px;max-height:18px;}"
                "QPushButton#WireframeColorSwatch:hover{border-color:#cbd5e1;}"
            )
        except Exception:
            pass
        try:
            btn.setToolTip(f"Wireframe color: {hex_color}\nClick to change")
        except Exception:
            pass

    def _apply_wireframe_color(self, color, *, sync_scene: bool = True) -> None:
        rgba = self._normalize_wireframe_color(color)
        gv = getattr(self, "gl_view", None)
        applied = False
        if gv is not None and bool(getattr(gv, "_use_moderngl", False)) and hasattr(gv, "_apply_mgl_wire_color"):
            try:
                gv._apply_mgl_wire_color(rgba, sync_scene=sync_scene)
                applied = True
            except Exception:
                applied = False
        if not applied:
            if gv is not None:
                try:
                    gv._mgl_wire_color = rgba
                    gv.update()
                except Exception:
                    pass
            if sync_scene:
                sc = getattr(self, "scene", None)
                if sc is not None:
                    try:
                        settings = getattr(sc, "_view_settings", None)
                        if not isinstance(settings, dict):
                            settings = {}
                        settings = dict(settings)
                        settings["wire_color"] = [float(c) for c in rgba]
                        sc._view_settings = settings
                    except Exception:
                        pass
        self._update_wireframe_color_swatch()

    def _pick_wireframe_color(self) -> None:
        gv = getattr(self, "gl_view", None)
        current = self._default_wireframe_color()
        if gv is not None:
            try:
                current = self._normalize_wireframe_color(getattr(gv, "_mgl_wire_color", current))
            except Exception:
                current = self._default_wireframe_color()
        qcolor = QtGui.QColor.fromRgbF(float(current[0]), float(current[1]), float(current[2]), 1.0)
        chosen = QtWidgets.QColorDialog.getColor(qcolor, self, "Wireframe Color")
        if not chosen.isValid():
            return
        self._apply_wireframe_color(chosen, sync_scene=True)

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

    def _on_fly_speed_mult_changed(self, value: int) -> None:
        try:
            mult = max(0.1, float(value) / 100.0)
        except Exception:
            mult = 1.0
        self._fly_speed_mult = mult
        lbl = getattr(self, "_fly_speed_value_lbl", None)
        if lbl is not None:
            lbl.setText(f"{mult:.2f}x")
        self._apply_pan_settings_to_gl_view()

    def _on_splat_log_toggled(self, checked: bool) -> None:
        self._splat_log_enabled = bool(checked)
        self._apply_pan_settings_to_gl_view()

    def _on_scene_skeleton_joint_names_toggled(self, checked: bool) -> None:
        self._scene_skeleton_joint_names_enabled = bool(checked)
        self._apply_pan_settings_to_gl_view()
        try:
            gv = getattr(self, "gl_view", None)
            if gv is not None:
                gv.update()
        except Exception:
            pass
        self._persist_app_layout_settings()

    def _on_shadow_quality_changed(self, index: int) -> None:
        combo = getattr(self, "_shadow_quality_combo", None)
        value = None
        if combo is not None:
            try:
                value = combo.itemData(int(index))
            except Exception:
                value = None
        self._shadow_quality = _normalize_shadow_quality(value, _SHADOW_QUALITY_DEFAULT)
        self._apply_pan_settings_to_gl_view()
        self._persist_app_layout_settings()

    def _on_cast_shadows_toggled(self, checked: bool) -> None:
        self._cast_shadows_enabled = bool(checked)
        self._apply_pan_settings_to_gl_view()
        self._persist_app_layout_settings()

    def _on_self_shadows_toggled(self, checked: bool) -> None:
        self._self_shadows_enabled = bool(checked)
        self._apply_pan_settings_to_gl_view()
        self._persist_app_layout_settings()

    def _on_ambient_light_toggled(self, checked: bool) -> None:
        self._ambient_light_enabled = bool(checked)
        self._apply_pan_settings_to_gl_view()
        self._persist_app_layout_settings()

    def _on_two_sided_shadows_toggled(self, checked: bool) -> None:
        self._two_sided_shadows_enabled = bool(checked)
        self._apply_pan_settings_to_gl_view()
        self._persist_app_layout_settings()

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

    def _open_selected_node_view(self) -> None:
        if bool(getattr(self, "_workflow_load_in_progress", False)):
            return
        try:
            if actions._focus_is_text_input():
                return
        except Exception:
            pass
        sc = getattr(self, "scene", None)
        if sc is None:
            return
        try:
            items = [it for it in sc.selectedItems() if isinstance(it, NodeItem)]
        except Exception:
            items = []
        if not items:
            return
        for it in items:
            kind = (getattr(getattr(it, "model", None), "kind", "") or "").lower()
            if kind in {"scene", "scene_assembly", "scene_outliner"}:
                try:
                    it._open_scene_assets()
                except Exception:
                    pass
                return
        for it in items:
            kind = (getattr(getattr(it, "model", None), "kind", "") or "").lower()
            if kind in {"import", "primitive", "uv_unwrap", "normals", "normal", "smooth_normals", "smooth normals"}:
                try:
                    path = (it._param_value("path") or "").strip()
                except Exception:
                    path = ""
                try:
                    it._open_import_preview(path)
                except Exception:
                    pass
                return

    def _set_view_mode_from_hotkey(self, mode: str) -> None:
        try:
            if actions._focus_is_text_input():
                return
        except Exception:
            pass
        try:
            self._set_view_mode(mode)
            self._auto_save_current_view_mode_preset()
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
        t_load_start = time.perf_counter()
        path = (path or "").strip()
        if not path:
            self._workflow_load_in_progress = False
            return False
        self._workflow_load_in_progress = True
        json_ms = 0.0
        deserialize_ms = 0.0
        post_ms = 0.0
        frame_ms = 0.0
        data = {}
        deserialize_profile = {}
        try:
            t_json = time.perf_counter()
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            json_ms = (time.perf_counter() - t_json) * 1000.0
        except Exception as e:
            _workflow_load_log(
                "workflow_load_failed",
                path=path,
                phase="read_json",
                error=str(e),
                total_ms=round((time.perf_counter() - t_load_start) * 1000.0, 3),
            )
            self._workflow_load_in_progress = False
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to open:\n{e}")
            return False

        counts = {
            "nodes": len((data.get("nodes") if isinstance(data, dict) else []) or []),
            "edges": len((data.get("edges") if isinstance(data, dict) else []) or []),
            "comments": len((data.get("comments") if isinstance(data, dict) else []) or []),
        }
        try:
            self._reset_viewport_scene_state_for_workflow_load()
        except Exception:
            pass
        try:
            t_deserialize = time.perf_counter()
            deserialize_profile = self.scene.from_dict(data) or {}
            deserialize_ms = (time.perf_counter() - t_deserialize) * 1000.0
        except Exception as e:
            _workflow_load_log(
                "workflow_load_failed",
                path=path,
                phase="deserialize_scene",
                error=str(e),
                counts=counts,
                json_ms=round(json_ms, 3),
                total_ms=round((time.perf_counter() - t_load_start) * 1000.0, 3),
            )
            self._workflow_load_in_progress = False
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to open:\n{e}")
            return False

        t_post = time.perf_counter()
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
        if not isinstance(settings, dict):
            settings = {}
        workflow_panel_layout = self._workflow_panel_layout_from_settings(settings)
        workflow_view_mode = self._workflow_view_mode_from_settings(settings)
        if workflow_view_mode is None and isinstance(data, dict):
            workflow_view_mode = self._normalize_workflow_view_mode(data.get("view_mode"), None)
        workflow_scene_restore = self._workflow_scene_restore_name_from_settings(settings)
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
        try:
            fly_speed = float(settings.get("fly_speed_mult", getattr(self, "_fly_speed_mult", 1.0)))
        except Exception:
            fly_speed = getattr(self, "_fly_speed_mult", 1.0)
        try:
            splat_log = bool(settings.get("splat_log", getattr(self, "_splat_log_enabled", False)))
        except Exception:
            splat_log = getattr(self, "_splat_log_enabled", False)
        try:
            scene_skeleton_joint_names = _coerce_bool(
                settings.get(
                    "scene_skeleton_joint_names",
                    getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT),
                ),
                _SCENE_SKELETON_JOINT_NAMES_DEFAULT,
            )
        except Exception:
            scene_skeleton_joint_names = bool(
                getattr(self, "_scene_skeleton_joint_names_enabled", _SCENE_SKELETON_JOINT_NAMES_DEFAULT)
            )
        try:
            shadow_quality = _normalize_shadow_quality(
                settings.get("shadow_quality", getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT)),
                getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT),
            )
        except Exception:
            shadow_quality = getattr(self, "_shadow_quality", _SHADOW_QUALITY_DEFAULT)
        try:
            cast_shadows = _coerce_bool(settings.get("cast_shadows", getattr(self, "_cast_shadows_enabled", True)), True)
        except Exception:
            cast_shadows = bool(getattr(self, "_cast_shadows_enabled", True))
        try:
            self_shadows = _coerce_bool(settings.get("self_shadows", getattr(self, "_self_shadows_enabled", True)), True)
        except Exception:
            self_shadows = bool(getattr(self, "_self_shadows_enabled", True))
        try:
            two_sided_shadows = _coerce_bool(
                settings.get("two_sided_shadows", getattr(self, "_two_sided_shadows_enabled", True)),
                True,
            )
        except Exception:
            two_sided_shadows = bool(getattr(self, "_two_sided_shadows_enabled", True))
        try:
            ambient_light = _coerce_bool(
                settings.get("ambient_light", getattr(self, "_ambient_light_enabled", True)),
                True,
            )
        except Exception:
            ambient_light = bool(getattr(self, "_ambient_light_enabled", True))
        try:
            ambient_light_strength = _normalize_ambient_light_strength(
                settings.get(
                    "ambient_light_strength",
                    getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
                ),
                getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
            )
        except Exception:
            ambient_light_strength = _normalize_ambient_light_strength(
                getattr(self, "_ambient_light_strength", _AMBIENT_LIGHT_STRENGTH_DEFAULT),
                _AMBIENT_LIGHT_STRENGTH_DEFAULT,
            )
        try:
            voice_audio_mode = _normalize_voice_audio_mode(
                settings.get("voice_audio_mode", getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT)),
                getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT),
            )
        except Exception:
            voice_audio_mode = getattr(self, "_voice_audio_mode", _VOICE_AUDIO_MODE_DEFAULT)
        try:
            voice_mic_device_index = _normalize_voice_mic_device_index(
                settings.get("voice_mic_device_index", getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT)),
                getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT),
            )
        except Exception:
            voice_mic_device_index = getattr(self, "_voice_mic_device_index", _VOICE_MIC_DEVICE_DEFAULT)
        self._pan_base = pan_base
        self._pan_exp = pan_exp
        self._pan_boost = pan_boost
        self._gizmo_zoom_scale = gizmo_zoom
        self._fly_speed_mult = fly_speed
        self._splat_log_enabled = splat_log
        self._scene_skeleton_joint_names_enabled = bool(scene_skeleton_joint_names)
        self._shadow_quality = shadow_quality
        self._cast_shadows_enabled = bool(cast_shadows)
        self._self_shadows_enabled = bool(self_shadows)
        self._two_sided_shadows_enabled = bool(two_sided_shadows)
        self._ambient_light_enabled = bool(ambient_light)
        self._ambient_light_strength = float(ambient_light_strength)
        self._voice_audio_mode = voice_audio_mode
        self._voice_mic_device_index = voice_mic_device_index
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
        if hasattr(self, "_fly_speed_slider"):
            self._fly_speed_slider.blockSignals(True)
            self._fly_speed_slider.setValue(int(round(fly_speed * 100.0)))
            self._fly_speed_slider.blockSignals(False)
        if hasattr(self, "_splat_log_toggle"):
            self._splat_log_toggle.blockSignals(True)
            self._splat_log_toggle.setChecked(bool(splat_log))
            self._splat_log_toggle.blockSignals(False)
        if hasattr(self, "_scene_skeleton_joint_names_toggle"):
            self._scene_skeleton_joint_names_toggle.blockSignals(True)
            self._scene_skeleton_joint_names_toggle.setChecked(bool(scene_skeleton_joint_names))
            self._scene_skeleton_joint_names_toggle.blockSignals(False)
        if hasattr(self, "_shadow_quality_combo"):
            self._shadow_quality_combo.blockSignals(True)
            idx = self._shadow_quality_combo.findData(shadow_quality)
            self._shadow_quality_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._shadow_quality_combo.blockSignals(False)
        if hasattr(self, "_cast_shadows_toggle"):
            self._cast_shadows_toggle.blockSignals(True)
            self._cast_shadows_toggle.setChecked(bool(cast_shadows))
            self._cast_shadows_toggle.blockSignals(False)
        if hasattr(self, "_self_shadows_toggle"):
            self._self_shadows_toggle.blockSignals(True)
            self._self_shadows_toggle.setChecked(bool(self_shadows))
            self._self_shadows_toggle.blockSignals(False)
        if hasattr(self, "_ambient_light_toggle"):
            self._ambient_light_toggle.blockSignals(True)
            self._ambient_light_toggle.setChecked(bool(ambient_light))
            self._ambient_light_toggle.blockSignals(False)
        if hasattr(self, "_two_sided_shadows_toggle"):
            self._two_sided_shadows_toggle.blockSignals(True)
            self._two_sided_shadows_toggle.setChecked(bool(two_sided_shadows))
            self._two_sided_shadows_toggle.blockSignals(False)
        if hasattr(self, "_voice_audio_toggle"):
            self._voice_audio_toggle.blockSignals(True)
            self._voice_audio_toggle.setChecked(self._voice_audio_mode_is_turn_taking())
            self._voice_audio_toggle.blockSignals(False)
        self._refresh_voice_microphone_options()
        if hasattr(self, "_pan_base_value_lbl"):
            self._pan_base_value_lbl.setText(f"{pan_base:.3f}")
        if hasattr(self, "_pan_exp_value_lbl"):
            self._pan_exp_value_lbl.setText(f"{pan_exp:.2f}")
        if hasattr(self, "_pan_boost_value_lbl"):
            self._pan_boost_value_lbl.setText(f"{pan_boost:.1f}")
        if hasattr(self, "_gizmo_zoom_value_lbl"):
            self._gizmo_zoom_value_lbl.setText(f"{gizmo_zoom:.3f}")
        if hasattr(self, "_fly_speed_value_lbl"):
            self._fly_speed_value_lbl.setText(f"{fly_speed:.2f}x")
        try:
            self._apply_pan_settings_to_gl_view()
        except Exception:
            pass
        self._apply_voice_audio_mode_to_scene(sync_view_settings=True)
        self._apply_voice_microphone_to_scene(sync_view_settings=True)
        try:
            light_intensity = settings.get("light_intensity", None)
            if light_intensity is not None:
                light_intensity = float(light_intensity)
        except Exception:
            light_intensity = None
        if light_intensity is not None:
            try:
                gv = getattr(self, "gl_view", None)
                if gv is not None and hasattr(gv, "_apply_mgl_light_intensity"):
                    gv._apply_mgl_light_intensity(light_intensity, sync_ui=True, sync_scene=False)
            except Exception:
                pass
        try:
            wire_color = settings.get("wire_color", None)
        except Exception:
            wire_color = None
        if wire_color is not None:
            try:
                self._apply_wireframe_color(wire_color, sync_scene=False)
            except Exception:
                pass
        else:
            try:
                self._update_wireframe_color_swatch()
            except Exception:
                pass
        post_ms = (time.perf_counter() - t_post) * 1000.0

        self._current_path = path
        try:
            if workflow_panel_layout is not None:
                self._apply_panel_layout_preset(workflow_panel_layout, persist_global=False)
            elif bool(getattr(self, "_save_layout_enabled", True)):
                self._apply_panel_layout_preset(
                    getattr(self, "_panel_layout_master_preset", None),
                    persist_global=False,
                )
        except Exception:
            pass
        if workflow_view_mode is not None:
            try:
                self._set_view_mode(workflow_view_mode)
            except Exception:
                pass
        try:
            self._pending_workflow_scene_restore = str(workflow_scene_restore or "").strip()
        except Exception:
            self._pending_workflow_scene_restore = ""
        self._remember_recent(path)
        self._update_window_title()
        try:
            self._timeline_controller.sync_timeline_context()
            self._timeline_controller.sync_timeline_menu_state()
        except Exception:
            pass
        t_frame = time.perf_counter()
        self._frame_all_nodes()
        frame_ms = (time.perf_counter() - t_frame) * 1000.0
        total_ms = (time.perf_counter() - t_load_start) * 1000.0
        try:
            file_size = int(os.path.getsize(path))
        except Exception:
            file_size = -1
        _workflow_load_log(
            "workflow_load_summary",
            path=path,
            file_size=file_size,
            counts=counts,
            total_ms=round(total_ms, 3),
            json_ms=round(json_ms, 3),
            deserialize_ms=round(deserialize_ms, 3),
            post_ms=round(post_ms, 3),
            frame_ms=round(frame_ms, 3),
            deserialize_profile=deserialize_profile if isinstance(deserialize_profile, dict) else {},
        )
        self._workflow_load_in_progress = False
        try:
            restore_name = str(getattr(self, "_pending_workflow_scene_restore", "") or "").strip()
            if restore_name:
                self._restore_workflow_active_scene_deferred(
                    restore_name,
                    workflow_path=path,
                    delay_ms=0,
                )
        except Exception:
            pass
        return True

    def _open_graph(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Graph (.json)",
            self._workflow_project_dir(),
            "JSON Files (*.json)",
        )
        if not path:
            return
        self._load_graph_file(path)

    def _save_graph(self):
        if not self._current_path:
            return self._export_graph()
        try:
            data = self.scene.to_dict()
            self._inject_panel_layout_into_workflow_data(data)
            self._inject_scene_restore_into_workflow_data(data)
            with open(self._current_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._remember_recent(self._current_path)
            self._update_window_title()
            try:
                self._timeline_controller.sync_timeline_context()
            except Exception:
                pass
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
            self._inject_panel_layout_into_workflow_data(data)
            self._inject_scene_restore_into_workflow_data(data)

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
            self._update_window_title()
            try:
                self._timeline_controller.sync_timeline_context()
            except Exception:
                pass
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
        scroll.setStyleSheet(
            "QScrollArea{background:#1a1f24;border:none;}"
            "QScrollArea>Viewport{background:#1a1f24;}"
        )
        self._cardsContainer.setStyleSheet("QWidget{background:#1a1f24;color:#e6edf3;}")
        try:
            self.infoDock.setMinimumWidth(360)
        except Exception:
            pass

        try:
            self.resizeDocks([self.infoDock], [400], QtCore.Qt.Horizontal)
        except Exception:
            pass

    def _toggle_fullscreen(self) -> None:
        try:
            is_full = bool(self.isFullScreen())
        except Exception:
            is_full = bool(getattr(self, "_fullscreen_enabled", False))
        self._set_fullscreen(not is_full)

    def _set_fullscreen(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled:
            try:
                if self.isFullScreen():
                    self._fullscreen_enabled = True
                    return
            except Exception:
                pass
            try:
                self._fullscreen_prev_state = self.windowState()
            except Exception:
                self._fullscreen_prev_state = None
            try:
                self._fullscreen_prev_geom = self.geometry()
            except Exception:
                self._fullscreen_prev_geom = None
            try:
                self._fullscreen_prev_was_max = bool(self._fullscreen_prev_state & QtCore.Qt.WindowMaximized)
            except Exception:
                self._fullscreen_prev_was_max = False
            self._fullscreen_prev_view_mode = getattr(self, "_view_mode", "2d")
            splitter = getattr(self, "_view_splitter", None)
            try:
                self._fullscreen_prev_split_sizes = splitter.sizes() if splitter else None
            except Exception:
                self._fullscreen_prev_split_sizes = None
            try:
                self._fullscreen_prev_info_visible = bool(self.infoDock.isVisible())
            except Exception:
                self._fullscreen_prev_info_visible = None
            try:
                self._fullscreen_prev_topbar_visible = bool(self._topbar.isVisible())
            except Exception:
                self._fullscreen_prev_topbar_visible = None

            target = None
            try:
                fw = QtWidgets.QApplication.instance().focusWidget()
                if fw is not None and getattr(self, "gl_view", None) is not None:
                    if fw is self.gl_view or self.gl_view.isAncestorOf(fw):
                        target = "gl"
                if fw is not None and target is None and getattr(self, "view", None) is not None:
                    if fw is self.view or self.view.isAncestorOf(fw):
                        target = "view"
            except Exception:
                target = None
            if target is None:
                mode = getattr(self, "_view_mode", "2d")
                target = "gl" if mode in {"3d", "split"} else "view"
            self._fullscreen_target = target

            try:
                if getattr(self, "_topbar", None) is not None:
                    self._topbar.setVisible(False)
            except Exception:
                pass
            try:
                if getattr(self, "infoDock", None) is not None:
                    self.infoDock.setVisible(False)
            except Exception:
                pass

            if splitter:
                try:
                    if target == "gl":
                        if getattr(self, "gl_view", None) is not None:
                            self.gl_view.show()
                        if getattr(self, "view", None) is not None:
                            self.view.hide()
                        splitter.setSizes([1, 0])
                    else:
                        if getattr(self, "view", None) is not None:
                            self.view.show()
                        if getattr(self, "gl_view", None) is not None:
                            self.gl_view.hide()
                        splitter.setSizes([0, 1])
                except Exception:
                    pass
            try:
                self.showFullScreen()
            except Exception:
                try:
                    self.showMaximized()
                except Exception:
                    pass
            self._fullscreen_enabled = True
        else:
            try:
                if not self.isFullScreen() and not bool(getattr(self, "_fullscreen_enabled", False)):
                    return
            except Exception:
                pass
            prev_state = getattr(self, "_fullscreen_prev_state", None)
            was_max = bool(getattr(self, "_fullscreen_prev_was_max", False))
            try:
                self.setUpdatesEnabled(False)
            except Exception:
                pass
            try:
                if prev_state is not None:
                    target_state = prev_state & ~QtCore.Qt.WindowFullScreen
                    if was_max:
                        target_state = target_state | QtCore.Qt.WindowMaximized
                    self.setWindowState(target_state)
            except Exception:
                pass
            try:
                prev_geom = getattr(self, "_fullscreen_prev_geom", None)
                if prev_geom is not None and not was_max:
                    self.setGeometry(prev_geom)
            except Exception:
                pass
            try:
                # avoid showNormal/showMaximized to prevent transient shrink
                self.show()
            except Exception:
                pass

            # Defer layout restore one tick to avoid intermediate shrink flicker.
            self._fullscreen_exit_pending = True
            QtCore.QTimer.singleShot(0, self._finalize_fullscreen_exit)

    def _finalize_fullscreen_exit(self) -> None:
        if not bool(getattr(self, "_fullscreen_exit_pending", False)):
            return
        self._fullscreen_exit_pending = False
        was_max = bool(getattr(self, "_fullscreen_prev_was_max", False))
        try:
            if getattr(self, "_topbar", None) is not None:
                self._topbar.setVisible(bool(self._fullscreen_prev_topbar_visible))
        except Exception:
            pass
        try:
            if getattr(self, "infoDock", None) is not None:
                self.infoDock.setVisible(bool(self._fullscreen_prev_info_visible))
        except Exception:
            pass
        prev_mode = getattr(self, "_fullscreen_prev_view_mode", None)
        if prev_mode:
            try:
                self._set_view_mode(prev_mode)
            except Exception:
                pass
        splitter = getattr(self, "_view_splitter", None)
        prev_sizes = getattr(self, "_fullscreen_prev_split_sizes", None)
        if splitter and prev_sizes:
            try:
                splitter.setSizes(prev_sizes)
            except Exception:
                pass
        try:
            prev_geom = getattr(self, "_fullscreen_prev_geom", None)
            if prev_geom is not None and not was_max:
                self.setGeometry(prev_geom)
        except Exception:
            pass
        self._fullscreen_enabled = False
        try:
            self.setUpdatesEnabled(True)
            self.update()
        except Exception:
            pass

    def add_info_card(self, node: GraphNode):
        try:
            node._graph_scene = self.scene
        except Exception:
            pass
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
            try:
                node._graph_scene = self.scene
            except Exception:
                pass
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

    def _active_scene_node_item(self):
        sc = getattr(self, "scene", None)
        if sc is None:
            return None
        active = getattr(self, "_active_scene_node", None)
        if active is None:
            return None
        if hasattr(active, "model"):
            return active
        active_name = str(getattr(active, "name", "") or "").strip()
        node_items = getattr(sc, "_node_items", None)
        if isinstance(node_items, dict):
            if active_name and active_name in node_items:
                return node_items.get(active_name)
            try:
                for item in node_items.values():
                    if getattr(item, "model", None) is active:
                        return item
            except Exception:
                pass
        return None

    def _node_feeds_active_scene(self, node_name: str) -> bool:
        target = str(node_name or "").strip().lower()
        if not target:
            return False
        sc = getattr(self, "scene", None)
        root = self._active_scene_node_item()
        if sc is None or root is None:
            return False
        seen = set()
        stack = [root]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            ident = id(item)
            if ident in seen:
                continue
            seen.add(ident)
            model = getattr(item, "model", None)
            if str(getattr(model, "name", "") or "").strip().lower() == target:
                return True
            try:
                edges = list(sc._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(sc._in_edges(item))
                except Exception:
                    edges = []
            for edge in edges:
                src = getattr(edge, "src", None)
                if src is not None:
                    stack.append(src)
        return False

    def _schedule_active_scene_refresh_for_node(self, node_name: str) -> None:
        try:
            if isinstance(getattr(self, "_active_scene_preview_context", None), dict):
                return
            if str(getattr(self, "_view_mode", "2d") or "").strip().lower() not in {"3d", "split"}:
                return
            if getattr(self, "gl_view", None) is None:
                return
            if not self._node_feeds_active_scene(node_name):
                return
            self._active_scene_refresh_node = str(node_name or "")
            timer = getattr(self, "_active_scene_refresh_timer", None)
            if timer is not None:
                timer.start()
            else:
                self._refresh_active_scene_from_graph()
        except Exception:
            pass

    def _refresh_active_scene_from_graph(self) -> None:
        if isinstance(getattr(self, "_active_scene_preview_context", None), dict):
            return
        item = self._active_scene_node_item()
        if item is None:
            return
        assets = []
        model = getattr(item, "model", None)
        kind = str(getattr(model, "kind", "") or "").strip().lower()
        if kind == "modeler":
            try:
                from nodes.modeler import spec as _modeler_spec  # type: ignore

                collect = getattr(_modeler_spec, "_modeler_scene_assets", None)
                if callable(collect):
                    assets = list(collect(getattr(self, "scene", None), item) or [])
            except Exception:
                assets = []
        else:
            try:
                collect = getattr(item, "_collect_scene_assets", None)
                if callable(collect):
                    assets = list(collect() or [])
            except Exception:
                assets = []
            if not assets:
                try:
                    from nodes.scene import spec as _scene_spec  # type: ignore

                    collect = getattr(_scene_spec, "_collect_assets", None)
                    if callable(collect):
                        assets = list(collect(item) or [])
                except Exception:
                    assets = []
        if not assets:
            return
        try:
            self._active_scene_node = getattr(item, "model", None)
            self._active_scene_preview_context = None
        except Exception:
            pass
        try:
            self._opening_scene_assets_from_scene_node = True
            self.open_scene_assets(assets, frame=False)
        except Exception:
            pass
        finally:
            try:
                self._opening_scene_assets_from_scene_node = False
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

        self._schedule_active_scene_refresh_for_node(node_name)


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
