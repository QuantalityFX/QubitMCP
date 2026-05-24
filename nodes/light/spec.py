from __future__ import annotations

from nodes.core import Spec


LIGHT_TYPES = ("directional", "point", "spot", "area")
LIGHT_UI_CONTROLLED_HIDDEN_PARAMS = {
    "pos",
    "rot",
    "scl",
    "range",
    "shadow_range",
    "shadow_fov",
    "shadow_near",
}
LIGHT_ALWAYS_HIDDEN_PARAMS = {"pos", "rot", "scl", "shadow_near"}
_LIGHT_TYPE_ALIASES = {
    "dir": "directional",
    "directional": "directional",
    "directional_light": "directional",
    "directional light": "directional",
    "point": "point",
    "point_light": "point",
    "point light": "point",
    "spot": "spot",
    "spot_light": "spot",
    "spot light": "spot",
    "spotlight": "spot",
    "area": "area",
    "area_light": "area",
    "area light": "area",
}


def normalize_light_type(value) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    text = " ".join(text.replace("_", " ").split())
    return _LIGHT_TYPE_ALIASES.get(text, _LIGHT_TYPE_ALIASES.get(text.replace(" ", "_"), "directional"))


def _param_value(node_item, name: str) -> str:
    model = getattr(node_item, "model", None)
    if model is None:
        return ""
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _default_type_for_kind(kind: str) -> str:
    return {
        "point_light": "point",
        "point light": "point",
        "spot_light": "spot",
        "spot light": "spot",
        "area_light": "area",
        "area light": "area",
    }.get((kind or "").strip().lower(), "directional")


def light_hidden_params(light_type: str) -> set[str]:
    kind = normalize_light_type(light_type)
    hidden = set(LIGHT_ALWAYS_HIDDEN_PARAMS)
    if kind == "directional":
        hidden.update({"range", "shadow_range", "shadow_fov"})
    elif kind == "point":
        hidden.update({"shadow_fov"})
    elif kind == "area":
        hidden.update({"shadow_fov"})
    return hidden


def sync_light_hidden_params(node_item, light_type: str | None = None) -> bool:
    model = getattr(node_item, "model", None)
    if model is None:
        return False
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    entry = None
    for p in params:
        if not isinstance(p, dict):
            continue
        if (p.get("name") or "").strip().lower() == store_key:
            entry = p
            break
    if entry is None:
        entry = {"name": store_key, "value": ""}
        params.append(entry)

    if light_type is None:
        kind = str(getattr(model, "kind", "") or "").strip().lower()
        light_type = _param_value(node_item, "type") or _param_value(node_item, "light_type") or _default_type_for_kind(kind)

    raw = str(entry.get("value") or "")
    hidden = {tok.strip().lower() for tok in raw.split(",") if tok.strip()}
    hidden.difference_update(LIGHT_UI_CONTROLLED_HIDDEN_PARAMS)
    hidden.update(light_hidden_params(light_type))
    new_value = ",".join(sorted(hidden))
    changed = new_value != raw
    entry["value"] = new_value
    model.params = params
    return changed


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
    kind = str(getattr(getattr(node_item, "model", None), "kind", "") or "").strip().lower()
    default_type = _default_type_for_kind(kind)
    _ensure_param(node_item, "type", default_type)
    _ensure_param(node_item, "intensity", "1.0")
    _ensure_param(node_item, "range", "300")
    _ensure_param(node_item, "shadow_strength", "1.0")
    _ensure_param(node_item, "shadow_range", "0")
    _ensure_param(node_item, "shadow_fov", "0")
    _ensure_param(node_item, "shadow_near", "0")
    _ensure_param(node_item, "shadow_bias", "0")
    # Light transforms live on the Scene node outliner.
    _remove_param(node_item, "pos")
    _remove_param(node_item, "rot")
    _remove_param(node_item, "scl")
    # shadow_near is kept for old graphs, but the renderer now chooses a tiny safe near clip automatically.
    sync_light_hidden_params(node_item)


LIGHT_SPEC = Spec(
    stripe_color="#facc15",
    build_ports=build_ports,
)
