# echograph/ui/dialogs.py
from __future__ import annotations
from typing import List, Dict, Any
from pathlib import Path

from echograph.qt_compat import QtCore, QtGui, QtWidgets, _qexec
from echograph.constants import LLM_URL, APP_TITLE
from echograph.ui import node_icons
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
def _kind_label(kind: str) -> str:
    text = (kind or "").strip()
    if not text:
        return "Node"
    key = text.lower().replace(" ", "_")
    if key in ("gen-x-videomocap", "gen-x_video_mocap", "genx_video_mocap", "genx_videomocap", "gemx_video_mocap"):
        return "GEN-X Video Mocap"
    if key in ("llm", "local_server", "localserver"):
        return "Local Server"
    acronyms = {"llm": "LLM", "uv": "UV", "fbx": "FBX", "bvh": "BVH", "html": "HTML"}
    words = []
    for part in text.split("_"):
        if not part:
            continue
        words.append(acronyms.get(part.lower(), part.capitalize()))
    return " ".join(words) if words else "Node"


def _kind_icon(kind: str) -> QtGui.QIcon:
    key = (kind or "").strip().lower()
    icon_pm = None
    if key == "database":
        icon_pm = node_icons._db_icon()
    elif key in ("llm", "local_server", "local server", "localserver"):
        icon_pm = node_icons._llm_server_icon()
    elif key == "llm_prompt":
        icon_pm = node_icons._llm_icon()
    elif key == "append":
        icon_pm = node_icons._append_icon()
    elif key == "note":
        icon_pm = node_icons._note_icon()
    elif key == "librarian":
        icon_pm = node_icons._librarian_icon()
    elif key == "import":
        icon_pm = node_icons._import_icon()
    elif key in ("fbx_import", "fbx import", "fbximport"):
        icon_pm = node_icons._fbx_icon() or node_icons._import_icon()
    elif key in ("mocap_import", "mocap import", "mocapimport", "bvh_import", "bvh import", "bvhimport"):
        icon_pm = node_icons._mocap_import_icon() or node_icons._import_icon()
    elif key in ("gen-x-videomocap", "gen-x video mocap", "genx_video_mocap", "genx video mocap", "genx_videomocap", "genx videomocap", "gemx_video_mocap", "gemx video mocap"):
        icon_pm = node_icons._genx_icon() or node_icons._mocap_import_icon() or node_icons._import_icon()
    elif key in ("anim_retarget", "anim retarget", "animretarget", "retarget"):
        icon_pm = node_icons._anim_retarget_icon() or node_icons._transforms_icon() or node_icons._import_icon()
    elif key == "switch":
        icon_pm = node_icons._switch_icon()
    elif key == "chatbot":
        icon_pm = node_icons._chatbot_icon()
    elif key in ("voice_actor", "voice actor", "voiceactor"):
        icon_pm = node_icons._voice_actor_icon() or node_icons._output_icon()
    elif key in ("mediator_agent", "mediator agent", "medigator_agent", "medigator agent", "medigator", "mediator"):
        icon_pm = node_icons._mediator_icon() or node_icons._python_icon() or node_icons._output_icon()
    elif key == "scene":
        icon_pm = node_icons._scene_icon()
    elif key == "camera":
        icon_pm = node_icons._camera_node_icon() or node_icons._screengrab_icon()
    elif key == "render":
        icon_pm = node_icons._render_node_icon() or node_icons._output_icon()
    elif key == "video_player":
        icon_pm = node_icons._video_player_icon() or node_icons._output_icon()
    elif key in ("post_process", "postprocess", "post_processing", "post_process_effect"):
        icon_pm = node_icons._post_process_icon() or node_icons._fx_node_icon() or node_icons._output_icon()
    elif key in ("sequence_to_mp4", "sequence mp4", "sequence_to_video", "image_sequence_to_mp4"):
        icon_pm = node_icons._sequence_to_mp4_icon() or node_icons._video_player_icon() or node_icons._output_icon()
    elif key == "instance":
        icon_pm = node_icons._instance_icon() or node_icons._output_icon()
    elif key == "primitive":
        icon_pm = node_icons._primitive_icon() or node_icons._output_icon()
    elif key == "html_preview":
        icon_pm = node_icons._html_preview_icon() or node_icons._output_icon()
    elif key == "image_collection":
        icon_pm = node_icons._image_collection_icon() or node_icons._output_icon()
    elif key == "split_volume":
        icon_pm = node_icons._volume_split_icon() or node_icons._output_icon()
    elif key == "uv_unwrap":
        icon_pm = node_icons._uv_unwrap_icon() or node_icons._output_icon()
    elif key in ("texture", "texture_pro"):
        icon_pm = node_icons._texture_node_icon() or node_icons._output_icon()
    elif key == "texture_layer":
        icon_pm = node_icons._texture_layer_icon() or node_icons._output_icon()
    elif key == "material":
        icon_pm = node_icons._material_node_icon() or node_icons._output_icon()
    elif key == "fx":
        icon_pm = node_icons._fx_node_icon() or node_icons._output_icon()
    elif key == "transforms":
        icon_pm = node_icons._transforms_icon() or node_icons._output_icon()
    elif key == "gantt_chart":
        icon_pm = node_icons._gantt_icon() or node_icons._output_icon()
    elif key in ("keyboard_sequence", "keyboard sequence", "keyboard_scheduler", "keyboard scheduler"):
        icon_pm = node_icons._keyboard_sequence_icon() or node_icons._output_icon()
    elif key in ("qubit_deck_controller", "qubit deck controller", "qubitdeckcontroller"):
        icon_pm = (
            node_icons._qubit_deck_controller_icon()
            or node_icons._librarian_icon()
            or node_icons._output_icon()
        )
    elif key == "export_fbx":
        icon_pm = node_icons._fbx_icon() or node_icons._output_icon()
    elif key == "output":
        icon_pm = node_icons._output_icon()
    elif key == "python":
        icon_pm = node_icons._python_icon()
    if icon_pm is not None:
        return QtGui.QIcon(icon_pm)
    style = QtWidgets.QApplication.style()
    if style is not None:
        return style.standardIcon(QtWidgets.QStyle.SP_FileIcon)
    return QtGui.QIcon()


class CreateNodeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, existing_names=None):
        super().__init__(parent)
        self.setWindowTitle("Create Node")
        self.setModal(True)
        self.setMinimumSize(660, 620)
        self.resize(700, 630)
        self._existing = set(existing_names or [])

        form = QtWidgets.QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(0)

        name_label = QtWidgets.QLabel("Node name:")
        name_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.name_edit = QtWidgets.QLineEdit(); self.name_edit.setPlaceholderText("e.g. My Tool")
        form.addWidget(name_label, 0, 0)
        form.addWidget(self.name_edit, 0, 1)

        self.kind_edit = QtWidgets.QComboBox()
        self.kind_edit.setEditable(True)
        self._kinds = [
            "node","camera","import","fbx_import","mocap_import","GEN-X-VideoMocap","anim_retarget","instance","primitive","uv_unwrap","texture","texture_pro","texture_layer","material","split_volume","transforms","fx","scene","render","video_player","post_process","sequence_to_mp4","export_fbx","html_preview","python","switch","output","local_server",
            "gantt_chart","keyboard_sequence",
            "qubit_deck_controller",
            "llm_prompt","chatbot","voice_actor","mediator_agent","librarian","note","append","image_collection","database"
        ]
        try:
            self._kinds.remove("note")
        except ValueError:
            pass
        self._kinds.insert(0, "note")
        try:
            self._kinds.remove("qubit_deck_controller")
        except ValueError:
            pass
        self._kinds.insert(1, "qubit_deck_controller")
        self.kind_edit.addItems(self._kinds)
        self.kind_edit.setEditText("note")
        kind_label = QtWidgets.QLabel("Node type:")
        kind_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.addWidget(kind_label, 1, 0)
        form.addWidget(self.kind_edit, 1, 1)

        self._llm_url_label = QtWidgets.QLabel("URL:")
        self._llm_url_edit  = QtWidgets.QLineEdit()
        self._llm_url_edit.setPlaceholderText("http://127.0.0.1:7860")
        self._llm_url_edit.setText(LLM_URL)
        self._llm_url_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        label_col_width = max(
            name_label.sizeHint().width(),
            kind_label.sizeHint().width(),
            self._llm_url_label.sizeHint().width(),
        )
        name_label.setFixedWidth(label_col_width)
        kind_label.setFixedWidth(label_col_width)
        self._llm_url_label.setFixedWidth(label_col_width)
        form.setColumnStretch(1, 1)

        self._llm_row = QtWidgets.QWidget()
        llm_row = QtWidgets.QHBoxLayout(self._llm_row)
        llm_row.setContentsMargins(0, 0, 0, 0)
        llm_row.setSpacing(6)
        llm_row.addWidget(self._llm_url_label, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        llm_row.addWidget(self._llm_url_edit, 1)
        self._llm_row.setVisible(False)

        nodes_box = QtWidgets.QGroupBox("Existing Nodes (click to create)")
        nodes_box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        qv = QtWidgets.QVBoxLayout(nodes_box)
        qv.setContentsMargins(6, 6, 6, 10)  # keep a bottom gutter before the Parameters section
        qv.setSpacing(4)
        quick_grid = QtWidgets.QGridLayout()
        quick_grid.setContentsMargins(0, 0, 0, 0)
        quick_grid.setHorizontalSpacing(3)
        quick_grid.setVerticalSpacing(4)
        quick_cols = 4
        for idx, kind in enumerate(self._kinds):
            row, col = divmod(idx, quick_cols)
            btn = QtWidgets.QToolButton()
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            btn.setText(_kind_label(kind))
            btn.setToolTip(kind)
            btn.setIcon(_kind_icon(kind))
            btn.setIconSize(QtCore.QSize(18, 18))
            btn.setMinimumHeight(28)
            btn.setMinimumWidth(117)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            btn.setStyleSheet(
                "QToolButton{"
                "background:transparent;"
                "border:1px solid transparent;"
                "border-radius:4px;"
                "padding:1px 4px;"
                "}"
                "QToolButton:hover{"
                "border:1px solid #22c55e;"
                "background:rgba(34,197,94,0.18);"
                "}"
                "QToolButton:pressed{"
                "border:1px solid #16a34a;"
                "background:rgba(34,197,94,0.28);"
                "}"
            )
            btn.clicked.connect(lambda _checked=False, k=kind: self._quick_create_from_kind(k))
            quick_grid.addWidget(btn, row, col)
        for col in range(quick_cols):
            quick_grid.setColumnStretch(col, 1)
        qv.addLayout(quick_grid)
        qv.addSpacing(4)
        quick_rows = max(1, (len(self._kinds) + quick_cols - 1) // quick_cols)
        button_row_h = 30
        grid_h = (quick_rows * button_row_h) + ((quick_rows - 1) * quick_grid.verticalSpacing())
        group_extra_h = 50  # title + frame + internal gutters
        nodes_box_min_h = grid_h + group_extra_h
        nodes_box.setFixedHeight(max(nodes_box.sizeHint().height() + 14, nodes_box_min_h))

        param_box = QtWidgets.QGroupBox("Parameters (optional)")
        param_box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        pv = QtWidgets.QVBoxLayout(param_box); pv.setContentsMargins(4,4,4,4); pv.setSpacing(3)
        self.param_list = QtWidgets.QListWidget()
        self.param_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.param_list.setMinimumHeight(56)
        self.param_list.setMaximumHeight(88)
        btns = QtWidgets.QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)
        add_btn = QtWidgets.QPushButton("Add…"); rem_btn = QtWidgets.QPushButton("Remove")
        btns.addWidget(add_btn); btns.addWidget(rem_btn); btns.addStretch(1)
        pv.addWidget(self.param_list); pv.addLayout(btns)
        add_btn.clicked.connect(self._add_param); rem_btn.clicked.connect(self._remove_param)
        param_box.setFixedHeight(param_box.sizeHint().height())

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
            is_llm = kind in ("llm", "local_server", "local server", "localserver")
            self._llm_row.setVisible(is_llm)

        self.kind_edit.currentTextChanged.connect(_toggle_code_box)
        _toggle_code_box(self.kind_edit.currentText())

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        layout.addLayout(form)
        layout.addWidget(self._llm_row)
        layout.addSpacing(8)
        layout.addWidget(nodes_box)
        layout.addSpacing(12)
        layout.addWidget(param_box)
        layout.addSpacing(6)
        layout.addWidget(code_box)
        layout.addWidget(bb)
        layout.setAlignment(QtCore.Qt.AlignTop)

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

    def _quick_create_from_kind(self, kind: str):
        kind = (kind or "").strip()
        if not kind:
            return
        self.kind_edit.setEditText(kind)
        self._accept()

    def _accept(self):
        name = self.name_edit.text().strip()
        if name in self._existing:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Node '{name}' already exists.")
            return
        self.accept()

    def result_payload(self):
        params = [{"name": self.param_list.item(i).text(), "value": ""} for i in range(self.param_list.count())]
        kind_raw = self.kind_edit.currentText().strip() or "node"
        kind_key = kind_raw.lower().replace(" ", "_")
        if kind_key == "localserver":
            kind_key = "local_server"
        if kind_key == "llm":
            kind_key = "local_server"
        kind = "local_server" if kind_key == "local_server" else kind_raw
        code = None
        if kind.lower() == "python":
            code_text = self.code_edit.toPlainText()
            code = code_text if code_text.strip() else ""
        if kind.lower().replace(" ", "_") in ("llm", "local_server", "localserver"):
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
