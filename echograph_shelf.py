# ==== echograph (single-file shelf script; runs on click) ======================
# Maya + Houdini PySide2 node-graph demo with info cards, Output path highlight,
# multi-input Switch with slider, JSON Open/Save/Export, node params, wiring, and code editor.
# Nav: Left-click = select / wire-drag; Middle-mouse = pan; Right-drag = zoom.
# Alt+LeftClick a link to delete it. Badge (type bubble) is below the name.
# Quick-create: right-click empty canvas (no drag) to open Create Node.
# Delete/Backspace removes selected nodes with their links.
# Clicking an Output node auto-fills Info pane with ordered branch cards (start → output).

import sys, re, json, math, os, time
from pathlib import Path

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
)

# Keep a local mutable scale (slider edits this)
LLM_SCALE = float(LLM_SCALE_DEFAULT)
def _llm_dims():
    return int(LLM_NODE_W_BASE * LLM_SCALE), int(LLM_NODE_H_BASE * LLM_SCALE)

# Derived dims used by node sizing
LLM_NODE_W, LLM_NODE_H = _llm_dims()

# --- WebEngine (for embedding Gradio UI) ---
try:
    from PySide6 import QtWebEngineWidgets as WebEngine
except Exception:
    try:
        from PySide2 import QtWebEngineWidgets as WebEngine
    except Exception:
        WebEngine = None

def set_global_llm_scale(new_scale: float, scene=None):
    """Apply a new global LLM scale and refresh all LLM nodes + layout."""
    global LLM_SCALE, LLM_NODE_W, LLM_NODE_H
    try:
        s = float(new_scale)
    except Exception:
        return
    s = max(0.25, min(1.75, s))  # clamp to sane range
    if abs(s - LLM_SCALE) < 1e-6:
        return

    LLM_SCALE = s
    LLM_NODE_W, LLM_NODE_H = _llm_dims()

    if scene is None:
        return

    # Rebuild all LLM nodes so the offscreen sampler and label resize
    for item in list(getattr(scene, "_node_items", {}).values()):
        try:
            if (item.model.kind or "").lower() == "llm":
                item._recompute_height()
                item._build_widgets()
        except Exception:
            pass

    # Refresh edges and scene rect
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

def _spec_stripe_color(kind: str) -> str:
    k = (kind or "node").lower()
    try:
        spec = core.get_spec(k)
    except Exception as e:
        print(f"[EchoGraph] get_spec({k}) failed:", e)
        spec = None

    if spec is not None:
        if isinstance(spec, dict):
            c = spec.get("stripe_color") or spec.get("color") or spec.get("stripe")
            if c: return str(c)
        for attr in ("stripe_color", "color", "stripe"):
            try:
                val = getattr(spec, attr)
                if val:
                    return str(val)
            except Exception:
                pass
        print(f"[EchoGraph] Spec for '{k}' has no stripe_color; using default.")
        return DEFAULT_STRIPE_HEX

    return DEFAULT_STRIPE_HEX

# --- host detection (Maya / Houdini / standalone) ---
HOST = "standalone"
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


def _hash_to_links(text:str)->str:
    return re.sub(
        r"#([^\n#]+)",
        lambda m: f'<a href="jump:/{QtCore.QUrl.toPercentEncoding(m.group(1).strip()).data().decode()}">#{m.group(1).strip()}</a>',
        text
    )

# model
class GraphNode:
    def __init__(self, name, kind="node", info="", code=None, params=None, switch_inputs=None, switch_index=0):
        self.name = name
        self.kind = kind
        self.info = info
        self.code = code
        self.pos = QtCore.QPointF(0, 0)
        self.params = list(params or [])
        self.switch_inputs = list(switch_inputs or [])
        self.switch_index = int(switch_index or 0)

# items
def _gi_flag(enum_name, fallback_enum):
    if hasattr(QtWidgets.QGraphicsItem, enum_name):
        return getattr(QtWidgets.QGraphicsItem, enum_name)
    if hasattr(QtWidgets.QGraphicsItem, "GraphicsItemFlag"):
        return getattr(QtWidgets.QGraphicsItem.GraphicsItemFlag, enum_name, fallback_enum)
    return fallback_enum

