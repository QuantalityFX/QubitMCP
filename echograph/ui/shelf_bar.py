from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from echograph.constants import APP_TITLE, script_dir
from echograph.qt_compat import QtCore, QtGui, QtWidgets, QAction
from echograph.services.python_runner import (
    PythonExecutionRequest,
    PythonExecutionResult,
    run_python_async,
)
from echograph.services.shelf_tools_store import (
    SUPPORTED_TOOL_TYPES,
    ShelfToolsStore,
    make_stored_path,
    resolve_stored_path,
)
from echograph.ui.shelf_tool_dialogs import edit_shelf_tool


_SHELF_TOOL_MIME = "application/x-qubit-shelf-tool-id"


class _ShelfToolButton(QtWidgets.QToolButton):
    def __init__(self, parent=None, *, normal_icon_opacity: float = 0.55):
        super().__init__(parent)
        self._shelf_source_icon = QtGui.QIcon()
        self._shelf_normal_icon_opacity = max(0.05, min(1.0, float(normal_icon_opacity)))
        self._shelf_hovered = False

    def setShelfIcon(self, icon: QtGui.QIcon) -> None:
        self._shelf_source_icon = QtGui.QIcon(icon)
        self._refresh_shelf_icon()

    def enterEvent(self, event):
        self._shelf_hovered = True
        self._refresh_shelf_icon()
        try:
            super().enterEvent(event)
        except Exception:
            pass

    def leaveEvent(self, event):
        self._shelf_hovered = False
        self._refresh_shelf_icon()
        try:
            super().leaveEvent(event)
        except Exception:
            pass

    def _refresh_shelf_icon(self) -> None:
        if self._shelf_source_icon.isNull():
            return
        icon = self._shelf_source_icon if self._shelf_hovered else self._icon_with_opacity(self._shelf_source_icon, self._shelf_normal_icon_opacity)
        QtWidgets.QToolButton.setIcon(self, icon)

    def _icon_with_opacity(self, icon: QtGui.QIcon, opacity: float) -> QtGui.QIcon:
        size = self.iconSize()
        if not size.isValid() or size.isEmpty():
            size = QtCore.QSize(24, 24)
        source = icon.pixmap(size)
        if source.isNull():
            return icon
        dimmed = QtGui.QPixmap(source.size())
        dimmed.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(dimmed)
        try:
            painter.setOpacity(max(0.05, min(1.0, float(opacity))))
            painter.drawPixmap(0, 0, source)
        finally:
            painter.end()
        return QtGui.QIcon(dimmed)


