from __future__ import annotations

from nodes.core import Spec


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
        setattr(model, "params", params)
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _remove_param(node_item, name: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        return
    key = name.strip().lower()
    filtered = [entry for entry in params if (entry.get("name") or "").strip().lower() != key]
    if len(filtered) != len(params):
        try:
            setattr(model, "params", filtered)
        except Exception:
            pass


def build_ports(node_item) -> None:
    _ensure_param(node_item, "count", "3")
    _ensure_param(node_item, "prefix", "")
    _remove_param(node_item, "path")
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


INSTANCE_SPEC = Spec(
    stripe_color="#ef4444",
    build_ports=build_ports,
)
