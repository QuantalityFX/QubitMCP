# nodes/librarian/__init__.py
from nodes.librarian.spec import LibrarianSpec

def register():
    # Preferred registrar
    try:
        from nodes.core import register_node_kind
        register_node_kind(LibrarianSpec)
        return
    except Exception:
        pass

    # Fallback: common dict-based registries
    try:
        import nodes.core as core
        reg = getattr(core, "SPEC_BY_KIND", None) or getattr(core, "KIND_REGISTRY", None)
        if isinstance(reg, dict):
            reg["librarian"] = LibrarianSpec
    except Exception:
        pass
