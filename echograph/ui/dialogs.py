# echograph/ui/dialogs.py
from __future__ import annotations
from typing import List, Dict, Any
from pathlib import Path

from echograph.qt_compat import QtCore, QtGui, QtWidgets, _qexec
from echograph.constants import LLM_URL, APP_TITLE
try:
    from nodes.python.highlighter import PythonSyntaxHighlighter
except Exception:  # pragma: no cover - optional plugin import
    PythonSyntaxHighlighter = None

# -------- Code Editor Dialog --------
class CodeEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, initial_code: str = ""):
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
        self._syntax_highlighter = None
        if PythonSyntaxHighlighter is not None:
            try:
                self._syntax_highlighter = PythonSyntaxHighlighter(self.edit.document())
            except Exception:
                self._syntax_highlighter = None
        v.addWidget(self.edit, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def code(self) -> str:
        return self.edit.toPlainText()

# -------- Param Editor Dialog --------
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

# -------- Big Text Edit Dialog --------
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

# -------- Comment Group Dialog --------
class CommentGroupDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title="Comment", body="", color="#1f2933", color_choices=None):
        super().__init__(parent)
        self.setWindowTitle("Comment")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._colors = list(color_choices or [
            "#1f2933", "#0f172a", "#312e81", "#1d4ed8", "#0ea5e9", "#22d3ee",
            "#10b981", "#065f46", "#f59e0b", "#f97316", "#ef4444", "#f472b6",
        ])
        self._selected_color = self._coerce_color(color or (self._colors[0] if self._colors else "#1f2933"))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self.title_edit = QtWidgets.QLineEdit(title or "Comment")
        self.body_edit = QtWidgets.QPlainTextEdit(body or "")
        self.body_edit.setPlaceholderText("Optional description")
        self.body_edit.setStyleSheet("QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}")
        form.addRow("Title:", self.title_edit)
        form.addRow("Body:", self.body_edit)
        layout.addLayout(form)

        color_box = QtWidgets.QGroupBox("Background color")
        grid = QtWidgets.QGridLayout(color_box)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self._color_group = QtWidgets.QButtonGroup(self)
        self._color_group.setExclusive(True)
        for idx, hex_color in enumerate(self._colors):
            btn = QtWidgets.QToolButton()
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setFixedSize(32, 32)
            btn.setProperty("color_hex", hex_color)
            btn.setStyleSheet(self._swatch_style(hex_color))
            self._color_group.addButton(btn, idx)
            row, col = divmod(idx, 6)
            grid.addWidget(btn, row, col)
            if hex_color.lower() == self._selected_color.lower():
                btn.setChecked(True)
        if self._color_group.checkedButton() is None and self._color_group.buttons():
            self._color_group.buttons()[0].setChecked(True)
            self._selected_color = self._colors[0]
        self._color_group.buttonClicked.connect(self._on_color_chosen)
        layout.addWidget(color_box)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _swatch_style(self, hex_color: str) -> str:
        return (
            "QToolButton{border:2px solid #0b1220;border-radius:8px;"
            "background:" + hex_color + ";}"
            "QToolButton:hover{border-color:#94a3b8;}"
            "QToolButton:checked{border-color:#e2e8f0;}"
        )

    def _coerce_color(self, value: str) -> str:
        qc = QtGui.QColor(value)
        if not qc.isValid():
            qc = QtGui.QColor(self._colors[0] if self._colors else "#1f2933")
        return qc.name(QtGui.QColor.HexRgb)

    def _on_color_chosen(self, btn):
        color = btn.property("color_hex") or ""
        self._selected_color = self._coerce_color(color)

    def result_payload(self) -> dict:
        title = (self.title_edit.text() or "").strip() or "Comment"
        body = (self.body_edit.toPlainText() or "").strip()
        return {"title": title, "body": body, "color": self._selected_color}

# -------- Create Node Dialog --------
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
        kinds = [
            "node","camera","import","instance","primitive","uv_unwrap","texture","texture_pro","texture_layer","split_volume","transforms","fx","scene","render","video_player","export_fbx","html_preview","python","switch","output","llm",
            "llm_prompt","chatbot","librarian","note","append","image_collection","database"
        ]
        try:
            kinds.remove("note")
        except ValueError:
            pass
        kinds.insert(0, "note")
        self.kind_edit.addItems(kinds)
        self.kind_edit.setEditText("note")
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

    def showEvent(self, e):
        super().showEvent(e)
        QtCore.QTimer.singleShot(0, self._focus_kind)

    def _focus_kind(self):
        try:
            self.kind_edit.setFocus(QtCore.Qt.TabFocusReason)
            le = self.kind_edit.lineEdit()
            if le: le.selectAll()
        except Exception:
            pass

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
        if name in self._existing:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Node '{name}' already exists.")
            return
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


class RecentGraphsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, recent_paths=None):
        super().__init__(parent)
        self.setWindowTitle("Recent Workflows")
        self.setMinimumWidth(420)
        self._paths = [str(p) for p in (recent_paths or []) if isinstance(p, str) and p.strip()]
        self._action = "open"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QtWidgets.QLabel("Select a recent workflow to open:")
        layout.addWidget(label)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        for path in self._paths:
            text = Path(path).name or path
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept_open())
        layout.addWidget(self.list, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_new = QtWidgets.QPushButton("New Workflow")
        btn_new.clicked.connect(self._accept_new)
        btn_row.addWidget(btn_new, 0, QtCore.Qt.AlignLeft)

        btn_row.addStretch(1)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept_open)
        btns.rejected.connect(self._reject_dialog)
        btn_row.addWidget(btns, 0, QtCore.Qt.AlignRight)

        layout.addLayout(btn_row)

    def selected_path(self) -> str:
        it = self.list.currentItem()
        if not it:
            return ""
        path = it.data(QtCore.Qt.UserRole)
        return str(path) if path else ""

    def _accept_open(self):
        self._action = "open"
        self.accept()

    def _accept_new(self):
        self._action = "new"
        self.accept()

    def _reject_dialog(self):
        self._action = "cancel"
        self.reject()

    def result_action(self) -> str:
        return self._action
