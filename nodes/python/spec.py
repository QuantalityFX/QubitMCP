# nodes/python/spec.py
from nodes.core import Spec

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
        if busy_item:
            try:
                busy_item.setBusyState(True, "running")
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                exec(src, ns, ns)
            out = out_buf.getvalue().strip()
            err = err_buf.getvalue().strip()
            if err:
                QtWidgets.QMessageBox.critical(card, "EchoGraph", err)
            elif out:
                QtWidgets.QMessageBox.information(card, "EchoGraph", out)
        except Exception:
            combined = out_buf.getvalue() + "\n" + err_buf.getvalue()
            tb = traceback.format_exc()
            QtWidgets.QMessageBox.critical(card, "EchoGraph", f"{combined}\n{tb}")
        finally:
            if busy_item:
                try:
                    busy_item.setBusyState(False, "")
                except Exception:
                    pass

    btn_edit = QtWidgets.QPushButton("Edit Code…")
    btn_edit.setToolTip("Edit and save this node's Python script")
    btn_edit.clicked.connect(_edit_code)

    btn_run = QtWidgets.QPushButton("Run Python")
    btn_run.setToolTip("Executes in a small sandbox; 'cmds' (Maya) and 'hou' (Houdini) are available if installed.")
    btn_run.clicked.connect(_run_code)

    footer_layout.addWidget(btn_edit)
    footer_layout.addWidget(btn_run)

    return True  # we fully own the footer for python
      

PYTHON_SPEC = Spec(
    stripe_color="#10b981",            # keep existing green
    augment_infocard_footer=augment_infocard_footer,
)
