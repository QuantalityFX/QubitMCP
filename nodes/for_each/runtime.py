"""Sequential For Each rendering, driven by completion rather than delays."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from PySide6 import QtCore
from shiboken6 import isValid

from nodes.mocap_import import spec as params
from nodes.mocap_collection import spec as collection
from nodes.render.takes import reserve_take_folder


def value(model, name, default=""):
    return params._param_value(model, name) or default


def incoming(item, port):
    from nodes.anim_retarget.spec import _connected_item_for_port
    source, issue = _connected_item_for_port(item.scene(), item, port)
    if issue:
        raise ValueError(issue)
    return source


def resolve_context(node_item, errors, warnings, *, target=False, _depth=0, _visited=None):
    """Pass the current input through, independently of the loop's run state."""
    from nodes.anim_retarget import spec as retarget

    role = "target" if target else "source"
    visited = set() if _visited is None else _visited
    if id(node_item) in visited or _depth > 32:
        errors.append(f"{role}: For Each input chain contains a cycle or is too deep.")
        return None
    visited.add(id(node_item))
    try:
        # 'source' is the persisted key for the input labelled 'collection'.
        upstream = incoming(node_item, "source")
    except ValueError as exc:
        errors.append(f"{role}: For Each collection input: {exc}")
        return None
    if upstream is None:
        errors.append(f"{role}: Connect Mocap Collection OUT to For Each Start's collection IN.")
        return None
    kind = str(getattr(upstream.model, "kind", "") or "").strip().lower()
    resolver = retarget._context_for_target_item if target else retarget._context_for_source_item
    error_count = len(errors)
    context = resolver(upstream, kind, errors, warnings, _depth=_depth + 1, _visited=visited)
    if context is None and len(errors) == error_count:
        errors.append(f"{role}: For Each input has unsupported node kind '{kind}'.")
    return context


def wrapper_for(start):
    scene = start.scene()
    groups = []
    for group in getattr(scene, "_comment_groups", []):
        if hasattr(scene, "_refresh_comment_group_membership"):
            scene._refresh_comment_group_membership(group)
        if start.model.name in group.members():
            groups.append(group)
    if not groups:
        raise ValueError("Place For Each Start and End inside the same wrapper.")
    # A surrounding organizational comment is allowed; use the smallest group.
    return min(groups, key=lambda group: group._rect.width() * group._rect.height())


def loop_nodes(start):
    scene = start.scene()
    group = wrapper_for(start)
    members = [scene._node_items[name] for name in group.members() if name in scene._node_items]
    starts = [item for item in members if item.model.kind == "for_each"]
    ends = [item for item in members if item.model.kind == "for_each_end"]
    renders = [item for item in members if item.model.kind in ("render", "render_sequence", "render node")]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("The wrapper needs exactly one For Each Start and one For Each End. Nested loops are not supported yet.")
    if len(renders) != 1:
        raise ValueError("Drag exactly one Render node inside the wrapper.")
    if incoming(ends[0], "render") is not renders[0]:
        raise ValueError("Connect the Render node's output to For Each End's render input.")
    return ends[0], renders[0]


