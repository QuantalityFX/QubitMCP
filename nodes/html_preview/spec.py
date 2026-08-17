from nodes.core import Spec


HTML_PREVIEW_PATH_INPUT = "path"


def build_ports(node_item) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(HTML_PREVIEW_PATH_INPUT)
    try:
        setattr(node_item, "_default_named_input", HTML_PREVIEW_PATH_INPUT)
        setattr(node_item, "_show_default_input_with_named", True)
    except Exception:
        pass


HTML_PREVIEW_SPEC = Spec(
    stripe_color="#f97316",
    build_ports=build_ports,
)
