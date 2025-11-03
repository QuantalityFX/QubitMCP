# nodes/append/spec.py
from nodes.core import Spec

# Qt import that works in both PySide6 and PySide2
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Adds Preview + Save JSON buttons to the Append node's InfoCard footer.
    Returns True if widgets were added.
    """
    # ---- helper lives INSIDE the augment, so no globals leak/NameError ----
    def _collect_upstream_texts() -> list:
        sc   = getattr(card, "_graph_scene", None)
        node = getattr(card, "_node_ref", None)
        if not sc or not node:
            return []

        # broaden keys; we’ll also fall back to node.info
        KEYS = {"text","body","content","prompt","note","message","desc","description","value"}

        seen = set()
        out  = []

        def grab_from_item(item) -> str:
            # 1) host helper (e.g., Prompt nodes)
            try:
                txt = sc.resolve_text_value(item)
                if txt:
                    return txt.strip()
            except Exception:
                pass

            # 2) params by common names
            try:
                for p in (getattr(item.model, "params", None) or []):
                    nm = (p.get("name", "") or "").strip().lower()
                    if nm in KEYS:
                        val = (p.get("value", "") or "").strip()
                        if val:
                            return val
            except Exception:
                pass

            # 3) fallback to node.info
            try:
                info = (getattr(item.model, "info", "") or "").strip()
                if info:
                    return info
            except Exception:
                pass
            return ""

        # DFS over ALL upstream nodes so chains also work
        try:
            stack = list(sc.upstream_of(node.name))
        except Exception:
            stack = []

        while stack:
            it = stack.pop()
            if it in seen:
                continue
            seen.add(it)

            txt = grab_from_item(it)
            if txt:
                out.append(txt)

            try:
                stack.extend(sc.upstream_of(it.model.name))
            except Exception:
                pass

        return out

    # ---- UI callbacks ----
    def _preview():
        parts = _collect_upstream_texts()
        QtWidgets.QMessageBox.information(
            card, "Preview (Append)",
            "No upstream text found." if not parts else "\n\n---\n\n".join(parts)[:5000]
        )

    def _save_json():
        import json
        from pathlib import Path
        parts = _collect_upstream_texts()
        if not parts:
            QtWidgets.QMessageBox.warning(card, "Append", "No upstream text found.")
            return
        payload = {
            "node": getattr(card, "_node_ref", None).name if hasattr(card, "_node_ref") else "append",
            "combined_text": "\n\n".join(parts),
            "parts": parts,
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

    # ---- actual footer buttons ----
    b1 = QtWidgets.QPushButton("Preview Merge")
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