def iteration_plan(start, source):
    mode = value(start.model, "mode", "outputs")
    limit = max(0, int(value(start.model, "loop_limit", "0")))
    plan = []
    if mode in ("outputs", "checked"):
        if source.model.kind != "mocap_collection":
            raise ValueError("Connect Mocap Collection to Start's collection input. The T-pose rig connects to Animation Retarget's source input.")
        rows = collection.entries(source.model)
        eligible = collection.output_rows(source.model) if mode == "outputs" else [i + 1 for i, row in enumerate(rows) if row["checked"]]
        base = params._workflow_dir_for_node(source)
        for row in eligible:
            path = params._resolve_existing_path(rows[row - 1]["path"], base)
            if path is None or not path.is_file():
                raise ValueError(f"BVH file is missing: {rows[row - 1]['path']}")
            plan.append({"source_path": str(path.resolve()), "collection_row": row, "value": row})
            if limit and len(plan) >= limit:
                break
    else:
        parameter = value(start.model, "parameter", "output")
        if parameter not in [row.get("name") for row in source.model.params]:
            raise ValueError(f"Input node has no '{parameter}' parameter.")
        count = max(0, int(value(start.model, "count", "1")))
        first = float(value(start.model, "first", "1"))
        step = float(value(start.model, "step", "1"))
        import math
        if not math.isfinite(first) or not math.isfinite(step):
            raise ValueError("First value and step must be finite numbers.")
        for i in range(min(count, limit) if limit else count):
            plan.append({"source_path": "", "parameter": parameter, "value": format(first + i * step, ".12g")})
    if not plan:
        raise ValueError("No eligible inputs. Check some collection paths or set a positive parameter count.")
    return plan


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def validate_retarget_inputs(render_item):
    from nodes.anim_retarget import spec as retarget
    pending, visited = [render_item], set()
    while pending:
        item = pending.pop()
        if id(item) in visited:
            continue
        visited.add(id(item))
        if item.model.kind in retarget.ANIM_RETARGET_KIND_ALIASES:
            result = retarget.resolve_anim_retarget_inputs(item, persist=True)
            if result.errors:
                raise ValueError(f"{item.model.name}: " + "\n".join(result.errors))
        pending.extend(edge.src for edge in retarget._ordered_in_edges(item.scene(), item))


