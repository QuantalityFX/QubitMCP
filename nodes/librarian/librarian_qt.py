# nodes/librarian/librarian_qt.py
# Qt UI wrapper for the Librarian core.
# Safe to be launched from external hosts (EchoGraph Python node, Maya, Houdini, etc.)

from pathlib import Path
import sys, os, traceback, json, time
# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap: make sure this folder and its local .venv site-packages are importable
# ─────────────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
ICON_PATH = HERE / "icons" / "librarian_search_s.png"

def _load_window_icon() -> 'QtGui.QIcon':
    # Build a multi-size icon from your PNG so Windows picks crisp sizes
    ic = QtGui.QIcon()
    p = str(ICON_PATH)  # librarian_search_s.png (your exact file)
    for sz in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addFile(p, QtCore.QSize(sz, sz))
    return ic

def _set_windows_app_id(app_id: str = "QuantalityFX.Librarian") -> None:
    # Makes the taskbar show this app’s custom icon and group correctly
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

def _enable_windows_dark_titlebar(widget: 'QtWidgets.QWidget') -> None:
    # Dark native title bar on Windows 10/11
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = int(widget.winId())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20  # 19 on older builds; 20 works on most
        set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        value = ctypes.c_int(1)
        set_window_attribute(wintypes.HWND(hwnd),
                             ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE),
                             ctypes.byref(value),
                             ctypes.sizeof(value))
    except Exception:
        pass


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
# ─────────────────────────────────────────────────────────────────────────────
# Simple thread worker (QObject + QRunnable so we can emit signals)
# ─────────────────────────────────────────────────────────────────────────────
class _Worker(QtCore.QObject, QtCore.QRunnable):
    finished = QtCore.Signal(object, object)  # (result, error)  error = (Exception, traceback_str) or None

    def __init__(self, fn, *args, **kwargs):
        QtCore.QObject.__init__(self)
        QtCore.QRunnable.__init__(self)
        self.fn = fn
        self.args = args
        self.kw = kwargs
        self.setAutoDelete(True)

    @QtCore.Slot()
    def run(self):
        import traceback
        try:
            res = self.fn(*self.args, **self.kw)
            self.finished.emit(res, None)
        except Exception as e:
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

        # ✅ ensure self.base exists before any IPC code touches it
        self.base = Path(base) if base else HERE

        # Core
        self.lib = Librarian(base=base)
        self._last_summary = ""
        self.pool = QtCore.QThreadPool.globalInstance()

        # UI
        self._build_ui()
        self._wire()

        # --- IPC inbox watcher (poll every ~0.8s) ---
        self._ipc_inbox = (self.base / "ipc" / "inbox")
        self._ipc_inbox.mkdir(parents=True, exist_ok=True)
        self._ipc_timer = QtCore.QTimer(self)
        self._ipc_timer.setInterval(800)
        self._ipc_timer.timeout.connect(self._poll_inbox)
        self._ipc_timer.start()
        self._append_log("[ipc] watching inbox …")

        # Style
        self.setStyleSheet(
            "QWidget{background:#1a1f24;color:#e6edf3;}"
            "QLineEdit,QPlainTextEdit,QTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            "QPushButton{background:#20242b;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;padding:4px 10px;}"
            "QPushButton:hover{background:#2a2f38;}"
            "QPushButton:pressed{background:#1b1f26;}"
            "QCheckBox{spacing:8px;}"
            "QGroupBox{border:1px solid #3c4450;border-radius:8px;margin-top:10px;padding:8px 8px 8px 8px;}"
            "QGroupBox::title{subcontrol-origin: margin; left:8px; padding:0 4px;}"
            "QLabel{color:#cbd5e1;}"
            "#btnAnalyze{background:#2563eb;color:#e6edf3;border:1px solid #1e3a8a;border-radius:8px;padding:6px 12px;font-weight:600;}"
            "#btnAnalyze:hover{background:#1d4ed8;}"
            "#btnAnalyze:pressed{background:#1e40af;}"
            "#btnAnalyzeSrc{background:#16a34a;color:#e6edf3;border:1px solid #14532d;border-radius:8px;padding:6px 12px;font-weight:600;}"
            "#btnAnalyzeSrc:hover{background:#15803d;}"
            "#btnAnalyzeSrc:pressed{background:#166534;}"
        )

        # Paths banner
        self._append_log(f"BASE:    {self.lib.cfg.base}")
        self._append_log(f"DOCS:    {self.lib.cfg.docs_dir}")
        self._append_log(f"VAULT:   {self.lib.cfg.obsidian_dir or '(none)'}")
        self._append_log(f"STORAGE: {self.lib.cfg.storage_dir}")

    def _poll_inbox(self):
        """Check nodes/librarian/ipc/inbox for one JSON cmd and execute it without blocking the UI."""
        if not hasattr(self, "_ipc_busy"):
            self._ipc_busy = False
        if self._ipc_busy:
            return

        inbox = getattr(self, "_ipc_inbox", None)
        if not inbox or not inbox.exists():
            return

        files = sorted(inbox.glob("cmd_*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            return

        fn = files[0]
        try:
            data = json.loads(fn.read_text(encoding="utf-8"))
        except Exception as e:
            self._append_log(f"[ipc] bad json: {fn.name} :: {e}")
            try:
                fn.unlink()
            except Exception:
                pass
            return

        cmd_type = (data.get("type") or data.get("action") or "").strip().lower()
        if not cmd_type:
            cmd_type = "search" if "query" in data else "summarize"

        self._ipc_busy = True
        self._append_log(f"[ipc] processing {fn.name} :: {cmd_type}")

        def _cleanup():
            try:
                fn.unlink()
            except Exception:
                pass
            self._ipc_busy = False

        # --- Helper to also save result JSON to outbox for EchoGraph ---
        def _save_outbox_result(ts: int, payload: dict | str):
            try:
                outbox = inbox.parent / "outbox"
                outbox.mkdir(parents=True, exist_ok=True)
                result_path = outbox / f"result_{ts}.json"
                text = json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, dict) else str(payload)
                result_path.write_text(text, encoding="utf-8")
                self._append_log(f"[ipc] wrote {result_path.name}")
            except Exception as e:
                self._append_log(f"[ipc] failed to write result: {e}")

        # === SEARCH ===
        if cmd_type == "search":
            q = (data.get("query") or "").strip()
            top_k = int(data.get("top_k", 5))
            if not q:
                self._append_log("[ipc] search missing 'query' string.")
                _cleanup()
                return

            def work():
                self.lib.ensure_index(force_rebuild=False, verbose=False)
                return self.lib.quick_search(q, top_k=top_k)

            def done(res, err):
                try:
                    ts = data.get("ts") or int(time.time() * 1000)
                    if err:
                        e, tb = err
                        self._append_log(f"[ipc] search ERROR: {e}\n{tb}")
                    else:
                        hits = res or []
                        lines = [f"--- Search: {q} (top_k={top_k}) ---", ""]
                        for i, (path, snip) in enumerate(hits, 1):
                            lines.append(f"[{i}] {path}\n{snip[:200]}…\n")
                        out_txt = "\n".join(lines) if hits else f"No results for: {q}"
                        self._set_out(out_txt)
                        _save_outbox_result(ts, {"query": q, "results": hits})
                        self._append_log("[ipc] search done.")
                finally:
                    _cleanup()

            self._run_async(work, done)
            return

        # === SUMMARIZE ===
        elif cmd_type == "summarize":
            mode = (data.get("mode") or "tree_summarize").strip()

            def work():
                self.lib.ensure_index(force_rebuild=False, verbose=False)
                return self.lib.summarize_all(prompt=None, recache=False, mode=mode, top_k=4)

            def done(res, err):
                try:
                    ts = data.get("ts") or int(time.time() * 1000)
                    if err:
                        e, tb = err
                        self._append_log(f"[ipc] summarize ERROR: {e}\n{tb}")
                    else:
                        txt = (res or "").strip()
                        self._last_summary = txt
                        self._set_out(txt or "[empty summary]")
                        _save_outbox_result(ts, {"summary": txt})
                        self._append_log("[ipc] summarize done.")
                finally:
                    _cleanup()

            self._run_async(work, done)
            return

        # === ANALYZE ===
        elif cmd_type in ("analyze", "analyze_with_sources"):
            question = (data.get("question") or data.get("query") or "").strip()
            top_k = int(data.get("top_k", 8))
            max_chars = int(data.get("max_context_chars", 4000))
            if not question:
                self._append_log("[ipc] analyze missing 'question' or 'query'.")
                _cleanup()
                return

            use_sources = cmd_type == "analyze_with_sources" or hasattr(self.lib, "analyze_with_sources")

            def work():
                if use_sources and hasattr(self.lib, "analyze_with_sources"):
                    return ("sources",) + self.lib.analyze_with_sources(question, top_k=top_k, max_context_chars=max_chars)
                else:
                    txt = self.lib.analysis_from_summary(self._last_summary or "", business_prompt=question)
                    return ("plain", txt)

            def done(res, err):
                try:
                    ts = data.get("ts") or int(time.time() * 1000)
                    if err:
                        e, tb = err
                        self._append_log(f"[ipc] analyze ERROR: {e}\n{tb}")
                    else:
                        if not res:
                            txt = "[no analysis result]"
                        elif res[0] == "sources":
                            _, answer, sources = res
                            lines = [answer.strip(), "", "— Sources —"]
                            for s in sources or []:
                                score = s.get("score")
                                sc_s = f" (score={score:.4f})" if isinstance(score, (int, float)) else ""
                                lines.append(f"[{s.get('idx')}] {s.get('path')}{sc_s}\n{(s.get('snippet') or '')[:200]}…")
                            txt = "\n".join(lines)
                            _save_outbox_result(ts, {"answer": answer, "sources": sources})
                        else:
                            _, answer = res
                            txt = (answer or "").strip()
                            _save_outbox_result(ts, {"answer": txt})
                        self._set_out(txt)
                        self._append_log("[ipc] analyze done.")
                finally:
                    _cleanup()

            self._run_async(work, done)
            return

        else:
            self._append_log(f"[ipc] unknown cmd type: {cmd_type}")
            try:
                fn.unlink()
            except Exception:
                pass
            self._ipc_busy = False

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

        self.btn_analyze     = QtWidgets.QPushButton("Run Analysis")
        self.btn_analyze_src = QtWidgets.QPushButton("Analyze (with Sources)")

        # IDs for styling
        self.btn_analyze.setObjectName("btnAnalyze")
        self.btn_analyze_src.setObjectName("btnAnalyzeSrc")

        for b in (self.btn_analyze, self.btn_analyze_src):
            b.setMinimumHeight(36)

        # Prompt full width; buttons on one row, right-aligned
        al.addWidget(self.txt_business_prompt, 0, 0, 1, 4)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_analyze)
        btn_row.addWidget(self.btn_analyze_src)
        al.addLayout(btn_row, 1, 0, 1, 4)

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
        self.btn_analyze_src.clicked.connect(self._do_analyze_with_sources)

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

    def _do_analyze_with_sources(self):
        q = (self.txt_business_prompt.toPlainText() or "").strip()
        if not q:
            self._append_log("[analysis-src] Enter a question/prompt in the box above.")
            return

        self._append_log("[analysis-src] retrieving sources and analyzing…")

        def work():
            # Calls your helper in librarian_core.py
            return self.lib.analyze_with_sources(q, top_k=8, max_context_chars=4000)

        def done(res, err):
            if err:
                e, tb = err
                self._append_log(f"[analysis-src] ERROR: {e}\n{tb}")
                return

            # Support both (answer, sources) or plain string
            if isinstance(res, tuple) and len(res) == 2:
                answer, sources = res
            else:
                answer, sources = (str(res) if res is not None else ""), []

            lines = []
            lines.append((answer or "").strip())
            lines.append("\n— Sources —")
            if sources:
                for s in sources:
                    idx   = s.get("idx", "?")
                    path  = s.get("path", "<unknown>")
                    score = s.get("score", None)
                    snip  = (s.get("snippet", "") or "").replace("\n", " ")[:200]
                    if score is not None:
                        lines.append(f"[{idx}] {path} (score={score:.4f})")
                    else:
                        lines.append(f"[{idx}] {path}")
                    lines.append(f"   {snip}…")
            else:
                lines.append("(none)")

            self._set_out("\n".join(lines))
            self._append_log("[analysis-src] done.")

        self._run_async(work, done)

# ─────────────────────────────────────────────────────────────────────────────
# Standalone launcher (for quick testing from console)
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
def launch_standalone(base: Path | None = None, run_event_loop: bool | None = None):
    """
    Launch the Librarian UI window.

    - If a QApplication already exists, we reuse it and DO NOT start a new event loop by default.
    - If no QApplication exists (true standalone), we create one and start the loop unless
      run_event_loop=False is explicitly passed.

    Returns:
        QtWidgets.QWidget : the LibrarianWidget instance.
    """
    import sys, traceback, datetime
    HERE = Path(__file__).resolve().parent
    LOG  = HERE / "librarian_crash.log"

    def _crash(e: Exception):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tb = traceback.format_exc()
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"\n[{ts}] launch_standalone crash:\n{tb}\n")
        except Exception:
            pass
        try:
            QtWidgets.QMessageBox.critical(None, "Librarian Error", f"{e}\n\nSee log:\n{LOG}")
        except Exception:
            pass

    try:
        # Decide whether we are standalone (no app yet) or embedded (app exists)
        app = QtWidgets.QApplication.instance()
        app_was_none = app is None

        # Windows taskbar grouping/icon must be set BEFORE creating QApplication
        if app_was_none:
            _set_windows_app_id("QuantalityFX.Librarian")

        # Create or reuse the app
        app = app or QtWidgets.QApplication(sys.argv)

        # App + window icons (from your librarian_search_s.png)
        icon = _load_window_icon()
        app.setWindowIcon(icon)

        # Create the window
        win = LibrarianWidget(base=base)
        win.setWindowIcon(icon)

        # Keep your extra styles (appended so we don’t replace widget styles)
        try:
            win.setStyleSheet(
                (win.styleSheet() or "") + """
                QToolBar {
                    background: #0b0b0b;
                    border: none;
                    border-bottom: 1px solid #111;
                    spacing: 6px;
                }
                QToolBar QToolButton {
                    color: #e6edf3;
                    background: #0b0b0b;
                    border: 1px solid #2a2f38;
                    border-radius: 6px;
                    padding: 4px 8px;
                }
                QToolBar QToolButton:hover  { background: #121212; }
                QToolBar QToolButton:pressed{ background: #0a0a0a; }

                #TopBar {
                    background: #0b0b0b;
                    border-bottom: 1px solid #111;
                }
                """
            )
        except Exception:
            pass

        # Real top-level window (taskbar-visible)
        try:
            win.setWindowFlag(QtCore.Qt.Window, True)
        except Exception:
            win.setWindowFlags(QtCore.Qt.Window)
        win.resize(900, 700)

        # Show it (this is non-blocking)
        win.show()
        win.raise_()
        win.activateWindow()

        # Native dark title bar on Windows (keeps the real system draggable bar)
        _enable_windows_dark_titlebar(win)

        # Small focus nudges
        QtCore.QTimer.singleShot(0,  lambda: win.windowHandle() and win.windowHandle().requestActivate())
        QtCore.QTimer.singleShot(250, lambda: win.activateWindow())

        # Decide whether to start the event loop:
        # - Standalone (no app before): default is True
        # - Embedded (app existed): default is False (don’t block the host)
        if run_event_loop is None:
            run_event_loop = app_was_none

        if run_event_loop:
            try:
                app.exec()
            except AttributeError:
                app.exec_()

        return win

    except Exception as e:
        _crash(e)
        return None


def _clear_top_hint(w):
    # Turn off the always-on-top flag and re-show so the change takes effect
    w.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, False)
    w.show()
    w.raise_()
    w.activateWindow()


if __name__ == "__main__":
    launch_standalone(base=HERE)
