# ==== echograph (single-file shelf script; runs on click) ======================
# Maya + Houdini PySide2 node-graph demo with info cards, Output path highlight,
# multi-input Switch with slider, JSON Open/Save/Export, node params, wiring, and code editor.
# Nav: Left-click = select / wire-drag; Middle-mouse = pan; Right-drag = zoom.
# Alt+LeftClick a link to delete it. Badge (type bubble) is below the name.
# Quick-create: right-click empty canvas (no drag) to open Create Node.
# Delete/Backspace removes selected nodes with their links.
# Clicking an Output node auto-fills Info pane with ordered branch cards (start → output).

# --- unified imports (PySide6 preferred, fallback to PySide2) ---
import sys, re, json, math, os, time
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from shiboken6 import wrapInstance
    except Exception:
        wrapInstance = None
    QT_IS_6 = True
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    try:
        from shiboken2 import wrapInstance
    except Exception:
        wrapInstance = None
    QT_IS_6 = False

# --- cross-version shortcut helpers (PySide6 vs PySide2) ---
try:
    QShortcut = QtGui.QShortcut
except AttributeError:
    QShortcut = QtWidgets.QShortcut

QKeySequence = QtGui.QKeySequence

try:
    QAction = QtGui.QAction
except AttributeError:
    QAction = QtWidgets.QAction

# ---- PySide2/6-safe modal exec ----
def _qexec(dlg: QtWidgets.QDialog) -> int:
    try:
        return dlg.exec()
    except AttributeError:
        return dlg.exec_()

# --- Hotkey used on Windows/Linux ---
KEY_BIGEDIT = "Ctrl+B"

# --- WebEngine (for embedding Gradio UI) ---
try:
    from PySide6 import QtWebEngineWidgets as WebEngine
except Exception:
    try:
        from PySide2 import QtWebEngineWidgets as WebEngine
    except Exception:
        WebEngine = None

LLM_URL = "http://127.0.0.1:7860"
LLM_EMBED_HEIGHT = 900
ASPECT_W, ASPECT_H = 16, 9
LLM_NODE_W = 1920
LLM_NODE_H = 1080 + 90

def _script_dir():
    if "__file__" in globals():
        try:
            return Path(__file__).resolve().parent
        except Exception:
            pass
    try:
        return Path.cwd()
    except Exception:
        return Path.home()

ICON_PATH = _script_dir() / "icons" / "EchoMatrixMCP_Icon_s.png"
APP_ICON = QtGui.QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QtGui.QIcon()

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

# ---- Librarian IPC (file-based) ----
def _librarian_inbox_dir() -> Path:
    env_root = os.getenv("LIBRARIAN_ROOT", "").strip().strip('"').strip("'")
    if env_root:
        base = Path(env_root).resolve()
        lib_dir = base
        if not (lib_dir / "ipc").exists() and (base / "nodes" / "librarian").exists():
            lib_dir = base / "nodes" / "librarian"
    else:
        base = _script_dir()
        lib_dir = base / "nodes" / "librarian"
    inbox = lib_dir / "ipc" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox

def _enqueue_librarian(cmd: dict) -> Path:
    env_root_raw = os.getenv("LIBRARIAN_ROOT", "").strip().strip('"').strip("'")
    if env_root_raw:
        base = Path(env_root_raw).resolve()
        lib_dir = base if (base / "ipc").exists() else (base / "nodes" / "librarian")
    else:
        base = _script_dir()
        lib_dir = base / "nodes" / "librarian"

    inbox = (lib_dir / "ipc" / "inbox")
    inbox.mkdir(parents=True, exist_ok=True)

    ctype = (cmd.get("type") or cmd.get("action") or "").strip().lower()
    if not ctype:
        if "query" in cmd:
            ctype = "search"
        elif "question" in cmd:
            ctype = "analyze"
        else:
            ctype = "summarize"
    cmd["type"] = ctype
    cmd["action"] = ctype
    cmd.setdefault("from", "EchoGraph")
    cmd.setdefault("ts", int(time.time() * 1000))

    fn = inbox / f"cmd_{int(time.time()*1000)}_{os.getpid()}.json"
    text = json.dumps(cmd, ensure_ascii=False, indent=2)
    with open(fn, "w", encoding="utf-8", newline="\n") as f:
        f.write(text); f.flush(); os.fsync(f.fileno())

    if not fn.exists() or fn.stat().st_size == 0:
        raise RuntimeError(
            f"IPC write verification failed.\nTried: {fn}\n"
            f"LIBRARIAN_ROOT={env_root_raw or '<unset>'}\n"
            f"script_dir={_script_dir()}\n"
            f"lib_dir={lib_dir}\n"
        )
    return fn

