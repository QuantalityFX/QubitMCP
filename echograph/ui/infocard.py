# echograph/ui/infocard.py
from __future__ import annotations
import re, time, json
from typing import Optional, List, Tuple

from echograph.qt_compat import QtCore, QtGui, QtWidgets, _qexec
from echograph.constants import APP_TITLE
import nodes.core as core

# Librarian IPC helpers (already centralized)
from echograph.services.librarian_ipc import (
    outbox_dir,
    enqueue,
    load_output_text_by_ts,
    ensure_running,
)

# Reuse dialog classes we extracted
from echograph.ui.dialogs import (
    CodeEditorDialog,
    ParamEditorDialog,
    BigTextEditDialog,
)

def _hash_to_links(text: str) -> str:
    """Turn '#Name With Spaces' into an internal jump link."""
    return re.sub(
        r"#([^\n#]+)",
        lambda m: f'<a href="jump:/{QtCore.QUrl.toPercentEncoding(m.group(1).strip()).data().decode()}">#{m.group(1).strip()}</a>',
        text or "",
    )

class InfoCard(QtWidgets.QFrame):
    requestJump = QtCore.Signal(str)
    closedForNode = QtCore.Signal(str)

    def __init__(self, node, order_index: int=None, order_total: int=None):
        super().__init__()
        self._node_name = node.name
        self._node_ref = node

        try:
            self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        except AttributeError:
            self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("InfoCard")
        self.setStyleSheet("#InfoCard{border:1px solid #3c4450;border-radius:8px;background:#1f232a;}")

        title = QtWidgets.QLineEdit(node.name)
        title.setObjectName("NodeNameEdit")
        f = title.font(); f.setBold(True); title.setFont(f)
        title.setStyleSheet("QLineEdit{background:#12151a;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}")
        title.setToolTip("Rename node")
        def _commit_rename():
            new_name = title.text().strip()
            old_name = getattr(self, "_node_name", "")
            sc = getattr(self, "_graph_scene", None)
            if not sc or not new_name or new_name == old_name:
                return
            ok, msg = sc.rename_node(old_name, new_name)
            if not ok:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, msg or "Rename failed.")
                title.setText(old_name)
                return
            self._node_name = new_name
            self._node_ref.name = new_name
        title.editingFinished.connect(_commit_rename)

        order_badge = None
        if isinstance(order_index, int) and isinstance(order_total, int):
            order_badge = QtWidgets.QLabel(str(order_index))
            order_badge.setFixedSize(22,22)
            order_badge.setAlignment(QtCore.Qt.AlignCenter)
            order_badge.setStyleSheet("QLabel{background:#22c55e;color:#0a0f0a;border-radius:11px;font-weight:700;}")
            order_badge.setToolTip(f"Step {order_index} of {order_total}")

        close_btn = QtWidgets.QToolButton()
        close_btn.setText("✕"); close_btn.setAutoRaise(True); close_btn.setToolTip("Close")
        close_btn.clicked.connect(self._emit_and_close)

        header = QtWidgets.QHBoxLayout(); header.setContentsMargins(0, 0, 0, 0)
        if order_badge: header.addWidget(order_badge)
        header.addWidget(title); header.addStretch(1); header.addWidget(close_btn)

        text = QtWidgets.QTextBrowser()
        self._text_browser = text
        text.setStyleSheet(
            "QTextBrowser{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
        )
        text.setOpenExternalLinks(False)
        text.setOpenLinks(False)
        safe = QtGui.QTextDocument(); safe.setPlainText(node.info or "No info.")
        html = _hash_to_links(safe.toPlainText())
        text.setHtml("<style>body{font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif; font-size:12px;}a{color:#60a5fa;}</style>"+html)
        text.setMinimumHeight(80)

        text.anchorClicked.connect(lambda url: self.requestJump.emit(url.path().lstrip("/")))

        footer = QtWidgets.QHBoxLayout(); footer.setContentsMargins(0, 0, 0, 0); footer.setSpacing(8)

        # Plugin augment placeholder
        _augmented_by_plugin = False
        try:
            spec = core.get_spec((node.kind or "node").lower())
            augment = (spec.get("augment_infocard_footer") if isinstance(spec, dict)
                       else getattr(spec, "augment_infocard_footer", None))
            if callable(augment):
                _augmented_by_plugin = bool(augment(self, footer))
        except Exception as e:
            print("[EchoGraph] augment_infocard_footer error:", e)
            _augmented_by_plugin = False

        # Kind-specific UI
        kind = (node.kind or "").lower()
        if kind == "append":
            box = self._build_append_box()
        else:
            box = None

        if kind == "librarian":
            self._build_librarian_footer(footer)

        elif node.code and not _augmented_by_plugin:
            run_btn = QtWidgets.QPushButton("Run Python")
            run_btn.setToolTip("Provides maya.cmds as 'cmds' and Houdini as 'hou'")
            run_btn.clicked.connect(self._run_code)
            footer.addWidget(run_btn)

        # Params table (shared)
        self._param_table = self._build_param_table()
        pcol = self._build_param_controls()

        footer.addStretch(1)

        # Root layout
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addLayout(header)
        lay.addWidget(text)
        lay.addWidget(self._param_table)
        lay.addLayout(pcol)

        # Librarian results panel
        if hasattr(self, "_result_view"):
            lay.addWidget(self._result_view)

        # Append reorder UI
        if (self._node_ref.kind or "").lower() == "append" and box is not None:
            lay.addWidget(box)

        lay.addLayout(footer)

    def attach_scene(self, scene):
        """Bind this card to a GraphScene so it live-updates on link changes."""
        try:
            self._graph_scene = scene
        except Exception:
            pass
        try:
            if hasattr(scene, "linksChanged"):
                scene.linksChanged.connect(self._on_links_changed)
        except Exception:
            pass

    def _on_links_changed(self):
        try:
            kind = (self._node_ref.kind or "").lower()
            if kind == "append":
                self.refresh_append_ui_from_model()
            elif kind == "output":
                self.apply_append_preview_if_output()
        except Exception:
            pass

    # ---------- shared helpers ----------
    def refresh_params_from_model(self):
        if not hasattr(self, "_param_table"):
            return
        self._param_table.blockSignals(True)
        try:
            self._param_table.setRowCount(0)
            for p in (self._node_ref.params or []):
                r = self._param_table.rowCount()
                self._param_table.insertRow(r)
                self._param_table.setItem(r, 0, QtWidgets.QTableWidgetItem(p.get("name","")))
                self._param_table.setItem(r, 1, QtWidgets.QTableWidgetItem(p.get("value","")))
        finally:
            self._param_table.blockSignals(False)

    def refresh_append_ui_from_model(self):
        try:
            if (self._node_ref.kind or "").lower() != "append": return
            lw = getattr(self, "_append_list", None)
            if not lw: return
            lw.blockSignals(True)
            lw.clear()
            for nm in (self._node_ref.switch_inputs or []):
                lw.addItem(nm)
        finally:
            try: lw.blockSignals(False)
            except Exception: pass

    def apply_append_preview_if_output(self):
        try:
            if (self._node_ref.kind or "").lower() != "output":
                return
            sc = getattr(self, "_graph_scene", None)
            tb = getattr(self, "_text_browser", None)
            if not sc or not tb or not hasattr(sc, "merged_text_for_output"):
                return
            pairs = sc.merged_text_for_output(self._node_ref.name)
            merged = "\n\n".join(t for _, t in pairs)
            tb.setPlainText(merged)
        except Exception:
            pass

    def _emit_and_close(self):
        try:
            if hasattr(self, "_poll_timer") and self._poll_timer is not None:
                self._poll_timer.stop()
                self._poll_timer.deleteLater()
        except Exception:
            pass
        try:
            self._waiting_ts = None
            self._last_query_text = ""
        except Exception:
            pass
        try:
            self.closedForNode.emit(self._node_name)
        except Exception:
            pass
        self.deleteLater()

    def closeEvent(self, e):
        try:
            if hasattr(self, "_poll_timer") and self._poll_timer is not None:
                self._poll_timer.stop()
                self._poll_timer.deleteLater()
        except Exception:
            pass
        try:
            self._waiting_ts = None
            self._last_query_text = ""
        except Exception:
            pass
        super().closeEvent(e)

    def _edit_code(self):
        dlg = CodeEditorDialog(self, initial_code=self._node_ref.code or "")
        if _qexec(dlg) == QtWidgets.QDialog.Accepted:
            self._node_ref.code = dlg.code()

    def _run_code(self):
        node = self._node_ref
        src = (node.code or "").strip()
        if not src: return
        # hosts are injected by caller; keep names stable
        from maya import cmds as maya_cmds  # may fail at runtime outside Maya (caller guards)
        import hou as hou_mod              # may fail at runtime outside Houdini
        ns = {"cmds": maya_cmds, "hou": hou_mod, "QtWidgets": QtWidgets, "QtCore": QtCore, "QtGui": QtGui, "__name__": "__echograph_exec__"}
        import io, contextlib, traceback
        out_buf = io.StringIO(); err_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                exec(src, ns, ns)
            out = out_buf.getvalue().strip()
            err = err_buf.getvalue().strip()
            if err:
                QtWidgets.QMessageBox.critical(self, APP_TITLE, err)
            elif out:
                QtWidgets.QMessageBox.information(self, APP_TITLE, out)
        except Exception:
            combined = out_buf.getvalue() + "\n" + err_buf.getvalue()
            tb = traceback.format_exc()
            QtWidgets.QMessageBox.critical(self, APP_TITLE, f"{combined}\n{tb}")

    # ---------- append UI ----------
    def _build_append_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Append Inputs (order)")
        lv = QtWidgets.QVBoxLayout(box); lv.setContentsMargins(8,8,8,8); lv.setSpacing(6)

        listw = QtWidgets.QListWidget()
        self._append_list = listw
        listw.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        for nm in (self._node_ref.switch_inputs or []):
            listw.addItem(nm)

        btns = QtWidgets.QHBoxLayout()
        up   = QtWidgets.QPushButton("↑")
        down = QtWidgets.QPushButton("↓")
        apply = QtWidgets.QPushButton("Apply Order")
        btns.addWidget(up); btns.addWidget(down); btns.addWidget(apply); btns.addStretch(1)

        def _move_selected(delta: int):
            r = listw.currentRow()
            if r < 0: return
            nr = r + delta
            if nr < 0 or nr >= listw.count(): return
            it = listw.takeItem(r)
            listw.insertItem(nr, it)
            listw.setCurrentRow(nr)

        def _apply_order():
            new_order = [listw.item(i).text() for i in range(listw.count())]
            self._node_ref.switch_inputs = new_order
            sc = getattr(self, "_graph_scene", None)
            if sc:
                sc.refresh_node_widget(self._node_ref.name)
                if getattr(sc, "_current_output_name", None):
                    seq = sc.ordered_upstream_items(sc._current_output_name)
                    if hasattr(sc.views()[0].window(), "populate_branch_info"):
                        sc.views()[0].window().populate_branch_info([it.model for it in seq])

        up.clicked.connect(lambda: _move_selected(-1))
        down.clicked.connect(lambda: _move_selected(+1))
        apply.clicked.connect(_apply_order)
        lv.addWidget(listw); lv.addLayout(btns)
        return box

    # ---------- librarian UI ----------
    def _build_librarian_footer(self, footer_layout: QtWidgets.QHBoxLayout):
        self._result_view = QtWidgets.QTextBrowser()
        self._result_view.setStyleSheet(
            "QTextBrowser{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
        )
        self._result_view.setMinimumHeight(140)
        self._result_view.setOpenExternalLinks(True)
        self._result_view.setOpenLinks(True)
        self._last_query_text = ""
        self._waiting_ts = None
        self._result_view.setPlainText("No results yet. Send a query to fetch results here.")

        open_btn = QtWidgets.QPushButton("Open Librarian")
        open_btn.setToolTip("Launch the Librarian UI in its own process")
        open_btn.clicked.connect(lambda: ensure_running(True))
        footer_layout.addWidget(open_btn)

        send_btn = QtWidgets.QPushButton("Send Query → Librarian")
        send_btn.setToolTip("Enqueue a search/summary request for the Librarian to pick up")
        footer_layout.addWidget(send_btn)

        def _find_param(params, names):
            for p in (params or []):
                nm = (p.get("name") or "").strip().lower()
                if nm in names:
                    return (p.get("value") or "").strip()
            return ""

        def _send_query():
            q_self = _find_param(self._node_ref.params, {"query", "prompt"})
            topk_str = _find_param(self._node_ref.params, {"top_k", "k"})
            try:
                top_k = max(1, int(topk_str)) if topk_str else 5
            except Exception:
                top_k = 5

            parts = []
            sc = getattr(self, "_graph_scene", None)
            if sc is not None and hasattr(sc, "upstream_of") and hasattr(sc, "resolve_text_value"):
                try:
                    for it in sc.upstream_of(self._node_ref.name):
                        txt = sc.resolve_text_value(it)
                        if txt: parts.append(txt.strip())
                except Exception:
                    pass

            if q_self:
                parts.append(q_self.strip())

            q = "\n\n".join([p for p in parts if p])[:4000]
            if not q:
                QtWidgets.QMessageBox.warning(self, APP_TITLE,
                    "No query text found.\nAdd a Prompt node upstream or set this node’s 'query'/'prompt' parameter.")
                return
            try:
                ts = int(time.time() * 1000)
                self._last_query_text = q
                self._waiting_ts = ts

                cmd = {"type": "search", "query": q, "top_k": top_k, "from": "EchoGraph", "ts": ts}
                fn = enqueue(cmd)
                ensure_running(True)

                self._waiting_ts = ts
                self._last_query_text = q
                try:
                    self._result_view.setPlainText(
                        "Queued search for Librarian:\n\n"
                        f"{q[:1000]}{'…' if len(q) > 1000 else ''}\n\n"
                        f"Ticket: result_{ts}.json\n"
                        "\nWaiting for results…"
                    )
                except Exception:
                    pass

                try:
                    QtWidgets.QToolTip.showText(
                        QtGui.QCursor.pos(),
                        f"Sent to Librarian\n{q[:200]}{'…' if len(q) > 200 else ''}",
                        self, self.rect(), 1500
                    )
                    QtWidgets.QToolTip.showText(
                        QtGui.QCursor.pos(),
                        f"Queued: {fn.name}",
                        self, self.rect(), 1500
                    )
                except Exception:
                    pass

                print(f"[EchoGraph] Enqueued Librarian cmd -> {fn}")

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, APP_TITLE, f"Failed to enqueue:\n{e}")

        send_btn.clicked.connect(_send_query)

        # polling
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(500)

        def _apply_result_if_ready():
            try:
                ts = getattr(self, "_waiting_ts", None)
                if not ts: return
                txt = load_output_text_by_ts(ts) or ""
                if not txt: return
                prefix = f"Query:\n{self._last_query_text}\n\n" if getattr(self, "_last_query_text", "") else ""
                combined = prefix + txt
                if combined != self._result_view.toPlainText():
                    self._result_view.setPlainText(combined)
                self._waiting_ts = None
            except Exception:
                pass

        def _poll_latest():
            _apply_result_if_ready()

        self._poll_timer.timeout.connect(_poll_latest)
        self._poll_timer.start()

        try:
            self._fswatcher = QtCore.QFileSystemWatcher(self)
            self._fswatcher.addPath(str(outbox_dir()))
            def _on_dir_change(_path):
                _apply_result_if_ready()
            self._fswatcher.directoryChanged.connect(_on_dir_change)
        except Exception:
            pass

    # ---------- params table & controls ----------
    def _build_param_table(self) -> QtWidgets.QTableWidget:
        tbl = QtWidgets.QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["Name", "Value"])
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setStyleSheet(
            "QTableWidget{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            "QHeaderView::section{background:#20242b;color:#e6edf3;border:none;}"
        )
        tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        tbl.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked |
            QtWidgets.QAbstractItemView.EditKeyPressed |
            QtWidgets.QAbstractItemView.SelectedClicked
        )
        for p in (self._node_ref.params or []):
            r = tbl.rowCount()
            tbl.insertRow(r)
            tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(p.get("name","")))
            tbl.setItem(r, 1, QtWidgets.QTableWidgetItem(p.get("value","")))
        return tbl

    def _build_param_controls(self) -> QtWidgets.QVBoxLayout:
        pbtns = QtWidgets.QHBoxLayout()
        addp  = QtWidgets.QPushButton("Add Param")
        delp  = QtWidgets.QPushButton("Remove Selected")
        savep = QtWidgets.QPushButton("Apply Changes")
        for b in (addp, delp, savep):
            pbtns.addWidget(b)
        pbtns.addStretch(1)

        def _add_param_row():
            r = self._param_table.rowCount()
            self._param_table.insertRow(r)
            self._param_table.setItem(r, 0, QtWidgets.QTableWidgetItem("param"))
            self._param_table.setItem(r, 1, QtWidgets.QTableWidgetItem(""))

        def _remove_selected_row():
            r = self._param_table.currentRow()
            if r >= 0:
                self._param_table.removeRow(r)

        def _apply_param_changes():
            new_params = []
            for r in range(self._param_table.rowCount()):
                name_item  = self._param_table.item(r, 0)
                value_item = self._param_table.item(r, 1)
                nm  = (name_item.text()  if name_item  else "").strip()
                val = (value_item.text() if value_item else "")
                if nm:
                    new_params.append({"name": nm, "value": val})
            sc = getattr(self, "_graph_scene", None)
            if sc:
                sc.set_node_params(self._node_name, new_params)
            self._node_ref.params = new_params

        addp.clicked.connect(_add_param_row)
        delp.clicked.connect(_remove_selected_row)
        savep.clicked.connect(_apply_param_changes)
        self._param_table.itemChanged.connect(lambda *_: None)

        # Edit/Rename row
        btn_edit = QtWidgets.QPushButton("Edit Params…")
        btn_edit.clicked.connect(self._edit_params)
        btn_rename = QtWidgets.QPushButton("Rename…")
        btn_rename.clicked.connect(self._rename_node)

        erow = QtWidgets.QHBoxLayout()
        erow.setContentsMargins(0, 0, 0, 0)
        erow.setSpacing(6)
        erow.addWidget(btn_edit)
        erow.addWidget(btn_rename)
        erow.addStretch(1)

        pcol = QtWidgets.QVBoxLayout()
        pcol.setContentsMargins(0, 0, 0, 0)
        pcol.setSpacing(6)
        pcol.addLayout(pbtns)
        pcol.addLayout(erow)
        return pcol

    def _edit_params(self):
        sc = getattr(self, "_graph_scene", None)
        if sc is None:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "Scene not available.")
            return
        dlg = ParamEditorDialog(self, title=f"Edit Parameters — {self._node_ref.name}",
                                params=self._node_ref.params)
        if _qexec(dlg) == QtWidgets.QDialog.Accepted:
            new_params = dlg.result_params()
            sc.set_node_params(self._node_name, new_params)
            self._node_ref.params = new_params
            try:
                self.refresh_params_from_model()
            except Exception:
                pass

    def _rename_node(self):
        sc = getattr(self, "_graph_scene", None)
        if sc is None:
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Node", "New name:", QtWidgets.QLineEdit.Normal, self._node_ref.name
        )
        if not ok or not text.strip():
            return
        old = self._node_ref.name
        success, msg = sc.rename_node(old, text.strip())
        if not success:
            if msg:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, msg)
            return
        self._node_ref.name = text.strip()
        self._node_name = self._node_ref.name
        try:
            name_edit = self.findChild(QtWidgets.QLineEdit, "NodeNameEdit")
            if name_edit:
                name_edit.setText(self._node_ref.name)
        except Exception:
            pass