class _ShelfItemButton(QtWidgets.QToolButton):
    def __init__(self, parent=None, *, owner=None):
        super().__init__(parent)
        self._owner = owner
        self._tool_id = ""
        self._drag_start_pos = QtCore.QPoint()
        self._dragging_tool = False
        self._suppress_click_after_drag = False
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def setShelfToolId(self, tool_id: str) -> None:
        self._tool_id = str(tool_id or "")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._dragging_tool = False
            self._suppress_click_after_drag = False
        try:
            super().mousePressEvent(event)
        except Exception:
            pass

    def mouseMoveEvent(self, event):
        if (
            self._tool_id
            and event.buttons() & QtCore.Qt.LeftButton
            and (event.pos() - self._drag_start_pos).manhattanLength() >= QtWidgets.QApplication.startDragDistance()
        ):
            self._start_reorder_drag()
            event.accept()
            return
        try:
            super().mouseMoveEvent(event)
        except Exception:
            pass

    def mouseReleaseEvent(self, event):
        if (self._dragging_tool or self._suppress_click_after_drag) and event.button() == QtCore.Qt.LeftButton:
            self._dragging_tool = False
            self._suppress_click_after_drag = False
            event.accept()
            return
        try:
            super().mouseReleaseEvent(event)
        except Exception:
            pass

    def dragEnterEvent(self, event):
        owner = self._owner
        if owner and owner._handle_shelf_drag_enter(event, self):
            return
        try:
            super().dragEnterEvent(event)
        except Exception:
            pass

    def dragMoveEvent(self, event):
        owner = self._owner
        if owner and owner._handle_shelf_drag_move(event, self):
            return
        try:
            super().dragMoveEvent(event)
        except Exception:
            pass

    def dragLeaveEvent(self, event):
        owner = self._owner
        if owner:
            owner._hide_drop_indicator()
        try:
            super().dragLeaveEvent(event)
        except Exception:
            pass

    def dropEvent(self, event):
        owner = self._owner
        if owner and owner._handle_shelf_drop(event, self):
            return
        try:
            super().dropEvent(event)
        except Exception:
            pass

    def _start_reorder_drag(self) -> None:
        self._dragging_tool = True
        self._suppress_click_after_drag = True
        self.setDown(False)
        mime = QtCore.QMimeData()
        mime.setData(_SHELF_TOOL_MIME, self._tool_id.encode("utf-8"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        try:
            pixmap = self.grab()
            drag.setPixmap(pixmap)
            drag.setHotSpot(self._drag_start_pos)
        except Exception:
            pass
        try:
            exec_fn = getattr(drag, "exec", None) or getattr(drag, "exec_", None)
            if exec_fn is None:
                return
            exec_fn(QtCore.Qt.MoveAction)
        finally:
            self._dragging_tool = False
            owner = self._owner
            if owner:
                owner._hide_drop_indicator()

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            rect = self.rect()
            bg = None
            if self.isDown():
                bg = QtGui.QColor("#1f7a45")
            elif self.underMouse():
                bg = QtGui.QColor("#2b313a")
            if bg is not None:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(QtCore.QRectF(rect).adjusted(0.0, 0.0, -1.0, -1.0), 2.0, 2.0)

            icon = self.icon()
            icon_size = self.iconSize()
            if not icon_size.isValid() or icon_size.isEmpty():
                icon_size = QtCore.QSize(22, 22)
            icon_top = 4
            if not icon.isNull():
                pixmap = icon.pixmap(icon_size)
                if not pixmap.isNull():
                    x = int((rect.width() - pixmap.width()) / 2)
                    y = icon_top
                    painter.drawPixmap(x, y, pixmap)

            font = self.font()
            font.setPixelSize(9)
            painter.setFont(font)
            color = QtGui.QColor("#e5e7eb")
            if not self.isEnabled():
                color = QtGui.QColor("#64748b")
            elif bool(self.property("missing")):
                color = QtGui.QColor("#fbbf24")
            elif bool(self.property("unsupported")):
                color = QtGui.QColor("#94a3b8")
            painter.setPen(color)
            fm = QtGui.QFontMetrics(font)
            text_top = icon_top + icon_size.height() + 1
            text_rect = rect.adjusted(4, text_top, -4, -2)
            lines = self._wrapped_label_lines(self.text(), fm, max(1, text_rect.width()))
            line_h = fm.lineSpacing()
            total_h = max(1, len(lines)) * line_h
            y = text_rect.top() + max(0, int((text_rect.height() - total_h) / 2)) + fm.ascent()
            for line in lines:
                painter.drawText(
                    QtCore.QRect(text_rect.left(), y - fm.ascent(), text_rect.width(), line_h),
                    QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                    line,
                )
                y += line_h
        finally:
            painter.end()

    def _wrapped_label_lines(self, text: str, fm: QtGui.QFontMetrics, width: int) -> list[str]:
        label = " ".join(str(text or "").split())
        if not label:
            return []
        if fm.horizontalAdvance(label) <= width:
            return [label]
        words = label.split(" ")
        if len(words) <= 1:
            return [fm.elidedText(label, QtCore.Qt.ElideRight, width)]
        line1 = ""
        used = 0
        for idx, word in enumerate(words):
            candidate = word if not line1 else f"{line1} {word}"
            if fm.horizontalAdvance(candidate) <= width:
                line1 = candidate
                used = idx + 1
                continue
            if not line1:
                return [fm.elidedText(word, QtCore.Qt.ElideRight, width)]
            break
        if used >= len(words):
            return [line1]
        line2 = " ".join(words[used:])
        if fm.horizontalAdvance(line2) > width:
            line2 = fm.elidedText(line2, QtCore.Qt.ElideRight, width)
        return [line1, line2]


class _ShelfToolsWidget(QtWidgets.QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self._owner._handle_shelf_drag_enter(event, self):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._owner._handle_shelf_drag_move(event, self):
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._owner._hide_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if self._owner._handle_shelf_drop(event, self):
            return
        super().dropEvent(event)


class _ShelfDivider(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        try:
            pen = QtGui.QPen(QtGui.QColor("#1c1f23"))
            pen.setWidth(0)
            painter.setPen(pen)
            y = self.height() - 1
            painter.drawLine(0, y, self.width(), y)
        finally:
            painter.end()


class ShelfBar(QtWidgets.QFrame):
    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._store = ShelfToolsStore()
        self._buttons: Dict[str, QtWidgets.QToolButton] = {}
        self._output_dialogs = []
        self.setObjectName("ShelfBar")
        self.setFixedHeight(58)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_background_menu)
        self.setStyleSheet(
            "#ShelfBar{background:#1a1f24;}"
            "#ShelfBar QToolButton{background:transparent;color:#e5e7eb;border:0px;"
            "border-radius:2px;padding:2px 4px;font-size:9px;}"
            "#ShelfBar QToolButton:hover{background:#2b313a;}"
            "#ShelfBar QToolButton:pressed{background:#1f7a45;}"
            "#ShelfBar QToolButton[missing=\"true\"]{color:#fbbf24;}"
            "#ShelfBar QToolButton[unsupported=\"true\"]{color:#94a3b8;}"
            "#ShelfBar QToolButton#ShelfAddButton{padding:0px;}"
            "#ShelfBar QToolButton#ShelfAddButton:hover{background:transparent;}"
            "#ShelfBar QToolButton#ShelfAddButton:pressed{background:transparent;}"
        )

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 0, 8, 0)
        root.setSpacing(5)

        self._add_button = _ShelfToolButton(self, normal_icon_opacity=0.58)
        self._add_button.setObjectName("ShelfAddButton")
        self._add_button.setToolTip("Add shelf tool")
        self._add_button.setCursor(QtCore.Qt.PointingHandCursor)
        self._add_button.setFixedSize(34, 34)
        self._add_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._add_button.setIconSize(QtCore.QSize(24, 24))
        add_icon = QtGui.QIcon(str(script_dir() / "icons" / "AddTool_Icon.png"))
        if not add_icon.isNull():
            self._add_button.setShelfIcon(add_icon)
        else:
            self._add_button.setText("+")
        self._add_button.clicked.connect(self._show_add_menu_at_button)
        root.addWidget(self._add_button, 0, QtCore.Qt.AlignVCenter)

        self._scroll = QtWidgets.QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:0px;}")
        self._tools_widget = _ShelfToolsWidget(self, self._scroll)
        self._tools_layout = QtWidgets.QHBoxLayout(self._tools_widget)
        self._tools_layout.setContentsMargins(0, 0, 0, 0)
        self._tools_layout.setSpacing(4)
        self._scroll.setWidget(self._tools_widget)
        root.addWidget(self._scroll, 1)

        self._drop_indicator = QtWidgets.QFrame(self._tools_widget)
        self._drop_indicator.setObjectName("ShelfDropIndicator")
        self._drop_indicator.setFixedWidth(2)
        self._drop_indicator.setStyleSheet("#ShelfDropIndicator{background:#60a5fa;border:0px;}")
        self._drop_indicator.hide()

        self._bottom_divider = _ShelfDivider(self)
        self._bottom_divider.setObjectName("ShelfBottomDivider")
        self._bottom_divider.raise_()

        self._load()

    def resizeEvent(self, event):
        try:
            self._bottom_divider.setGeometry(0, max(0, self.height() - 1), self.width(), 1)
            self._bottom_divider.raise_()
        except Exception:
            pass
        try:
            super().resizeEvent(event)
        except Exception:
            pass

    def _load(self) -> None:
        self._store.load()
        self.refresh()
        if self._store.load_error:
            QtCore.QTimer.singleShot(0, self, self._show_load_warning)

    def _show_load_warning(self) -> None:
        QtWidgets.QMessageBox.warning(self, APP_TITLE, self._store.load_error)

    def refresh(self) -> None:
        self._hide_drop_indicator()
        while self._tools_layout.count():
            item = self._tools_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        for tool in self._store.all_tools():
            button = self._build_tool_button(tool)
            self._tools_layout.addWidget(button, 0)
            self._buttons[str(tool.get("id", ""))] = button
        self._tools_layout.addStretch(1)

    def add_python_node_snapshot(self, node: Any) -> None:
        label = str(getattr(node, "name", "") or "Python Tool")
        tool = {
            "type": "python_code",
            "label": label,
            "tooltip": f"Run Python code copied from {label}",
            "code": str(getattr(node, "code", "") or ""),
            "params": list(getattr(node, "params", None) or []),
            "execution": {"thread_mode": "worker", "show_output": True},
        }
        edited = edit_shelf_tool(self, tool=tool, default_type="python_code")
        if edited is None:
            return
        self._save_new_tool(edited)

    def _build_add_menu(self, parent) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(parent)
        menu.setStyleSheet(
            "QMenu{background:#1b2026;color:#e5e7eb;border:1px solid #333;padding:4px;}"
            "QMenu::item{padding:5px 24px 5px 8px;}"
            "QMenu::item:selected{background:#1f7a45;}"
        )
        add_script = QAction("Add Python Script...", menu)
        add_script.triggered.connect(self._add_python_script)
        menu.addAction(add_script)

        add_code = QAction("Add Python Code...", menu)
        add_code.triggered.connect(self._add_python_code)
        menu.addAction(add_code)

        add_workflow = QAction("Add Current Workflow", menu)
        add_workflow.triggered.connect(self._add_current_workflow)
        menu.addAction(add_workflow)

        menu.addSeparator()
        add_selected_nodes = QAction("Add Selected Nodes", menu)
        add_selected_nodes.triggered.connect(self._add_selected_nodes)
        menu.addAction(add_selected_nodes)

        add_copied_nodes = QAction("Add Copied Nodes", menu)
        add_copied_nodes.triggered.connect(self._add_copied_nodes)
        menu.addAction(add_copied_nodes)
        return menu

    def _build_tool_button(self, tool: Dict[str, Any]) -> QtWidgets.QToolButton:
        button = _ShelfItemButton(self._tools_widget, owner=self)
        tool_id = str(tool.get("id", "") or "")
        button.setShelfToolId(tool_id)
        button.setText(str(tool.get("label", "") or "Tool"))
        button.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedSize(76, 58)
        button.setIconSize(QtCore.QSize(26, 26))
        button.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, tid=tool_id, btn=button: self._show_tool_menu(tid, btn.mapToGlobal(pos))
        )
        button.clicked.connect(lambda checked=False, tid=tool_id: self._run_tool_by_id(tid))

        icon = self._tool_icon(tool)
        if not icon.isNull():
            button.setIcon(icon)

        tooltip = self._tool_tooltip(tool)
        button.setToolTip(tooltip)
        missing = self._tool_missing_path(tool)
        unsupported = str(tool.get("type", "") or "") not in SUPPORTED_TOOL_TYPES
        button.setProperty("missing", bool(missing))
        button.setProperty("unsupported", bool(unsupported))
        return button

    def _tool_icon(self, tool: Dict[str, Any]) -> QtGui.QIcon:
        icon_path = str(tool.get("icon", "") or "").strip()
        if icon_path:
            icon = QtGui.QIcon(str(resolve_stored_path(icon_path)))
            if not icon.isNull():
                return icon
        tool_type = str(tool.get("type", "") or "")
        if tool_type in {"python_script", "python_code"}:
            return QtGui.QIcon(str(script_dir() / "icons" / "Python_Icon.png"))
        if tool_type == "workflow_shortcut":
            return QtGui.QIcon(str(script_dir() / "icons" / "Out_Node_Icon.png"))
        if tool_type == "graph_snippet":
            return QtGui.QIcon(str(script_dir() / "icons" / "Copy_Icon.png"))
        return QtGui.QIcon()

    def _tool_tooltip(self, tool: Dict[str, Any]) -> str:
        parts = []
        tooltip = str(tool.get("tooltip", "") or "").strip()
        if tooltip:
            parts.append(tooltip)
        tool_type = str(tool.get("type", "") or "")
        if tool_type == "python_script":
            parts.append(str(resolve_stored_path(tool.get("script_path", ""), tool.get("path_mode"))))
        elif tool_type == "workflow_shortcut":
            parts.append(str(resolve_stored_path(tool.get("workflow_path", ""), tool.get("path_mode"))))
        elif tool_type == "graph_snippet":
            payload = tool.get("payload") if isinstance(tool.get("payload"), dict) else {}
            nodes = payload.get("nodes") if isinstance(payload, dict) else []
            edges = payload.get("edges") if isinstance(payload, dict) else []
            comments = payload.get("comments") if isinstance(payload, dict) else []
            node_count = len(nodes) if isinstance(nodes, list) else 0
            edge_count = len(edges) if isinstance(edges, list) else 0
            comment_count = len(comments) if isinstance(comments, list) else 0
            summary = f"{node_count} nodes, {edge_count} connections"
            if comment_count:
                wrapper_word = "wrapper" if comment_count == 1 else "wrappers"
                summary = f"{summary}, {comment_count} comment {wrapper_word}"
            parts.append(summary)
        elif tool_type not in SUPPORTED_TOOL_TYPES:
            parts.append(f"Unsupported shelf tool type: {tool_type}")
        missing = self._tool_missing_path(tool)
        if missing:
            parts.append(f"Missing: {missing}")
        return "\n".join(parts).strip()

    def _tool_missing_path(self, tool: Dict[str, Any]) -> str:
        tool_type = str(tool.get("type", "") or "")
        if tool_type == "python_script":
            raw = str(tool.get("script_path", "") or "").strip()
            if not raw:
                return "No script path"
            path = resolve_stored_path(raw, tool.get("path_mode"))
            if not str(path) or not path.exists():
                return str(path)
        if tool_type == "workflow_shortcut":
            raw = str(tool.get("workflow_path", "") or "").strip()
            if not raw:
                return "No workflow path"
            path = resolve_stored_path(raw, tool.get("path_mode"))
            if not str(path) or not path.exists():
                return str(path)
        return ""

    def _show_background_menu(self, pos) -> None:
        menu = self._build_add_menu(self)
        menu.exec(self.mapToGlobal(pos)) if hasattr(menu, "exec") else menu.exec_(self.mapToGlobal(pos))

    def _show_add_menu_at_button(self) -> None:
        menu = self._build_add_menu(self._add_button)
        pos = self._add_button.mapToGlobal(QtCore.QPoint(0, self._add_button.height()))
        menu.exec(pos) if hasattr(menu, "exec") else menu.exec_(pos)

    def _show_tool_menu(self, tool_id: str, global_pos) -> None:
        tool = self._store.tool_by_id(tool_id)
        if not tool:
            return
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1b2026;color:#e5e7eb;border:1px solid #333;padding:4px;}"
            "QMenu::item{padding:5px 24px 5px 8px;}"
            "QMenu::item:selected{background:#1f7a45;}"
        )
        action_label = "Run"
        if tool.get("type") == "workflow_shortcut":
            action_label = "Open"
        elif tool.get("type") == "graph_snippet":
            action_label = "Paste"
        run_action = QAction(action_label, menu)
        run_action.triggered.connect(lambda: self._run_tool_by_id(tool_id))
        menu.addAction(run_action)

        edit_action = QAction("Edit...", menu)
        edit_action.triggered.connect(lambda: self._edit_tool(tool_id))
        menu.addAction(edit_action)

        duplicate_action = QAction("Duplicate", menu)
        duplicate_action.triggered.connect(lambda: self._duplicate_tool(tool_id))
        menu.addAction(duplicate_action)

        menu.addSeparator()
        left_action = QAction("Move Left", menu)
        left_action.triggered.connect(lambda: self._move_tool(tool_id, -1))
        menu.addAction(left_action)

        right_action = QAction("Move Right", menu)
        right_action.triggered.connect(lambda: self._move_tool(tool_id, 1))
        menu.addAction(right_action)

        menu.addSeparator()
        remove_action = QAction("Remove", menu)
        remove_action.triggered.connect(lambda: self._remove_tool(tool_id))
        menu.addAction(remove_action)

        menu.exec(global_pos) if hasattr(menu, "exec") else menu.exec_(global_pos)

    def _add_python_script(self) -> None:
        tool = edit_shelf_tool(self, default_type="python_script")
        if tool is not None:
            self._save_new_tool(tool)

    def _add_python_code(self) -> None:
        tool = edit_shelf_tool(self, default_type="python_code")
        if tool is not None:
            self._save_new_tool(tool)

    def _add_current_workflow(self) -> None:
        path = str(getattr(self._window, "_current_path", "") or "").strip()
        if not path:
            answer = QtWidgets.QMessageBox.question(
                self,
                APP_TITLE,
                "Save the current workflow before adding it to the shelf?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
            try:
                self._window._save_graph()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to save workflow:\n{exc}")
                return
            path = str(getattr(self._window, "_current_path", "") or "").strip()
            if not path:
                return
        stored, path_mode = make_stored_path(path)
        tool = {
            "type": "workflow_shortcut",
            "label": Path(path).stem,
            "tooltip": f"Open {Path(path).name} in a new EchoGraph window",
            "workflow_path": stored,
            "path_mode": path_mode,
            "open_mode": "new_instance",
        }
        edited = edit_shelf_tool(self, tool=tool, default_type="workflow_shortcut")
        if edited is not None:
            self._save_new_tool(edited)

    def _add_selected_nodes(self) -> None:
        scene = getattr(self._window, "scene", None)
        builder = getattr(scene, "selection_clipboard_payload", None)
        payload = builder() if callable(builder) else None
        if not payload:
            QtWidgets.QMessageBox.information(self, APP_TITLE, "Select one or more nodes first.")
            return
        self._add_graph_snippet_payload(payload)

    def _add_copied_nodes(self) -> None:
        payload = self._clipboard_graph_payload()
        if not payload:
            QtWidgets.QMessageBox.information(
                self,
                APP_TITLE,
                "Copy nodes from the graph first, then add them to the shelf.",
            )
            return
        self._add_graph_snippet_payload(payload)

    def _clipboard_graph_payload(self) -> Dict[str, Any] | None:
        try:
            text = QtWidgets.QApplication.clipboard().text()
        except Exception:
            text = ""
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("format") != "EchoGraphClipboard":
            return None
        nodes = payload.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return None
        return payload

    def _add_graph_snippet_payload(self, payload: Dict[str, Any]) -> None:
        nodes = payload.get("nodes") if isinstance(payload, dict) else []
        edges = payload.get("edges") if isinstance(payload, dict) else []
        comments = payload.get("comments") if isinstance(payload, dict) else []
        nodes = nodes if isinstance(nodes, list) else []
        edges = edges if isinstance(edges, list) else []
        comments = comments if isinstance(comments, list) else []
        label = "Node Snippet"
        if len(nodes) == 1 and isinstance(nodes[0], dict):
            label = str(nodes[0].get("name", "") or nodes[0].get("kind", "") or label)
        tooltip = f"Paste {len(nodes)} nodes and {len(edges)} connections"
        if comments:
            wrapper_word = "wrapper" if len(comments) == 1 else "wrappers"
            tooltip = f"{tooltip} with {len(comments)} comment {wrapper_word}"
        tooltip = f"{tooltip} into the graph"
        tool = {
            "type": "graph_snippet",
            "label": label,
            "tooltip": tooltip,
            "payload": payload,
        }
        edited = edit_shelf_tool(self, tool=tool, default_type="graph_snippet")
        if edited is not None:
            self._save_new_tool(edited)

    def _save_new_tool(self, tool: Dict[str, Any]) -> None:
        try:
            self._store.add_tool(tool)
            self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Failed to save shelf tool:\n{exc}")

    def _edit_tool(self, tool_id: str) -> None:
        tool = self._store.tool_by_id(tool_id)
        if not tool:
            return
        edited = edit_shelf_tool(self, tool=tool, default_type=str(tool.get("type", "python_script")))
        if edited is None:
            return
        try:
            self._store.update_tool(tool_id, edited)
            self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Failed to save shelf tool:\n{exc}")

    def _duplicate_tool(self, tool_id: str) -> None:
        try:
            self._store.duplicate_tool(tool_id)
            self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Failed to duplicate shelf tool:\n{exc}")

    def _move_tool(self, tool_id: str, delta: int) -> None:
        try:
            if self._store.move_tool(tool_id, delta):
                self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Failed to reorder shelf tool:\n{exc}")

    def _reorder_tool_to_index(self, tool_id: str, target_index: int) -> None:
        try:
            if self._store.move_tool_to_index(tool_id, target_index):
                self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Failed to reorder shelf tool:\n{exc}")

    def _shelf_drag_tool_id(self, event) -> str:
        try:
            mime = event.mimeData()
        except Exception:
            return ""
        if not mime or not mime.hasFormat(_SHELF_TOOL_MIME):
            return ""
        try:
            return bytes(mime.data(_SHELF_TOOL_MIME)).decode("utf-8").strip()
        except Exception:
            return ""

    def _event_pos_in_tools_widget(self, event, source_widget) -> QtCore.QPoint:
        try:
            raw = event.position() if hasattr(event, "position") else event.pos()
            if hasattr(raw, "toPoint"):
                raw = raw.toPoint()
            local = QtCore.QPoint(int(raw.x()), int(raw.y()))
            return self._tools_widget.mapFromGlobal(source_widget.mapToGlobal(local))
        except Exception:
            return QtCore.QPoint(0, 0)

    def _ordered_tool_buttons(self, *, exclude_tool_id: str = "") -> list[tuple[str, QtWidgets.QToolButton]]:
        ordered = []
        exclude = str(exclude_tool_id or "")
        for tool in self._store.all_tools():
            tool_id = str(tool.get("id", "") or "")
            if not tool_id or tool_id == exclude:
                continue
            button = self._buttons.get(tool_id)
            if button is not None:
                ordered.append((tool_id, button))
        return ordered

    def _drop_index_for_pos(self, pos: QtCore.QPoint, source_tool_id: str) -> int:
        buttons = self._ordered_tool_buttons(exclude_tool_id=source_tool_id)
        for index, (_tool_id, button) in enumerate(buttons):
            if pos.x() < button.geometry().center().x():
                return index
        return len(buttons)

    def _show_drop_indicator(self, target_index: int, source_tool_id: str) -> None:
        buttons = self._ordered_tool_buttons(exclude_tool_id=source_tool_id)
        if buttons:
            index = max(0, min(int(target_index), len(buttons)))
            if index <= 0:
                x = max(0, buttons[0][1].geometry().left() - 3)
            elif index >= len(buttons):
                x = buttons[-1][1].geometry().right() + 3
            else:
                x = max(0, buttons[index][1].geometry().left() - 3)
        else:
            x = 0
        height = max(24, self._tools_widget.height() - 8)
        self._drop_indicator.setGeometry(int(x), 4, 2, int(height))
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _hide_drop_indicator(self) -> None:
        indicator = getattr(self, "_drop_indicator", None)
        if indicator is not None:
            indicator.hide()

    def _handle_shelf_drag_enter(self, event, source_widget) -> bool:
        tool_id = self._shelf_drag_tool_id(event)
        if not tool_id or not self._store.tool_by_id(tool_id):
            return False
        try:
            event.setDropAction(QtCore.Qt.MoveAction)
            event.accept()
        except Exception:
            event.acceptProposedAction()
        pos = self._event_pos_in_tools_widget(event, source_widget)
        self._show_drop_indicator(self._drop_index_for_pos(pos, tool_id), tool_id)
        return True

    def _handle_shelf_drag_move(self, event, source_widget) -> bool:
        tool_id = self._shelf_drag_tool_id(event)
        if not tool_id or not self._store.tool_by_id(tool_id):
            return False
        pos = self._event_pos_in_tools_widget(event, source_widget)
        self._show_drop_indicator(self._drop_index_for_pos(pos, tool_id), tool_id)
        try:
            event.setDropAction(QtCore.Qt.MoveAction)
            event.accept()
        except Exception:
            event.acceptProposedAction()
        return True

    def _handle_shelf_drop(self, event, source_widget) -> bool:
        tool_id = self._shelf_drag_tool_id(event)
        if not tool_id or not self._store.tool_by_id(tool_id):
            return False
        pos = self._event_pos_in_tools_widget(event, source_widget)
        target_index = self._drop_index_for_pos(pos, tool_id)
        self._hide_drop_indicator()
        self._reorder_tool_to_index(tool_id, target_index)
        try:
            event.setDropAction(QtCore.Qt.MoveAction)
            event.accept()
        except Exception:
            event.acceptProposedAction()
        return True

    def _remove_tool(self, tool_id: str) -> None:
        tool = self._store.tool_by_id(tool_id)
        label = str((tool or {}).get("label", "") or "this shelf tool")
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_TITLE,
            f"Remove {label} from the shelf?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            if self._store.remove_tool(tool_id):
                self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Failed to remove shelf tool:\n{exc}")

    def _run_tool_by_id(self, tool_id: str) -> None:
        tool = self._store.tool_by_id(tool_id)
        if not tool:
            return
        if not bool(tool.get("enabled", True)):
            return
        tool_type = str(tool.get("type", "") or "")
        if tool_type == "python_script":
            self._run_python_script(tool)
        elif tool_type == "python_code":
            self._run_python_code(tool)
        elif tool_type == "workflow_shortcut":
            self._open_workflow_shortcut(tool)
        elif tool_type == "graph_snippet":
            self._paste_graph_snippet(tool)
        else:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Unsupported shelf tool type:\n{tool_type}")

    def _paste_graph_snippet(self, tool: Dict[str, Any]) -> None:
        payload = tool.get("payload") if isinstance(tool.get("payload"), dict) else None
        if not payload:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "This shelf node snippet has no saved payload.")
            return
        scene = getattr(self._window, "scene", None)
        paste = getattr(scene, "paste_payload", None)
        if not callable(paste):
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "This graph cannot paste node snippets.")
            return
        if not paste(payload):
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "Failed to paste node snippet.")

    def _run_python_script(self, tool: Dict[str, Any]) -> None:
        path = resolve_stored_path(tool.get("script_path", ""), tool.get("path_mode"))
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Python script does not exist:\n{path}")
            return
        working_dir = str(tool.get("working_dir", "") or "").strip()
        if working_dir:
            working_dir = str(resolve_stored_path(working_dir))
        execution = tool.get("execution") if isinstance(tool.get("execution"), dict) else {}
        request = PythonExecutionRequest(
            source_kind="script",
            script_path=str(path),
            working_dir=working_dir,
            args=[str(arg) for arg in (tool.get("args") or [])],
            graph_scene=getattr(self._window, "scene", None),
            parent_widget=self,
            thread_mode=str(execution.get("thread_mode", "worker") or "worker"),
            show_output=bool(execution.get("show_output", True)),
        )
        self._set_tool_busy(str(tool.get("id", "")), True)
        run_python_async(request, lambda result, t=tool: self._on_python_finished(t, result))

    def _run_python_code(self, tool: Dict[str, Any]) -> None:
        code = str(tool.get("code", "") or "")
        if not code.strip():
            QtWidgets.QMessageBox.information(self, APP_TITLE, "This shelf tool has no Python code.")
            return
        execution = tool.get("execution") if isinstance(tool.get("execution"), dict) else {}
        request = PythonExecutionRequest(
            source_kind="code",
            code=code,
            params=list(tool.get("params") or []),
            graph_scene=getattr(self._window, "scene", None),
            parent_widget=self,
            thread_mode=str(execution.get("thread_mode", "worker") or "worker"),
            show_output=bool(execution.get("show_output", True)),
        )
        self._set_tool_busy(str(tool.get("id", "")), True)
        run_python_async(request, lambda result, t=tool: self._on_python_finished(t, result))

    def _on_python_finished(self, tool: Dict[str, Any], result: PythonExecutionResult) -> None:
        self._set_tool_busy(str(tool.get("id", "")), False)
        label = str(tool.get("label", "") or "Shelf Tool")
        if not result.ok:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"{label} failed:\n\n{result.message}")
            return
        execution = tool.get("execution") if isinstance(tool.get("execution"), dict) else {}
        if not bool(execution.get("show_output", True)):
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"{label} finished.", self, self.rect(), 1200)
            return
        message = result.message
        if message:
            self._show_output_dialog(label, message)
        else:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"{label} finished.", self, self.rect(), 1200)

    def _set_tool_busy(self, tool_id: str, busy: bool) -> None:
        button = self._buttons.get(str(tool_id or ""))
        if button is None:
            return
        button.setEnabled(not busy)
        text = str(button.text() or "")
        if busy and not text.endswith("..."):
            button.setText(f"{text}...")
        elif not busy and text.endswith("..."):
            button.setText(text[:-3])

    def _show_output_dialog(self, label: str, message: str) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(label)
        dlg.resize(640, 360)
        layout = QtWidgets.QVBoxLayout(dlg)
        edit = QtWidgets.QPlainTextEdit(dlg)
        edit.setReadOnly(True)
        edit.setPlainText(message)
        edit.setStyleSheet("QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}")
        layout.addWidget(edit, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok, dlg)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons, 0)
        self._output_dialogs.append(dlg)
        dlg.finished.connect(lambda _code, d=dlg: self._forget_output_dialog(d))
        dlg.show()

    def _forget_output_dialog(self, dlg) -> None:
        try:
            self._output_dialogs.remove(dlg)
        except ValueError:
            pass
        try:
            dlg.deleteLater()
        except Exception:
            pass

    def _open_workflow_shortcut(self, tool: Dict[str, Any]) -> None:
        path = resolve_stored_path(tool.get("workflow_path", ""), tool.get("path_mode"))
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Workflow does not exist:\n{path}")
            return
        open_mode = str(tool.get("open_mode", "new_instance") or "new_instance")
        if open_mode == "current_window":
            self._open_workflow_in_current_window(str(path))
            return
        launcher = getattr(self._window, "_launch_new_instance", None)
        if not callable(launcher):
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "This window cannot launch a new workflow instance.")
            return
        launcher(workflow_path=str(path))

    def _open_workflow_in_current_window(self, path: str) -> None:
        current = str(getattr(self._window, "_current_path", "") or "").strip()
        if current and str(Path(current).resolve()) == str(Path(path).resolve()):
            loader = getattr(self._window, "_load_graph_file", None)
            if callable(loader):
                loader(path)
            return
        if not self._confirm_replace_current_workflow():
            return
        loader = getattr(self._window, "_load_graph_file", None)
        if not callable(loader):
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "This window cannot open workflows.")
            return
        loader(path)

    def _confirm_replace_current_workflow(self) -> bool:
        if not self._current_window_has_workflow_content():
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_TITLE)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText("Open this workflow in the current window?")
        box.setInformativeText("The current workflow will be replaced. Save it first or discard changes.")
        save_btn = box.addButton(QtWidgets.QMessageBox.Save)
        discard_btn = box.addButton(QtWidgets.QMessageBox.Discard)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(save_btn)
        box.exec() if hasattr(box, "exec") else box.exec_()
        clicked = box.clickedButton()
        if clicked is save_btn:
            before = str(getattr(self._window, "_current_path", "") or "").strip()
            try:
                self._window._save_graph()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to save workflow:\n{exc}")
                return False
            after = str(getattr(self._window, "_current_path", "") or "").strip()
            return bool(before or after)
        return clicked is discard_btn

    def _current_window_has_workflow_content(self) -> bool:
        scene = getattr(self._window, "scene", None)
        if scene is None:
            return bool(getattr(self._window, "_current_path", "") or "")
        try:
            if getattr(scene, "_nodes_by_name", None):
                return True
        except Exception:
            pass
        try:
            if getattr(scene, "_edges", None):
                return True
        except Exception:
            pass
        try:
            return bool(getattr(self._window, "_current_path", "") or "")
        except Exception:
            return False
