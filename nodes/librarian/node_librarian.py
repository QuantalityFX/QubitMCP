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
import sys, traceback

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

        root.addWidget(title)
        root.addWidget(self.btn_open)
        root.addWidget(self.lbl_status)

        # Light styling that matches your dark theme reasonably well
        self.setStyleSheet(
            "QWidget{background:#1a1f24;color:#e6edf3;}"
            "QPushButton{background:#2563eb;border:1px solid #3c4450;"
            "color:#e6edf3;border-radius:8px;padding:6px 12px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:pressed{background:#1e40af;}"
        )

    def _wire(self):
        self.btn_open.clicked.connect(self._on_open)

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
    "version": "1.0.0",
    "create_widget": LibrarianNode,   # Or use LibrarianNodeWidget directly if your graph prefers
    # Optional icon: place a 24x24 png in nodes/librarian/icons/librarian.png
    "icon_path": str(LIBRARIAN_DIR / "icons" / "librarian.png"),
}
