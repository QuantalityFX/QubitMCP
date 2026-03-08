from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    try:
        from PySide2 import QtWidgets, QtCore  # type: ignore
    except Exception:
        QtWidgets = None  # type: ignore
        QtCore = None  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

_PASS_THROUGH_KINDS = {
    "wire",
    "switch",
    "uv_unwrap",
    "texture",
    "texture_pro",
    "texture_layer",
    "split_volume",
    "volume_selector",
    "transforms",
    "fx",
    "fx_trail",
}

_PLY_SCALAR_TYPES = {
    "char": "b",
    "int8": "b",
    "uchar": "B",
    "uint8": "B",
    "short": "h",
    "int16": "h",
    "ushort": "H",
    "uint16": "H",
    "int": "i",
    "int32": "i",
    "uint": "I",
    "uint32": "I",
    "float": "f",
    "float32": "f",
    "double": "d",
    "float64": "d",
}

_WIRE_CACHE_VERSION = "wire-v2"


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "wire"


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


def _bool_param(raw: str, default: bool = True) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _normalize_order(raw: str) -> str:
    mode = str(raw or "").strip().lower()
    aliases = {
        "pid": "id",
        "point": "id",
        "point_id": "id",
        "index": "id",
        "row": "file",
        "prob": "probability",
        "hamming_weight": "hamming",
    }
    mode = aliases.get(mode, mode)
    if mode in {"id", "file", "x", "y", "z", "probability", "hamming"}:
        return mode
    return "id"


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

    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    existing = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            existing = entry
            break
    if existing is None:
        existing = {"name": store_key, "value": ""}
        params.append(existing)

    raw = existing.get("value", "")
    hidden = {part.strip().lower() for part in str(raw).split(",") if part.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(hidden))
    model.params = params


def _ordered_in_edges(scene, item) -> list:
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _pick_input_edge(scene, item, wanted_ports=None):
    edges = _ordered_in_edges(scene, item)
    if not edges:
        return None
    if wanted_ports:
        wanted = {str(p).strip().lower() for p in wanted_ports if str(p).strip()}
        for edge in edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in wanted:
                return edge
    return edges[0]


def _resolve_wire_source(node_item) -> str:
    scene = node_item.scene()
    model = getattr(node_item, "model", None)
    if scene is None:
        return _param_value(model, "mesh") or _param_value(model, "source") or _param_value(model, "path")

    edge = _pick_input_edge(scene, node_item, wanted_ports=("mesh", "path", "source"))
    if edge is None:
        return _param_value(model, "mesh") or _param_value(model, "source") or _param_value(model, "path")

    item = getattr(edge, "src", None)
    visited = set()
    depth = 0
    while item is not None and id(item) not in visited and depth < 16:
        visited.add(id(item))
        depth += 1
        up_model = getattr(item, "model", None)
        if up_model is None:
            break
        kind = (getattr(up_model, "kind", "") or "").strip().lower()

        if kind == "transforms":
            src = _param_value(up_model, "source")
            if src:
                return src

        path = _param_value(up_model, "path") or _param_value(up_model, "mesh") or _param_value(up_model, "source")
        if kind not in _PASS_THROUGH_KINDS:
            return path

        up_edge = _pick_input_edge(scene, item, wanted_ports=("mesh", "path", "source"))
        if up_edge is None:
            up_edge = _pick_input_edge(scene, item, wanted_ports=None)
        if up_edge is None:
            return path
        item = getattr(up_edge, "src", None)

    return ""


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _json_payload(json_path: Path) -> dict:
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _state_key(raw) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    try:
        if text.startswith("0x"):
            return f"0x{int(text, 16):x}"
        return f"0x{int(text, 0):x}"
    except Exception:
        return text


def _same_point(a, b, eps: float = 1e-8) -> bool:
    try:
        return (
            abs(float(a[0]) - float(b[0])) <= eps
            and abs(float(a[1]) - float(b[1])) <= eps
            and abs(float(a[2]) - float(b[2])) <= eps
        )
    except Exception:
        return False


