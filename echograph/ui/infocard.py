# echograph/ui/infocard.py
from __future__ import annotations
import os
import re, time, json
from typing import Optional, List, Tuple
from pathlib import Path
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

try:
    from nodes.gpt_prompt import spec as _gpt_prompt_spec  # optional
except Exception:  # pragma: no cover - optional
    _gpt_prompt_spec = None

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


_MASKED_VALUE_TEXT = "******"
_QUBIT_DECK_CONTROLLER_KINDS = {
    "qubit_deck_controller",
    "qubit deck controller",
    "qubitdeckcontroller",
    "qubitdeck controller",
}
_QUBIT_DECK_SENSITIVE_PARAMS = {
    "api_base",
    "server_address",
    "server address",
    "address",
    "url",
    "endpoint",
}


def _is_qubit_deck_controller_kind(kind: str) -> bool:
    return str(kind or "").strip().lower() in _QUBIT_DECK_CONTROLLER_KINDS


class _SensitiveParamValueDelegate(QtWidgets.QStyledItemDelegate):
    def _is_sensitive_value(self, index: QtCore.QModelIndex) -> bool:
        if not index.isValid() or index.column() != 2:
            return False
        table = self.parent()
        names = getattr(table, "_masked_value_param_names", set())
        if not names:
            return False
        name_index = index.sibling(index.row(), 1)
        param_name = str(name_index.data(QtCore.Qt.DisplayRole) or "").strip().lower()
        return param_name in names

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if self._is_sensitive_value(index) and str(option.text or ""):
            option.text = _MASKED_VALUE_TEXT

