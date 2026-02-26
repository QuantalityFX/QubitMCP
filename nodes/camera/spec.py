from __future__ import annotations

from nodes.core import Spec


def _remove_param(node_item, name: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        return
    key = (name or "").strip().lower()
    out = []
    changed = False
    for entry in params:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        if (entry.get("name") or "").strip().lower() == key:
            changed = True
            continue
        out.append(entry)
    if changed:
        model.params = out


def _ensure_param(node_item, name: str, default: str) -> None:
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
    key = (name or "").strip().lower()
    for entry in params:
        if not isinstance(entry, dict):
            continue
        if (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(node_item, names: list[str]) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    entry = None
    for p in params:
        if (p.get("name") or "").strip().lower() == store_key:
            entry = p
            break
    if entry is None:
        entry = {"name": store_key, "value": ""}
        params.append(entry)
    raw = str(entry.get("value") or "")
    hidden = {tok.strip().lower() for tok in raw.split(",") if tok.strip()}
    for name in names:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    model.params = params


def build_ports(node_item) -> None:
    _ensure_param(node_item, "fov", "60")
    _ensure_param(node_item, "aspect_width", "1920")
    _ensure_param(node_item, "aspect_height", "1080")
    # Camera transforms live on the Scene node outliner, not on the camera node.
    _remove_param(node_item, "pos")
    _remove_param(node_item, "rot")
    _remove_param(node_item, "scl")
    _remove_param(node_item, "near")
    _remove_param(node_item, "far")
    _ensure_hidden_params(node_item, ["pos", "rot", "scl", "near", "far"])


CAMERA_SPEC = Spec(
    stripe_color="#f59e0b",
    build_ports=build_ports,
)