def _librarian_outbox_dir() -> Path:
    env_root_raw = os.getenv("LIBRARIAN_ROOT", "").strip().strip('"').strip("'")
    if env_root_raw:
        base = Path(env_root_raw).resolve()
        lib_dir = base if (base / "ipc").exists() else (base / "nodes" / "librarian")
    else:
        base = _script_dir()
        lib_dir = base / "nodes" / "librarian"
    outbox = lib_dir / "ipc" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    return outbox

def _read_json_silent(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _render_hits_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "No result data."
    lines = []
    if payload.get("summary"):
        lines.append(str(payload["summary"]).strip())
    if payload.get("answer"):
        lines.append(str(payload["answer"]).strip())
    for key in ("results", "hits", "items"):
        arr = payload.get(key)
        if isinstance(arr, (list, tuple)) and arr:
            lines.append("")
            lines.append(f"Top {min(len(arr), 5)} results:")
            for i, it in enumerate(arr[:5], 1):
                if isinstance(it, dict):
                    title = it.get("title") or it.get("name") or it.get("id") or f"Result {i}"
                    snippet = it.get("snippet") or it.get("summary") or it.get("text") or ""
                    lines.append(f"{i}. {title}")
                    if snippet:
                        s = str(snippet).strip().replace("\r", "").replace("\n", " ")
                        if len(s) > 240: s = s[:240] + "…"
                        lines.append(f"   {s}")
                else:
                    s = str(it)
                    if len(s) > 240: s = s[:240] + "…"
                    lines.append(f"{i}. {s}")
            break
    if not lines:
        lines = [json.dumps(payload, ensure_ascii=False, indent=2)]
    return "\n".join(lines).strip()

def _load_librarian_output_text_by_ts(ts: int) -> str:
    outbox = _librarian_outbox_dir()
    p = outbox / f"result_{int(ts)}.json"
    if not p.exists():
        return ""
    payload = _read_json_silent(p)
    return _render_hits_text(payload) if payload else ""

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

APP_TITLE = "EchoGraph"
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
        self._switch_proxy = None
        self._llm_proxy = None
        self._llm_view = None

        self._recompute_height()
        self._build_widgets()

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
        switch_h = self._PARAM_ROW_H if (self.model.kind or "").lower() == "switch" else 0
        n_params = len(self.model.params)
        params_h = n_params * self._PARAM_ROW_H + (self._PADDING if n_params else 0)
        if (self.model.kind or "").lower() == "llm":
            body_h = LLM_NODE_H
            node_w = LLM_NODE_W
        else:
            body_h = 0
            node_w = max(self._BASE_W, self.width)
        new_h = self._BASE_H + switch_h + params_h + body_h
        self.prepareGeometryChange()
        self.width  = int(node_w)
        self.height = int(new_h)

    def _clear_widget_proxies(self):
        if self._switch_proxy:
            try:
                sc = self.scene()
                if sc: sc.removeItem(self._switch_proxy)
            except Exception:
                pass
            self._switch_proxy = None
        for pr in list(self._param_proxies):
            try:
                sc = self.scene()
                if sc: sc.removeItem(pr)
            except Exception:
                pass
        self._param_proxies[:] = []
        if self._llm_proxy:
            try:
                sc = self.scene()
                if sc: sc.removeItem(self._llm_proxy)
            except Exception:
                pass
            self._llm_proxy = None
        self._llm_view = None

    def _build_widgets(self):
        self._clear_widget_proxies()
        y_cursor = 38 + 16 + self._PADDING

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
            for i, p in enumerate(self.model.params):
                row = QtWidgets.QWidget()
                row.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                lay = QtWidgets.QHBoxLayout(row)
                lay.setContentsMargins(6, 0, 6, 0)
                lay.setSpacing(6)

                lab = QtWidgets.QLabel(p["name"])
                lab.setStyleSheet("color:#cbd5e1;")

                edit = QtWidgets.QLineEdit(p.get("value", ""))
                edit.setPlaceholderText("value")
                edit.setStyleSheet(
                    "QLineEdit{background:#12151a;color:#e6edf3;"
                    "border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
                )
                edit.textEdited.connect(lambda txt, idx=i: self._on_param_changed(idx, txt))

                # Focus-only Ctrl+B wiring
                self._wire_bigedit_shortcut(edit, p.get("name", "value"))

                # Context action
                act = QAction("Open Big Editor (Ctrl+B)", edit)
                act.triggered.connect(
                    lambda _=False, e=edit, nm=p.get("name", "value"):
                        self._open_big_param_editor(f"Edit: {nm}", e.text(), e)
                )
                edit.addAction(act)
                edit.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

                lay.addWidget(lab); lay.addWidget(edit, 1)

                proxy = QtWidgets.QGraphicsProxyWidget(self)
                proxy.setWidget(row)
                proxy.setZValue(self.zValue() + 0.1)
                proxy.setPos(0, y_cursor)
                proxy.resize(self.width, self._PARAM_ROW_H)
                self._param_proxies.append(proxy)

                y_cursor += self._PARAM_ROW_H

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
                container = QtWidgets.QWidget()
                container.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                container.setMinimumSize(self.width, LLM_NODE_H)
                container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

                v = QtWidgets.QVBoxLayout(container); v.setContentsMargins(0,0,0,0); v.setSpacing(0)

                view = WebEngine.QWebEngineView(container)
                view.setObjectName("LLMWebView")
                view.setMinimumHeight(LLM_NODE_H)
                view.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                view.setUrl(QtCore.QUrl(self._llm_url_from_params()))
                self._llm_view = view
                v.addWidget(view)

                proxy = QtWidgets.QGraphicsProxyWidget(self)
                proxy.setWidget(container)
                proxy.setZValue(self.zValue() + 0.1)
                proxy.setPos(0, y_cursor)
                proxy.resize(self.width, LLM_NODE_H)
                try: proxy.setPreferredSize(self.width, LLM_NODE_H)
                except AttributeError: pass
                self._llm_proxy = proxy

                y_cursor += LLM_NODE_H

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
        if (self.model.kind or "").lower() == "llm":
            try:
                name = (self.model.params[idx]["name"] or "").lower()
            except Exception:
                name = ""
            if name in ("url", "address", "endpoint") and getattr(self, "_llm_view", None):
                self._llm_view.setUrl(QtCore.QUrl(self._normalize_url(txt)))

    def _wire_bigedit_shortcut(self, edit: QtWidgets.QLineEdit, param_name: str):
        # Focusability through the proxy
        edit.setFocusPolicy(QtCore.Qt.StrongFocus)
        row = edit.parent() if isinstance(edit.parent(), QtWidgets.QWidget) else None
        if row:
            row.setFocusPolicy(QtCore.Qt.StrongFocus)

        def _activate_bigedit(e=edit, nm=param_name):
            self._open_big_param_editor(f"Edit: {nm}", e.text(), e)

        # Per-edit KeyPress filter: only sees keys when this edit has focus
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

        # Strict focus-only QShortcut living on the edit
        try:
            seq = QKeySequence(KEY_BIGEDIT)
            sc = QShortcut(seq, edit)
            sc.setContext(QtCore.Qt.WidgetShortcut)  # requires the edit itself to have focus
            sc.activated.connect(_activate_bigedit)
            self._hotkey_refs.append(sc)
        except Exception:
            pass

        # Register with the main window so the app-level filter can resolve from FOCUS
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
            apply_to_lineedit.setText(dlg.text())

    def boundingRect(self):
        m = 6
        return QtCore.QRectF(-m, -m, self.width + 2 * m, self.height + 2 * m)

    def shape(self):
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(0, 0, self.width, self.height), self.radius, self.radius)
        return path

    def paint(self, p, opt, w=None):
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = QtCore.QRectF(0, 0, self.width, self.height)
        body = QtGui.QColor("#262930" if not self._hover else "#2f343c")
        p.setBrush(QtGui.QBrush(body))
        p.setPen(self.pen)
        p.drawRoundedRect(r, self.radius, self.radius)

        color_map = {
            "switch": "#f59e0b",
            "python": "#10b981",
            "import": "#3b82f6",
            "output": "#a855f7",
            "node": "#64748b",
            "llm": "#14b8a6",
            "librarian": "#74d603",
        }
        stripe = color_map.get((self.model.kind or "node").lower(), "#64748b")
        p.setBrush(QtGui.QColor(stripe))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(QtCore.QRectF(0, 0, self.width, 8), self.radius, self.radius)
        p.drawRect(QtCore.QRectF(0, 4, self.width, 4))

        p.setPen(self.titlePen)
        fm = QtGui.QFontMetrics(p.font())
        p.drawText(
            QtCore.QPointF(10, 28),
            fm.elidedText(self.model.name, QtCore.Qt.ElideRight, int(self.width - 16)),
        )

        kb_y = 38
        kb = QtCore.QRectF(self.width - 90, kb_y, 80, 16)
        p.setBrush(QtGui.QBrush(QtGui.QColor("#3b82f6")))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(kb, 8, 8)
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        badge = (self.model.kind or "node").upper()
        p.drawText(kb.adjusted(6, 1, -6, -2), QtCore.Qt.AlignCenter, badge)

        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#cbd5e1"))
        p.drawEllipse(QtCore.QRectF(-4, self._BASE_H / 2.0 - 4, 8, 8))
        p.drawEllipse(QtCore.QRectF(self.width - 4, self._BASE_H / 2.0 - 4, 8, 8))

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

