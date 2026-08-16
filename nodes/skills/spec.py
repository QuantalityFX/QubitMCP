from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.services.skills_library import scan_skills_library, skills_root, update_agent_template_status
from nodes.core import Spec


BODY_W = 520
BODY_H = 360


def _asset_label(asset: Dict[str, Any]) -> str:
    if asset.get("kind") == "agent_template":
        version = str(asset.get("template_version_id") or "").strip()
        status = str(asset.get("status") or "").strip()
        badges: List[str] = []
        if asset.get("is_latest_approved"):
            badges.append("latest approved")
        elif asset.get("is_latest_version"):
            badges.append("latest")
        suffix = " ".join(part for part in (version, status, *badges) if part)
        return f"{asset.get('name', '')}  {suffix}".strip()
    return str(asset.get("name", "") or "")


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
            QListWidget{background:#111827;color:#e5e7eb;border:1px solid #334155;border-radius:4px;}
            QListWidget::item{padding:4px 6px;}
            QListWidget::item:selected{background:#1f3a5f;color:#f8fafc;}
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
        self._approve_btn = QtWidgets.QPushButton("Approve")
        self._draft_btn = QtWidgets.QPushButton("Draft")
        self._deprecate_btn = QtWidgets.QPushButton("Deprecate")
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setObjectName("SkillsSubtle")
        self._status_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._approve_btn.clicked.connect(lambda _=False: self._set_selected_status("approved"))
        self._draft_btn.clicked.connect(lambda _=False: self._set_selected_status("draft"))
        self._deprecate_btn.clicked.connect(lambda _=False: self._set_selected_status("deprecated"))
        actions.addWidget(self._approve_btn, 0)
        actions.addWidget(self._draft_btn, 0)
        actions.addWidget(self._deprecate_btn, 0)
        actions.addWidget(self._status_label, 1)
        layout.addLayout(actions)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._list = QtWidgets.QListWidget()
        self._list.currentRowChanged.connect(self._show_asset)
        self._details = QtWidgets.QTextBrowser()
        self._details.setOpenExternalLinks(False)
        split.addWidget(self._list)
        split.addWidget(self._details)
        split.setSizes([230, 290])
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
            self._list.setCurrentRow(row)
        else:
            warnings = data.get("warnings", [])
            self._details.setHtml(self._details_html(None, warnings))
            self._sync_actions(None)

    def _add_asset(self, asset: Dict[str, Any]):
        row = len(self._assets)
        self._assets.append(asset)
        item = QtWidgets.QListWidgetItem(_asset_label(asset))
        item.setData(QtCore.Qt.UserRole, row)
        if asset.get("kind") == "human_template":
            item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon))
        else:
            item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        if asset.get("warnings"):
            item.setForeground(QtGui.QBrush(QtGui.QColor("#fbbf24")))
        elif asset.get("is_latest_approved"):
            item.setForeground(QtGui.QBrush(QtGui.QColor("#86efac")))
        elif str(asset.get("status") or "").strip().lower() == "approved":
            item.setForeground(QtGui.QBrush(QtGui.QColor("#bbf7d0")))
        elif asset.get("is_latest_version"):
            item.setForeground(QtGui.QBrush(QtGui.QColor("#93c5fd")))
        item.setToolTip(str(asset.get("path") or ""))
        self._list.addItem(item)

    def _show_asset(self, row: int):
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
        status = str((asset or {}).get("status") or "").strip().lower()
        for button in (self._approve_btn, self._draft_btn, self._deprecate_btn):
            button.setEnabled(is_agent)
        if is_agent:
            self._approve_btn.setEnabled(status != "approved")
            self._draft_btn.setEnabled(status != "draft")
            self._deprecate_btn.setEnabled(status != "deprecated")
            latest = str(asset.get("latest_version_id") or "")
            approved = str(asset.get("latest_approved_version_id") or "")
            if approved:
                self._status_label.setText(f"latest {latest} / approved {approved}")
            else:
                self._status_label.setText(f"latest {latest} / no approved")
        else:
            self._status_label.setText("")

    def _selected_asset(self) -> Dict[str, Any] | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._assets):
            return None
        return self._assets[row]

    def _set_selected_status(self, status: str):
        asset = self._selected_asset()
        if not asset or asset.get("kind") != "agent_template":
            return
        path = str(asset.get("path") or "")
        ok, message = update_agent_template_status(path, status, root=self._skills_root_text())
        self._status_label.setText(message)
        if ok:
            self.refresh(select_path=path)

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
        text = text.strip()
        if len(text) > 1800:
            return text[:1800].rstrip() + "\n..."
        return text

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
    root_param = None
    hidden_param = None
    for param in params:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name", "") or "").strip().lower()
        legacy_key = str(param.get("key", "") or "").strip().lower()
        if name == "root" or (legacy_key == "root" and not name):
            param["name"] = "root"
            param.pop("key", None)
            root_param = param
        elif name == "__ui_hidden_params":
            hidden_param = param
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
    hidden_param["value"] = ",".join(sorted(hidden))


def render_node_body(node_item, y_cursor: int) -> int:
    _ensure_skills_params(node_item)
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
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
)
