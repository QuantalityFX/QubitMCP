# nodes/librarian/librarian_qt.py
# Qt UI wrapper for the Librarian core.
# Safe to be launched from external hosts (EchoGraph Python node, Maya, Houdini, etc.)

from pathlib import Path
import sys, os, traceback

# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap: make sure this folder and its local .venv site-packages are importable
# ─────────────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent

# 1) Add this folder so "import librarian_core" works even without a package import
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# 2) Add nodes/librarian/.venv site-packages so llama_index, etc. can be imported
if os.name == "nt":
    _site_dir = HERE / ".venv" / "Lib" / "site-packages"
else:
    _site_dir = HERE / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"

if _site_dir.is_dir() and str(_site_dir) not in sys.path:
    sys.path.insert(0, str(_site_dir))

# 3) Friendly error if llama_index still missing
try:
    import llama_index  # noqa: F401
except Exception as e:
    raise RuntimeError(
        "llama_index is not available in the current interpreter.\n"
        "Either run your host with nodes\\librarian\\.venv, or install requirements into that venv.\n"
        f"Tried site-packages here:\n  {_site_dir}\n\n"
        "If missing, run:\n"
        r'  V:\Source\Repos\EchoMatrixMCP\nodes\librarian\.venv\Scripts\python.exe -m pip install -r V:\Source\Repos\EchoMatrixMCP\nodes\librarian\requirements.txt'
    ) from e

# ─────────────────────────────────────────────────────────────────────────────
# Qt imports
# ─────────────────────────────────────────────────────────────────────────────
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    QT_IS_6 = True
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets
    QT_IS_6 = False

# ─────────────────────────────────────────────────────────────────────────────
# Import core (must live next to this file as nodes/librarian/librarian_core.py)
# ─────────────────────────────────────────────────────────────────────────────
from librarian_core import Librarian  # now resolves because HERE is on sys.path


# ─────────────────────────────────────────────────────────────────────────────
# Simple thread worker (QObject + QRunnable so signals work)
# ─────────────────────────────────────────────────────────────────────────────
class _Worker(QtCore.QObject, QtCore.QRunnable):
    finished = QtCore.Signal(object, object)  # (result, error)

    def __init__(self, fn, *args, **kwargs):
        QtCore.QObject.__init__(self)
        QtCore.QRunnable.__init__(self)
        self.setAutoDelete(True)
        self.fn = fn
        self.args = args
        self.kw = kwargs

    @QtCore.Slot()
    def run(self):
        try:
            res = self.fn(*self.args, **self.kw)
            self.finished.emit(res, None)
        except Exception as e:
            import traceback
            self.finished.emit(None, (e, traceback.format_exc()))


