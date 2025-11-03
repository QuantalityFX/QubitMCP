# loader.py
"""
Central plugin bootstrapper for EchoGraph.
Call bootstrap_plugins() after nodes/ is on sys.path and nodes.core is imported.
"""

from __future__ import annotations
import importlib

# Expect nodes.core to be importable by the time this module is used.
from nodes import core


def _safe_probe(kind: str):
    """Small helper to print spec details safely."""
    try:
        spec = core.get_spec(kind)
        stripe = getattr(spec, "stripe_color", None)
        has_hook = bool(getattr(spec, "augment_infocard_footer", None))
        print(f"[EchoGraph] {kind} spec: {type(spec).__name__} stripe: {stripe} has_hook: {has_hook}")
    except Exception as e:
        print(f"[EchoGraph] core.get_spec('{kind}') failed:", e)


def bootstrap_plugins():
    """Register core defaults and optional node plugins (e.g., Librarian)."""

    # 1) Core defaults (single source of truth)
    try:
        core.register_defaults()
    except Exception as e:
        print("[EchoGraph] register_defaults failed:", e)

    # 2) Librarian
    try:
        from nodes import librarian
        if hasattr(librarian, "register"):
            librarian.register()
            _safe_probe("librarian")
        else:
            print("[EchoGraph] Librarian module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Librarian plugin import failed:", e)

    # 3) Note
    try:
        from nodes import note
        if hasattr(note, "register"):
            note.register()
            _safe_probe("note")
        else:
            print("[EchoGraph] Note module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Note plugin import failed:", e)

    # 4) Python
    try:
        from nodes import python as python_node
        if hasattr(python_node, "register"):
            python_node.register()
            _safe_probe("python")
        else:
            print("[EchoGraph] Python module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Python plugin import failed:", e)

    # 5) Output
    try:
        from nodes import output
        if hasattr(output, "register"):
            output.register()
            _safe_probe("output")
        else:
            print("[EchoGraph] Output module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Output plugin import failed:", e)

    # 6) Append (use importlib to guarantee the submodule gets loaded)
    try:
        append_node = importlib.import_module("nodes.append")
        print("[EchoGraph] append module:", getattr(append_node, "__file__", "<no __file__>"))
        if hasattr(append_node, "register"):
            append_node.register()
            _safe_probe("append")
        else:
            print("[EchoGraph] Append module missing 'register' (got:", dir(append_node), ")")
    except Exception as e:
        print("[EchoGraph] Append plugin import failed:", e)

    # 7) Switch
    try:
        from nodes import switch as switch_node
        if hasattr(switch_node, "register"):
            switch_node.register()
            _safe_probe("switch")
        else:
            print("[EchoGraph] Switch module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Switch plugin import failed:", e)
