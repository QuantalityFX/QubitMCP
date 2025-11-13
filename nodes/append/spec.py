# nodes/append/spec.py
from nodes.core import Spec

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

try:
    from pygments import highlight
    from pygments.lexers import guess_lexer, TextLexer
    from pygments.formatters import HtmlFormatter
    _HAS_PYGMENTS = True
except Exception:
    highlight = None  # type: ignore
    guess_lexer = TextLexer = HtmlFormatter = None  # type: ignore
    _HAS_PYGMENTS = False

def _render_rich_html(text: str) -> str:
    if not _HAS_PYGMENTS or not text.strip():
        return ""
    try:
        lexer = guess_lexer(text)
    except Exception:
        lexer = TextLexer()
    try:
        formatter = HtmlFormatter(style="monokai", noclasses=True, nowrap=True)
        colored = highlight(text, lexer, formatter)
    except Exception:
        return ""
    return (
        "<html><head><meta charset='utf-8'></head>"
        "<body style='margin:0;background:#0f1216;color:#f8f8f2;'>"
        "<pre style='margin:0;padding:12px;font-family:\"Fira Code\",\"Consolas\",\"Courier New\",monospace;"
        "font-size:13px;line-height:1.4;white-space:pre-wrap;'>"
        f"{colored}"
        "</pre></body></html>"
    )

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Adds Preview + Save JSON buttons to the Append node's InfoCard footer.
    This version HONORS the Append UI order (switch_inputs) exactly.
    """

    def _collect_append_entries() -> list[dict]:
        sc   = getattr(card, "_graph_scene", None)
        node = getattr(card, "_node_ref", None)
        if not sc or not node:
            return []

        # Get the live NodeItem for this Append node
        try:
            append_item = sc._node_items.get(node.name)
        except Exception:
            append_item = None
        if append_item is None:
            return []

        if hasattr(sc, "describe_append_inputs"):
            try:
                entries = sc.describe_append_inputs(append_item)
            except Exception:
                entries = []
            rich = []
            for entry in entries or []:
                txt = (entry.get("text") or "").strip()
                if not txt:
                    continue
                rich.append({
                    "node": entry.get("node") or "",
                    "param": entry.get("param") or "",
                    "text": txt,
                    "is_local": bool(entry.get("is_local")),
                })
            if rich:
                return rich

        rows = []
        try:
            ordered_in_edges = sc._ordered_in_edges(append_item)
        except Exception:
            ordered_in_edges = []
        for e in ordered_in_edges:
            try:
                t = sc.resolve_text_value(e.src)
            except Exception:
                t = ""
            txt = (t or "").strip()
            if not txt:
                continue
            label = ""
            if hasattr(sc, "resolve_text_label"):
                try:
                    label = sc.resolve_text_label(e.src) or ""
                except Exception:
                    label = ""
            rows.append({
                "node": e.src.model.name,
                "param": label,
                "text": txt,
                "is_local": False,
            })
        return rows

    # ---- UI callbacks ----
    def _preview():
        entries = _collect_append_entries()
        if not entries:
            QtWidgets.QMessageBox.information(card, "Append", "No upstream text found.")
            return

        merged_text = "\n\n---\n\n".join(entry["text"] for entry in entries)

        dlg = QtWidgets.QDialog(card)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dlg.setWindowTitle("Preview (Append order)")
        dlg.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, True)
        dlg.setWindowFlag(QtCore.Qt.WindowMinMaxButtonsHint, True)
        dlg.setWindowModality(QtCore.Qt.NonModal)
        dlg.resize(780, 580)

        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        summary = QtWidgets.QLabel(f"{len(entries)} inputs · {len(merged_text)} chars total")
        summary.setStyleSheet("color:#94a3b8;")
        layout.addWidget(summary, 0)

        selector = QtWidgets.QComboBox()
        selector.addItem("Merged output", {"text": merged_text})
        for idx, entry in enumerate(entries, start=1):
            node_label = entry["node"] or "(Append)"
            if entry.get("is_local"):
                node_label = f"{node_label} (self)"
            param = entry.get("param") or ""
            label = f"{idx}. {node_label}" + (f" · {param}" if param else "")
            selector.addItem(label, dict(entry))

        copy_btn = QtWidgets.QPushButton("Copy Text")
        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(selector, 1)
        controls.addWidget(copy_btn, 0)
        layout.addLayout(controls)

        viewer = QtWidgets.QTextBrowser()
        viewer.setOpenExternalLinks(True)
        viewer.setStyleSheet("QTextBrowser{border:1px solid #3c4450;border-radius:6px;background:#0f1216;color:#e6edf3;}")
        viewer.setMinimumHeight(260)
        layout.addWidget(viewer, 1)

        table = QtWidgets.QTreeWidget()
        table.setColumnCount(4)
        table.setHeaderLabels(["#", "Node", "Parameter", "Chars"])
        table.setRootIsDecorated(False)
        table.setAlternatingRowColors(True)
        layout.addWidget(table, 1)

        for idx, entry in enumerate(entries, start=1):
            node_label = entry["node"] or "(Append)"
            if entry.get("is_local"):
                node_label = f"{node_label} (self)"
            row = QtWidgets.QTreeWidgetItem([
                str(idx),
                node_label,
                entry.get("param") or "",
                str(len(entry.get("text", ""))),
            ])
            row.setData(0, QtCore.Qt.UserRole, idx)
            row.setToolTip(1, entry.get("text", "")[:400])
            table.addTopLevelItem(row)

        syncing = {"active": False}

        def _apply_view_from_combo():
            data = selector.currentData() or {}
            text = data.get("text", "")
            html = _render_rich_html(text)
            if html:
                viewer.setHtml(html)
            else:
                viewer.setPlainText(text)
            if syncing["active"]:
                return
            syncing["active"] = True
            try:
                idx = selector.currentIndex()
                if idx <= 0:
                    table.clearSelection()
                else:
                    for i in range(table.topLevelItemCount()):
                        item = table.topLevelItem(i)
                        if item.data(0, QtCore.Qt.UserRole) == idx:
                            table.setCurrentItem(item)
                            break
            finally:
                syncing["active"] = False

        def _apply_combo_from_table(item, _prev):
            if syncing["active"] or not item:
                return
            idx = item.data(0, QtCore.Qt.UserRole)
            if not idx:
                return
            syncing["active"] = True
            try:
                selector.setCurrentIndex(int(idx))
            finally:
                syncing["active"] = False

        def _copy_current():
            data = selector.currentData() or {}
            text = data.get("text", "")
            QtWidgets.QApplication.clipboard().setText(text)

        selector.currentIndexChanged.connect(lambda _: _apply_view_from_combo())
        table.currentItemChanged.connect(_apply_combo_from_table)
        copy_btn.clicked.connect(_copy_current)
        _apply_view_from_combo()

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(dlg.close)
        bb.accepted.connect(dlg.close)
        layout.addWidget(bb, 0)

        registry = getattr(card, "_append_preview_dialogs", None)
        if registry is None:
            registry = set()
            card._append_preview_dialogs = registry
        registry.add(dlg)
        dlg.destroyed.connect(lambda *_: registry.discard(dlg))
        dlg.show()

    def _save_json():
        import json
        from pathlib import Path

        entries = _collect_append_entries()
        if not entries:
            QtWidgets.QMessageBox.warning(card, "Append", "No upstream text found.")
            return
        parts = [entry["text"] for entry in entries]

        payload = {
            "node": getattr(card, "_node_ref", None).name if hasattr(card, "_node_ref") else "append",
            "combined_text": "\n\n".join(parts),
            "parts": parts,
            "entries": entries,
        }

        # suggest next to current graph path if available
        win = card.window()
        suggested = "append_result.json"
        try:
            cur = getattr(win, "_current_path", "") or ""
            if cur:
                suggested = str(Path(cur).resolve().parent / suggested)
        except Exception:
            pass

        path, _ = QtWidgets.QFileDialog.getSaveFileName(card, "Save Combined JSON", suggested, "JSON Files (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Saved:\n{Path(path).name}", card, card.rect(), 1500)
        except Exception as e:
            QtWidgets.QMessageBox.critical(card, "Append", f"Failed to save:\n{e}")

    # ---- footer buttons ----
    b1 = QtWidgets.QPushButton("Preview Merge (Append order)")
    b1.clicked.connect(_preview)
    footer_layout.addWidget(b1)

    b2 = QtWidgets.QPushButton("Save JSON")
    b2.clicked.connect(_save_json)
    footer_layout.addWidget(b2)

    return True

APPEND_SPEC = Spec(
    stripe_color="#b45309",                    # amber-700 stripe
    augment_infocard_footer=augment_infocard_footer,
)
