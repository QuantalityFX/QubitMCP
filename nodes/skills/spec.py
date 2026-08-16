from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.services.sales_agent import generate_sales_deck_from_template
from echograph.services.skills_library import scan_skills_library, skills_root, update_agent_template_status
from echograph.services.teacher_agent import (
    prepare_teacher_agent_conversion_request,
    write_teacher_agent_ai_response,
)
from nodes.core import Spec


BODY_W = 680
BODY_H = 380
MEDIATOR_INPUT_PORT = "mediator"
DATA_NEXUS_INPUT_PORT = "data_nexus"
SALES_DECK_INDEX_PARAM = "__sales_agent_last_deck_index"
SALES_DECK_DIR_PARAM = "__sales_agent_last_deck_dir"
DATA_NEXUS_KINDS = {"data_nexus", "data nexus", "data_graph", "data graph", "nexus"}


def _asset_kind_label(asset: Dict[str, Any]) -> str:
    if asset.get("kind") == "agent_template":
        return "Agent"
    if asset.get("kind") == "human_template":
        return "Human"
    return str(asset.get("kind") or "")


def _asset_marker(asset: Dict[str, Any]) -> str:
    if asset.get("warnings"):
        return "warnings"
    if asset.get("kind") != "agent_template":
        return ""
    status = str(asset.get("status") or "").strip().lower()
    version = str(asset.get("template_version_id") or "").strip()
    latest_approved = str(asset.get("latest_approved_version_id") or "").strip()
    if asset.get("is_latest_approved"):
        return "active"
    if asset.get("is_latest_version"):
        return "latest"
    if latest_approved and version != latest_approved:
        return "older"
    return ""


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _connected_mediator_node(scene, node_item):
    if scene is None or node_item is None:
        return None
    mediator_kinds = {
        "mediator_agent",
        "medigator_agent",
        "medigator",
        "medigator agent",
        "mediator",
        "mediator agent",
    }
    try:
        edges = list(scene._in_edges(node_item))
    except Exception:
        edges = []
    for edge in edges:
        dst_port = ""
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(edge, attr):
                dst_port = str(getattr(edge, attr) or "").strip().lower()
                if dst_port:
                    break
        src = getattr(edge, "src", None)
        if dst_port == MEDIATOR_INPUT_PORT and _kind_of_item(src) in mediator_kinds:
            return src
    for edge in edges:
        src = getattr(edge, "src", None)
        if _kind_of_item(src) in mediator_kinds:
            return src
    return None


