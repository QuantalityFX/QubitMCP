# nodes/librarian/__init__.py
from .spec import LibrarianSpec

def register():
    """
    Called by EchoGraph’s plugin bootstrap:
      - imports nodes.librarian
      - looks for callable 'register'
      - runs it
    This must register the spec into nodes.core.
    """
    try:
        import nodes.core as core
    except Exception as e:
        print("[Librarian] core import failed:", e)
        return

    spec = LibrarianSpec()

    # Try common registry APIs, fall back to dict injection if present.
    for fn_name in ("register", "register_kind", "register_spec", "add_spec"):
        fn = getattr(core, fn_name, None)
        if callable(fn):
            try:
                # prefer (kind, spec)
                fn("librarian", spec)
            except TypeError:
                # some registries accept just the spec with spec.kind attribute
                fn(spec)
            print("[Librarian] registered via core.%s" % fn_name)
            return

    # Last-ditch fallback if core exposes a dict
    try:
        reg = getattr(core, "_REGISTRY", None) or getattr(core, "REGISTRY", None) or getattr(core, "_registry", None)
        if isinstance(reg, dict):
            reg["librarian"] = spec
            print("[Librarian] registered via registry dict fallback")
            return
    except Exception:
        pass

    print("[Librarian] failed to register: no known registry API found")