class ForEachSession(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, start):
        super().__init__(start.scene())
        self.start_item = start
        self.scene = start.scene()
        self.running = False
        self.cancel_requested = False
        self.status = "Ready"
        self.index = 0
        self.plan = []
        self.manifest_path = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)

    def begin(self):
        if self.running:
            return
        if getattr(self.scene, "_foreach_active_session", None) is not None:
            raise ValueError("Another For Each loop is running in this graph.")
        self.end_item, self.render_item = loop_nodes(self.start_item)
        self.source = incoming(self.start_item, "source")
        if self.source is None:
            raise ValueError("Connect Mocap Collection (or the parameter node to iterate) to Start's collection input.")
        self.plan = iteration_plan(self.start_item, self.source)
        from nodes.render.spec import RenderNodeWidget, _resolve_scene_input_item
        if _resolve_scene_input_item(self.render_item) is None:
            raise ValueError("Connect Scene to the Render node inside the wrapper.")
        # Catch accidentally rendering a branch that is not driven by the input.
        from nodes.util_graph import param_change_relevant
        if not param_change_relevant(self.render_item, self.source.model.name):
            raise ValueError("The Render scene must depend on the node connected to For Each Start's collection input.")
        self.renderer = next((proxy.widget() for proxy in self.render_item._plugin_proxies
                              if isinstance(proxy.widget(), RenderNodeWidget)), None)
        if self.renderer is None or self.renderer._executing:
            raise ValueError("The Render node is unavailable or already rendering.")
        self.renderer._sync_controls_from_params()
        self.template, _ = self.renderer._resolved_template()
        self.initial_rows = collection.entries(self.source.model) if "collection_row" in self.plan[0] else None
        self.initial_checked_only = value(self.source.model, "checked_only", "0")
        self.initial_live = value(self.source.model, "live_view", "0")
        self.cancel_requested = False
        self.index = 0
        self.records = []
        self.manifest_path = None
        self.scene._foreach_active_session = self
        self.running = True
        self.end_item.model._foreach_session = self
        try:
            if self.initial_rows is not None:
                params._set_param_value(self.source.model, "live_view", "0")
                self.scene.set_node_params(self.source.model.name, list(self.source.model.params), rebuild=False, emit=True)
            self.status = f"Starting {len(self.plan)} take(s)…"
            self.changed.emit()
            self._timer.start(0)
        except Exception:
            self._finish("error", "Could not start For Each.")
            raise

    def stop(self):
        if self.running:
            self.cancel_requested = True
            self.status = "Stopping after the current frame…"
            self.changed.emit()

    def _alive(self):
        return all(isValid(item) and item.scene() is self.scene for item in
                   (self.start_item, self.end_item, self.source, self.render_item)) and isValid(self.renderer)

    def _next(self):
        if not self.running:
            return
        try:
            if self.cancel_requested or not self._alive():
                self._finish("cancelled", "For Each stopped.")
                return
            if loop_nodes(self.start_item) != (self.end_item, self.render_item):
                raise ValueError("The wrapper changed while the loop was running.")
            if self.index >= len(self.plan):
                self._finish("completed", f"Completed {len(self.plan)} take(s).")
                return
            entry = self.plan[self.index]
            if self.initial_rows is not None:
                if collection.entries(self.source.model) != self.initial_rows or value(self.source.model, "checked_only", "0") != self.initial_checked_only:
                    raise ValueError("The collection list or output filter changed during rendering. Run again with the new selection.")
                collection.save_state(self.source.model, self.initial_rows, entry["collection_row"], self.scene)
                if collection.output_number(self.source.model) != entry["collection_row"]:
                    raise ValueError("The requested collection output is no longer available.")
            else:
                params._set_param_value(self.source.model, entry["parameter"], entry["value"])
                self.scene.set_node_params(self.source.model.name, list(self.source.model.params), rebuild=False, emit=True)
            number = self.index + 1
            for item in (self.start_item, self.end_item):
                params._set_param_value(item.model, "iteration", str(number))
            self.renderer._take_number.setValue(number)
            self.renderer._take_folders.setChecked(True)
            validate_retarget_inputs(self.render_item)
            self.status = f"Rendering take {number}/{len(self.plan)}"
            self.changed.emit()
            folder = reserve_take_folder(self.template.parent, number, entry["source_path"])
            if self.manifest_path is None:
                # Name the manifest after the first uniquely reserved take folder.
                self.manifest_path = self.template.parent / f"{folder.name}_manifest.json"
            record = dict(entry, take_number=number, take_folder=str(folder),
                          output_template=str(folder / self.template.name), status="rendering",
                          started_at=datetime.now(timezone.utc).isoformat())
            self.records.append(record)
            self._write_manifest("running")
            write_json(folder / "take.json", record)
            result = self.renderer.render_sequence(output_template=folder / self.template.name,
                batch=True, cancel_requested=lambda: self.cancel_requested or not self._alive())
            record.update(result.to_dict(), finished_at=datetime.now(timezone.utc).isoformat())
            write_json(folder / "take.json", record)
            self._write_manifest("running")
            if result.status != "completed":
                self._finish(result.status, result.message or f"Take {number} {result.status}.")
                return
            # End is reached only after render_sequence has returned its outcome.
            self.index += 1
            self.status = f"Finished take {number}/{len(self.plan)}"
            self.changed.emit()
            self._timer.start(0)
        except Exception as exc:
            self._finish("error", str(exc))

    def _write_manifest(self, status):
        if self.manifest_path is not None:
            write_json(self.manifest_path, {"schema_version": 1, "status": status, "message": self.status,
                "for_each": self.start_item.model.name, "source_node": self.source.model.name,
                "planned_takes": len(self.plan), "takes": self.records})

    def _finish(self, status, message):
        self._timer.stop()
        self.running = False
        self.status = message
        if getattr(self.scene, "_foreach_active_session", None) is self:
            self.scene._foreach_active_session = None
        if self.initial_rows is not None and isValid(self.source) and self.source.scene() is self.scene:
            params._set_param_value(self.source.model, "live_view", self.initial_live)
            self.scene.set_node_params(self.source.model.name, list(self.source.model.params), rebuild=False, emit=True)
        try:
            if self.records and self.records[-1]["status"] == "rendering":
                record = self.records[-1]
                record.update(status=status, message=message, finished_at=datetime.now(timezone.utc).isoformat())
                write_json(Path(record["take_folder"]) / "take.json", record)
            self._write_manifest(status)
        except OSError as exc:
            self.status += f" Manifest could not be saved: {exc}"
        self.changed.emit()


def session_for(item):
    session = getattr(item.model, "_foreach_session", None)
    if session is None or not isValid(session):
        session = ForEachSession(item)
        item.model._foreach_session = session
    return session
