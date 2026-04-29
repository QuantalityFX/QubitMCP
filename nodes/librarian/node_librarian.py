# nodes/node_librarian.py
"""
Librarian Node
--------------
A tiny node that launches the Librarian UI (running in its own venv/process).
Shows one big button and an optional status line.

Assumptions:
- Your Librarian lives in nodes/librarian/
- nodes/librarian/launch_librarian.py exists (the silent launcher we made)
- Your graph system scans this file and registers nodes via NODE_META below
"""

from __future__ import annotations
from pathlib import Path
import sys, traceback, os

# --- Qt imports (PySide6 first, fallback to PySide2) -------------------------
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore

HERE = Path(__file__).resolve().parent
LIBRARIAN_DIR = HERE / "librarian"   # nodes/librarian

# Make sure we can import the launcher without touching heavy deps
if str(LIBRARIAN_DIR) not in sys.path:
    sys.path.insert(0, str(LIBRARIAN_DIR))

# --- Widget ------------------------------------------------------------------
class LibrarianNodeWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LibrarianNodeWidget")
        self._build_ui()
        self._wire()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QtWidgets.QLabel("Librarian")
        f = title.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        title.setFont(f)

        self.btn_open = QtWidgets.QPushButton("Open Librarian")
        self.btn_open.setMinimumHeight(34)

        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#9aa4b2;")

        # --- Docs path row (override + remember) ------------------------------
        roww = QtWidgets.QHBoxLayout()
        self.le_docs = QtWidgets.QLineEdit()
        self.le_docs.setPlaceholderText("Docs folder (overrides settings.json)")
        btn_browse = QtWidgets.QPushButton("Browse")
        btn_use = QtWidgets.QPushButton("Use")
        roww.addWidget(self.le_docs, 1)
        roww.addWidget(btn_browse)
        roww.addWidget(btn_use)

        root.addWidget(title)
        root.addLayout(roww)
        root.addWidget(self.btn_open)
        root.addWidget(self.lbl_status)

        # Light styling that matches your dark theme reasonably well
        self.setStyleSheet(
            "QWidget{background:#1a1f24;color:#e6edf3;}"
            "QLineEdit{background:#12151a;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:4px 8px;}"
            "QPushButton{background:#2563eb;border:1px solid #3c4450;"
            "color:#e6edf3;border-radius:8px;padding:6px 12px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:pressed{background:#1e40af;}"
        )

        # load last persisted docs_dir (nodes/librarian/settings.json)
        try:
            import json
            st = LIBRARIAN_DIR / "settings.json"
            if st.exists():
                data = json.loads(st.read_text(encoding="utf-8"))
                v = (data.get("docs_dir") or "").strip()
                if v:
                    self.le_docs.setText(v)
        except Exception:
            pass

        self._btn_browse = btn_browse
        self._btn_use = btn_use

    def _wire(self):
        self.btn_open.clicked.connect(self._on_open)
        self._btn_browse.clicked.connect(self._on_browse)
        self._btn_use.clicked.connect(self._on_use)

    def _on_browse(self):
        start = self.le_docs.text().strip() or str(LIBRARIAN_DIR)
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose docs folder", start)
        if p:
            self.le_docs.setText(p)

    def _persist_docs_dir(self, path: str):
        # write nodes/librarian/settings.json so the standalone remembers it
        try:
            import json
            p = (LIBRARIAN_DIR / "settings.json")
            data = {}
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data["docs_dir"] = path
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_use(self):
        path = self.le_docs.text().strip()
        if not path:
            self._set_status("No path set.", error=True); return
        os.environ["LIBRARIAN_DOCS_DIR"] = path  # override for next launch in this process
        self._persist_docs_dir(path)             # persist for future standalone launches
        self._set_status(f"Docs set:\n{path}", error=False)

    def _on_open(self):
        # Import inside the handler so the node loads fast in the graph
        try:
            import launch_librarian as launcher  # nodes/librarian/launch_librarian.py
        except Exception:
            try:
                from librarian import launch_librarian as launcher  # if using package layout
            except Exception as e:
                self._set_status(f"Import error: {e}\n{traceback.format_exc()}", error=True)
                return

        # If the user set a path, ensure it’s passed down to the child
        path = self.le_docs.text().strip()
        if path:
            os.environ["LIBRARIAN_DOCS_DIR"] = path
            self._persist_docs_dir(path)

        try:
            # Silent spawn; raises if the venv/python is missing
            launcher.launch(verbose=False)
            self._set_status("Launched Librarian.", error=False)
        except Exception as e:
            self._set_status(f"Launch failed: {e}", error=True)

    def _set_status(self, msg: str, error: bool = False):
        if error:
            self.lbl_status.setStyleSheet("color:#fca5a5;")  # soft red
        else:
            self.lbl_status.setStyleSheet("color:#86efac;")  # soft green
        self.lbl_status.setText(msg or "")

# --- Optional: Node wrapper if your graph expects a Node class ----------------
# If your framework uses a NodeBase class, you can adapt this minimal wrapper.
# Otherwise your toolbox/registry can directly use LibrarianNodeWidget + NODE_META.

class LibrarianNode(QtWidgets.QFrame):
    """Minimal shim so many graph frameworks can treat it as a node widget."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.inner = LibrarianNodeWidget(self)
        lay.addWidget(self.inner)

# --- Registration metadata ----------------------------------------------------
# Many node graphs scan for a dict like this to populate the right-click menu.
NODE_META = {
    "type_name": "Librarian",
    "category": "AI / Tools",
    "version": "1.2.5",
    "create_widget": LibrarianNode,   # Or use LibrarianNodeWidget directly if your graph prefers
    # Optional icon: place a 24x24 png in nodes/librarian/icons/librarian.png
    "icon_path": str(LIBRARIAN_DIR / "icons" / "librarian.png"),
}
