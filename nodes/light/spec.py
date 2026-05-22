from __future__ import annotations

from nodes.core import Spec


LIGHT_TYPES = ("directional", "point", "spot", "area")
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
    _ensure_param(node_item, "type", "directional")
    _ensure_param(node_item, "intensity", "1.0")
    _ensure_param(node_item, "shadow_strength", "1.0")
    # Light transforms live on the Scene node outliner.
    _remove_param(node_item, "pos")
    _remove_param(node_item, "rot")
    _remove_param(node_item, "scl")
    _ensure_hidden_params(node_item, ["pos", "rot", "scl"])


LIGHT_SPEC = Spec(
    stripe_color="#facc15",
    build_ports=build_ports,
)