def _open_polyline(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    out = [(float(x), float(y), float(z)) for x, y, z in (points or [])]
    if len(out) >= 3 and _same_point(out[0], out[-1]):
        out = out[:-1]
    return out


def _find_sidecar_json_for_ply(ply_path: Path) -> Path | None:
    stem = ply_path.stem
    candidates = []
    if stem.endswith("_3dgs"):
        base = stem[:-5]
        candidates.append(ply_path.with_name(f"{base}_houdini.json"))
        candidates.append(ply_path.with_name(f"{base}.json"))
    candidates.append(ply_path.with_suffix(".json"))
    candidates.append(ply_path.with_name(f"{stem}_houdini.json"))

    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return None


def _json_rows_from_payload(payload: dict) -> list[dict]:
    points = payload.get("points")
    if not isinstance(points, list):
        return []

    out = []
    for p in points:
        if not isinstance(p, dict):
            continue
        x = _safe_float(p.get("x"))
        y = _safe_float(p.get("y"))
        z = _safe_float(p.get("z"))
        if x is None or y is None or z is None:
            continue
        out.append(
            {
                "x": float(x),
                "y": float(y),
                "z": float(z),
                "point_id": _safe_int(p.get("point_id")),
                "probability": _safe_float(p.get("probability")),
                "hamming_weight": _safe_float(p.get("hamming_weight")),
                "hex_state": _state_key(p.get("hex_state")),
                "decimal_state": _safe_int(p.get("decimal_state")),
            }
        )
        if not out[-1]["hex_state"] and out[-1]["decimal_state"] is not None:
            out[-1]["hex_state"] = f"0x{int(out[-1]['decimal_state']):x}"
    return out


def _json_rows(json_path: Path) -> list[dict]:
    return _json_rows_from_payload(_json_payload(json_path))


def _ordered_points_from_rows(rows: list[dict], mode: str) -> list[tuple[float, float, float]]:
    if len(rows) < 2:
        return []

    mode = _normalize_order(mode)
    if mode == "id":
        if all(r.get("point_id") is not None for r in rows):
            rows = sorted(rows, key=lambda r: int(r.get("point_id") or 0))
        else:
            return []
    elif mode in {"probability", "y"}:
        if any(r.get("probability") is not None for r in rows):
            rows = sorted(rows, key=lambda r: float("inf") if r.get("probability") is None else float(r.get("probability")))
        else:
            rows = sorted(rows, key=lambda r: float(r["y"]))
    elif mode in {"hamming", "z"}:
        if any(r.get("hamming_weight") is not None for r in rows):
            rows = sorted(rows, key=lambda r: float("inf") if r.get("hamming_weight") is None else float(r.get("hamming_weight")))
        else:
            rows = sorted(rows, key=lambda r: float(r["z"]))
    elif mode == "x":
        rows = sorted(rows, key=lambda r: float(r["x"]))
    elif mode == "file":
        pass

    return [(float(r["x"]), float(r["y"]), float(r["z"])) for r in rows]


def _ordered_points_from_json(json_path: Path, mode: str) -> list[tuple[float, float, float]]:
    return _ordered_points_from_rows(_json_rows(json_path), mode)


def _json_key_sequences(payload: dict) -> list[list[str]]:
    result_payload = payload.get("result_payload")
    if not isinstance(result_payload, dict):
        return []

    raw_key = result_payload.get("Key")
    if not isinstance(raw_key, list):
        return []
    if raw_key and not isinstance(raw_key[0], (list, tuple)):
        raw_key = [raw_key]

    out = []
    for seq in raw_key:
        if not isinstance(seq, (list, tuple)):
            continue
        states = []
        for token in seq:
            key = _state_key(token)
            if key:
                states.append(key)
        if states:
            out.append(states)
    return out


def _ordered_polylines_from_json(json_path: Path, mode: str) -> list[list[tuple[float, float, float]]]:
    payload = _json_payload(json_path)
    rows = _json_rows_from_payload(payload)
    if len(rows) < 2:
        return []

    mode = _normalize_order(mode)
    if mode == "id":
        key_seqs = _json_key_sequences(payload)
        if key_seqs:
            by_state = {}
            for row in rows:
                key = str(row.get("hex_state") or "").strip().lower()
                if key and key not in by_state:
                    by_state[key] = row

            if by_state:
                polylines = []
                used_states = set()
                for seq in key_seqs:
                    poly = []
                    for state in seq:
                        row = by_state.get(state)
                        if row is None:
                            continue
                        point = (float(row["x"]), float(row["y"]), float(row["z"]))
                        if poly and _same_point(poly[-1], point):
                            continue
                        poly.append(point)
                        used_states.add(state)
                    poly = _open_polyline(poly)
                    if len(poly) >= 2:
                        polylines.append(poly)

                # Keep legacy full-point behavior unless JSON explicitly carries
                # multiple paths, or the key coverage spans all known states.
                if polylines and (len(polylines) > 1 or len(used_states) >= len(rows)):
                    return polylines

    points = _ordered_points_from_rows(rows, mode)
    points = _open_polyline(points)
    if len(points) < 2:
        return []
    return [points]


def _x_looks_like_point_id(points: list[tuple[float, float, float]]) -> bool:
    seen = set()
    for x, _y, _z in points:
        xi = int(round(x))
        if abs(float(x) - float(xi)) > 1e-4:
            return False
        if xi in seen:
            return False
        seen.add(xi)
    return len(seen) == len(points)


def _order_points_from_ply(points: list[tuple[float, float, float]], mode: str) -> list[tuple[float, float, float]]:
    mode = _normalize_order(mode)
    if mode == "file":
        return list(points)
    if mode == "id":
        if _x_looks_like_point_id(points):
            return sorted(points, key=lambda p: int(round(float(p[0]))))
        return list(points)
    if mode == "x":
        return sorted(points, key=lambda p: float(p[0]))
    if mode in {"probability", "y"}:
        return sorted(points, key=lambda p: float(p[1]))
    if mode in {"hamming", "z"}:
        return sorted(points, key=lambda p: float(p[2]))
    return list(points)


def _read_ply_header(path: Path):
    fmt = ""
    vertex_count = 0
    vertex_props = []
    current_element = ""
    header_end = 0

    with path.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            parts = text.split()
            if not parts:
                continue

            head = parts[0].lower()
            if head == "format" and len(parts) >= 2:
                fmt = parts[1].strip().lower()
            elif head == "element" and len(parts) >= 3:
                current_element = parts[1].strip().lower()
                if current_element == "vertex":
                    try:
                        vertex_count = int(parts[2])
                    except Exception:
                        vertex_count = 0
                    vertex_props = []
            elif head == "property" and current_element == "vertex":
                if len(parts) >= 3 and parts[1].strip().lower() != "list":
                    ptype = parts[1].strip().lower()
                    pname = parts[2].strip()
                    vertex_props.append((ptype, pname))
                else:
                    raise RuntimeError("PLY vertex list properties are not supported for wire extraction.")
            elif head == "end_header":
                header_end = f.tell()
                break

    return fmt, int(vertex_count), list(vertex_props), int(header_end)


def _load_points_from_ply(path: Path) -> list[tuple[float, float, float]]:
    fmt, vertex_count, props, header_end = _read_ply_header(path)
    if vertex_count <= 0 or not props:
        return []

    names = [name for _ptype, name in props]
    if "x" not in names or "y" not in names or "z" not in names:
        return []
    ix = names.index("x")
    iy = names.index("y")
    iz = names.index("z")

    if fmt == "ascii":
        points = []
        with path.open("rb") as f:
            f.seek(header_end)
            for _ in range(vertex_count):
                line = f.readline()
                if not line:
                    break
                parts = line.decode("utf-8", errors="replace").strip().split()
                if len(parts) <= max(ix, iy, iz):
                    continue
                try:
                    points.append((float(parts[ix]), float(parts[iy]), float(parts[iz])))
                except Exception:
                    continue
        return points

    if fmt not in {"binary_little_endian", "binary_big_endian"}:
        return []
    endian = "<" if fmt == "binary_little_endian" else ">"

    row_codes = []
    for ptype, _name in props:
        code = _PLY_SCALAR_TYPES.get(str(ptype).strip().lower())
        if not code:
            return []
        row_codes.append(code)
    row_struct = struct.Struct(endian + "".join(row_codes))

    points = []
    with path.open("rb") as f:
        f.seek(header_end)
        for _ in range(vertex_count):
            raw = f.read(row_struct.size)
            if len(raw) < row_struct.size:
                break
            vals = row_struct.unpack(raw)
            try:
                points.append((float(vals[ix]), float(vals[iy]), float(vals[iz])))
            except Exception:
                continue
    return points


def _signature_for_wire_cache(source_path: Path, mode: str, json_path: Path | None) -> str:
    mode = _normalize_order(mode)
    parts = [_WIRE_CACHE_VERSION, str(source_path.resolve()), mode]
    try:
        st = source_path.stat()
        parts.extend([str(int(st.st_size)), str(int(st.st_mtime_ns))])
    except Exception:
        parts.extend(["0", "0"])

    if json_path is not None:
        parts.append(str(json_path.resolve()))
        try:
            st = json_path.stat()
            parts.extend([str(int(st.st_size)), str(int(st.st_mtime_ns))])
        except Exception:
            parts.extend(["0", "0"])
    else:
        parts.extend(["", "0", "0"])

    return "|".join(parts)


def _write_wire_obj(
    path: Path,
    polylines: list[list[tuple[float, float, float]]],
    source_path: Path,
    mode: str,
    json_path: Path | None,
) -> None:
    clean_polylines = []
    total_points = 0
    for poly in polylines or []:
        opened = _open_polyline(poly)
        if len(opened) < 2:
            continue
        clean_polylines.append(opened)
        total_points += len(opened)
    if total_points < 2 or not clean_polylines:
        raise RuntimeError("Wire needs at least 2 points.")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# EchoGraph wire path\n")
        f.write(f"# source={source_path}\n")
        f.write(f"# mode={_normalize_order(mode)}\n")
        if json_path is not None:
            f.write(f"# json={json_path}\n")
        f.write(f"# curves={len(clean_polylines)}\n")
        for poly in clean_polylines:
            for x, y, z in poly:
                f.write(f"v {float(x):.9g} {float(y):.9g} {float(z):.9g}\n")

        first_idx = 1
        chunk = 2048
        for poly in clean_polylines:
            count = int(len(poly))
            start = 0
            while start < (count - 1):
                end = min(start + chunk, count)
                if (end - start) < 2:
                    break
                indices = " ".join(str(first_idx + i) for i in range(start, end))
                f.write(f"l {indices}\n")
                start = end - 1
            first_idx += count


def _build_wire_obj_from_ply(source_path: str, mode: str = "id") -> tuple[str, str]:
    src = Path(str(source_path or "").strip())
    if not src.exists():
        return "", "Input path not found."
    if src.suffix.lower() != ".ply":
        return "", "Wire supports .ply inputs."

    mode = _normalize_order(mode)
    wants_json = mode in {"id", "probability", "hamming"}
    json_path = _find_sidecar_json_for_ply(src) if wants_json else None

    sig = _signature_for_wire_cache(src, mode, json_path)
    digest = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / "EchoGraph" / "wire_cache"
    out_path = out_dir / f"{src.stem}__wire__{digest}.obj"
    if out_path.exists():
        return str(out_path), "cached"

    polylines = []
    used_json = False
    if json_path is not None:
        polylines = _ordered_polylines_from_json(json_path, mode)
        used_json = bool(polylines)

    if not polylines:
        ply_points = _load_points_from_ply(src)
        if len(ply_points) < 2:
            return "", "PLY has fewer than 2 points."
        points = _open_polyline(_order_points_from_ply(ply_points, mode))
        if len(points) >= 2:
            polylines = [points]

    total_points = sum(len(poly) for poly in polylines)
    if total_points < 2:
        return "", "Wire needs at least 2 points."

    try:
        _write_wire_obj(out_path, polylines, src, mode, json_path if used_json else None)
    except Exception as exc:
        return "", f"Failed writing wire OBJ: {exc}"

    detail = f"{total_points} pts / {len(polylines)} curve(s) via {'json' if used_json else 'ply'}"
    return str(out_path), detail


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    scene_path = getattr(scene, "_filename", None) if scene is not None else None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass

    workflow_path = workflow_path or scene_path
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _wire_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "wire"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resolve_window(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                return views[0].window()
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    if QtWidgets is not None:
        try:
            aw = QtWidgets.QApplication.activeWindow()
            if aw is not None and aw.isWindow():
                return aw
        except Exception:
            pass
    return None


def _norm_path_key(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(text))
    except Exception:
        return text.lower()


def _force_scene_wire_white(win, wire_paths) -> None:
    targets = {_norm_path_key(p) for p in (wire_paths or []) if str(p or "").strip()}
    if not targets or win is None:
        return
    try:
        glv = getattr(win, "gl_view", None)
    except Exception:
        glv = None
    if glv is None:
        return
    renderer = getattr(glv, "_mgl_renderer", None) or glv
    scene_obj = getattr(renderer, "_mgl_scene", None)
    if scene_obj is None:
        return

    items = []
    iter_by_tag = getattr(scene_obj, "iter_by_tag", None)
    if callable(iter_by_tag):
        try:
            items = list(iter_by_tag("scene-wire"))
        except Exception:
            items = []
    if not items:
        try:
            items = [it for it in scene_obj.items() if str(getattr(it, "tag", "") or "") == "scene-wire"]
        except Exception:
            items = []

    changed = False
    for item in items:
        payload = getattr(item, "payload", None)
        if not isinstance(payload, dict):
            continue
        key = _norm_path_key(payload.get("path"))
        if key not in targets:
            continue
        payload["color"] = (1.0, 1.0, 1.0, 1.0)
        changed = True

    if changed:
        try:
            renderer.update()
        except Exception:
            try:
                glv.update()
            except Exception:
                pass


def _set_node_param_value(node_item, name: str, value: str, *, notify_scene: bool = False) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    key = (name or "").strip().lower()
    try:
        cur = _param_value(model, key)
    except Exception:
        cur = ""
    if str(cur or "") == str(value or ""):
        return
    if hasattr(node_item, "_set_param_value"):
        try:
            node_item._set_param_value(name, str(value or ""), rebuild=False, notify_scene=notify_scene)
            return
        except Exception:
            pass
    params = list(getattr(model, "params", None) or [])
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value or "")
            break
    else:
        params.append({"name": name, "value": str(value or "")})
    try:
        model.params = params
    except Exception:
        pass


def _refresh_wire_params(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return

    enabled = _bool_param(_param_value(model, "enabled"), default=True)
    order_mode = _normalize_order(_param_value(model, "order") or "id")
    _set_node_param_value(node_item, "order", order_mode, notify_scene=False)

    src_path = (_resolve_wire_source(node_item) or "").strip()
    _set_node_param_value(node_item, "source", src_path, notify_scene=False)

    if (not enabled) or (not src_path):
        _set_node_param_value(node_item, "path", "", notify_scene=False)
        return

    out_path, _detail = _build_wire_obj_from_ply(src_path, mode=order_mode)
    if not out_path:
        _set_node_param_value(node_item, "path", "", notify_scene=False)
        return
    _set_node_param_value(node_item, "path", str(out_path), notify_scene=False)


if QtWidgets is not None and QtCore is not None:
    class WireWidget(QtWidgets.QWidget):
        def __init__(self, node_item, parent=None):
            super().__init__(parent)
            self._node_item = node_item
            self._scene = None
            self._scene_connected = False
            self._pending = False

            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(4)

            self._status = QtWidgets.QLabel("")
            self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
            self._status.setMinimumWidth(0)
            self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            layout.addWidget(self._status, 1)

            self._view_btn = QtWidgets.QPushButton("View")
            self._view_btn.setFixedWidth(64)
            self._view_btn.setStyleSheet(
                "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
                "QPushButton:hover{background:#1d4ed8;}"
                "QPushButton:disabled{background:#334155;color:#94a3b8;}"
            )
            self._view_btn.clicked.connect(self._on_view_clicked)
            layout.addWidget(self._view_btn, 0)

            self._ensure_scene()
            QtCore.QTimer.singleShot(0, self._update_wire)

        def sizeHint(self):
            return QtCore.QSize(220, 32)

        def _ensure_scene(self):
            if self._scene is None:
                self._scene = self._node_item.scene()
            if self._scene is None or self._scene_connected:
                return
            if hasattr(self._scene, "linksChanged"):
                try:
                    self._scene.linksChanged.connect(self._schedule_update)
                except Exception:
                    pass
            if hasattr(self._scene, "paramChanged"):
                try:
                    self._scene.paramChanged.connect(self._on_scene_param_changed)
                except Exception:
                    pass
            self._scene_connected = True

        def _on_scene_param_changed(self, name=None, _params=None):
            if _param_change_relevant(self._node_item, name):
                self._schedule_update()

        def _schedule_update(self):
            if self._pending:
                return
            self._pending = True
            QtCore.QTimer.singleShot(60, self._update_wire)

        def _set_param(self, name: str, value: str, notify_scene: bool = True):
            try:
                current = ""
                for p in (getattr(self._node_item.model, "params", None) or []):
                    if (p.get("name") or "").strip().lower() == (name or "").strip().lower():
                        current = p.get("value", "") or ""
                        break
                if current == value:
                    return
            except Exception:
                pass
            try:
                self._node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
            except Exception:
                pass

        def _update_wire(self):
            self._pending = False
            model = getattr(self._node_item, "model", None)
            enabled = _bool_param(_param_value(model, "enabled"), default=True)
            order_mode = _normalize_order(_param_value(model, "order") or "id")
            self._set_param("order", order_mode, notify_scene=False)

            src_path = (_resolve_wire_source(self._node_item) or "").strip()
            self._set_param("source", src_path, notify_scene=False)

            if not enabled:
                self._status.setText("Disabled.")
                self._view_btn.setEnabled(False)
                self._set_param("path", "", notify_scene=True)
                return

            if not src_path:
                self._status.setText("No input mesh.")
                self._view_btn.setEnabled(False)
                self._set_param("path", "", notify_scene=True)
                return

            if not os.path.exists(src_path):
                self._status.setText("Input not found.")
                self._view_btn.setEnabled(False)
                self._set_param("path", "", notify_scene=True)
                return

            ext = Path(src_path).suffix.lower()
            if ext != ".ply":
                self._status.setText("Wire expects .ply input.")
                self._view_btn.setEnabled(False)
                self._set_param("path", "", notify_scene=True)
                return

            out_path, detail = _build_wire_obj_from_ply(src_path, mode=order_mode)
            if not out_path:
                self._status.setText(detail or "Wire build failed.")
                self._view_btn.setEnabled(False)
                self._set_param("path", "", notify_scene=True)
                return

            # Keep a copy near the workflow so exports and cleanup are easier.
            try:
                node_name = _sanitize_name(getattr(model, "name", "") or "wire")
                src_stem = _sanitize_name(Path(src_path).stem)
                local_out = _wire_dir(self._node_item) / f"{node_name}_{src_stem}_{order_mode}.obj"
                if not local_out.exists() or os.path.getmtime(out_path) >= os.path.getmtime(str(local_out)):
                    try:
                        local_out.write_text(Path(out_path).read_text(encoding="utf-8"), encoding="utf-8")
                        out_path = str(local_out)
                    except Exception:
                        pass
            except Exception:
                pass

            self._status.setText(detail or Path(src_path).name)
            self._view_btn.setEnabled(True)
            self._set_param("path", str(out_path), notify_scene=True)

        def _on_view_clicked(self):
            model = getattr(self._node_item, "model", None)
            enabled = _bool_param(_param_value(model, "enabled"), default=True)
            order_mode = _normalize_order(_param_value(model, "order") or "id")
            src_path = str((_resolve_wire_source(self._node_item) or _param_value(model, "source") or "").strip())
            try:
                path = _param_value(self._node_item.model, "path")
            except Exception:
                path = ""

            # View is preview-only; do not require or trigger a graph refresh here.
            if enabled and src_path and os.path.exists(src_path) and Path(src_path).suffix.lower() == ".ply":
                try:
                    preview_path, detail = _build_wire_obj_from_ply(src_path, mode=order_mode)
                    if preview_path:
                        path = str(preview_path)
                        self._status.setText(detail or Path(src_path).name)
                except Exception:
                    pass

            if not path:
                return

            win = _resolve_window(self._node_item)
            handler = getattr(win, "open_scene_assets", None) if win is not None else None
            if callable(handler):
                assets = []
                if src_path and os.path.exists(src_path) and Path(src_path).suffix.lower() == ".ply":
                    assets.append(
                        {
                            "path": src_path,
                            "ext": ".ply",
                            "node": (getattr(self._node_item.model, "name", "") or "wire_source") + "_source",
                            "visible": True,
                        }
                    )
                assets.append(
                    {
                        "path": path,
                        "ext": ".obj",
                        "node": getattr(self._node_item.model, "name", "") or "wire",
                        "wire_only": True,
                        "visible": True,
                    }
                )
                try:
                    glv = getattr(win, "gl_view", None)
                    if glv is not None:
                        toggle = getattr(glv, "_mgl_wireframe_toggle", None)
                        if toggle is not None:
                            try:
                                if not bool(toggle.isChecked()):
                                    toggle.setChecked(True)
                            except Exception:
                                pass
                        else:
                            on_toggle = getattr(glv, "_on_mgl_wireframe_toggled", None)
                            if callable(on_toggle):
                                try:
                                    on_toggle(True)
                                except Exception:
                                    pass
                except Exception:
                    pass
                try:
                    handler(assets, frame=True)
                    _force_scene_wire_white(win, [path])
                    return
                except TypeError:
                    try:
                        handler(assets)
                        _force_scene_wire_white(win, [path])
                        return
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                self._node_item._open_import_preview(path)
            except Exception:
                pass


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "enabled", "1")
    _ensure_param(node_item, "order", "id")
    _ensure_hidden_params(getattr(node_item, "model", None), ("mesh", "source", "path"))
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _ensure_body_space(node_item, body_h: int) -> None:
    try:
        extra = max(0.0, float(body_h) + 4.0)
        old_h = float(getattr(node_item, "height", 0.0) or 0.0)
        new_h = old_h + extra
        if new_h > old_h + 0.5:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = new_h
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        _refresh_wire_params(node_item)
    except Exception:
        pass

    if QtWidgets is None or QtCore is None:
        return y_cursor

    body = WireWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = max(30, int(body.sizeHint().height()))
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    _ensure_body_space(node_item, h)
    return y_cursor + h


WIRE_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