# ─────────────────────────────────────────────────────────────────────────────
# Main UI Widget
# ─────────────────────────────────────────────────────────────────────────────
class LibrarianWidget(QtWidgets.QWidget):
    """
    Control panel for the Librarian core:
      - Build/Load vector index
      - Summarize (with mode, top_k, recache)
      - Quick search (no LLM)
      - Focused analysis from last summary
    """
    def __init__(self, parent=None, base: Path | None = None):
        super().__init__(parent)
        self.setObjectName("LibrarianWidget")
        self.setWindowTitle("Librarian")
        self.resize(820, 620)

        # Core
        self.lib = Librarian(base=base)
        self._last_summary = ""
        self.pool = QtCore.QThreadPool.globalInstance()

        # UI
        self._build_ui()
        self._wire()

        # Style
        self.setStyleSheet(
            "QWidget{background:#1a1f24;color:#e6edf3;}"
            "QLineEdit,QPlainTextEdit,QTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            "QPushButton{background:#20242b;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;padding:4px 10px;}"
            "QPushButton:hover{background:#2a2f38;} QPushButton:pressed{background:#1b1f26;}"
            "QCheckBox{spacing:8px;}"
            "QGroupBox{border:1px solid #3c4450;border-radius:8px;margin-top:10px;padding:8px 8px 8px 8px;}"
            "QGroupBox::title{subcontrol-origin: margin; left:8px; padding:0 4px;}"
            "QLabel{color:#cbd5e1;}"
        )

        # Paths banner
        self._append_log(f"BASE:    {self.lib.cfg.base}")
        self._append_log(f"DOCS:    {self.lib.cfg.docs_dir}")
        self._append_log(f"VAULT:   {self.lib.cfg.obsidian_dir or '(none)'}")
        self._append_log(f"STORAGE: {self.lib.cfg.storage_dir}")

    # UI layout
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Top row
        top = QtWidgets.QHBoxLayout()
        self.btn_build = QtWidgets.QPushButton("Build / Load Index")
        self.chk_rebuild = QtWidgets.QCheckBox("Force rebuild")
        self.chk_rebuild.setToolTip("Delete existing storage and rebuild FAISS index")
        top.addWidget(self.btn_build)
        top.addWidget(self.chk_rebuild)
        top.addStretch(1)

        # Summarize group
        grp_sum = QtWidgets.QGroupBox("Summarize Corpus")
        gl = QtWidgets.QGridLayout(grp_sum)
        gl.setContentsMargins(8, 8, 8, 8)
        gl.setHorizontalSpacing(8)
        gl.setVerticalSpacing(6)

        self.txt_prompt = QtWidgets.QPlainTextEdit()
        self.txt_prompt.setPlaceholderText(
            "Optional custom prompt. Leave empty to use the default summary prompt."
        )
        self.chk_recache = QtWidgets.QCheckBox("Re-cache")
        self.chk_recache.setToolTip("Ignore cached answer and re-run LLM")

        self.cmb_mode = QtWidgets.QComboBox()
        self.cmb_mode.addItems(["tree_summarize", "compact"])
        self.cmb_mode.setToolTip("LlamaIndex response_mode")

        self.spn_topk = QtWidgets.QSpinBox()
        self.spn_topk.setRange(1, 20)
        self.spn_topk.setValue(4)
        self.spn_topk.setPrefix("top_k=")

        self.btn_summarize = QtWidgets.QPushButton("Summarize")

        gl.addWidget(QtWidgets.QLabel("Prompt:"), 0, 0)
        gl.addWidget(self.txt_prompt,               0, 1, 1, 3)
        gl.addWidget(QtWidgets.QLabel("Mode:"),     1, 0)
        gl.addWidget(self.cmb_mode,                 1, 1)
        gl.addWidget(self.spn_topk,                 1, 2)
        gl.addWidget(self.chk_recache,              1, 3)
        gl.addWidget(self.btn_summarize,            2, 3, 1, 1)

        # Search group
        grp_search = QtWidgets.QGroupBox("Quick Search (no LLM)")
        sl = QtWidgets.QGridLayout(grp_search)
        self.ed_search = QtWidgets.QLineEdit()
        self.ed_search.setPlaceholderText("Search text…")
        self.spn_k = QtWidgets.QSpinBox()
        self.spn_k.setRange(1, 50)
        self.spn_k.setValue(5)
        self.spn_k.setPrefix("top_k=")
        self.btn_search = QtWidgets.QPushButton("Search")
        sl.addWidget(QtWidgets.QLabel("Query:"), 0, 0)
        sl.addWidget(self.ed_search,             0, 1, 1, 2)
        sl.addWidget(self.spn_k,                 0, 3)
        sl.addWidget(self.btn_search,            0, 4)

        # Analysis group
        grp_ana = QtWidgets.QGroupBox("Focused Analysis")
        al = QtWidgets.QGridLayout(grp_ana)
        self.txt_business_prompt = QtWidgets.QPlainTextEdit()
        self.txt_business_prompt.setPlaceholderText(
            "High-level analysis request.\n"
            "e.g. Find me the Business Plan for a Windows macro app comparable to Stream Deck…"
        )
        self.btn_analyze = QtWidgets.QPushButton("Run Analysis (from last summary)")
        al.addWidget(self.txt_business_prompt, 0, 0, 1, 3)
        al.addWidget(self.btn_analyze,         0, 3, 1, 1)

        # Output / log splitter
        splitter = QtWidgets.QSplitter()
        try:
            splitter.setOrientation(QtCore.Qt.Vertical)
        except Exception:
            splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        self.out_text = QtWidgets.QTextEdit()
        self.out_text.setReadOnly(True)
        self.out_text.setPlaceholderText("Results will appear here…")

        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("Log…")

        splitter.addWidget(self.out_text)
        splitter.addWidget(self.log_text)
        splitter.setSizes([380, 160])

        # Assemble
        root.addLayout(top)
        root.addWidget(grp_sum)
        root.addWidget(grp_search)
        root.addWidget(grp_ana)
        root.addWidget(splitter, 1)

    def _wire(self):
        self.btn_build.clicked.connect(self._do_build_or_load)
        self.btn_summarize.clicked.connect(self._do_summarize)
        self.btn_search.clicked.connect(self._do_search)
        self.btn_analyze.clicked.connect(self._do_analyze)

    # helpers
    def _append_log(self, msg: str):
        self.log_text.append(msg)

    def _set_out(self, text: str):
        self.out_text.setPlainText(text or "")

    def _run_async(self, fn, done_cb, *args, **kwargs):
        w = _Worker(fn, *args, **kwargs)
        w.finished.connect(done_cb)
        self.pool.start(w)

    # actions
    def _do_build_or_load(self):
        force = self.chk_rebuild.isChecked()
        self._append_log(f"[build] force_rebuild={force}")

        def work():
            self.lib.load_documents(verbose=True)
            idx = self.lib.ensure_index(force_rebuild=force, verbose=True)
            try:
                count = len(idx._docstore.docs)
            except Exception:
                count = None
            return f"Index ready. docs={count}"

        def done(res, err):
            if err:
                e, tb = err
                self._append_log(f"[build] ERROR: {e}\n{tb}")
            else:
                self._append_log(f"[build] {res}")

        self._run_async(work, done)

    def _do_summarize(self):
        prompt = (self.txt_prompt.toPlainText() or "").strip() or None
        recache = self.chk_recache.isChecked()
        mode = self.cmb_mode.currentText().strip()
        topk = self.spn_topk.value()
        self._append_log(f"[summarize] mode={mode} top_k={topk} recache={recache}")

        def work():
            self.lib.ensure_index(force_rebuild=False, verbose=False)
            return self.lib.summarize_all(prompt=prompt, recache=recache, mode=mode, top_k=topk)

        def done(res, err):
            if err:
                e, tb = err
                self._append_log(f"[summarize] ERROR: {e}\n{tb}")
            else:
                self._last_summary = res or ""
                self._set_out(self._last_summary)
                self._append_log("[summarize] done.")

        self._run_async(work, done)

    def _do_search(self):
        q = (self.ed_search.text() or "").strip()
        if not q:
            self._append_log("[search] empty query.")
            return
        k = self.spn_k.value()
        self._append_log(f"[search] '{q}' top_k={k}")

        def work():
            self.lib.ensure_index(force_rebuild=False, verbose=False)
            return self.lib.quick_search(q, top_k=k)

        def done(res, err):
            if err:
                e, tb = err
                self._append_log(f"[search] ERROR: {e}\n{tb}")
            else:
                lines = [f"--- Top {len(res)} matches for: {q!r} ---", ""]
                for i, (path, snip) in enumerate(res, 1):
                    lines.append(f"{i}. ({path})\n   {snip}...\n")
                self._set_out("\n".join(lines))
                self._append_log("[search] done.")

        self._run_async(work, done)

    def _do_analyze(self):
        if not self._last_summary:
            self._append_log("[analysis] No summary available. Run Summarize first.")
            return
        p = (self.txt_business_prompt.toPlainText() or "").strip() or None
        self._append_log("[analysis] running with last summary…")

        def work():
            return self.lib.analysis_from_summary(self._last_summary, business_prompt=p)

        def done(res, err):
            if err:
                e, tb = err
                self._append_log(f"[analysis] ERROR: {e}\n{tb}")
            else:
                self._set_out(res or "")
                self._append_log("[analysis] done.")

        self._run_async(work, done)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone launcher (for quick testing from console)
# ─────────────────────────────────────────────────────────────────────────────
def launch_standalone(base: Path | None = None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = LibrarianWidget(base=base)
    try:
        w.setWindowFlag(QtCore.Qt.Window, True)
    except Exception:
        w.setWindowFlags(QtCore.Qt.Window)
    w.resize(900, 700)
    w.show()
    w.raise_()
    w.activateWindow()
    app.exec_()


if __name__ == "__main__":
    # Optional: run directly for testing this panel
    launch_standalone(base=HERE)