# -------- Code Editor Dialog --------
class CodeEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, initial_code:str=""):
        super().__init__(parent)
        self.setWindowTitle("Python Code")
        self.setMinimumSize(600, 360)
        v = QtWidgets.QVBoxLayout(self)
        self.edit = QtWidgets.QPlainTextEdit()
        self.edit.setPlainText(initial_code or "")
        fm = self.edit.fontMetrics()
        try:
            space_w = fm.horizontalAdvance(' ')
        except AttributeError:
            space_w = fm.width(' ')
        self.edit.setTabStopDistance(4 * space_w)
        self.edit.setStyleSheet("QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}")
        v.addWidget(self.edit, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)
    def code(self)->str:
        return self.edit.toPlainText()

class ParamEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, title="Edit Parameters", params=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 360)

        v = QtWidgets.QVBoxLayout(self)

        self.table = QtWidgets.QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Name", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed
        )
        v.addWidget(self.table, 1)

        for p in (params or []):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(p.get("name", "")))
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(p.get("value", "")))

        rowBtns = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add")
        rem_btn = QtWidgets.QPushButton("Remove")
        up_btn  = QtWidgets.QPushButton("↑")
        dn_btn  = QtWidgets.QPushButton("↓")
        rowBtns.addWidget(add_btn); rowBtns.addWidget(rem_btn); rowBtns.addStretch(1)
        rowBtns.addWidget(up_btn); rowBtns.addWidget(dn_btn)
        v.addLayout(rowBtns)

        def add_row():
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(""))
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(""))
            self.table.editItem(self.table.item(r, 0))
        def rm_row():
            for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
                self.table.removeRow(r)
        def move_row(delta):
            sel = sorted({i.row() for i in self.table.selectedIndexes()})
            if len(sel) != 1: return
            r = sel[0]; nr = r + delta
            if nr < 0 or nr >= self.table.rowCount(): return
            for c in range(2):
                a = self.table.takeItem(r, c)
                b = self.table.takeItem(nr, c)
                self.table.setItem(r, c, b); self.table.setItem(nr, c, a)
            self.table.selectRow(nr)

        add_btn.clicked.connect(add_row)
        rem_btn.clicked.connect(rm_row)
        up_btn.clicked.connect(lambda: move_row(-1))
        dn_btn.clicked.connect(lambda: move_row(+1))

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        v.addWidget(bb)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

    def result_params(self):
        out = []
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text() if self.table.item(r,0) else "").strip()
            value = (self.table.item(r, 1).text() if self.table.item(r,1) else "")
            if name:
                out.append({"name": name, "value": value})
        return out

class BigTextEditDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, title="Edit Text", initial=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 360)
        self.setModal(True)
        v = QtWidgets.QVBoxLayout(self)

        self.edit = QtWidgets.QPlainTextEdit()
        self.edit.setPlainText(initial or "")
        self.edit.setStyleSheet(
            "QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}"
        )
        v.addWidget(self.edit, 1)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def text(self):
        return self.edit.toPlainText()

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

        kind = (node.kind or "").lower()

        if kind == "python":
            edit_btn = QtWidgets.QPushButton("Edit Code…")
            edit_btn.setToolTip("Edit and save this node's Python script")
            edit_btn.clicked.connect(self._edit_code)
            footer.addWidget(edit_btn)

            run_btn = QtWidgets.QPushButton("Run Python")
            run_btn.setToolTip("Provides maya.cmds as 'cmds' and Houdini as 'hou'")
            run_btn.clicked.connect(self._run_code)
            footer.addWidget(run_btn)

        elif (node.kind or "").lower() == "librarian":
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

            open_btn = QtWidgets.QPushButton("Open Librarian")
            open_btn.setToolTip("Launch the Librarian UI in its own process")

            def _open_librarian():
                now = time.time()
                last = getattr(self, "_last_lib_launch", 0.0)
                if (now - last) < 1.0:
                    return
                self._last_lib_launch = now
                try:
                    from nodes.librarian import launch_librarian as L
                except Exception:
                    try:
                        import launch_librarian as L
                    except Exception as e:
                        QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Import error:\n{e}")
                        return
                proc = getattr(self, "_librarian_proc", None)
                try:
                    alive = (proc is not None) and (proc.poll() is None)
                except Exception:
                    alive = False
                if alive:
                    try:
                        QtWidgets.QToolTip.showText(
                            QtGui.QCursor.pos(), "Librarian already running.", self, self.rect(), 1500
                        )
                    except Exception:
                        pass
                    return
                try:
                    self._librarian_proc = L.launch(verbose=False)
                    try:
                        QtWidgets.QToolTip.showText(
                            QtGui.QCursor.pos(), "Librarian launched.", self, self.rect(), 1500
                        )
                    except Exception:
                        pass
                except Exception as e:
                    self._librarian_proc = None
                    QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to launch Librarian:\n{e}")

            open_btn.clicked.connect(_open_librarian)
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
                    fn = _enqueue_librarian(cmd)
                    _open_librarian()

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
                    txt = _load_librarian_output_text_by_ts(ts) or ""
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
                self._fswatcher.addPath(str(_librarian_outbox_dir()))
                def _on_dir_change(_path):
                    _apply_result_if_ready()
                self._fswatcher.directoryChanged.connect(_on_dir_change)
            except Exception:
                pass

        elif node.code:
            run_btn = QtWidgets.QPushButton("Run Python")
            run_btn.setToolTip("Provides maya.cmds as 'cmds' and Houdini as 'hou'")
            run_btn.clicked.connect(self._run_code)
            footer.addWidget(run_btn)

        edit_params_btn = QtWidgets.QPushButton("Edit Params…")
        def _edit_params():
            sc = getattr(self, "_graph_scene", None)
            if sc is None:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, "Scene not available.")
                return
            dlg = ParamEditorDialog(self, title=f"Edit Parameters — {self._node_ref.name}",
                                    params=self._node_ref.params)
            if dlg.exec_() == QtWidgets.QDialog.Accepted:
                self._node_ref.params = dlg.result_params()
                try:
                    node_item = sc._node_items.get(self._node_ref.name)
                    if node_item:
                        node_item._recompute_height()
                        node_item._build_widgets()
                except Exception:
                    pass
        edit_params_btn.clicked.connect(_edit_params)
        footer.addWidget(edit_params_btn)

        rename_btn = QtWidgets.QPushButton("Rename…")
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

        rename_btn.clicked.connect(_rename_node)
        footer.addWidget(rename_btn)

        footer.addStretch(1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(8)
        lay.addLayout(header)
        lay.addWidget(text)

        self._param_table = QtWidgets.QTableWidget(0, 2)
        self._param_table.setHorizontalHeaderLabels(["Name", "Value"])
        self._param_table.horizontalHeader().setStretchLastSection(True)
        self._param_table.setStyleSheet(
            "QTableWidget{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            "QHeaderView::section{background:#20242b;color:#e6edf3;border:none;}"
        )
        self._param_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._param_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._param_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked |
                                          QtWidgets.QAbstractItemView.EditKeyPressed |
                                          QtWidgets.QAbstractItemView.SelectedClicked)

        def _load_params_into_table():
            self._param_table.setRowCount(0)
            for p in (self._node_ref.params or []):
                row = self._param_table.rowCount()
                self._param_table.insertRow(row)
                nitem = QtWidgets.QTableWidgetItem(p.get("name",""))
                vitem = QtWidgets.QTableWidgetItem(p.get("value",""))
                self._param_table.setItem(row, 0, nitem)
                self._param_table.setItem(row, 1, vitem)
        _load_params_into_table()

        pbtns = QtWidgets.QHBoxLayout()
        addp = QtWidgets.QPushButton("Add Param")
        delp = QtWidgets.QPushButton("Remove Selected")
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
                name_item = self._param_table.item(r, 0)
                value_item = self._param_table.item(r, 1)
                nm = (name_item.text() if name_item else "").strip()
                val = (value_item.text() if value_item else "")
                if nm:
                    new_params.append({"name": nm, "value": val})
            sc = getattr(self, "_graph_scene", None)
            if sc:
                sc.set_node_params(self._node_name, new_params)
            self._node_ref.params = new_params

        addp.clicked.connect(_add_param_row)
        delp.clicked.connect(_remove_selected_row)
        savep.clicked.connect(_apply_param_changes)

        self._param_table.itemChanged.connect(lambda *_: None)

        lay.addWidget(self._param_table)
        lay.addLayout(pbtns)

        if hasattr(self, "_result_view"):
            lay.addWidget(self._result_view)
        lay.addLayout(footer)

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
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
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
        return True

    def rename_node(self, old_name: str, new_name: str):
        new_name = (new_name or "").strip()
        if not old_name or not new_name or new_name == old_name:
            return False, "No change."
        if new_name in self._nodes_by_name:
            return False, f"A node named '{new_name}' already exists."
        item = self._node_items.get(old_name)
        node = self._nodes_by_name.get(old_name)
        if not item or not node:
            return False, f"Node '{old_name}' not found."
        self._node_items[new_name] = self._node_items.pop(old_name)
        self._nodes_by_name[new_name] = self._nodes_by_name.pop(old_name)
        node.name = new_name
        item.model.name = new_name
        item.update()
        for it in self._node_items.values():
            if (it.model.kind or "").lower() == "switch":
                if old_name in it.model.switch_inputs:
                    it.model.switch_inputs = [
                        (new_name if n == old_name else n) for n in it.model.switch_inputs
                    ]
                    self._refresh_switch_widget(it)
        if self._current_output_name == old_name:
            self._current_output_name = new_name
        if self._current_output_name:
            self.recompute_active_path(self._current_output_name)
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
        try:
            kind = (node_item.model.kind or "").lower()
            if kind == "prompt":
                for p in (node_item.model.params or []):
                    if (p.get("name","") or "").strip().lower() == "prompt":
                        return p.get("value","")
        except Exception:
            pass
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
        if dlg.exec_() != QtWidgets.QDialog.Accepted: return
        data = dlg.result_payload()
        node = GraphNode(data["name"], kind=data["kind"], info="User-created node.", params=data["params"], code=data.get("code"))
        item = self.add_node(node, scene_pos)
        item.setPos(scene_pos - QtCore.QPointF(item.width/2.0, item.height/2.0))
        if callable(self.on_info): self.on_info(node)

    def to_dict(self):
        nodes=[]
        for node in self._nodes_by_name.values():
            nd = {
                "name": node.name, "kind": node.kind, "info": node.info or "",
                "code": node.code if node.code is not None else None,
                "pos": [float(node.pos.x()), float(node.pos.y())],
                "params": [{"name": p["name"], "value": p.get("value","")} for p in (node.params or [])],
            }
            if (node.kind or "").lower() == "switch":
                nd["switch_inputs"] = list(node.switch_inputs)
                nd["switch_index"] = int(node.switch_index)
            nodes.append(nd)
        edges=[{"src": e.src.model.name, "dst": e.dst.model.name} for e in self._edges]
        return {"nodes": nodes, "edges": edges}

    def from_dict(self, data):
        self.clear_scene()
        for nd in data.get("nodes", []):
            n = GraphNode(
                nd["name"], nd.get("kind","node"), nd.get("info",""), nd.get("code"),
                params=nd.get("params", []),
                switch_inputs=nd.get("switch_inputs", []),
                switch_index=nd.get("switch_index", 0)
            )
            pos = QtCore.QPointF(*nd.get("pos",[0,0]))
            self.add_node(n, pos)
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
        src=self._node_items[src_name]; dst=self._node_items[dst_name]
        edge=EdgeItem(src,dst)
        self._edges.append(edge); self.addItem(edge)
        if (dst.model.kind or "").lower()=="switch":
            if src.model.name not in dst.model.switch_inputs:
                dst.model.switch_inputs.append(src.model.name)
            dst.model.switch_index = max(0, min(dst.model.switch_index, max(0, len(dst.model.switch_inputs)-1)))
            self._refresh_switch_widget(dst)
        return edge

    def _on_edge_removed(self, edge: 'EdgeItem'):
        dst = edge.dst; src = edge.src
        if isinstance(dst, NodeItem) and (dst.model.kind or "").lower()=="switch":
            try:
                if src.model.name in dst.model.switch_inputs:
                    dst.model.switch_inputs.remove(src.model.name)
            except Exception: pass
            dst.model.switch_index = max(0, min(dst.model.switch_index, max(0, len(dst.model.switch_inputs)-1)))
            self._refresh_switch_widget(dst)
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
        self._clear_path_highlight()
        out_item = self._node_items.get(output_name)
        if not out_item:
            return []
        active_edges = set()
        stack = [out_item]
        visited = set()
        while stack:
            it = stack.pop()
            if it in visited:
                continue
            visited.add(it)
            in_edges = [e for e in self._edges if e.dst is it]
            kind = (it.model.kind or "").lower()
            if kind == "switch":
                byname={}
                for e in in_edges:
                    byname.setdefault(e.src.model.name, e)
                ordered=[]
                for nm in it.model.switch_inputs:
                    if nm in byname: ordered.append(byname[nm])
                for e in in_edges:
                    if e not in ordered: ordered.append(e)
                if ordered:
                    idx = max(0, min(it.model.switch_index, len(ordered)-1))
                    chosen = ordered[idx]
                    active_edges.add(chosen)
                    stack.append(chosen.src)
            else:
                for e in in_edges:
                    active_edges.add(e)
                    stack.append(e.src)
        for e in active_edges:
            e.setHighlighted(True)
        if not active_edges:
            return [out_item.model]
        nodes = set()
        for e in active_edges:
            nodes.add(e.src); nodes.add(e.dst)
        indeg = {n: 0 for n in nodes}
        adj   = {n: [] for n in nodes}
        for e in active_edges:
            adj[e.src].append(e.dst)
            indeg[e.dst] += 1
        def node_key(n):
            try:
                x = float(n.scenePos().x())
            except Exception:
                x = 0.0
            return (x, n.model.name)
        S = [n for n in nodes if indeg[n] == 0]; S.sort(key=node_key)
        L = []
        while S:
            n = S.pop(0)
            L.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    S.append(m); S.sort(key=node_key)
        rev = {n: [] for n in nodes}
        for e in active_edges:
            rev[e.dst].append(e.src)
        reach = set([out_item]); todo = [out_item]
        while todo:
            cur = todo.pop()
            for src in rev.get(cur, ()):
                if src not in reach:
                    reach.add(src); todo.append(src)
        ordered_items = [n for n in L if n in reach]
        if out_item not in ordered_items:
            ordered_items.append(out_item)
        else:
            ordered_items = [n for n in ordered_items if n is not out_item] + [out_item]
        return [it.model for it in ordered_items]

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

# --------- App-level Ctrl+B (focus-only) ----------
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

# Create Node Dialog (+ optional initial python block)
class CreateNodeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, existing_names=None):
        super().__init__(parent)
        self.setWindowTitle("Create Node")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._existing = set(existing_names or [])

        form = QtWidgets.QFormLayout(); form.setLabelAlignment(QtCore.Qt.AlignRight)

        self.name_edit = QtWidgets.QLineEdit(); self.name_edit.setPlaceholderText("e.g. My Tool")
        form.addRow("Node name:", self.name_edit)

        self.kind_edit = QtWidgets.QComboBox()
        self.kind_edit.setEditable(True)
        self.kind_edit.addItems(["node","import","python","switch","output","llm","librarian"])
        self.kind_edit.setEditText("node")
        form.addRow("Node type:", self.kind_edit)

        self._llm_url_label = QtWidgets.QLabel("URL:")
        self._llm_url_edit  = QtWidgets.QLineEdit()
        self._llm_url_edit.setPlaceholderText("http://127.0.0.1:7860")
        self._llm_url_edit.setText(LLM_URL)
        form.addRow(self._llm_url_label, self._llm_url_edit)
        self._llm_url_label.setVisible(False)
        self._llm_url_edit.setVisible(False)

        param_box = QtWidgets.QGroupBox("Parameters (optional)")
        pv = QtWidgets.QVBoxLayout(param_box); pv.setContentsMargins(8,8,8,8); pv.setSpacing(6)
        self.param_list = QtWidgets.QListWidget()
        self.param_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        btns = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add…"); rem_btn = QtWidgets.QPushButton("Remove")
        btns.addWidget(add_btn); btns.addWidget(rem_btn); btns.addStretch(1)
        pv.addWidget(self.param_list); pv.addLayout(btns)
        add_btn.clicked.connect(self._add_param); rem_btn.clicked.connect(self._remove_param)

        code_box = QtWidgets.QGroupBox("Initial Python (optional)")
        code_box.setCheckable(False)
        cv = QtWidgets.QVBoxLayout(code_box); cv.setContentsMargins(8,8,8,8); cv.setSpacing(6)
        self.code_edit = QtWidgets.QPlainTextEdit()
        self.code_edit.setPlaceholderText("# Write Python code that runs in host context.\n# 'cmds' is Maya, 'hou' is Houdini.\n")
        self.code_edit.setStyleSheet("QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}")
        cv.addWidget(self.code_edit)

        def _toggle_code_box(kind_text):
            kind = (kind_text or "").strip().lower()
            code_box.setVisible(kind == "python")
            is_llm = (kind == "llm")
            self._llm_url_label.setVisible(is_llm)
            self._llm_url_edit.setVisible(is_llm)

        self.kind_edit.currentTextChanged.connect(_toggle_code_box)
        _toggle_code_box(self.kind_edit.currentText())

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(param_box)
        layout.addWidget(code_box)
        layout.addWidget(bb)

    def _add_param(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Parameter", "Parameter name:")
        if not ok or not name.strip(): return
        name = name.strip()
        existing = [self.param_list.item(i).text() for i in range(self.param_list.count())]
        if name in existing:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Parameter '{name}' already exists."); return
        self.param_list.addItem(name)

    def _remove_param(self):
        for it in self.param_list.selectedItems():
            row = self.param_list.row(it)
            self.param_list.takeItem(row)

    def _accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "Please enter a node name."); return
        if name in self._existing:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Node '{name}' already exists."); return
        self.accept()

    def result_payload(self):
        params = [{"name": self.param_list.item(i).text(), "value": ""} for i in range(self.param_list.count())]
        kind = self.kind_edit.currentText().strip() or "node"
        code = None
        if kind.lower() == "python":
            code_text = self.code_edit.toPlainText()
            code = code_text if code_text.strip() else ""
        if kind.lower() == "llm":
            url_val = (self._llm_url_edit.text() or "").strip() or LLM_URL
            names = {p["name"].strip().lower() for p in params}
            if "url" in names:
                for p in params:
                    if p["name"].strip().lower() == "url":
                        p["value"] = url_val
                        break
            else:
                params.append({"name": "URL", "value": url_val})
        return {"name": self.name_edit.text().strip(), "kind": kind, "params": params, "code": code}

