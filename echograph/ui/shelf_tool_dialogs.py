from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict, List

from echograph.qt_compat import QtCore, QtWidgets, _qexec
from echograph.services.shelf_tools_store import make_stored_path, resolve_stored_path


_TYPE_LABELS = {
    "python_script": "Python Script",
    "python_code": "Python Code",
    "workflow_shortcut": "Workflow Shortcut",
}


def _args_to_text(args: Any) -> str:
    if not isinstance(args, list):
        return ""
    parts = []
    for arg in args:
        text = str(arg)
        if not text:
            continue
        try:
            parts.append(shlex.quote(text))
        except Exception:
            parts.append(text)
    return " ".join(parts)


def _args_from_text(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        return [str(v) for v in shlex.split(raw, posix=False)]
    except Exception:
        return [raw]


class ShelfToolDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        *,
        tool: Dict[str, Any] | None = None,
        default_type: str = "python_script",
    ):
        super().__init__(parent)
        self.setWindowTitle("Shelf Tool")
        self.resize(640, 520)
        self._tool = dict(tool or {})

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        layout.addLayout(form, 0)

        self.type_combo = QtWidgets.QComboBox(self)
        for key in ("python_script", "python_code", "workflow_shortcut"):
            self.type_combo.addItem(_TYPE_LABELS[key], key)
        form.addRow("Type", self.type_combo)

        self.label_edit = QtWidgets.QLineEdit(self)
        form.addRow("Label", self.label_edit)

        self.tooltip_edit = QtWidgets.QLineEdit(self)
        form.addRow("Tooltip", self.tooltip_edit)

        self.icon_edit = QtWidgets.QLineEdit(self)
        icon_row = QtWidgets.QWidget(self)
        icon_layout = QtWidgets.QHBoxLayout(icon_row)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(6)
        icon_layout.addWidget(self.icon_edit, 1)
        icon_browse = QtWidgets.QPushButton("Browse", icon_row)
        icon_browse.clicked.connect(self._browse_icon)
        icon_layout.addWidget(icon_browse, 0)
        icon_clear = QtWidgets.QPushButton("Clear", icon_row)
        icon_clear.clicked.connect(self.icon_edit.clear)
        icon_layout.addWidget(icon_clear, 0)
        form.addRow("Icon", icon_row)

        self.stack = QtWidgets.QStackedWidget(self)
        layout.addWidget(self.stack, 1)

        self._script_page = self._build_script_page()
        self._code_page = self._build_code_page()
        self._workflow_page = self._build_workflow_page()
        self.stack.addWidget(self._script_page)
        self.stack.addWidget(self._code_page)
        self.stack.addWidget(self._workflow_page)

        self.show_output_check = QtWidgets.QCheckBox("Show output after run", self)
        self.show_output_check.setChecked(True)
        layout.addWidget(self.show_output_check, 0)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0)

        self.type_combo.currentIndexChanged.connect(self._sync_page)
        self._load_tool(self._tool, default_type)

    def _build_script_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(page)
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.script_path_edit = QtWidgets.QLineEdit(page)
        script_row = QtWidgets.QWidget(page)
        script_layout = QtWidgets.QHBoxLayout(script_row)
        script_layout.setContentsMargins(0, 0, 0, 0)
        script_layout.setSpacing(6)
        script_layout.addWidget(self.script_path_edit, 1)
        browse_script = QtWidgets.QPushButton("Browse", script_row)
        browse_script.clicked.connect(self._browse_script)
        script_layout.addWidget(browse_script, 0)
        form.addRow("Script", script_row)

        self.working_dir_edit = QtWidgets.QLineEdit(page)
        wd_row = QtWidgets.QWidget(page)
        wd_layout = QtWidgets.QHBoxLayout(wd_row)
        wd_layout.setContentsMargins(0, 0, 0, 0)
        wd_layout.setSpacing(6)
        wd_layout.addWidget(self.working_dir_edit, 1)
        browse_dir = QtWidgets.QPushButton("Browse", wd_row)
        browse_dir.clicked.connect(self._browse_working_dir)
        wd_layout.addWidget(browse_dir, 0)
        form.addRow("Working Dir", wd_row)

        self.args_edit = QtWidgets.QLineEdit(page)
        form.addRow("Args", self.args_edit)

        self.thread_combo = QtWidgets.QComboBox(page)
        self.thread_combo.addItem("Worker", "worker")
        self.thread_combo.addItem("Main Thread", "main_thread")
        form.addRow("Thread", self.thread_combo)
        return page

    def _build_code_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.code_edit = QtWidgets.QPlainTextEdit(page)
        self.code_edit.setStyleSheet(
            "QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}"
        )
        layout.addWidget(self.code_edit, 1)

        self.code_thread_combo = QtWidgets.QComboBox(page)
        self.code_thread_combo.addItem("Worker", "worker")
        self.code_thread_combo.addItem("Main Thread", "main_thread")
        layout.addWidget(self.code_thread_combo, 0)
        return page

    def _build_workflow_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(page)
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.workflow_path_edit = QtWidgets.QLineEdit(page)
        row = QtWidgets.QWidget(page)
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(self.workflow_path_edit, 1)
        browse = QtWidgets.QPushButton("Browse", row)
        browse.clicked.connect(self._browse_workflow)
        row_layout.addWidget(browse, 0)
        form.addRow("Workflow", row)

        self.workflow_open_mode_combo = QtWidgets.QComboBox(page)
        self.workflow_open_mode_combo.addItem("New Instance", "new_instance")
        self.workflow_open_mode_combo.addItem("Current Window", "current_window")
        form.addRow("Open In", self.workflow_open_mode_combo)
        return page

    def _load_tool(self, tool: Dict[str, Any], default_type: str) -> None:
        tool_type = str(tool.get("type", default_type) or default_type)
        idx = self.type_combo.findData(tool_type)
        self.type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.label_edit.setText(str(tool.get("label", "") or ""))
        self.tooltip_edit.setText(str(tool.get("tooltip", "") or ""))
        self.icon_edit.setText(str(tool.get("icon", "") or ""))

        self.script_path_edit.setText(str(tool.get("script_path", "") or ""))
        self.working_dir_edit.setText(str(tool.get("working_dir", "") or ""))
        self.args_edit.setText(_args_to_text(tool.get("args")))
        execution = tool.get("execution") if isinstance(tool.get("execution"), dict) else {}
        thread_mode = str(execution.get("thread_mode", "worker") or "worker")
        idx = self.thread_combo.findData(thread_mode)
        self.thread_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.code_thread_combo.findData(thread_mode)
        self.code_thread_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.show_output_check.setChecked(bool(execution.get("show_output", True)))

        self.code_edit.setPlainText(str(tool.get("code", "") or ""))
        self.workflow_path_edit.setText(str(tool.get("workflow_path", "") or ""))
        workflow_open_mode = str(tool.get("open_mode", "new_instance") or "new_instance")
        idx = self.workflow_open_mode_combo.findData(workflow_open_mode)
        self.workflow_open_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._sync_page()

    def _sync_page(self) -> None:
        tool_type = self.type_combo.currentData()
        index = {"python_script": 0, "python_code": 1, "workflow_shortcut": 2}.get(tool_type, 0)
        self.stack.setCurrentIndex(index)
        self.show_output_check.setVisible(tool_type in {"python_script", "python_code"})

    def _browse_script(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Python Script",
            str(Path(self.script_path_edit.text() or ".").expanduser().parent),
            "Python Files (*.py);;All Files (*.*)",
        )
        if not path:
            return
        stored, _ = make_stored_path(path)
        self.script_path_edit.setText(stored)
        if not self.working_dir_edit.text().strip():
            wd, _ = make_stored_path(Path(path).parent)
            self.working_dir_edit.setText(wd)
        if not self.label_edit.text().strip():
            self.label_edit.setText(Path(path).stem.replace("_", " ").title())

    def _browse_icon(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Icon",
            str(Path(self.icon_edit.text() or ".").expanduser().parent),
            "Image Files (*.png *.jpg *.jpeg *.bmp *.ico);;All Files (*.*)",
        )
        if not path:
            return
        stored, _ = make_stored_path(path)
        self.icon_edit.setText(stored)

    def _browse_working_dir(self) -> None:
        start = self.working_dir_edit.text().strip() or "."
        try:
            start = str(resolve_stored_path(start))
        except Exception:
            pass
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Working Directory", start)
        if path:
            stored, _ = make_stored_path(path)
            self.working_dir_edit.setText(stored)

    def _browse_workflow(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Workflow",
            str(Path(self.workflow_path_edit.text() or ".").expanduser().parent),
            "JSON Files (*.json);;All Files (*.*)",
        )
        if not path:
            return
        stored, _ = make_stored_path(path)
        self.workflow_path_edit.setText(stored)
        if not self.label_edit.text().strip():
            self.label_edit.setText(Path(path).stem)

    def result_tool(self) -> Dict[str, Any]:
        tool_type = str(self.type_combo.currentData() or "python_script")
        label = self.label_edit.text().strip()
        tooltip = self.tooltip_edit.text().strip()
        base: Dict[str, Any] = {
            "type": tool_type,
            "label": label,
            "tooltip": tooltip,
            "enabled": True,
        }
        icon_text = self.icon_edit.text().strip()
        if icon_text:
            icon_stored, _ = make_stored_path(icon_text)
            base["icon"] = icon_stored
        else:
            base["icon"] = ""
        existing_id = str(self._tool.get("id", "") or "").strip()
        if existing_id:
            base["id"] = existing_id

        if tool_type == "python_script":
            script_stored, script_mode = make_stored_path(self.script_path_edit.text().strip())
            working_dir_text = self.working_dir_edit.text().strip()
            working_stored, _ = make_stored_path(working_dir_text) if working_dir_text else ("", "absolute")
            base.update(
                {
                    "script_path": script_stored,
                    "path_mode": script_mode,
                    "working_dir": working_stored,
                    "args": _args_from_text(self.args_edit.text()),
                    "execution": {
                        "thread_mode": str(self.thread_combo.currentData() or "worker"),
                        "show_output": self.show_output_check.isChecked(),
                    },
                }
            )
        elif tool_type == "python_code":
            base.update(
                {
                    "code": self.code_edit.toPlainText(),
                    "params": list(self._tool.get("params", []) or []),
                    "execution": {
                        "thread_mode": str(self.code_thread_combo.currentData() or "worker"),
                        "show_output": self.show_output_check.isChecked(),
                    },
                }
            )
        else:
            workflow_stored, workflow_mode = make_stored_path(self.workflow_path_edit.text().strip())
            base.update(
                {
                    "workflow_path": workflow_stored,
                    "path_mode": workflow_mode,
                    "open_mode": str(self.workflow_open_mode_combo.currentData() or "new_instance"),
                }
            )
        return base

    def accept(self) -> None:
        tool_type = str(self.type_combo.currentData() or "")
        if not self.label_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "Shelf Tool", "Enter a label.")
            return
        if tool_type == "python_script" and not self.script_path_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "Shelf Tool", "Select a Python script.")
            return
        if tool_type == "python_code" and not self.code_edit.toPlainText().strip():
            QtWidgets.QMessageBox.warning(self, "Shelf Tool", "Enter Python code.")
            return
        if tool_type == "workflow_shortcut" and not self.workflow_path_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "Shelf Tool", "Select a workflow.")
            return
        super().accept()


def edit_shelf_tool(parent, tool: Dict[str, Any] | None = None, *, default_type: str = "python_script") -> Dict[str, Any] | None:
    dlg = ShelfToolDialog(parent, tool=tool, default_type=default_type)
    if _qexec(dlg) != QtWidgets.QDialog.Accepted:
        return None
    return dlg.result_tool()