def _edge_dst_port(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        if hasattr(edge, attr):
            value = str(getattr(edge, attr) or "").strip().lower()
            if value:
                return value
    return ""


def _connected_data_nexus_node(scene, node_item):
    if scene is None or node_item is None:
        return None
    try:
        edges = list(scene._in_edges(node_item))
    except Exception:
        edges = []
    for edge in edges:
        src = getattr(edge, "src", None)
        if _edge_dst_port(edge) == DATA_NEXUS_INPUT_PORT and _kind_of_item(src) in DATA_NEXUS_KINDS:
            return src
    for edge in edges:
        src = getattr(edge, "src", None)
        if _kind_of_item(src) in DATA_NEXUS_KINDS:
            return src
    return None


class SkillsLibraryWidget(QtWidgets.QFrame):
    def __init__(self, node_item=None, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._assets: List[Dict[str, Any]] = []
        self._current_path = ""
        self.setObjectName("SkillsLibraryWidget")
        self.setStyleSheet(
            """
            QFrame#SkillsLibraryWidget{background:#0f1216;border:1px solid #334155;border-radius:6px;}
            QLabel{color:#e5e7eb;}
            QLabel#SkillsTitle{font-weight:600;color:#f8fafc;}
            QLabel#SkillsSubtle{color:#94a3b8;}
            QTreeWidget{background:#111827;color:#e5e7eb;border:1px solid #334155;border-radius:4px;}
            QTreeWidget::item{padding:3px 4px;}
            QTreeWidget::item:selected{background:#1f3a5f;color:#f8fafc;}
            QHeaderView::section{background:#0f172a;color:#cbd5e1;border:0;border-right:1px solid #334155;padding:4px 6px;}
            QTextBrowser{background:#0b1018;color:#dbeafe;border:1px solid #334155;border-radius:4px;}
            QPushButton{background:#1e293b;color:#e5e7eb;border:1px solid #475569;border-radius:4px;padding:4px 8px;}
            QPushButton:hover{background:#334155;}
            """
        )

        root = self._skills_root_text()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Skills Library")
        title.setObjectName("SkillsTitle")
        self._summary = QtWidgets.QLabel("")
        self._summary.setObjectName("SkillsSubtle")
        self._summary.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(title, 0)
        header.addWidget(self._summary, 1)
        header.addWidget(refresh_btn, 0)
        layout.addLayout(header)

        self._root_label = QtWidgets.QLabel(root)
        self._root_label.setObjectName("SkillsSubtle")
        self._root_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self._root_label)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._convert_btn = QtWidgets.QPushButton("Convert")
        self._convert_btn.setToolTip("Convert selected human template with Teacher Agent")
        self._generate_btn = QtWidgets.QPushButton("Generate Deck")
        self._generate_btn.setToolTip("Generate an HTML deck from the selected approved Sales Agent template and connected Data Nexus")
        self._approve_btn = QtWidgets.QPushButton("Approve")
        self._draft_btn = QtWidgets.QPushButton("Draft")
        self._deprecate_btn = QtWidgets.QPushButton("Deprecate")
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setObjectName("SkillsSubtle")
        self._status_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._convert_btn.clicked.connect(self._convert_selected_human_template)
        self._generate_btn.clicked.connect(self._generate_sales_deck_from_selected_template)
        self._approve_btn.clicked.connect(lambda _=False: self._set_selected_status("approved"))
        self._draft_btn.clicked.connect(lambda _=False: self._set_selected_status("draft"))
        self._deprecate_btn.clicked.connect(lambda _=False: self._set_selected_status("deprecated"))
        actions.addWidget(self._convert_btn, 0)
        actions.addWidget(self._generate_btn, 0)
        actions.addWidget(self._approve_btn, 0)
        actions.addWidget(self._draft_btn, 0)
        actions.addWidget(self._deprecate_btn, 0)
        actions.addWidget(self._status_label, 1)
        layout.addLayout(actions)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._list = QtWidgets.QTreeWidget()
        self._list.setHeaderLabels(["Name", "Kind", "Version", "Status", "Marker"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._list.currentItemChanged.connect(self._show_asset)
        self._list.customContextMenuRequested.connect(self._show_asset_context_menu)
        header_view = self._list.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 5):
            header_view.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        self._details = QtWidgets.QTextBrowser()
        self._details.setOpenExternalLinks(False)
        split.addWidget(self._list)
        split.addWidget(self._details)
        split.setSizes([380, 300])
        layout.addWidget(split, 1)

        self.refresh()

    def _skills_root_text(self) -> str:
        try:
            root_param = getattr(self._node_item, "_param_value", lambda _key: "")("root")
        except Exception:
            root_param = ""
        return str(root_param or "Skills")

    def sizeHint(self):
        return QtCore.QSize(BODY_W, BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(440, 300)

    def refresh(self, select_path: str = ""):
        wanted_path = str(select_path or self._current_path or "").strip()
        root = self._skills_root_text()
        snapshot = scan_skills_library(root)
        data = snapshot.to_dict()
        self._assets = []
        self._list.clear()

        for asset in data.get("human_templates", []):
            self._add_asset(asset)
        for asset in data.get("agent_templates", []):
            self._add_asset(asset)

        human_count = len(data.get("human_templates", []))
        agent_count = len(data.get("agent_templates", []))
        warning_count = len(data.get("warnings", [])) + sum(len(a.get("warnings", [])) for a in self._assets)
        self._summary.setText(f"{human_count} human / {agent_count} agent / {warning_count} warnings")
        if self._assets:
            row = 0
            if wanted_path:
                for idx, asset in enumerate(self._assets):
                    if str(asset.get("path") or "") == wanted_path:
                        row = idx
                        break
            self._list.setCurrentItem(self._list.topLevelItem(row))
        else:
            warnings = data.get("warnings", [])
            self._details.setHtml(self._details_html(None, warnings))
            self._sync_actions(None)

    def _add_asset(self, asset: Dict[str, Any]):
        row = len(self._assets)
        self._assets.append(asset)
        item = QtWidgets.QTreeWidgetItem(
            [
                str(asset.get("name") or ""),
                _asset_kind_label(asset),
                str(asset.get("template_version_id") or ""),
                str(asset.get("status") or ""),
                _asset_marker(asset),
            ]
        )
        for column in range(5):
            item.setData(column, QtCore.Qt.UserRole, row)
            item.setToolTip(column, str(asset.get("path") or ""))
        if asset.get("kind") == "human_template":
            item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon))
        else:
            item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        if asset.get("warnings"):
            color = QtGui.QColor("#fbbf24")
        elif asset.get("is_latest_approved"):
            color = QtGui.QColor("#86efac")
        elif str(asset.get("status") or "").strip().lower() == "approved":
            color = QtGui.QColor("#bbf7d0")
        elif asset.get("is_latest_version"):
            color = QtGui.QColor("#93c5fd")
        else:
            color = QtGui.QColor("#e5e7eb")
        brush = QtGui.QBrush(color)
        for column in range(5):
            item.setForeground(column, brush)
        self._list.addTopLevelItem(item)

    def _show_asset(self, current, _previous=None):
        if current is None:
            row = -1
        else:
            raw_row = current.data(0, QtCore.Qt.UserRole)
            row = int(raw_row) if raw_row is not None else -1
        if row < 0 or row >= len(self._assets):
            self._current_path = ""
            self._sync_actions(None)
            return
        asset = self._assets[row]
        self._current_path = str(asset.get("path") or "")
        self._details.setHtml(self._details_html(asset, []))
        self._sync_actions(asset)

    def _sync_actions(self, asset: Dict[str, Any] | None):
        is_agent = bool(asset and asset.get("kind") == "agent_template")
        is_human = bool(asset and asset.get("kind") == "human_template")
        status = str((asset or {}).get("status") or "").strip().lower()
        self._convert_btn.setEnabled(is_human)
        self._generate_btn.setEnabled(
            bool(
                is_agent
                and status == "approved"
                and str(asset.get("target_agent") or "").strip().lower() == "sales_agent"
                and str(asset.get("artifact_kind") or "").strip().lower() == "html_deck"
            )
        )
        for button in (self._approve_btn, self._draft_btn, self._deprecate_btn):
            button.setEnabled(is_agent)
        if is_agent:
            self._approve_btn.setEnabled(status != "approved")
            self._draft_btn.setEnabled(status != "draft")
            self._deprecate_btn.setEnabled(status != "deprecated")
            latest = str(asset.get("latest_version_id") or "")
            approved = str(asset.get("latest_approved_version_id") or "")
            current = str(asset.get("template_version_id") or "")
            if asset.get("is_latest_approved"):
                self._status_label.setText(f"active approved {current}")
            elif status == "approved" and approved:
                self._status_label.setText(f"older approved / active {approved}")
            elif approved:
                self._status_label.setText(f"latest {latest} / approved {approved}")
            else:
                self._status_label.setText(f"latest {latest} / no approved")
        else:
            self._status_label.setText("")

    def _selected_asset(self) -> Dict[str, Any] | None:
        current = self._list.currentItem()
        if current is None:
            row = -1
        else:
            raw_row = current.data(0, QtCore.Qt.UserRole)
            row = int(raw_row) if raw_row is not None else -1
        if row < 0 or row >= len(self._assets):
            return None
        return self._assets[row]

    def _asset_from_item(self, item: QtWidgets.QTreeWidgetItem | None) -> Dict[str, Any] | None:
        if item is None:
            return None
        raw_row = item.data(0, QtCore.Qt.UserRole)
        try:
            row = int(raw_row)
        except Exception:
            return None
        if row < 0 or row >= len(self._assets):
            return None
        return self._assets[row]

    def _show_asset_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self._list.itemAt(pos)
        asset = self._asset_from_item(item)
        if not asset:
            return
        self._list.setCurrentItem(item)
        path_text = str(asset.get("path") or "")
        menu = QtWidgets.QMenu(self)
        open_action = menu.addAction("Open in Explorer")
        open_action.setEnabled(bool(path_text.strip()))
        open_action.triggered.connect(lambda _checked=False, p=path_text: self._open_asset_folder(p))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    @staticmethod
    def _resolve_asset_path(path_text: Any) -> Path | None:
        raw = str(path_text or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = skills_root().parent / path
        return path

    def _open_asset_folder(self, path_text: Any) -> None:
        path = self._resolve_asset_path(path_text)
        if path is None:
            return
        folder = path.parent if path.suffix else path
        if not folder.exists():
            self._status_label.setText(f"Folder not found: {folder}")
            return
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))
        if ok:
            self._status_label.setText(f"Opened {folder}")
        else:
            self._status_label.setText(f"Could not open folder: {folder}")

    def _set_selected_status(self, status: str):
        asset = self._selected_asset()
        if not asset or asset.get("kind") != "agent_template":
            return
        path = str(asset.get("path") or "")
        ok, message = update_agent_template_status(path, status, root=self._skills_root_text())
        self._status_label.setText(message)
        if ok:
            self.refresh(select_path=path)

    def _convert_selected_human_template(self):
        asset = self._selected_asset()
        if not asset or asset.get("kind") != "human_template":
            return
        request = prepare_teacher_agent_conversion_request(
            str(asset.get("path") or ""),
            root=self._skills_root_text(),
            target_agent="sales_agent",
            artifact_kind="html_deck",
        )
        if not request.ok:
            self._status_label.setText(request.message)
            return

        scene = None
        try:
            scene = self._node_item.scene()
        except Exception:
            scene = None
        mediator_node = _connected_mediator_node(scene, self._node_item)
        if mediator_node is None:
            self._status_label.setText("Connect a Mediator node to run Teacher Agent AI conversion.")
            return

        try:
            from nodes.mediator_agent import spec as mediator_spec
        except Exception as exc:
            self._status_label.setText(f"Mediator unavailable: {exc}")
            return
        runner = getattr(mediator_spec, "run_teacher_agent_conversion_from_item", None)
        if not callable(runner):
            self._status_label.setText("Mediator Teacher Agent bridge is unavailable.")
            return

        request_data = request.to_dict()

        def _finish(exit_code: int, error_text: str, response_text: str):
            def _apply():
                if int(exit_code) != 0 or str(error_text or "").strip():
                    self._status_label.setText(str(error_text or f"Teacher Agent exited with code {exit_code}."))
                    return
                result = write_teacher_agent_ai_response(
                    response_text,
                    request_data,
                    root=self._skills_root_text(),
                )
                self._status_label.setText(result.message)
                if result.ok:
                    self.refresh(select_path=result.agent_template_path)

            try:
                QtCore.QTimer.singleShot(0, self, _apply)
            except Exception:
                _apply()

        ok, message = runner(scene, mediator_node, request.prompt, request.signature, on_done=_finish)
        self._status_label.setText(message)

    def _set_hidden_node_param(self, name: str, value: str) -> None:
        if self._node_item is None:
            return
        setter = getattr(self._node_item, "_set_param_value", None)
        if callable(setter):
            try:
                setter(name, value, rebuild=False, notify_scene=True)
                return
            except Exception:
                pass
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        params = list(getattr(model, "params", None) or [])
        target = str(name or "").strip().lower()
        for param in params:
            if isinstance(param, dict) and str(param.get("name", "") or "").strip().lower() == target:
                param["value"] = str(value or "")
                break
        else:
            params.append({"name": name, "value": str(value or "")})
        try:
            model.params = params
        except Exception:
            pass

    def _generate_sales_deck_from_selected_template(self):
        asset = self._selected_asset()
        if not asset or asset.get("kind") != "agent_template":
            return
        scene = None
        try:
            scene = self._node_item.scene()
        except Exception:
            scene = None
        nexus_node = _connected_data_nexus_node(scene, self._node_item)
        if nexus_node is None:
            self._status_label.setText("Connect a Data Nexus node to the Skills data_nexus input.")
            return

        try:
            from nodes.data_nexus import spec as data_nexus_spec
        except Exception as exc:
            self._status_label.setText(f"Data Nexus unavailable: {exc}")
            return
        bundle_helper = getattr(data_nexus_spec, "normalized_data_nexus_points_from_item", None)
        if not callable(bundle_helper):
            self._status_label.setText("Data Nexus point bundle helper is unavailable.")
            return

        try:
            bundle = bundle_helper(nexus_node)
        except Exception as exc:
            self._status_label.setText(f"Failed to read Data Nexus bundle: {exc}")
            return

        result = generate_sales_deck_from_template(
            str(asset.get("path") or ""),
            bundle,
            root=self._skills_root_text(),
        )
        self._status_label.setText(result.message)
        if not result.ok:
            return
        self._set_hidden_node_param(SALES_DECK_INDEX_PARAM, result.index_path)
        self._set_hidden_node_param(SALES_DECK_DIR_PARAM, result.deck_dir)
        self._status_label.setToolTip(result.index_path)

    def _details_html(self, asset: Dict[str, Any] | None, library_warnings: List[str]) -> str:
        css = (
            "<style>"
            "body{font-family:Segoe UI,Roboto,Arial,sans-serif;font-size:12px;color:#dbeafe;}"
            "h3{margin:0 0 8px 0;color:#f8fafc;font-size:14px;}"
            "dl{margin:0;}dt{color:#93c5fd;font-weight:600;margin-top:6px;}dd{margin:1px 0 4px 0;color:#e5e7eb;}"
            "code{color:#fef3c7;}ul{margin-top:4px;padding-left:18px;}li{margin-bottom:3px;}"
            ".warn{color:#fbbf24;}.ok{color:#86efac;}"
            "</style>"
        )
        if asset is None:
            items = "".join(f"<li>{self._esc(w)}</li>" for w in library_warnings) or "<li>No templates found.</li>"
            return f"{css}<h3>No Selection</h3><ul class='warn'>{items}</ul>"

        rows = []
        for key in (
            "kind",
            "path",
            "template_id",
            "template_family_id",
            "template_version_id",
            "target_agent",
            "artifact_kind",
            "delivery_formats",
            "status",
            "family_version_count",
            "is_latest_version",
            "latest_version_id",
            "is_latest_approved",
            "latest_approved_version_id",
            "slot_count",
            "slide_count",
            "duplicate_slots",
            "modified_at",
            "conversion_report",
            "generated_by",
            "generated_at",
        ):
            value = asset.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            rows.append(f"<dt>{self._esc(key)}</dt><dd><code>{self._esc(value)}</code></dd>")
        warnings = asset.get("warnings", [])
        if warnings:
            warn_html = "".join(f"<li>{self._esc(w)}</li>" for w in warnings)
        else:
            warn_html = "<li class='ok'>Validation basics passed.</li>"
        preview = self._read_preview(asset.get("path", ""))
        return (
            f"{css}<h3>{self._esc(asset.get('name', 'Template'))}</h3>"
            f"<dl>{''.join(rows)}</dl>"
            f"<h3 style='margin-top:10px;'>Validation</h3><ul>{warn_html}</ul>"
            f"<h3 style='margin-top:10px;'>Preview</h3>"
            f"<pre style='white-space:pre-wrap;color:#cbd5e1;'>{self._esc(preview)}</pre>"
        )

    @staticmethod
    def _read_preview(path_text: Any) -> str:
        raw = str(path_text or "").strip()
        if not raw:
            return ""
        path = Path(raw)
        if not path.is_absolute():
            root = skills_root().parent
            path = root / raw
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"Preview unavailable: {exc}"
        return text.strip()

    @staticmethod
    def _esc(value: Any) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