def _top_level_parent_for_dialog() -> QtWidgets.QWidget | None:
    aw = QtWidgets.QApplication.activeWindow()
    if aw and aw.isWindow():
        return aw
    try:
        if _WINDOW and _WINDOW.isWindow():
            return _WINDOW
    except Exception:
        pass
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.isWindow() and w.isVisible():
                return w
        except Exception:
            continue
    return None

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
    
    def __init__(self, model: GraphNode):
        try:
            super().__init__()
        except TypeError:
            super(NodeItem, self).__init__()

        self.model = model
        self.width = self._BASE_W
        self.height = self._BASE_H
        self.radius = 10

        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)

        try:
            self.setCacheMode(QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache)
        except AttributeError:
            self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)

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


        self._recompute_height()
        self._build_widgets()


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
        """Return featured set ∩ valid_names and write back if anything was pruned."""
        feat = self._get_featured_set()
        pruned = {n for n in feat if n in valid_names}
        if pruned != feat:
            self._set_featured_set(pruned)
        return pruned

    def _set_featured_set(self, names: set[str]):
        """Write back a NEW set instance (copy-on-write)."""
        setattr(self.model, "_featured_params", set(names))


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

        # Params block (regular rows)
        n_params = len(self.model.params or [])
        params_h = n_params * self._PARAM_ROW_H

        # Note: add a big block per featured param (prune orphans first)
        if kind == "note":
            try:
                current_names = { (p.get("name") or "") for p in (self.model.params or []) if (p.get("name") or "") }
                feat_set = self._pruned_featured_set(current_names)
                params_h += len(feat_set) * self._NOTE_FEATURED_H
            except Exception:
                pass

        if n_params:
            params_h += self._PADDING  # breathing room below params

        # Kind-specific body additions
        if kind == "llm":
            body_h = int(LLM_NODE_H_BASE * LLM_SCALE)
            node_w = max(self._BASE_W, int(LLM_NODE_W_BASE * LLM_SCALE))
        elif kind == "append":
            count = max(1, len(self.model.switch_inputs or []))
            body_h = 6 + count * self._PARAM_ROW_H
            node_w = self._BASE_W
        else:
            body_h = 0
            node_w = self._BASE_W

        new_w = max(node_w, self._BASE_W)
        new_h = max(self._BASE_H, header_h + switch_h + params_h + body_h + self._PADDING)

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


    def _build_widgets(self):
        # prevent re-entrancy while we're tearing down/creating proxies
        if getattr(self, "_is_building", False):
            return
        self._is_building = True
        try:
            self._clear_widget_proxies()
            y_cursor = 38 + 16 + self._PADDING

            # --- Plugin body hook (lets specs draw a custom node body) ---
            try:
                spec = core.get_spec((self.model.kind or "node").lower())
                render = None
                if isinstance(spec, dict):
                    render = spec.get("render_node_body")
                else:
                    render = getattr(spec, "render_node_body", None)
                if callable(render):
                    new_y = render(self, y_cursor)
                    if isinstance(new_y, (int, float)):
                        y_cursor = int(new_y)
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

            # --- Parameters ---
            if self.model.params:
                kind = (self.model.kind or "").lower()
                if kind == "note":
                    current_names = { (p.get("name") or "") for p in (self.model.params or []) if (p.get("name") or "") }
                    feat_set = self._pruned_featured_set(current_names)
                else:
                    feat_set = set()

                for i, p in enumerate(self.model.params):
                    pname = p.get("name", "")
                    pval  = p.get("value", "")

                    # Row 1: eye (optional) + label + line edit
                    row = QtWidgets.QWidget()
                    row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                    lay = QtWidgets.QHBoxLayout(row)
                    lay.setContentsMargins(6, 0, 6, 0)
                    lay.setSpacing(6)

                    if kind == "note":
                        eye_btn = QtWidgets.QToolButton()
                        is_featured = pname in feat_set
                        eye_btn.setAutoRaise(True)
                        eye_btn.setToolTip("Toggle big view for this parameter")
                        eye_btn.setText("🙈" if not is_featured else "👁")

                        def _mk_toggle(nm=pname, btn=eye_btn):
                            def _toggle():
                                fs = set(self._get_featured_set())  # copy-on-write
                                if nm in fs:
                                    fs.remove(nm)
                                else:
                                    fs.add(nm)
                                # write back (use helper if you later add one)
                                try:
                                    setattr(self.model, "_featured_params", set(fs))
                                except Exception:
                                    pass
                                # defer heavy rebuild to end of event loop tick
                                self._schedule_rebuild()
                            return _toggle
                        eye_btn.clicked.connect(_mk_toggle())
                        lay.addWidget(eye_btn)

                    lab = QtWidgets.QLabel(pname)
                    lab.setStyleSheet("color:#cbd5e1;")
                    lay.addWidget(lab)

                    edit = QtWidgets.QLineEdit(pval)
                    edit.setPlaceholderText("value")
                    edit.setStyleSheet(
                        "QLineEdit{background:#12151a;color:#e6edf3;"
                        "border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
                    )
                    edit.textChanged.connect(lambda txt, idx=i: self._on_param_changed(idx, txt))

                    # Ctrl+B shortcut + context action
                    self._wire_bigedit_shortcut(edit, p.get("name", "value"))
                    act = QAction("Open Big Editor (Ctrl+B)", edit)
                    act.triggered.connect(
                        lambda _=False, e=edit, nm=p.get("name","value"):
                            self._open_big_param_editor(f"Edit: {nm}", e.text(), e)
                    )
                    edit.addAction(act)
                    edit.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
                    lay.addWidget(edit, 1)

                    proxy = QtWidgets.QGraphicsProxyWidget(self)
                    proxy.setWidget(row)
                    proxy.setZValue(self.zValue() + 0.1)
                    proxy.setPos(0, y_cursor)
                    proxy.resize(self.width, self._PARAM_ROW_H)
                    self._param_proxies.append(proxy)

                    y_cursor += self._PARAM_ROW_H

                    # If featured → add a SECOND row right BELOW with a large QTextEdit
                    if kind == "note" and pname in feat_set:
                        big_row = QtWidgets.QWidget()
                        big_row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                        vlay = QtWidgets.QVBoxLayout(big_row)
                        vlay.setContentsMargins(6, 4, 6, 6)  # left flush, a bit of bottom breathing room
                        vlay.setSpacing(4)

                        big = QtWidgets.QTextEdit()
                        big.setAcceptRichText(False)
                        big.setPlainText(pval)
                        big.setStyleSheet(
                            "QTextEdit{background:#0f1216;color:#e6edf3;"
                            "border:1px solid #3c4450;border-radius:6px;padding:6px;}"
                        )
                        def _sync_big(idx=i, w=big):
                            self._on_param_changed(idx, w.toPlainText())
                        big.textChanged.connect(_sync_big)
                        vlay.addWidget(big, 1)

                        big_proxy = QtWidgets.QGraphicsProxyWidget(self)
                        big_proxy.setWidget(big_row)
                        big_proxy.setZValue(self.zValue() + 0.1)
                        big_proxy.setPos(0, y_cursor)
                        big_proxy.resize(self.width, self._NOTE_FEATURED_H)
                        self._param_proxies.append(big_proxy)

                        y_cursor += self._NOTE_FEATURED_H

            # --- LLM embedded webview ---
            if (self.model.kind or "").lower() == "llm":
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
                    proxy.resize(self.width, max(200, LLM_NODE_H // 3))
                    try: proxy.setPreferredSize(self.width, max(200, LLM_NODE_H // 3))
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

                    S = float(LLM_SCALE)
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

                    y_cursor += int(LLM_NODE_H_BASE * LLM_SCALE)

        finally:
            self._is_building = False


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

    def _wire_bigedit_shortcut(self, edit: QtWidgets.QLineEdit, param_name: str):
        # Focus-only hotkey path:
        edit.setFocusPolicy(QtCore.Qt.StrongFocus)
        row = edit.parent() if isinstance(edit.parent(), QtWidgets.QWidget) else None
        if row:
            row.setFocusPolicy(QtCore.Qt.StrongFocus)

        def _activate_bigedit(e=edit, nm=param_name):
            self._open_big_param_editor(f"Edit: {nm}", e.text(), e)

        class _HotkeyFilter(QtCore.QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QtCore.QEvent.KeyPress:
                    if (ev.key() == QtCore.Qt.Key_B) and (ev.modifiers() & QtCore.Qt.ControlModifier):
                        _activate_bigedit()
                        return True
                return super().eventFilter(obj, ev)

        hf = _HotkeyFilter(edit)
        edit.installEventFilter(hf)

        if not hasattr(self, "_hotkey_refs"):
            self._hotkey_refs = []
        self._hotkey_refs.append(hf)

        try:
            seq = QKeySequence(KEY_BIGEDIT)
            sc = QShortcut(seq, edit)
            sc.setContext(QtCore.Qt.WidgetShortcut)  # requires the edit itself to have focus
            sc.activated.connect(_activate_bigedit)
            self._hotkey_refs.append(sc)
        except Exception:
            pass

        try:
            v = self.scene().views()[0] if self.scene() and self.scene().views() else None
            win = v.window() if v else None
            if win and hasattr(win, "_register_bigedit_target"):
                win._register_bigedit_target(edit, self, param_name)
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
        return QtCore.QRectF(-m, -m, self.width + 2 * m, self.height + 2 * m)

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
            p.setBrush(QtGui.QBrush(body))
            p.setPen(self.pen)  # outline pen set in __init__
            p.drawRoundedRect(r, self.radius, self.radius)
        except Exception as e:
            print("[EchoGraph][paint] body fail:", e)

        # --- Stripe color (from registry or default) ---
        stripe_hex = _spec_stripe_color((self.model.kind or "node").lower())

        # --- Top stripe ---
        try:
            p.setOpacity(1.0)
            p.setBrush(QtGui.QColor(stripe_hex))
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(QtCore.QRectF(0, 0, self.width, 8), self.radius, self.radius)
            p.drawRect(QtCore.QRectF(0, 4, self.width, 4))  # solid bar under the rounded cap
        except Exception as e:
            print("[EchoGraph][paint] stripe fail:", e)

        # --- Title (node name) ---
        try:
            # In NodeItem.paint()
            p.setPen(self.titlePen)
            fm = QtGui.QFontMetrics(p.font())
            name_txt = self.model.name or "<Unnamed>"
            print(f"Rendering node with name: {name_txt}")  # Debugging statement
            p.drawText(
                QtCore.QPointF(10, 28),
                fm.elidedText(name_txt, QtCore.Qt.ElideRight, int(self.width - 16)),
            )
        except Exception as e:
            print("[EchoGraph][paint] title fail:", e)

        # --- Kind badge (type pill) ---
        try:
            kb_y = 38  # under the title line
            kb = QtCore.QRectF(self.width - 90, kb_y, 80, 16)
            p.setBrush(QtGui.QBrush(QtGui.QColor("#3b82f6")))
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(kb, 8, 8)
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
            badge = (self.model.kind or "node").upper()
            p.drawText(kb.adjusted(6, 1, -6, -2), QtCore.Qt.AlignCenter, badge)
        except Exception as e:
            print("[EchoGraph][paint] badge fail:", e)

        # --- IO sockets ---
        try:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#cbd5e1"))
            # left (input)
            p.drawEllipse(QtCore.QRectF(-4, self._BASE_H / 2.0 - 4, 8, 8))
            # right (output)
            p.drawEllipse(QtCore.QRectF(self.width - 4, self._BASE_H / 2.0 - 4, 8, 8))
        except Exception as e:
            print("[EchoGraph][paint] sockets fail:", e)


    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            self.model.pos = value
            sc = self.scene()
            if sc:
                for edge in getattr(sc, "_edges", []):
                    if edge.src is self or edge.dst is self:
                        edge.updatePath()
                if hasattr(sc, "_reframe_to_nodes"):
                    try:
                        sc._reframe_to_nodes(margin=8000.0)
                    except Exception:
                        pass
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._lmb_press_scene = self.mapToScene(e.pos())
            self._lmb_started_wire = False
            on_right_socket = (self.width - 12 <= e.pos().x() <= self.width + 6) and (0 <= e.pos().y() <= self._BASE_H)
            if on_right_socket:
                try:
                    self.startWireDrag.emit(self)
                    self._lmb_started_wire = True
                except Exception:
                    pass
            else:
                try:
                    self.clicked.emit(self.model)
                except Exception:
                    pass
            super().mousePressEvent(e)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
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

class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, src, dst):
        super().__init__()
        self.setZValue(0)
        self.src = src
        self.dst = dst
        self.pen_normal = QtGui.QPen(QtGui.QColor("#586473"), 2)
        self.pen_path   = QtGui.QPen(QtGui.QColor("#22c55e"), 3)
        self._highlight = False
        self.setPen(self.pen_normal)
        self.setBrush(QtCore.Qt.NoBrush)
        self.updatePath()
        self.setAcceptHoverEvents(True)

    def setHighlighted(self, h: bool):
        self._highlight = h
        self.setPen(self.pen_path if h else self.pen_normal)
        self.update()

    def updatePath(self):
        s = self.src.scenePos() + QtCore.QPointF(self.src.width, self.src._BASE_H / 2)
        d = self.dst.scenePos() + QtCore.QPointF(0, self.dst._BASE_H / 2)
        dx = max(80, abs(d.x() - s.x()) * 0.5)
        c1 = QtCore.QPointF(s.x() + dx, s.y())
        c2 = QtCore.QPointF(d.x() - dx, d.y())
        path = QtGui.QPainterPath(s)
        path.cubicTo(c1, c2, d)
        self.setPath(path)

    def hoverEnterEvent(self, e):
        pen = QtGui.QPen(self.pen().color(), self.pen().widthF() + 0.5)
        self.setPen(pen)

    def hoverLeaveEvent(self, e):
        self.setHighlighted(self._highlight)

    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if e.button() == QtCore.Qt.LeftButton and (e.modifiers() & QtCore.Qt.AltModifier):
            sc = self.scene()
            if sc and hasattr(sc, "_edges"):
                try: sc._on_edge_removed(self)
                except Exception: pass
                try: sc.removeItem(self)
                except Exception: pass
                try: sc._edges.remove(self)
                except Exception: pass
                e.accept()
                return
        super().mousePressEvent(e)

class TempWire(QtWidgets.QGraphicsPathItem):
    def __init__(self, start_pos: QtCore.QPointF):
        super().__init__()
        self.start = start_pos
        pen = QtGui.QPen(QtGui.QColor("#a1a1aa"), 2, QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen); self.setZValue(5)
    def updateTo(self, end_pos: QtCore.QPointF):
        s = self.start; d = end_pos
        dx = max(80, abs(d.x()-s.x())*0.5)
        c1 = QtCore.QPointF(s.x()+dx, s.y())
        c2 = QtCore.QPointF(d.x()-dx, d.y())
        path = QtGui.QPainterPath(s); path.cubicTo(c1, c2, d)
        self.setPath(path)

# in-window card
class InfoCard(QtWidgets.QFrame):
    requestJump = QtCore.Signal(str)
    closedForNode = QtCore.Signal(str)

    def __init__(self, node: GraphNode, order_index: int=None, order_total: int=None):
        super().__init__()
        self._node_name = node.name
        self._node_ref = node

        try:
            self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        except AttributeError:
            self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("InfoCard")
        self.setStyleSheet("#InfoCard{border:1px solid #3c4450;border-radius:8px;background:#1f232a;}")

        title = QtWidgets.QLineEdit(node.name)
        title.setObjectName("NodeNameEdit")
        f = title.font(); f.setBold(True); title.setFont(f)
        title.setStyleSheet("QLineEdit{background:#12151a;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}")
        title.setToolTip("Rename node")
        def _commit_rename():
            new_name = title.text().strip()
            old_name = getattr(self, "_node_name", "")
            sc = getattr(self, "_graph_scene", None)
            if not sc or not new_name or new_name == old_name:
                return
            ok, msg = sc.rename_node(old_name, new_name)
            if not ok:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, msg or "Rename failed.")
                title.setText(old_name)
                return
            self._node_name = new_name
            self._node_ref.name = new_name
        title.editingFinished.connect(_commit_rename)

        order_badge = None
        if isinstance(order_index, int) and isinstance(order_total, int):
            order_badge = QtWidgets.QLabel(str(order_index))
            order_badge.setFixedSize(22,22)
            order_badge.setAlignment(QtCore.Qt.AlignCenter)
            order_badge.setStyleSheet("QLabel{background:#22c55e;color:#0a0f0a;border-radius:11px;font-weight:700;}")
            order_badge.setToolTip(f"Step {order_index} of {order_total}")

        close_btn = QtWidgets.QToolButton()
        close_btn.setText("✕"); close_btn.setAutoRaise(True); close_btn.setToolTip("Close")
        close_btn.clicked.connect(self._emit_and_close)

        header = QtWidgets.QHBoxLayout(); header.setContentsMargins(0, 0, 0, 0)
        if order_badge: header.addWidget(order_badge)
        header.addWidget(title); header.addStretch(1); header.addWidget(close_btn)

        text = QtWidgets.QTextBrowser()
        self._text_browser = text
        text.setStyleSheet(
            "QTextBrowser{background:#0f1216;color:#e6edf3;"
            "border:1px solid #3c4450;border-radius:6px;}"
        )
        text.setOpenExternalLinks(False)
        text.setOpenLinks(False)
        safe = QtGui.QTextDocument(); safe.setPlainText(node.info or "No info.")
        html = _hash_to_links(safe.toPlainText())
        text.setHtml("<style>body{font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif; font-size:12px;}a{color:#60a5fa;}</style>"+html)
        text.setMinimumHeight(80)

        text.anchorClicked.connect(lambda url: self.requestJump.emit(url.path().lstrip("/")))

        footer = QtWidgets.QHBoxLayout(); footer.setContentsMargins(0, 0, 0, 0); footer.setSpacing(8)

        # Give plugins a chance to add buttons into the footer
        # Give plugins a chance to add buttons into the footer
        _augmented_by_plugin = False
        try:
            spec = core.get_spec((node.kind or "node").lower())
            augment = None
            if isinstance(spec, dict):
                augment = spec.get("augment_infocard_footer")
            else:
                augment = getattr(spec, "augment_infocard_footer", None)

            if callable(augment):
                try:
                    did = bool(augment(self, footer))
                    _augmented_by_plugin = bool(did)
                except Exception as e:
                    print("[EchoGraph] augment_infocard_footer raised:", e)
                    _augmented_by_plugin = False
        except Exception as e:
            print("[EchoGraph] augment_infocard_footer error:", e)
            _augmented_by_plugin = False

        # optional debug (helps confirm the branch you’ll hit next)
        print(f"[EchoGraph] augment hook for '{(node.kind or '').lower()}': "
            f"callable={callable(augment)} result={_augmented_by_plugin}")

        kind = (node.kind or "").lower()

        if kind == "append":
            box = QtWidgets.QGroupBox("Append Inputs (order)")
            lv = QtWidgets.QVBoxLayout(box); lv.setContentsMargins(8,8,8,8); lv.setSpacing(6)

            listw = QtWidgets.QListWidget()
            self._append_list = listw
            listw.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            for nm in (self._node_ref.switch_inputs or []):
                listw.addItem(nm)

            btns = QtWidgets.QHBoxLayout()
            up   = QtWidgets.QPushButton("↑")
            down = QtWidgets.QPushButton("↓")
            apply = QtWidgets.QPushButton("Apply Order")
            btns.addWidget(up); btns.addWidget(down); btns.addWidget(apply); btns.addStretch(1)

            def _move_selected(delta: int):
                r = listw.currentRow()
                if r < 0: return
                nr = r + delta
                if nr < 0 or nr >= listw.count(): return
                it = listw.takeItem(r)
                listw.insertItem(nr, it)
                listw.setCurrentRow(nr)

            def _apply_order():
                new_order = [listw.item(i).text() for i in range(listw.count())]
                self._node_ref.switch_inputs = new_order
                sc = getattr(self, "_graph_scene", None)
                if sc:
                    sc.refresh_node_widget(self._node_ref.name)
                    # Refresh the active output preview using the Append-aware order
                    if getattr(sc, "_current_output_name", None):
                        # rebuild the cards using the new ordered sequence
                        seq = sc.ordered_upstream_items(sc._current_output_name)
                        if hasattr(sc.views()[0].window(), "populate_branch_info"):
                            sc.views()[0].window().populate_branch_info([it.model for it in seq])


            up.clicked.connect(lambda: _move_selected(-1))
            down.clicked.connect(lambda: _move_selected(+1))
            apply.clicked.connect(_apply_order)
            lv.addWidget(listw)
            lv.addLayout(btns)

        if (node.kind or "").lower() == "librarian":
            self._result_view = QtWidgets.QTextBrowser()
            self._result_view.setStyleSheet(
                "QTextBrowser{background:#0f1216;color:#e6edf3;"
                "border:1px solid #3c4450;border-radius:6px;}"
            )
            self._result_view.setMinimumHeight(140)
            self._result_view.setOpenExternalLinks(True)
            self._result_view.setOpenLinks(True)

            self._last_query_text = ""
            self._waiting_ts = None

            self._result_view.setPlainText(
                "No results yet. Send a query to fetch results here."
            )

            # (no top param actions row anymore)
            open_btn = QtWidgets.QPushButton("Open Librarian")
            open_btn.setToolTip("Launch the Librarian UI in its own process")

            open_btn.clicked.connect(lambda: ensure_running(True))
            footer.addWidget(open_btn)

            send_btn = QtWidgets.QPushButton("Send Query → Librarian")
            send_btn.setToolTip("Enqueue a search/summary request for the Librarian to pick up")

            def _send_query():
                def _find_param(params, names):
                    for p in (params or []):
                        nm = (p.get("name") or "").strip().lower()
                        if nm in names:
                            return (p.get("value") or "").strip()
                    return ""

                q_self = _find_param(self._node_ref.params, {"query", "prompt"})
                topk_str = _find_param(self._node_ref.params, {"top_k", "k"})
                try:
                    top_k = max(1, int(topk_str)) if topk_str else 5
                except Exception:
                    top_k = 5

                parts = []
                sc = getattr(self, "_graph_scene", None)
                if sc is not None and hasattr(sc, "upstream_of") and hasattr(sc, "resolve_text_value"):
                    try:
                        for it in sc.upstream_of(self._node_ref.name):
                            txt = sc.resolve_text_value(it)
                            if txt:
                                parts.append(txt.strip())
                    except Exception:
                        pass

                if q_self:
                    parts.append(q_self.strip())

                q = "\n\n".join([p for p in parts if p])[:4000]
                if not q:
                    QtWidgets.QMessageBox.warning(self, APP_TITLE,
                        "No query text found.\nAdd a Prompt node upstream or set this node’s 'query'/'prompt' parameter.")
                    return
                try:
                    ts = int(time.time() * 1000)
                    self._last_query_text = q
                    self._waiting_ts = ts

                    cmd = {
                        "type": "search",
                        "query": q,
                        "top_k": top_k,
                        "from": "EchoGraph",
                        "ts": ts,
                    }
                    fn = enqueue(cmd)
                    # Ensure Librarian UI is up (non-blocking, single instance)
                    ensure_running(True)

                    self._waiting_ts = ts
                    self._last_query_text = q
                    try:
                        self._result_view.setPlainText(
                            "Queued search for Librarian:\n\n"
                            f"{q[:1000]}{'…' if len(q) > 1000 else ''}\n\n"
                            f"Ticket: result_{ts}.json\n"
                            "\nWaiting for results…"
                        )
                    except Exception:
                        pass

                    try:
                        if hasattr(self, "_poll_timer") and self._poll_timer is not None:
                            if not self._poll_timer.isActive():
                                self._poll_timer.start()
                    except Exception:
                        pass

                    try:
                        QtWidgets.QToolTip.showText(
                            QtGui.QCursor.pos(),
                            f"Sent to Librarian\n{q[:200]}{'…' if len(q) > 200 else ''}",
                            self, self.rect(), 1500
                        )
                        QtWidgets.QToolTip.showText(
                            QtGui.QCursor.pos(),
                            f"Queued: {fn.name}",
                            self, self.rect(), 1500
                        )
                    except Exception:
                        pass

                    print(f"[EchoGraph] Enqueued Librarian cmd -> {fn}")

                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to enqueue:\n{e}")

            send_btn.clicked.connect(_send_query)
            footer.addWidget(send_btn)

            self._poll_timer = QtCore.QTimer(self)
            self._poll_timer.setInterval(500)

            def _apply_result_if_ready():
                try:
                    ts = getattr(self, "_waiting_ts", None)
                    if not ts:
                        return
                    txt = load_output_text_by_ts(ts) or ""
                    if not txt:
                        return
                    prefix = f"Query:\n{self._last_query_text}\n\n" if getattr(self, "_last_query_text", "") else ""
                    combined = prefix + txt
                    if combined != self._result_view.toPlainText():
                        self._result_view.setPlainText(combined)
                    self._waiting_ts = None
                except Exception:
                    pass

            def _poll_latest():
                _apply_result_if_ready()

            self._poll_timer.timeout.connect(_poll_latest)
            self._poll_timer.start()

            try:
                self._fswatcher = QtCore.QFileSystemWatcher(self)
                self._fswatcher.addPath(str(outbox_dir()))
                def _on_dir_change(_path):
                    _apply_result_if_ready()
                self._fswatcher.directoryChanged.connect(_on_dir_change)
            except Exception:
                pass

        elif node.code and not _augmented_by_plugin:
            run_btn = QtWidgets.QPushButton("Run Python")
            run_btn.setToolTip("Provides maya.cmds as 'cmds' and Houdini as 'hou'")
            run_btn.clicked.connect(self._run_code)
            footer.addWidget(run_btn)

        # --- Edit/Rename handlers (shared) ---
        def _edit_params():
            sc = getattr(self, "_graph_scene", None)
            if sc is None:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, "Scene not available.")
                return
            dlg = ParamEditorDialog(self, title=f"Edit Parameters — {self._node_ref.name}",
                                    params=self._node_ref.params)
            if _qexec(dlg) == QtWidgets.QDialog.Accepted:
                new_params = dlg.result_params()
                sc.set_node_params(self._node_name, new_params)
                self._node_ref.params = new_params
                try:
                    self.refresh_params_from_model()
                except Exception:
                    pass

        def _rename_node():
            sc = getattr(self, "_graph_scene", None)
            if sc is None:
                return
            text, ok = QtWidgets.QInputDialog.getText(
                self, "Rename Node", "New name:", QtWidgets.QLineEdit.Normal, self._node_ref.name
            )
            if not ok or not text.strip():
                return
            old = self._node_ref.name
            success, msg = sc.rename_node(old, text.strip())
            if not success:
                if msg:
                    QtWidgets.QMessageBox.warning(self, APP_TITLE, msg)
                return
            self._node_ref.name = text.strip()
            self._node_name = self._node_ref.name
            try:
                name_edit = self.findChild(QtWidgets.QLineEdit, "NodeNameEdit")
                if name_edit:
                    name_edit.setText(self._node_ref.name)
            except Exception:
                pass

        # --- Params table + buttons (single source of truth) ---
        self._param_table = QtWidgets.QTableWidget(0, 2)
        self._param_table.setHorizontalHeaderLabels(["Name", "Value"])
        self._param_table.horizontalHeader().setStretchLastSection(True)
        self._param_table.setStyleSheet(
            "QTableWidget{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            "QHeaderView::section{background:#20242b;color:#e6edf3;border:none;}"
        )
        self._param_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._param_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._param_table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked |
            QtWidgets.QAbstractItemView.EditKeyPressed |
            QtWidgets.QAbstractItemView.SelectedClicked
        )

        def _load_params_into_table():
            self._param_table.setRowCount(0)
            for p in (self._node_ref.params or []):
                r = self._param_table.rowCount()
                self._param_table.insertRow(r)
                self._param_table.setItem(r, 0, QtWidgets.QTableWidgetItem(p.get("name","")))
                self._param_table.setItem(r, 1, QtWidgets.QTableWidgetItem(p.get("value","")))
        _load_params_into_table()

        pbtns = QtWidgets.QHBoxLayout()
        addp  = QtWidgets.QPushButton("Add Param")
        delp  = QtWidgets.QPushButton("Remove Selected")
        savep = QtWidgets.QPushButton("Apply Changes")
        for b in (addp, delp, savep):
            pbtns.addWidget(b)
        pbtns.addStretch(1)

        def _add_param_row():
            r = self._param_table.rowCount()
            self._param_table.insertRow(r)
            self._param_table.setItem(r, 0, QtWidgets.QTableWidgetItem("param"))
            self._param_table.setItem(r, 1, QtWidgets.QTableWidgetItem(""))

        def _remove_selected_row():
            r = self._param_table.currentRow()
            if r >= 0:
                self._param_table.removeRow(r)

        def _apply_param_changes():
            new_params = []
            for r in range(self._param_table.rowCount()):
                name_item  = self._param_table.item(r, 0)
                value_item = self._param_table.item(r, 1)
                nm  = (name_item.text()  if name_item  else "").strip()
                val = (value_item.text() if value_item else "")
                if nm:
                    new_params.append({"name": nm, "value": val})
            sc = getattr(self, "_graph_scene", None)
            if sc:
                sc.set_node_params(self._node_name, new_params)
            self._node_ref.params = new_params

        # wire buttons
        addp.clicked.connect(_add_param_row)
        delp.clicked.connect(_remove_selected_row)
        savep.clicked.connect(_apply_param_changes)
        self._param_table.itemChanged.connect(lambda *_: None)

        # --- Place Edit/Rename (stacked under Add/Remove/Apply for EVERY node) ---
        btn_edit = QtWidgets.QPushButton("Edit Params…")
        btn_edit.clicked.connect(_edit_params)
        btn_rename = QtWidgets.QPushButton("Rename…")
        btn_rename.clicked.connect(_rename_node)

        erow = QtWidgets.QHBoxLayout()
        erow.setContentsMargins(0, 0, 0, 0)
        erow.setSpacing(6)
        erow.addWidget(btn_edit)
        erow.addWidget(btn_rename)
        erow.addStretch(1)

        # keep the first row buttons left-aligned
        # (only once — no duplicates)
        pbtns.addStretch(1)

        # stack: row1 (Add/Remove/Apply), row2 (Edit/Rename)
        pcol = QtWidgets.QVBoxLayout()
        pcol.setContentsMargins(0, 0, 0, 0)
        pcol.setSpacing(6)
        pcol.addLayout(pbtns)   # row 1
        pcol.addLayout(erow)    # row 2

        footer.addStretch(1)

        # --- Root layout (assemble ONCE) ---
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        lay.addLayout(header)          # title + close
        lay.addWidget(text)            # info text browser
        lay.addWidget(self._param_table)
        lay.addLayout(pcol)            # stacked param controls (all nodes)

        if hasattr(self, "_result_view"):
            lay.addWidget(self._result_view)   # Librarian results panel

        # Append reorder UI (only for Append cards)
        if (self._node_ref.kind or "").lower() == "append" and 'box' in locals() and box is not None:
            lay.addWidget(box)

        lay.addLayout(footer)          # footer (e.g., Preview Merge / Save JSON)

    def refresh_params_from_model(self):
        """Reload the params table from the live node model."""
        if not hasattr(self, "_param_table"):
            return
        self._param_table.blockSignals(True)
        try:
            self._param_table.setRowCount(0)
            for p in (self._node_ref.params or []):
                r = self._param_table.rowCount()
                self._param_table.insertRow(r)
                self._param_table.setItem(r, 0, QtWidgets.QTableWidgetItem(p.get("name","")))
                self._param_table.setItem(r, 1, QtWidgets.QTableWidgetItem(p.get("value","")))
        finally:
            self._param_table.blockSignals(False)

    def refresh_append_ui_from_model(self):
        """Reload the Append inputs list from the node model's switch_inputs."""
        try:
            if (self._node_ref.kind or "").lower() != "append":
                return
            lw = getattr(self, "_append_list", None)
            if not lw:
                return
            lw.blockSignals(True)
            lw.clear()
            for nm in (self._node_ref.switch_inputs or []):
                lw.addItem(nm)
        finally:
            try:
                lw.blockSignals(False)
            except Exception:
                pass


    def apply_append_preview_if_output(self):
        """Rebuild the preview for Output cards using Append-respecting order."""
        try:
            if (self._node_ref.kind or "").lower() != "output":
                return
            sc = getattr(self, "_graph_scene", None)
            tb = getattr(self, "_text_browser", None)
            if not sc or not tb or not hasattr(sc, "merged_text_for_output"):
                return
            pairs = sc.merged_text_for_output(self._node_ref.name)  # [(node, text)] in correct order
            merged = "\n\n".join(t for _, t in pairs)
            tb.setPlainText(merged)
        except Exception:
            pass


    def _emit_and_close(self):
        try:
            if hasattr(self, "_poll_timer") and self._poll_timer is not None:
                self._poll_timer.stop()
                self._poll_timer.deleteLater()
        except Exception:
            pass
        try:
            self._waiting_ts = None
            self._last_query_text = ""
        except Exception:
            pass
        try:
            self.closedForNode.emit(self._node_name)
        except Exception:
            pass
        self.deleteLater()

    def closeEvent(self, e):
        try:
            if hasattr(self, "_poll_timer") and self._poll_timer is not None:
                self._poll_timer.stop()
                self._poll_timer.deleteLater()
        except Exception:
            pass
        try:
            self._waiting_ts = None
            self._last_query_text = ""
        except Exception:
            pass
        super().closeEvent(e)

    def _edit_code(self):
        dlg = CodeEditorDialog(self, initial_code=self._node_ref.code or "")
        if _qexec(dlg) == QtWidgets.QDialog.Accepted:
            self._node_ref.code = dlg.code()

    def _run_code(self):
        node = self._node_ref
        src = (node.code or "").strip()
        if not src:
            return
        ns = {
            "cmds": maya_cmds,
            "hou":  hou_mod,
            "QtWidgets": QtWidgets,
            "QtCore": QtCore,
            "QtGui": QtGui,
            "__name__": "__echograph_exec__",
        }
        import io, contextlib, traceback
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                exec(src, ns, ns)
            out = out_buf.getvalue().strip()
            err = err_buf.getvalue().strip()
            if err:
                QtWidgets.QMessageBox.critical(self, APP_TITLE, err)
            elif out:
                QtWidgets.QMessageBox.information(self, APP_TITLE, out)
        except Exception:
            combined = out_buf.getvalue() + "\n" + err_buf.getvalue()
            tb = traceback.format_exc()
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"{combined}\n{tb}")

# scene/view/graph ops
class GraphScene(QtWidgets.QGraphicsScene):
    nodeDeleted = QtCore.Signal(str)
    paramChanged = QtCore.Signal(str, list)  # (node_name, params)
    
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
        self._drag_src_item = None
        self._temp_wire = None
        self._current_output_name = None

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

    def resolve_text_value(self, node_item) -> str:
        """Return a textual value for a node.
        Priority:
        1) Well-known keys ('prompt', 'text', 'content') if present.
        2) Otherwise join ALL non-empty param values (in row order).
        3) Fallback to node.info.
        """
        try:
            params = list(node_item.model.params or [])
            # 1) well-known keys first
            keys = {"prompt", "text", "content"}
            for k in keys:
                for p in params:
                    if (p.get("name","") or "").strip().lower() == k:
                        v = (p.get("value","") or "").strip()
                        if v:
                            return v

            # 2) ANY non-empty params (makes Append accept arbitrary names)
            vals = []
            for p in params:
                v = (p.get("value","") or "").strip()
                if v:
                    vals.append(v)
            if vals:
                return "\n".join(vals)

            # 3) fallback
            return (node_item.model.info or "").strip()
        except Exception:
            return ""

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

        node = GraphNode(final_name, kind=data["kind"], info="User-created node.",
                        params=data["params"], code=data.get("code"))
                
        # Check if it's a Librarian node
        if (data["kind"] or "").lower() == "librarian":
            node.info = "Librarian node created"
            # Ensure a 'query' param exists; don't blow away user-specified params
            names = { (p.get("name") or "").strip().lower() for p in (node.params or []) }
            if "query" not in names:
                node.params = list(node.params or [])
                node.params.append({"name": "query", "value": ""})

        item = self.add_node(node, scene_pos)
        item.setPos(scene_pos - QtCore.QPointF(item.width/2.0, item.height/2.0))
        if callable(self.on_info): self.on_info(node)

    # --- in GraphScene.to_dict(self) ---
    def to_dict(self):
        nodes = []
        for node in self._nodes_by_name.values():
            nd = {
                "name": node.name,
                "kind": node.kind,
                "info": node.info or "",
                "code": node.code if node.code is not None else None,
                "pos": [float(node.pos.x()), float(node.pos.y())],
                "params": [{"name": p["name"], "value": p.get("value","")} for p in (node.params or [])],
            }

            k = (node.kind or "").lower()
            if k in ("switch", "append"):
                nd["switch_inputs"] = list(node.switch_inputs)
                if k == "switch":
                    nd["switch_index"] = int(node.switch_index)

            # ▶ NEW: persist Note “eye” visibility state
            if (node.kind or "").lower() == "note":
                feat = getattr(node, "_featured_params", None)
                if isinstance(feat, set):
                    nd["featured_params"] = sorted(feat)
                elif isinstance(feat, (list, tuple)):
                    nd["featured_params"] = [str(x) for x in feat if x]
                else:
                    # legacy single string support
                    legacy = getattr(node, "_featured_param", "")
                    if legacy:
                        nd["featured_params"] = [legacy]

            nodes.append(nd)

        edges = [{"src": e.src.model.name, "dst": e.dst.model.name} for e in self._edges]
        return {
            "nodes": nodes,
            "edges": edges,
            "llm_scale": float(LLM_SCALE),
            "settings": {"llm_scale": float(LLM_SCALE)},
        }

    # --- in GraphScene.from_dict(self, data) ---
    def from_dict(self, data):
        self.clear_scene()

        # ADD this (apply saved scale before building nodes):
        try:
            s = float((data.get("settings", {}) or {}).get("llm_scale",
                    data.get("llm_scale", LLM_SCALE)))
            set_global_llm_scale(s, self)
        except Exception:
            pass

        for nd in data.get("nodes", []):
            n = GraphNode(
                nd["name"], nd.get("kind","node"), nd.get("info",""), nd.get("code"),
                params=nd.get("params", []),
                switch_inputs=nd.get("switch_inputs", []),
                switch_index=nd.get("switch_index", 0)
            )
            
            # ▶ NEW: restore Note “eye” visibility state
            if (n.kind or "").lower() == "note":
                raw = nd.get("featured_params")
                if not raw:
                    # migrate legacy fields if present
                    raw = []
                    for k in ("featured_param", "_featured_param"):
                        v = nd.get(k)
                        if v:
                            raw.append(v)
                            break
                try:
                    s = {str(x) for x in (raw or []) if x}
                except Exception:
                    s = set()
                setattr(n, "_featured_params", s)
                # clear legacy single slot to avoid confusion
                try:
                    setattr(n, "_featured_param", "")
                except Exception:
                    pass

            pos = QtCore.QPointF(*nd.get("pos",[0,0]))
            self.add_node(n, pos)
        # Rebuild edges  
        for ed in data.get("edges", []):
            try: self._add_edge_and_update_switch(ed["src"], ed["dst"])
            except Exception: pass
        self._refresh_all_switch_widgets()
        self._reframe_to_nodes(margin=8000.0)
        if self._current_output_name and self._current_output_name in self._node_items:
            self.recompute_active_path(self._current_output_name)
        else:
            self._clear_path_highlight()

    def clear_scene(self):
        for e in list(self._edges):
            try: self.removeItem(e)
            except Exception: pass
        self._edges.clear()
        for it in list(self._node_items.values()):
            try: self.removeItem(it)
            except Exception: pass
        self._node_items.clear(); self._nodes_by_name.clear()
        self._current_output_name = None
        self.setSceneRect(QtCore.QRectF(-20000, -20000, 40000, 40000))

    def add_node(self, node: GraphNode, pos):
        self._ensure_space(pos)
        item = NodeItem(node); item.setPos(pos); node.pos=pos
        item.clicked.connect(self._on_node_clicked)
        item.requestCenter.connect(self.center_on_name)
        item.startWireDrag.connect(self._on_start_wire_drag)
        item.switchIndexChanged.connect(self._on_switch_index_changed)
        self.addItem(item)
        self._nodes_by_name[node.name]=node; self._node_items[node.name]=item
        self._reframe_to_nodes(margin=8000.0)
        return item

    def add_edge(self, src_name, dst_name):
        return self._add_edge_and_update_switch(src_name, dst_name)

    def _add_edge_and_update_switch(self, src_name, dst_name):
        src = self._node_items[src_name]; dst = self._node_items[dst_name]
        edge = EdgeItem(src, dst)
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
                dst.model.switch_index = max(0, min(dst.model.switch_index,
                                                    max(0, len(dst.model.switch_inputs)-1)))
            # refresh UI for both cases
            self.refresh_node_widget(dst.model.name)

        if self._current_output_name:
            self.recompute_active_path(self._current_output_name)

    def delete_node_by_name(self, name: str):
        item = self._node_items.get(name)
        if not item: return
        for e in list(self._edges):
            if e.src is item or e.dst is item:
                try: self._on_edge_removed(e)
                except Exception: pass
                try: self.removeItem(e)
                except Exception: pass
                try: self._edges.remove(e)
                except Exception: pass
        try: self.removeItem(item)
        except Exception: pass
        self._node_items.pop(name, None)
        self._nodes_by_name.pop(name, None)
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

    def _refresh_all_switch_widgets(self):
        for it in self._node_items.values():
            if (it.model.kind or "").lower()=="switch":
                self._refresh_switch_widget(it)

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
            target = self._node_at_left_socket(e.scenePos())
            if target and (target is not self._drag_src_item):
                try: self._add_edge_and_update_switch(self._drag_src_item.model.name, target.model.name)
                except Exception: pass
            self._cancel_temp_wire(); e.accept(); return
        super().mouseReleaseEvent(e)

    def _node_at_left_socket(self, scene_pos: QtCore.QPointF):
        items = self.items(scene_pos)
        for it in items:
            if isinstance(it, NodeItem):
                lp = it.mapFromScene(scene_pos)
                if -6 <= lp.x() <= 12 and 0 <= lp.y() <= it._BASE_H:
                    return it
        return None

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



class GraphView(QtWidgets.QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)

        try:
            self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        except AttributeError:
            self.setViewportUpdateMode(QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

        try:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        except AttributeError:
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)

        self.setCursor(QtCore.Qt.ArrowCursor)

        try:
            self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        except AttributeError:
            self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.NoAnchor)

        self.setBackgroundBrush(QtGui.QColor("#1a1f24"))
        try:
            self.viewport().setStyleSheet("background:#1a1f24;")
        except Exception:
            pass
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        try:
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        except Exception:
            pass

        self._mm_dragging = False
        self._mm_last_pos = None

        self._rc_dragging = False
        self._rc_press_pos = None
        self._rc_started_over_llm = False
        self._rc_start_transform = QtGui.QTransform()
        self._rc_press_scene_pt = QtCore.QPointF()

        self._context_click_thresh = 4.0

        self._drag_divisor = 13.0
        self._zoom_multiplier = 1.1
        self._min_scale = 0.02
        self._max_scale = 50.0

    def _zoom_at(self, viewport_pos: QtCore.QPoint, factor: float):
        before = self.mapToScene(viewport_pos)
        self.scale(factor, factor)
        after = self.mapToScene(viewport_pos)
        delta = after - before
        self.translate(delta.x(), delta.y())

    def _current_scale_x(self) -> float:
        t = self.transform()
        try:
            return float(t.m11())
        except Exception:
            return 1.0

    def _clamp_factor_from(self, start_scale: float, factor: float) -> float:
        target = start_scale * factor
        if target < self._min_scale:
            return self._min_scale / max(start_scale, 1e-12)
        if target > self._max_scale:
            return self._max_scale / max(start_scale, 1e-12)
        return factor

    def _is_over_llm_view(self, viewport_pos: QtCore.QPoint) -> bool:
        sp = self.mapToScene(viewport_pos)
        for it in self.scene().items(sp):
            if isinstance(it, QtWidgets.QGraphicsProxyWidget):
                w = it.widget()
                if w is not None:
                    if w.findChild(QtWidgets.QWidget, "LLMWebView") is not None:
                        return True
            if isinstance(it, NodeItem):
                pr = getattr(it, "_llm_proxy", None)
                if isinstance(pr, QtWidgets.QGraphicsProxyWidget):
                    if pr.mapRectToScene(pr.boundingRect()).contains(sp):
                        return True
        return False

    def drawBackground(self, p: QtGui.QPainter, rect: QtCore.QRectF):
        p.fillRect(rect, QtGui.QColor("#1a1f24"))

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() == QtCore.Qt.Key_Delete:
            sc = self.scene()
            if hasattr(sc, "delete_selected_nodes"):
                sc.delete_selected_nodes()
                e.accept()
                return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            self._mm_dragging = True
            self._mm_last_pos = e.pos()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept(); return

        if e.button() == QtCore.Qt.RightButton:
            self._rc_started_over_llm = self._is_over_llm_view(e.pos())
            if self._rc_started_over_llm:
                super().mousePressEvent(e)
                return
            self._rc_dragging = True
            self._rc_press_pos = e.pos()
            self._rc_start_transform = QtGui.QTransform(self.transform())
            self._rc_press_scene_pt = self.mapToScene(self._rc_press_pos)
            e.accept(); return

        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._mm_dragging and self._mm_last_pos is not None:
            delta = e.pos() - self._mm_last_pos
            self._mm_last_pos = e.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            e.accept()
            return
        if self._rc_dragging and self._rc_press_pos is not None:
            dx = e.pos().x() - self._rc_press_pos.x()
            dy = e.pos().y() - self._rc_press_pos.y()
            distance = dy - dx
            exponent = abs(distance) / self._drag_divisor
            base = self._zoom_multiplier
            factor = base ** (-exponent) if distance > 0 else base ** (exponent)
            start_sx = float(self._rc_start_transform.m11()) or 1.0
            factor = self._clamp_factor_from(start_sx, factor)
            p = self._rc_press_scene_pt
            T = QtGui.QTransform(self._rc_start_transform)
            T.translate(p.x(), p.y())
            T.scale(factor, factor)
            T.translate(-p.x(), -p.y())
            self.setTransform(T)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            self._mm_dragging = False
            self._mm_last_pos = None
            self.viewport().setCursor(QtCore.Qt.ArrowCursor)
            e.accept(); return

        if e.button() == QtCore.Qt.RightButton:
            if self._rc_started_over_llm:
                self._rc_started_over_llm = False
                super().mouseReleaseEvent(e)
                return
            is_context = False
            if self._rc_press_pos is not None:
                dx = e.pos().x() - self._rc_press_pos.x()
                dy = e.pos().y() - self._rc_press_pos.y()
                if math.hypot(dx, dy) <= self._context_click_thresh:
                    is_context = True
            self._rc_dragging = False
            self._rc_press_pos = None
            if is_context and not self._is_over_llm_view(e.pos()):
                sp = self.mapToScene(e.pos())
                sc = self.scene()
                if hasattr(sc, "show_create_dialog_at"):
                    sc.show_create_dialog_at(sp)
                e.accept(); return
            e.accept(); return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e: QtGui.QWheelEvent):
        factor = 1.15 if e.angleDelta().y() > 0 else 1/1.15
        try:
            vp = e.position()
            vp = QtCore.QPoint(int(vp.x()), int(vp.y()))
        except AttributeError:
            vp = e.pos()
        start_sx = self._current_scale_x()
        factor = self._clamp_factor_from(start_sx, factor)
        self._zoom_at(vp, factor)

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

    def _register_bigedit_target(self, lineedit: QtWidgets.QLineEdit, node_item: 'NodeItem', param_name: str):
        self._bigedit_registry[lineedit] = (node_item, param_name)

    def _build_topbar(self):
        bar = QtWidgets.QFrame(); bar.setObjectName("TopBar")
        bar.setStyleSheet("#TopBar{background:#20242b;border-bottom:1px solid #333;} PushButton{padding:6px 12px;font-weight:600;}")
        bar.setFixedHeight(36)
        h = QtWidgets.QHBoxLayout(bar); h.setContentsMargins(8,4,8,4); h.setSpacing(8)

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
            info="User-created node.",
            params=data["params"],
            code=data.get("code"),
        )

        # Librarian nicety (same as right-click path)
        if (data["kind"] or "").lower() == "librarian":
            node.info = "Librarian node created"
            names = { (p.get("name") or "").strip().lower() for p in (node.params or []) }
            if "query" not in names:
                node.params = list(node.params or [])
                node.params.append({"name": "query", "value": ""})

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


    def _open_graph(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Graph (.json)", "", "JSON Files (*.json)")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.scene.from_dict(data)

            # ← restore LLM scale and slider from file (default to current if missing)
            s = float(data.get("llm_scale", LLM_SCALE))
            set_global_llm_scale(s, self.scene)
            if hasattr(self, "_llm_slider"):
                self._llm_slider.blockSignals(True)
                self._llm_slider.setValue(int(round(s * 100)))
                if hasattr(self, "_llm_value_lbl"):
                    self._llm_value_lbl.setText(f"{int(round(s*100))}%")
                self._llm_slider.blockSignals(False)

            self._current_path = path
            if self.scene._node_items:
                first = next(iter(self.scene._node_items.values()))
                self.view.centerOn(first)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to open:\n{e}")

    def _save_graph(self):
        if not self._current_path:
            return self._export_graph()
        try:
            data = self.scene.to_dict()
            with open(self._current_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
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

            # --- Append-respecting preview (if we have an active Output and the helper exists) ---
            try:
                out_name = getattr(self.scene, "_current_output_name", "") or ""
                if out_name and hasattr(self.scene, "merged_text_for_output"):
                    pairs = self.scene.merged_text_for_output(out_name)  # [(node_name, text)]
                    data.setdefault("preview", {})
                    data["preview"]["output"] = out_name
                    data["preview"]["ordered_pairs"] = [{"node": n, "text": t} for (n, t) in pairs]
                    data["preview"]["merged_text"] = "\n\n".join(t for _, t in pairs)
            except Exception:
                # Don't block export if preview assembly fails
                pass

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            self._current_path = path
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
        try:
            card._graph_scene = self.scene
        except Exception:
            pass

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
            try:
                card._graph_scene = self.scene
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