class InfoCard(QtWidgets.QFrame):
    requestJump = QtCore.Signal(str)
    closedForNode = QtCore.Signal(str)

    def __init__(self, node, order_index: int=None, order_total: int=None):
        super().__init__()
        self._node_name = node.name
        self._node_ref = node
        try:
            self._graph_scene = getattr(node, "_graph_scene", None)
        except Exception:
            self._graph_scene = None

        try:
            self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        except AttributeError:
            self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("InfoCard")
        self.setStyleSheet("#InfoCard{border:1px solid #3c4450;border-radius:8px;background:#1f232a;}")

        title = QtWidgets.QLineEdit(node.name)
        self._title_edit = title
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
            try:
                win = self.window()
                handler = getattr(win, "rename_scene_asset_owner", None) if win is not None else None
                if callable(handler):
                    handler(old_name, new_name)
            except Exception:
                pass
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
        info_str = (node.info or "").strip()
        safe = QtGui.QTextDocument(); safe.setPlainText(info_str or "No info.")
        html = _hash_to_links(safe.toPlainText())
        text.setHtml("<style>body{font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif; font-size:12px;}a{color:#60a5fa;}</style>"+html)
        text.setMinimumHeight(80)

        # Hide the info block if it's just the default placeholder and not needed
        default_info = info_str.lower() in ("", "user-created node.", "user-created node", "librarian node created", "librarian node created.")
        if default_info and (node.kind or "").lower() != "output":
            text.setVisible(False)
            text.setMinimumHeight(0)
            text.setMaximumHeight(0)

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

        if (not _augmented_by_plugin) and _gpt_prompt_spec:
            try:
                if (node.kind or "").strip().lower() in _gpt_prompt_spec.PROMPT_NODE_KINDS:
                    _augmented_by_plugin = bool(_gpt_prompt_spec.augment_infocard_footer(self, footer))
            except Exception as e:
                print("[EchoGraph] llm_prompt footer error:", e)

        # Kind-specific UI
        kind = (node.kind or "").lower()
        if (not _augmented_by_plugin) and kind in ("scene", "scene_assembly", "scene_outliner"):
            try:
                from nodes.scene import spec as _scene_spec  # type: ignore
                if hasattr(_scene_spec, "augment_infocard_footer"):
                    _augmented_by_plugin = bool(_scene_spec.augment_infocard_footer(self, footer))
            except Exception as e:
                print("[EchoGraph] scene footer error:", e)
        if kind == "append":
            box = self._build_append_box()
        else:
            box = None

        if kind == "librarian":
            self._build_librarian_footer(footer)
        elif kind in ("import", "html_preview"):
            self._build_import_footer(footer)

        elif node.code and not _augmented_by_plugin:
            run_btn = QtWidgets.QPushButton("Run Python")
            run_btn.setToolTip("Provides maya.cmds as 'cmds' and Houdini as 'hou'")
            run_btn.clicked.connect(self._run_code)
            footer.addWidget(run_btn)


        # Params table (shared)
        self._param_table = self._build_param_table()
        pcol = self._build_param_controls()

        # Scene footer wants full-width (outliner), so don't add the trailing stretch spacer
        if (node.kind or "").lower() not in (
            "scene",
            "scene_assembly",
            "scene_outliner",
            "modeler",
            "codex_sandbox",
            "codex sandbox",
            "sandbox",
        ):
            footer.addStretch(1)

        # Root layout
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addLayout(header)
        lay.addWidget(text)
        lay.addWidget(self._param_table)
        try:
            QtCore.QTimer.singleShot(0, self._fit_param_table_columns)
        except Exception:
            pass
        # Snapshot version picker (import/scene only) - display only for now
        kind = (getattr(self._node_ref, "kind", "") or "").lower()
        
        if kind in ("import", "scene"):
            snap_row = QtWidgets.QHBoxLayout()
            snap_row.setContentsMargins(0, 0, 0, 0)
            snap_row.setSpacing(6)

            snap_lbl = QtWidgets.QLabel("Snapshot")
            self._snap_combo = QtWidgets.QComboBox()
            self._snap_combo.setMinimumWidth(140)

            snap_row.addWidget(snap_lbl, 0)
            snap_row.addWidget(self._snap_combo, 1)

            lay.addLayout(snap_row)
            # populate from current thumbnail folder
            try:
                thumb_path = ""
                for p in (self._node_ref.params or []):
                    if p.get("name") == "thumbnail":
                        thumb_path = (p.get("value") or "").strip()
                        break

                if thumb_path:
                    folder = Path(thumb_path).parent
                    pngs = sorted(folder.glob("*.png"), key=lambda p: p.name)

                    self._snap_combo.blockSignals(True)
                    self._snap_combo.clear()

                    for i, pth in enumerate(pngs, start=1):
                        label = f"v{i:03d}"
                        self._snap_combo.addItem(label, pth.name)

                    # pick current if thumbnail_choice exists
                    chosen = ""
                    for p in (self._node_ref.params or []):
                        if p.get("name") == "thumbnail_choice":
                            chosen = (p.get("value") or "").strip()
                            break

                    if chosen:
                        for idx in range(self._snap_combo.count()):
                            if self._snap_combo.itemData(idx) == chosen:
                                self._snap_combo.setCurrentIndex(idx)
                                break

                    self._snap_combo.blockSignals(False)
            except Exception:
                pass

            def _on_snapshot_changed(_idx: int):
                # prevent re-entrancy while we rebuild UI / refresh combo
                if getattr(self, "_snap_updating", False):
                    return

                combo = getattr(self, "_snap_combo", None)
                if combo is None:
                    return

                fname = combo.currentData()
                if not fname:
                    return

                thumb_path = (self._param_value("thumbnail") or "").strip()
                if not thumb_path:
                    return

                folder = Path(thumb_path).parent
                new_thumb = str((folder / str(fname)).resolve())

                self._snap_updating = True

                def _apply():
                    try:
                        # update params
                        self._set_param_value("thumbnail", new_thumb)
                        self._set_param_value("thumbnail_choice", str(fname))
                        self._set_param_value("thumbnail_rev", str(time.time()))  # cache bust

                        # refresh the combo labels immediately (so new snapshot appears)
                        try:
                            self._refresh_snapshot_combo()
                        except Exception:
                            pass
                    finally:
                        self._snap_updating = False

                # defer so we do not mutate UI while Qt is inside the signal emission
                QtCore.QTimer.singleShot(0, _apply)

            self._snap_combo.currentIndexChanged.connect(_on_snapshot_changed)
            self._refresh_snapshot_combo()

        else:
            self._snap_combo = None


        lay.addLayout(pcol)

        # Python output console (always present)
        is_python = (getattr(self._node_ref, "kind", "") or "").lower() == "python"
        has_code = any((p.get("name") == "code") and (str(p.get("value") or "").strip()) for p in (self._node_ref.params or []))

        if is_python or has_code:
            self._py_console = QtWidgets.QPlainTextEdit()
            self._py_console.setReadOnly(True)
            self._py_console.setMaximumHeight(140)
            self._py_console.setStyleSheet(
                "QPlainTextEdit{background:#0b0e12;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            )
            self._py_console.setVisible(False)
            lay.addWidget(self._py_console)
        else:
            self._py_console = None

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
            if hasattr(scene, "paramChanged"):
                scene.paramChanged.connect(self._on_scene_param_changed)
        except Exception:
            pass
        try:
            if hasattr(scene, "linksChanged"):
                scene.linksChanged.connect(self._on_links_changed)
        except Exception:
            pass
        try:
            connect = getattr(self, "_scene_outliner_connect", None)
            if callable(connect):
                connect(scene)
        except Exception:
            pass
        try:
            refresh = getattr(self, "_scene_outliner_refresh", None)
            if callable(refresh):
                refresh(scene)
        except Exception:
            pass
        try:
            connect = getattr(self, "_modeler_outliner_connect", None)
            if callable(connect):
                connect(scene)
        except Exception:
            pass
        try:
            refresh = getattr(self, "_modeler_outliner_refresh", None)
            if callable(refresh):
                refresh(scene)
        except Exception:
            pass
        self._refresh_append_list_labels()

    def _on_links_changed(self):
        try:
            kind = (self._node_ref.kind or "").lower()
            if kind == "append":
                self.refresh_append_ui_from_model()
            elif kind == "output":
                self.apply_append_preview_if_output()
        except Exception:
            pass
            
    def _on_scene_param_changed(self, node_name: str, params: list):
        if (node_name or "") != (getattr(self, "_node_name", "") or ""):
            return
        try:
            self._node_ref.params = list(params or [])
        except Exception:
            pass

        try:
            if not self._refresh_params_incremental(params):
                self.refresh_params_from_model()
        except Exception:
            pass

        # If you added a snapshot dropdown + _refresh_snapshot_combo(), update it too
        try:
            fn = getattr(self, "_refresh_snapshot_combo", None)
            if callable(fn):
                fn()
        except Exception:
            pass
            
    # ---------- shared helpers ----------
    def _hidden_signature_from_params(self, params: list) -> str:
        raw = ""
        has_override = False
        for pp in (params or []):
            if (pp.get("name") or "").strip().lower() == "__ui_hidden_params":
                raw = (pp.get("value") or "").strip()
                has_override = True
                break
        return f"{1 if has_override else 0}:{raw}"

    def _visible_param_rows(self, params: list) -> tuple[list[str], dict[str, str]]:
        visible = []
        values: dict[str, str] = {}
        for p in (params or []):
            pname = (p.get("name", "") or "").strip()
            if not pname:
                continue
            if pname.strip().lower() == "__ui_hidden_params":
                continue
            visible.append(pname)
            values[pname] = p.get("value", "") or ""
        return visible, values

    def _refresh_params_incremental(self, params: list) -> bool:
        tbl = getattr(self, "_param_table", None)
        if not tbl:
            return False
        try:
            current_names = []
            for r in range(tbl.rowCount()):
                item = tbl.item(r, 1)
                if not item:
                    return False
                current_names.append(item.text())

            visible, values = self._visible_param_rows(params)
            if visible != current_names:
                return False

            hidden_sig = self._hidden_signature_from_params(params)
            if getattr(self, "_hidden_sig", "") != hidden_sig:
                return False

            tbl.blockSignals(True)
            for r, pname in enumerate(visible):
                val = values.get(pname, "")
                item = tbl.item(r, 2)
                if item is None:
                    item = QtWidgets.QTableWidgetItem(val)
                    item.setToolTip(val)
                    tbl.setItem(r, 2, item)
                else:
                    if item.text() != val:
                        item.setText(val)
                    item.setToolTip(val)
            return True
        finally:
            try:
                tbl.blockSignals(False)
            except Exception:
                pass

    def refresh_params_from_model(self):
        if not hasattr(self, "_param_table"):
            return

        # preserve scroll + current selection before rebuild
        vpos = 0
        hpos = 0
        cur_row = -1
        try:
            vpos = int(self._param_table.verticalScrollBar().value())
            hpos = int(self._param_table.horizontalScrollBar().value())
            cur_row = int(self._param_table.currentRow())
        except Exception:
            pass

        try:
            lay = self.layout()
            if lay is None:
                return

            idx = lay.indexOf(self._param_table)
            if idx < 0:
                idx = 0

            old = self._param_table
            lay.removeWidget(old)
            try:
                old.deleteLater()
            except Exception:
                pass

            self._param_table = self._build_param_table()
            lay.insertWidget(idx, self._param_table)

            # restore after layout stabilizes (two ticks)
            def _restore():
                try:
                    vb = self._param_table.verticalScrollBar()
                    hb = self._param_table.horizontalScrollBar()
                    vb.setValue(vpos)
                    hb.setValue(hpos)
                    if cur_row >= 0 and cur_row < self._param_table.rowCount():
                        self._param_table.setCurrentCell(cur_row, 1)
                except Exception:
                    pass

            QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, _restore))

        except Exception:
            pass


    def refresh_append_ui_from_model(self):
        try:
            if (self._node_ref.kind or "").lower() != "append":
                return
            lw = getattr(self, "_append_list", None)
            if not lw:
                return
            lw.blockSignals(True)
            lw.clear()
            for nm in (self._node_ref.switch_inputs or []):
                item = QtWidgets.QListWidgetItem(nm)
                item.setData(QtCore.Qt.UserRole, nm)
                lw.addItem(item)
            self._refresh_append_list_labels()
        finally:
            try:
                lw.blockSignals(False)
            except Exception:
                pass

    def _append_display_label(self, node_name: str) -> str:
        return node_name

    def _refresh_append_list_labels(self):
        listw = getattr(self, "_append_list", None)
        if not listw:
            return
        for i in range(listw.count()):
            it = listw.item(i)
            if not it:
                continue
            base = it.data(QtCore.Qt.UserRole) or it.text()
            it.setText(self._append_display_label(base))

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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_param_table_columns()

    def _fit_param_table_columns(self) -> None:
        tbl = getattr(self, "_param_table", None)
        if tbl is None:
            return
        try:
            viewport_w = int(tbl.viewport().width())
        except Exception:
            viewport_w = 0
        if viewport_w <= 0:
            return
        eye_w = 24
        name_w = max(92, min(190, int(viewport_w * 0.26)))
        value_w = max(120, viewport_w - eye_w - name_w - 6)
        try:
            hdr = tbl.horizontalHeader()
            hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
            hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
            hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
            tbl.setColumnWidth(0, eye_w)
            tbl.setColumnWidth(1, name_w)
            tbl.setColumnWidth(2, value_w)
        except Exception:
            pass

    def _edit_code(self):
        dlg = CodeEditorDialog(self, initial_code=self._node_ref.code or "")
        if _qexec(dlg) == QtWidgets.QDialog.Accepted:
            self._node_ref.code = dlg.code()

    def _run_code(self):
        if hasattr(self, "_py_console"):
            self._py_console.setVisible(True)

        node = self._node_ref

        # pull code from node.code, else from common param names
        src = (getattr(node, "code", "") or "").strip()
        if not src:
            for p in (getattr(node, "params", None) or []):
                nm = (p.get("name") or "").strip().lower()
                if nm in ("code", "script", "py", "python", "source"):
                    src = (p.get("value") or "").strip()
                    if src:
                        break

        # show debug no matter what
        self._py_console.setVisible(True)
        self._py_console.setPlainText(
            f"kind={getattr(node,'kind',None)}\n"
            f"name={getattr(node,'name',None)}\n"
            f"node.code_len={len((getattr(node,'code', '') or '').strip())}\n"
            f"src_len={len(src)}\n"
            f"param_names={[ (pp.get('name') or '') for pp in (getattr(node,'params',None) or []) ]}\n"
        )

        if not src:
            return

        # optional DCC hooks
        try:
            from maya import cmds as maya_cmds
        except Exception:
            maya_cmds = None

        try:
            import hou as hou_mod
        except Exception:
            hou_mod = None

        ns = {
            "cmds": maya_cmds,
            "hou": hou_mod,
            "QtWidgets": QtWidgets,
            "QtCore": QtCore,
            "QtGui": QtGui,
            "__name__": "__echograph_exec__",
        }
        params_map = {
            (p.get("name") or f"param{i+1}"): p.get("value", "")
            for i, p in enumerate(node.params or [])
        }
        raw_params = list(node.params or [])
        sc = getattr(self, "_graph_scene", None)
        inputs = {}
        primary_input = ""
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
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                exec(src, ns, ns)
            out = out_buf.getvalue().rstrip()
            err = err_buf.getvalue().rstrip()

            msg = ""
            if out:
                msg += out
            if err:
                if msg:
                    msg += "\n\n"
                msg += "[stderr]\n" + err
            if not msg:
                msg = "(no output)"

            if hasattr(self, "_py_console") and self._py_console is not None:
                self._py_console.setVisible(True)
                self._py_console.setPlainText(msg)
            else:
                QtWidgets.QMessageBox.information(self, APP_TITLE, msg)

        except Exception:
            combined = (out_buf.getvalue() + "\n" + err_buf.getvalue()).rstrip()
            tb = traceback.format_exc().rstrip()

            msg = ""
            if combined.strip():
                msg += combined + "\n\n"
            msg += tb

            if hasattr(self, "_py_console") and self._py_console is not None:
                self._py_console.setVisible(True)
                self._py_console.setPlainText(msg)
            else:
                QtWidgets.QMessageBox.critical(self, APP_TITLE, msg)

    # ---------- append UI ----------
    def _build_append_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Append Inputs (order)")
        lv = QtWidgets.QVBoxLayout(box); lv.setContentsMargins(8,8,8,8); lv.setSpacing(6)

        listw = QtWidgets.QListWidget()
        self._append_list = listw
        listw.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        for nm in (self._node_ref.switch_inputs or []):
            item = QtWidgets.QListWidgetItem(nm)
            item.setData(QtCore.Qt.UserRole, nm)
            listw.addItem(item)
        self._refresh_append_list_labels()

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
            new_order = []
            for i in range(listw.count()):
                it = listw.item(i)
                base = it.data(QtCore.Qt.UserRole)
                new_order.append(base or it.text())
            self._node_ref.switch_inputs = new_order
            sc = getattr(self, "_graph_scene", None)
            if sc:
                sc.refresh_node_widget(self._node_ref.name)
                if getattr(sc, "_current_output_name", None):
                    try:
                        seq = sc.ordered_upstream_items(sc._current_output_name)
                        views = sc.views()
                        if views:
                            win = views[0].window()
                            if hasattr(win, "populate_branch_info"):
                                win.populate_branch_info([it.model for it in seq])
                    except Exception:
                        pass
                try:
                    if hasattr(sc, "linksChanged"):
                        sc.linksChanged.emit()
                except Exception:
                    pass
                try:
                    if hasattr(sc, "paramChanged"):
                        sc.paramChanged.emit(self._node_ref.name, list(getattr(self._node_ref, "params", []) or []))
                except Exception:
                    pass

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

        send_btn = QtWidgets.QPushButton("Send -> Librarian")
        send_btn.setToolTip("Enqueue a search/analyze request for the Librarian to pick up")
        footer_layout.addWidget(send_btn)

        def _find_param(params, names):
            for p in (params or []):
                nm = (p.get("name") or "").strip().lower()
                if nm in names:
                    return (p.get("value") or "").strip()
            return ""

        def _normalize_action(raw: str, fallback: str = "search") -> str:
            aliases = {
                "analysis": "analyze",
                "analyse": "analyze",
                "analyse_with_sources": "analyze_with_sources",
                "analyze with sources": "analyze_with_sources",
                "analyse with sources": "analyze_with_sources",
                "search with sources": "analyze_with_sources",
            }
            val = (raw or "").strip()
            if not val:
                return fallback
            key = aliases.get(val.lower().replace(" ", "_"), val.lower().replace(" ", "_"))
            allowed = {"search", "analyze", "analyze_with_sources", "summarize", "summarize_with_sources"}
            return key if key in allowed else fallback

        def _send_query():
            try:
                from nodes.librarian import spec as librarian_spec
            except Exception:
                librarian_spec = None

            sc = getattr(self, "_graph_scene", None)
            node_item = None
            if sc is not None:
                try:
                    node_item = sc._node_items.get(self._node_ref.name)  # type: ignore[attr-defined]
                except Exception:
                    node_item = None

            def _param_value(name: str) -> str:
                target = (name or "").strip().lower()
                for p in (self._node_ref.params or []):
                    if (p.get("name") or "").strip().lower() == target:
                        return p.get("value") or ""
                return ""

            def _wired_value(port: str) -> str:
                if librarian_spec and hasattr(librarian_spec, "_text_from_input") and node_item:
                    try:
                        txt = librarian_spec._text_from_input(self, node_item, port)
                        if txt and txt.strip():
                            return txt
                    except Exception:
                        pass
                return ""

            def _val(port: str) -> str:
                wired = _wired_value(port)
                return wired if wired.strip() else _param_value(port)

            def _legacy_query_fallback() -> str:
                q_param = _find_param(self._node_ref.params, {"query", "prompt"})
                parts = []
                if sc is not None and hasattr(sc, "upstream_of") and hasattr(sc, "resolve_text_value"):
                    try:
                        for it in sc.upstream_of(self._node_ref.name):
                            txt = sc.resolve_text_value(it)
                            if txt:
                                parts.append(txt.strip())
                    except Exception:
                        pass
                if q_param:
                    parts.append(q_param.strip())
                return "\n\n".join([p for p in parts if p])

            def _kval(name: str, default: int) -> int:
                raw = _val(name) or _find_param(self._node_ref.params, {name})
                try:
                    return int(raw.strip())
                except Exception:
                    return default

            query = (_val("query") or "").strip()
            if not query:
                query = _legacy_query_fallback().strip()
            query = query[:4000]

            if not query:
                QtWidgets.QMessageBox.warning(
                    self,
                    APP_TITLE,
                    "No query text found.\nAdd a Prompt node upstream or set this node’s 'query' parameter.",
                )
                return

            docs_dir = (_val("docs_dir") or "").strip()
            mode = (_val("mode") or _find_param(self._node_ref.params, {"mode"}) or "tree_summarize").strip()
            top_k = max(1, _kval("top_k", 5))
            action = _normalize_action(_val("action") or _find_param(self._node_ref.params, {"action"}) or "", "search")

            if action == "search" and not query:
                QtWidgets.QMessageBox.warning(self, APP_TITLE, "Search requires a non-empty query.")
                return

            try:
                ts = int(time.time() * 1000)
                self._last_query_text = "\n".join([
                    f"Action: {action}",
                    f"Docs: {docs_dir or '(default)'}",
                    "",
                    query
                ]).strip()
                self._waiting_ts = ts

                cmd = {
                    "type": action,
                    "action": action,
                    "query": query,
                    "top_k": top_k,
                    "mode": mode,
                    "from": "EchoGraph",
                    "ts": ts,
                }
                if docs_dir:
                    cmd["docs_dir"] = docs_dir

                fn = enqueue(cmd)
                ensure_running(True)

                self._waiting_ts = ts
                try:
                    self._result_view.setPlainText(
                        f"Queued {action} for Librarian:\n\n"
                        f"{query[:1000]}{'…' if len(query) > 1000 else ''}\n\n"
                        f"Ticket: result_{ts}.json\n"
                        "\nWaiting for results…"
                    )
                except Exception:
                    pass

                try:
                    QtWidgets.QToolTip.showText(
                        QtGui.QCursor.pos(),
                        f"Sent ({action})\n{query[:200]}{'…' if len(query) > 200 else ''}",
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

    def _build_import_footer(self, footer_layout: QtWidgets.QHBoxLayout):
        sc = getattr(self, "_graph_scene", None)
        if not sc or not hasattr(sc, "set_node_params"):
            return False

        path = self._param_value("path")
        path_edit = QtWidgets.QLineEdit(path)
        path_edit.setPlaceholderText("C:/docs/page.html")

        browse_btn = QtWidgets.QPushButton()
        browse_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        browse_btn.setToolTip("Browse for file")
        view_btn = QtWidgets.QPushButton("View")
        view_btn.setEnabled(bool(path))

        def _apply(new_path: str):
            new_path = (new_path or "").strip()
            self._set_param_value("path", new_path)
            path_edit.setText(new_path)
            view_btn.setEnabled(bool(new_path))

        def _browse():
            start = path or os.path.expanduser("~")
            file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Select HTML File",
                start,
                "HTML Files (*.html *.htm);;All Files (*.*)",
            )
            if file_path:
                _apply(file_path)

        def _view():
            self._preview_import_file(path_edit.text().strip())

        browse_btn.clicked.connect(_browse)
        view_btn.clicked.connect(_view)
        path_edit.editingFinished.connect(lambda: _apply(path_edit.text()))

        footer_layout.addWidget(QtWidgets.QLabel("File:"))
        footer_layout.addWidget(path_edit, 1)
        footer_layout.addWidget(browse_btn)
        footer_layout.addWidget(view_btn)

        return True

    def _refresh_snapshot_combo(self) -> None:
        if getattr(self, "_snap_combo", None) is None:
            return

        kind = (getattr(self._node_ref, "kind", "") or "").lower()
        if kind not in ("import", "scene"):
            return

        thumb_path = (self._param_value("thumbnail") or "").strip()
        if not thumb_path:
            self._snap_combo.blockSignals(True)
            self._snap_combo.clear()
            self._snap_combo.blockSignals(False)
            return

        folder = Path(thumb_path).parent
        try:
            pngs = sorted(folder.glob("*.png"), key=lambda p: p.name)
        except Exception:
            pngs = []

        chosen = (self._param_value("thumbnail_choice") or "").strip()

        self._snap_combo.blockSignals(True)
        self._snap_combo.clear()

        for i, pth in enumerate(pngs, start=1):
            label = f"v{i:03d}"
            self._snap_combo.addItem(label, pth.name)

        if chosen:
            for idx in range(self._snap_combo.count()):
                if self._snap_combo.itemData(idx) == chosen:
                    self._snap_combo.setCurrentIndex(idx)
                    break

        self._snap_combo.blockSignals(False)


    # ---------- params table & controls ----------
    def _build_param_table(self) -> QtWidgets.QTableWidget:
        try:
            import sys
            sys.stderr.write(f"[INFOCARD] _build_param_table from: {__file__}\n")
            sys.stderr.flush()
        except Exception:
            pass
        tbl = QtWidgets.QTableWidget(0, 3)
        tbl.setHorizontalHeaderLabels(["", "Name", "Value"])
        tbl.setStyleSheet(
            "QTableWidget{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:6px;}"
            "QHeaderView::section{background:#20242b;color:#e6edf3;border:none;}"
        )
        tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        try:
            tbl.setTextElideMode(QtCore.Qt.ElideNone)
        except Exception:
            pass
        try:
            tbl.setWordWrap(False)
        except Exception:
            pass
        tbl.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked |
            QtWidgets.QAbstractItemView.EditKeyPressed |
            QtWidgets.QAbstractItemView.SelectedClicked
        )
        if _is_qubit_deck_controller_kind(getattr(self._node_ref, "kind", "")):
            tbl._masked_value_param_names = set(_QUBIT_DECK_SENSITIVE_PARAMS)
            tbl.setItemDelegate(_SensitiveParamValueDelegate(tbl))

        # The resize handler gives most available width to Value so paths
        # show as much text as the current InfoCard width allows.
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        tbl.setColumnWidth(0, 26)

        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        hdr.setStretchLastSection(False)

        tbl.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        # InfoCard shows everything; node surface decides what is hidden.
        HIDE_PARAMS = set()

        store_key = "__ui_hidden_params"

        # read current hidden list (comma-separated)
        raw = ""
        has_override = False
        for pp in (self._node_ref.params or []):
            if (pp.get("name") or "").strip().lower() == store_key:
                raw = (pp.get("value") or "").strip()
                has_override = True
                break
        try:
            self._hidden_sig = f"{1 if has_override else 0}:{raw}"
        except Exception:
            pass

        hidden = set()
        if raw:
            for part in raw.split(","):
                nm = part.strip().lower()
                if nm:
                    hidden.add(nm)

        # match node defaults when there is no override stored yet
        kind = (self._node_ref.kind or "").lower()
        if not has_override:
            if kind == "import":
                hidden.update({"thumbnail", "thumbnail_rev", "thumbnail_choice"})
            elif kind in ("scene", "scene_assembly", "scene_outliner"):
                # scene-only defaults we keep off the node surface
                hidden.update({"thumbnail", "thumbnail_rev", "thumbnail_choice", "splat_depth_test"})
             
        tbl.verticalHeader().setDefaultSectionSize(24)
        tbl.setColumnWidth(0, 24)

        icons_dir = Path(__file__).resolve().parents[2] / "icons"
        eye_open  = QtGui.QIcon(str(icons_dir / "EyeOpen_s_Icon.png"))
        eye_close = QtGui.QIcon(str(icons_dir / "EyeClose_s_Icon.png"))
        
        for p in (self._node_ref.params or []):
            pname = (p.get("name", "") or "").strip()
            if not pname:
                continue
            if pname.strip().lower() == "__ui_hidden_params":
                continue
            if pname in HIDE_PARAMS:
                continue

            key = pname.strip().lower()
            is_hidden = key in hidden

            r = tbl.rowCount()
            tbl.insertRow(r)

            eye = QtWidgets.QToolButton()
            eye.setCheckable(True)
            eye.setAutoRaise(True)

            eye.blockSignals(True)
            eye.setChecked(not is_hidden)
            eye.blockSignals(False)

            eye.setText("")
            eye.setFixedSize(22, 22)
            tbl.setColumnWidth(0, 24)
            eye.setIconSize(QtCore.QSize(20, 20))    # icon inside square
            eye.setStyleSheet(
                "QToolButton{padding:0;margin:0;border:1px solid #2a2f37;border-radius:4px;background:transparent;}"
                "QToolButton:checked{border-color:#3b4452;}"
                "QToolButton:hover{background:rgba(255,255,255,18);}"
            )


            # visible = checked True, hidden = checked False
            eye.setIcon(eye_open if (not is_hidden) else eye_close)
            w = QtWidgets.QWidget()
            lay = QtWidgets.QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.setAlignment(QtCore.Qt.AlignCenter)
            lay.addWidget(eye)
            tbl.setCellWidget(r, 0, w)

            name_item = QtWidgets.QTableWidgetItem(pname)
            name_item.setToolTip(pname)
            value_text = p.get("value", "") or ""
            value_item = QtWidgets.QTableWidgetItem(value_text)
            value_item.setToolTip(value_text)
            value_item.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            tbl.setItem(r, 1, name_item)
            tbl.setItem(r, 2, value_item)
        

            def _apply_toggle(checked: bool, nm=key, btn=eye, _open=eye_open, _close=eye_close):
                # recompute from current node params
                raw2 = ""
                has_override2 = False
                for pp2 in (self._node_ref.params or []):
                    if (pp2.get("name") or "").strip().lower() == store_key:
                        raw2 = (pp2.get("value") or "").strip()
                        has_override2 = True
                        break

                cur = set()
                if raw2:
                    for part2 in raw2.split(","):
                        t = part2.strip().lower()
                        if t:
                            cur.add(t)

                # if there was no override yet, start from defaults so toggling one
                # doesn't unintentionally unhide the others
                kind2 = (self._node_ref.kind or "").lower()
                if not has_override2:
                    if kind2 == "import":
                        cur.update({"thumbnail", "thumbnail_rev", "thumbnail_choice"})
                    elif kind2 in ("scene", "scene_assembly", "scene_outliner"):
                        cur.update({"thumbnail", "thumbnail_rev", "thumbnail_choice", "splat_depth_test"})

                if checked:
                    cur.discard(nm)   # visible
                else:
                    cur.add(nm)       # hidden

                new_val = ",".join(sorted(cur))

                params = list(self._node_ref.params or [])
                found = False
                for pp3 in params:
                    if (pp3.get("name") or "").strip().lower() == store_key:
                        pp3["value"] = new_val
                        found = True
                        break
                if not found:
                    params.append({"name": store_key, "value": new_val})

                # push through the scene; defer to avoid re-entrancy issues
                sc = getattr(self, "_graph_scene", None)
                if sc and hasattr(sc, "set_node_params"):
                    QtCore.QTimer.singleShot(
                        0, lambda: sc.set_node_params(self._node_ref.name, params)
                    )
                btn.setIcon(_open if checked else _close)      
            eye.toggled.connect(_apply_toggle)

        try:
            QtCore.QTimer.singleShot(0, self._fit_param_table_columns)
        except Exception:
            pass
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
            # col 0 is the visibility/eye column
            self._param_table.setItem(r, 1, QtWidgets.QTableWidgetItem("param"))  # Name
            self._param_table.setItem(r, 2, QtWidgets.QTableWidgetItem(""))       # Value

        def _remove_selected_row():
            r = self._param_table.currentRow()
            if r >= 0:
                self._param_table.removeRow(r)

        def _apply_param_changes():
            new_params = []
            for r in range(self._param_table.rowCount()):
                name_item  = self._param_table.item(r, 1)   # Name
                value_item = self._param_table.item(r, 2)   # Value
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
            win = self.window()
            handler = getattr(win, "rename_scene_asset_owner", None) if win is not None else None
            if callable(handler):
                handler(old, self._node_ref.name)
        except Exception:
            pass
        try:
            name_edit = self.findChild(QtWidgets.QLineEdit, "NodeNameEdit")
            if name_edit:
                name_edit.setText(self._node_ref.name)
        except Exception:
            pass

    def _param_value(self, name: str) -> str:
        key = (name or "").strip().lower()
        for p in (self._node_ref.params or []):
            if (p.get("name", "") or "").strip().lower() == key:
                return p.get("value", "") or ""
        return ""

    def _set_param_value(self, name: str, value: str):
        key = (name or "").strip().lower()
        params = list(self._node_ref.params or [])
        found = False
        for p in params:
            if (p.get("name", "") or "").strip().lower() == key:
                p["value"] = value
                found = True
                break
        if not found:
            params.append({"name": name, "value": value})

        sc = getattr(self, "_graph_scene", None)
        if sc:
            sc.set_node_params(self._node_name, params)
            try:
                sc.refresh_node_widget(self._node_name)
            except Exception:
                pass
        self._node_ref.params = params
        try:
            self.refresh_params_from_model()
        except Exception:
            pass

    def _preview_import_file(self, path: str):
        sc = getattr(self, "_graph_scene", None)
        if not sc:
            return
        node_item = sc._node_items.get(self._node_name)
        if node_item and hasattr(node_item, "_open_import_preview"):
            node_item._open_import_preview(path)