def _ensure_skills_params(node_item) -> None:
    model = getattr(node_item, "model", None)
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        return
    filtered_params: List[Dict[str, Any]] = []
    root_param = None
    hidden_param = None
    for param in params:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name", "") or "").strip().lower()
        legacy_key = str(param.get("key", "") or "").strip().lower()
        if name == MEDIATOR_INPUT_PORT:
            continue
        if name == "root" or (legacy_key == "root" and not name):
            param["name"] = "root"
            param.pop("key", None)
            root_param = param
        elif name == "__ui_hidden_params":
            hidden_param = param
        filtered_params.append(param)
    if len(filtered_params) != len(params):
        params = filtered_params
        try:
            model.params = params
        except Exception:
            pass
    if root_param is None:
        root_param = {"name": "root", "value": "Skills"}
        params.append(root_param)
    elif not str(root_param.get("value", "") or "").strip():
        root_param["value"] = "Skills"

    if hidden_param is None:
        hidden_param = {"name": "__ui_hidden_params", "value": ""}
        params.append(hidden_param)
    hidden = {part.strip().lower() for part in str(hidden_param.get("value", "") or "").split(",") if part.strip()}
    hidden.add("root")
    hidden.add("__skills_size")
    hidden.add(MEDIATOR_INPUT_PORT)
    hidden.add(DATA_NEXUS_INPUT_PORT)
    hidden.add(SALES_DECK_INDEX_PARAM)
    hidden.add(SALES_DECK_DIR_PARAM)
    hidden_param["value"] = ",".join(sorted(hidden))