# main window
class EchoGraphWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

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

        self.scene = GraphScene(on_info=self.add_info_card, on_branch=self.populate_branch_info)
        try:
            self.scene.nodeDeleted.connect(self._on_node_deleted)
        except Exception:
            pass

        self.view = GraphView(self.scene)
        self.view.setMinimumSize(400, 300)
        v.addWidget(self.view, 1)

        self.setCentralWidget(central)

        self._init_info_dock()

        # --- App-level Ctrl+B filter (MOST ROBUST; focus-only) ---
        self._ctrlb_filter = _CtrlBEventFilter(self)
        QtWidgets.QApplication.instance().installEventFilter(self._ctrlb_filter)
        print("[EchoGraph] CtrlB filter installed.")

    # -------- Ctrl+B registration + focus-only resolve ----------
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

        h.addStretch(1)
        return bar

    def _create_node_interactive(self):
        dlg = CreateNodeDialog(self, existing_names=list(self.scene._nodes_by_name.keys()))
        if dlg.exec_() != QtWidgets.QDialog.Accepted: return
        data = dlg.result_payload()
        node = GraphNode(data["name"], kind=data["kind"], info="User-created node.", params=data["params"], code=data.get("code"))
        center_scene = self.view.mapToScene(self.view.viewport().rect().center())
        item = self.scene.add_node(node, center_scene)
        item.setPos(center_scene - QtCore.QPointF(item.width/2.0, item.height/2.0))
        self.scene.center_on_name(node.name); self.add_info_card(node)

    def _open_graph(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Graph (.json)", "", "JSON Files (*.json)")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f: data = json.load(f)
            self.scene.from_dict(data)
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
            print(f"[EchoGraph] Saved: {self._current_path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to save:\n{e}")

    def _export_graph(self):
        suggested = self._current_path if self._current_path else "graph.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Graph (.json)", suggested, "JSON Files (*.json)")
        if not path:
            return
        try:
            data = self.scene.to_dict()
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
            print(f"[EchoGraph] Exported: {path}")
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
            self.resizeDocks([self.infoDock], [250], QtCore.Qt.Horizontal)
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
        self._cardsLayout.insertWidget(0, card)
        self._card_by_node[node.name] = card
        self._trim_cards()

    def populate_branch_info(self, ordered_nodes):
        try:
            self.infoDock.setVisible(True)
            self.infoDock.raise_()
        except Exception:
            pass
        for i in reversed(range(self._cardsLayout.count()-1)):
            w = self._cardsLayout.itemAt(i).widget()
            if w: w.deleteLater()
        self._card_by_node.clear()
        if not ordered_nodes:
            return
        total = len(ordered_nodes)
        for idx, node in enumerate(ordered_nodes, start=1):
            card = InfoCard(node, order_index=idx, order_total=total)
            card.requestJump.connect(self.scene.center_on_name)
            card.closedForNode.connect(self._on_card_closed)
            self._cardsLayout.insertWidget(self._cardsLayout.count()-1, card)

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
