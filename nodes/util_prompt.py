"""Consumer-side selection of Note values; the Note output remains unfiltered."""
from __future__ import annotations


def note_prompt_values(model) -> dict[str, str]:
    checked = getattr(model, "_completed_params", ())
    if isinstance(checked, str):
        checked = (checked,)
    if not isinstance(checked, (set, list, tuple)):
        checked = ()
    checked = {str(name) for name in checked}
    params = [entry for entry in (getattr(model, "params", None) or []) if isinstance(entry, dict)]
    hidden_text = next((entry.get("value", "") for entry in params if entry.get("name") == "__ui_hidden_params"), "")
    hidden = {name.strip() for name in str(hidden_text or "").split(",") if name.strip()}
    params = [entry for entry in params if entry.get("name")
              and not str(entry["name"]).startswith("__") and str(entry["name"]) not in hidden]
    values = {}
    for entry in params:
        name = str(entry["name"])
        raw = entry.get("value")
        text = "" if raw is None else str(raw).strip()
        if text and (len(params) == 1 or name in checked):
            values[name] = text
    return values


def _param(model, name):
    return next((str(entry.get("value") or "") for entry in (getattr(model, "params", None) or [])
                 if isinstance(entry, dict) and entry.get("name") == name), "")


def prompt_text(scene, source, *, source_port="", preferred_params=()) -> str:
    model = source.model
    kind = str(model.kind or "").strip().lower()
    if kind == "note":
        values = note_prompt_values(model)
        names = {entry.get("name") for entry in model.params if isinstance(entry, dict)}
        if source_port and source_port in names:
            return values.get(source_port, "")
        return "\n\n".join(values.values())
    # Retain direct path/text-output contracts for consumers that already used
    # them, while Append/Switch must preserve the provenance of upstream Notes.
    if kind not in {"append", "switch"}:
        for name in (source_port, *preferred_params):
            if name:
                text = _param(model, name)
                if text.strip():
                    return text
    flatten = getattr(scene, "_flatten_text_segments", None)
    if callable(flatten):
        texts = []
        for segment in flatten(source):
            owner = getattr(scene, "_nodes_by_name", {}).get(segment.get("node"))
            if owner is None:
                owner = getattr(scene, "_node_items", {}).get(segment.get("node"))
            owner = getattr(owner, "model", owner)
            if owner is not None and str(owner.kind or "").strip().lower() == "note":
                text = note_prompt_values(owner).get(segment.get("param"), "")
            else:
                text = segment.get("text", "")
            if text:
                texts.append(text)
        return "\n\n".join(texts)
    return str(scene.resolve_text_value(source) or "")


def connected_prompt(item, *, preferred_params=()) -> tuple[bool, str]:
    """Distinguish an empty connected selection from a disconnected prompt."""
    scene = item.scene()
    if scene is None:
        return False, ""
    edges = scene._ordered_in_edges(item) if hasattr(scene, "_ordered_in_edges") else scene._in_edges(item)
    connected, texts = False, []
    for edge in edges:
        port = (getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None)
                or getattr(edge, "dst_name", None) or "")
        if str(port).strip().lower() != "prompt":
            continue
        connected = True
        text = prompt_text(scene, edge.src, source_port=getattr(edge, "src_port_name", "") or "",
                           preferred_params=preferred_params)
        if text.strip():
            texts.append(text.strip())
    return connected, "\n\n".join(texts)