def build_ports(node_item) -> None:
    _ensure_skills_params(node_item)
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(MEDIATOR_INPUT_PORT)
        node_item.ensure_input(DATA_NEXUS_INPUT_PORT)
    try:
        setattr(node_item, "_default_named_input", MEDIATOR_INPUT_PORT)
        setattr(node_item, "_show_default_input_with_named", True)
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = SkillsLibraryWidget(node_item, None)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    pad = float(getattr(node_item, "_PADDING", 0.0) or 0.0)
    current_w = float(getattr(node_item, "width", BODY_W) or BODY_W)
    current_h = float(getattr(node_item, "height", BODY_H) or BODY_H)
    available_h = int(max(0.0, current_h - float(y_cursor) - pad))
    w = max(BODY_W, int(hint.width()), int(current_w))
    h = max(BODY_H, int(hint.height()), available_h)
    try:
        proxy.setMinimumSize(w, h)
        proxy.setPreferredSize(w, h)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    try:
        node_item._input_port_pos[DATA_NEXUS_INPUT_PORT] = (
            QtCore.QPointF(0.0, float(y_cursor) + 76.0),
            DATA_NEXUS_INPUT_PORT,
        )
    except Exception:
        pass

    bottom_y = int(y_cursor + h)
    try:
        required_height = float(bottom_y) + pad
        if current_w < float(w) or current_h < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = max(current_h, required_height)
            node_item.width = max(current_w, float(w))
            try:
                node_item.update()
            except Exception:
                pass
    except Exception:
        pass
    return bottom_y


SKILLS_SPEC = Spec(
    stripe_color="#1e3a8a",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
