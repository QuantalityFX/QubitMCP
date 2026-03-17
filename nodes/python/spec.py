# nodes/python/spec.py
from nodes.core import Spec
import threading

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Replicates the 'Edit Code…' and 'Run Python' buttons for python nodes.
    Returns True to take ownership of the footer UI.
    """
    # Qt imports (PySide6 first, then PySide2)
    try:
        from PySide6 import QtWidgets, QtGui, QtCore
    except Exception:
        from PySide2 import QtWidgets, QtGui, QtCore
    try:
        from .highlighter import PythonSyntaxHighlighter
    except Exception:
        PythonSyntaxHighlighter = None

    node = getattr(card, "_node_ref", None)
    if not node or (node.kind or "").lower() != "python":
        return False

    # --- Simple inline code editor dialog (keeps plugin self-contained)
    class _CodeEditor(QtWidgets.QDialog):
        def __init__(self, parent=None, initial=""):
            super().__init__(parent)
            self.setWindowTitle("Python Code")
            self.resize(600, 360)
            v = QtWidgets.QVBoxLayout(self)
            self.edit = QtWidgets.QPlainTextEdit()
            self.edit.setPlainText(initial or "")
            # nicer tab width
            fm = self.edit.fontMetrics()
            try:
                space_w = fm.horizontalAdvance(" ")
            except AttributeError:
                space_w = fm.width(" ")
            self.edit.setTabStopDistance(4 * space_w)
            self.edit.setStyleSheet(
                "QPlainTextEdit{background:#0f1216;color:#e6edf3;border:1px solid #334;}"
            )
            self._syntax_highlighter = None
            if PythonSyntaxHighlighter is not None:
                try:
                    self._syntax_highlighter = PythonSyntaxHighlighter(self.edit.document())
                except Exception:
                    self._syntax_highlighter = None
            v.addWidget(self.edit, 1)
            bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            bb.accepted.connect(self.accept)
            bb.rejected.connect(self.reject)
            v.addWidget(bb)
        def code(self) -> str:
            return self.edit.toPlainText()

    def _edit_code():
        dlg = _CodeEditor(card, initial=node.code or "")
        if dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec():
            node.code = dlg.code()

    def _run_code():
        src = (node.code or "").strip()
        if not src:
            QtWidgets.QMessageBox.information(card, "EchoGraph", "No code to run.")
            return

        # Build a host-agnostic namespace: try Maya/Houdini, but don't require them.
        ns = {
            "__name__": "__echograph_exec__",
            "QtWidgets": QtWidgets,
            "QtCore": QtCore,
            "QtGui": QtGui,
        }
        try:
            from maya import cmds as _cmds  # type: ignore
            ns["cmds"] = _cmds
        except Exception:
            ns["cmds"] = None
        try:
            import hou as _hou  # type: ignore
            ns["hou"] = _hou
        except Exception:
            ns["hou"] = None

        params_map = {
            (p.get("name") or f"param{i+1}"): p.get("value", "")
            for i, p in enumerate(node.params or [])
        }
        raw_params = list(node.params or [])

        sc = getattr(card, "_graph_scene", None)
        inputs = {}
        primary_input = ""
        python_item = None
        if sc and hasattr(sc, "_node_items"):
            python_item = sc._node_items.get(node.name)
            if python_item is not None:
                try:
                    in_edges = sc._ordered_in_edges(python_item)
                except Exception:
                    in_edges = sc._in_edges(python_item)
                for idx, edge in enumerate(in_edges or []):
                    try:
                        txt = sc.resolve_text_value(edge.src)
                    except Exception:
                        txt = ""
                    if not txt:
                        continue
                    if not primary_input:
                        primary_input = txt
                    key = edge.src.model.name
                    inputs.setdefault(key, txt)
                    inputs.setdefault(f"in{idx+1}", txt)

        ns.update(
            {
                "node": node,
                "params": params_map,
                "raw_params": raw_params,
                "inputs": inputs,
                "primary_input": primary_input,
                "graph_scene": sc,
            }
        )

        import io, contextlib, traceback
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        busy_item = python_item if python_item and hasattr(python_item, "setBusyState") else None

        def _set_busy(active: bool, label: str = "") -> None:
            if not busy_item:
                return

            def _apply():
                try:
                    busy_item.setBusyState(active, label)
                except Exception:
                    pass

            QtCore.QTimer.singleShot(0, card, _apply)

        def _notify(message: str, *, error: bool = False) -> None:
            def _show():
                msg = (message or "").strip() or "(no output)"

                # Prefer the InfoCard console if present
                console = getattr(card, "_py_console", None)
                if console is not None:
                    try:
                        console.setVisible(True)
                        console.setPlainText(msg)
                        return
                    except Exception:
                        pass

                # Fallback to messagebox
                fn = QtWidgets.QMessageBox.critical if error else QtWidgets.QMessageBox.information
                fn(card, "EchoGraph", msg)

            # IMPORTANT: schedule on the card's (GUI) thread
            QtCore.QTimer.singleShot(0, card, _show)

        def _cleanup_timer(t):
            if t is None:
                return
            try:
                t.stop()
            except Exception:
                pass
            try:
                t.deleteLater()
            except Exception:
                pass

        def _emit_output_changed():
            if sc is None or not hasattr(sc, "paramChanged"):
                return
            try:
                sc.paramChanged.emit(node.name, list(getattr(node, "params", None) or []))
            except Exception:
                pass


        def _clear_busy_state():
            if python_item:
                timer_obj = getattr(python_item, "_python_busy_timer", None)
                _cleanup_timer(timer_obj)
                setattr(python_item, "_python_busy_timer", None)
                setattr(python_item, "_python_worker_thread", None)
            _set_busy(False, "")

        if python_item:
            _clear_busy_state()

        def _worker():
            try:
                with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                    exec(src, ns, ns)
                out = out_buf.getvalue().strip()
                err = err_buf.getvalue().strip()
                if not err:
                    output_value = ns.get("output_text", None)
                    if output_value is None:
                        output_value = ns.get("result", None)
                    if output_value is None and out:
                        output_value = out
                    if output_value is not None:
                        try:
                            node.info = str(output_value)
                        except Exception:
                            pass
                QtCore.QTimer.singleShot(0, card, _emit_output_changed)
                if err:
                    _notify(err, error=True)
                elif out:
                    _notify(out)
            except Exception:
                combined = out_buf.getvalue() + "\n" + err_buf.getvalue()
                tb = traceback.format_exc()
                _notify(f"{combined}\n{tb}", error=True)
            finally:
                QtCore.QTimer.singleShot(0, card, _clear_busy_state)

        _set_busy(True, "running")
        worker_thread = threading.Thread(target=_worker, daemon=True)
        worker_thread.start()
        if python_item:
            setattr(python_item, "_python_worker_thread", worker_thread)

            def _check_worker():
                th = getattr(python_item, "_python_worker_thread", None)
                if not th or not th.is_alive():
                    QtCore.QTimer.singleShot(0, card, _clear_busy_state)

            timer = QtCore.QTimer(card)
            timer.setInterval(250)
            timer.timeout.connect(_check_worker)
            setattr(python_item, "_python_busy_timer", timer)
            timer.start()

    btn_edit = QtWidgets.QPushButton("Edit Code…")
    btn_edit.setToolTip("Edit and save this node's Python script")
    btn_edit.setStyleSheet(
        "QPushButton{background:#1e3a8a;color:#e5e7eb;border-radius:2px;padding:4px 10px;}"
        "QPushButton:hover{background:#1e40af;}"
        "QPushButton:pressed{background:#1d4ed8;}"
        "QPushButton:disabled{background:#334155;color:#94a3b8;}"
    )
    btn_edit.clicked.connect(_edit_code)

    btn_run = QtWidgets.QPushButton("Run Python")
    btn_run.setStyleSheet(
        "QPushButton{background:#166534;color:#e5e7eb;border-radius:2px;padding:4px 10px;}"
        "QPushButton:hover{background:#15803d;}"
        "QPushButton:pressed{background:#16a34a;}"
        "QPushButton:disabled{background:#334155;color:#94a3b8;}"
    )
    btn_run.clicked.connect(_run_code)
    footer_layout.addWidget(btn_edit)
    footer_layout.addWidget(btn_run)

    return True  # we fully own the footer for python


PYTHON_SPEC = Spec(
    stripe_color="#10b981",            # keep existing green
    augment_infocard_footer=augment_infocard_footer,
    
)
