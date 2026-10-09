"""Small viewport bridge for cloth; solver code stays independent of Qt/GL."""
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, Future
import hashlib
import json
import time

import numpy as np
from scipy.spatial import cKDTree
from PySide6 import QtCore

from echograph.physics.cloth import ClothSettings, ClothSolver
from echograph.physics.cloth.cache import FrameCache, read_bake, write_bake
from echograph.physics.cloth.collisions import MeshCollider


def transform_points(points, matrix):
    result = np.c_[points, np.ones(len(points))] @ np.asarray(matrix)
    return result[:, :3] / np.where(np.abs(result[:, 3:4]) > 1e-12, result[:, 3:4], 1)


def cloth_signature(config, matrix):
    physical_settings = {k: v for k, v in config["settings"].items() if k not in {"interactive", "enabled"}}
    value = {"asset": config["asset"].signature, "settings": physical_settings,
             "pins": config.get("pins", []), "collider_owner": config.get("collider_owner", ""),
             "colliders": [{"node": c.get("node"), "path": c.get("path"), "source_path": c.get("source_path")}
                           for c in config.get("colliders", [])],
             "matrix": np.asarray(matrix).round(7).tolist()}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass
class ClothState:
    solver: ClothSolver
    signature: str
    matrix: np.ndarray
    frame: int
    last_timeline: int
    playing: bool = False
    next_tick: float = 0.0
    cache: FrameCache = field(default_factory=FrameCache)
    disk_frames: dict = field(default_factory=dict)
    collider_previous: dict = field(default_factory=dict)
    bake: dict | None = None
    cache_path: str = ""
    note: str = ""
    version: int = 0
    pending: Future | None = None
    pending_kind: str = ""
    pending_frame: int = 0
    display_positions: np.ndarray | None = None


class ClothViewportMixin:
    def _cloth_items(self):
        scene = getattr(self, "_mgl_scene", None)
        return list(scene.iter_by_tag("scene-model")) if scene is not None else []

    def _cloth_matrix(self, owner):
        matrix = self._mgl_build_scene_asset_model_matrix(owner)
        return np.asarray(matrix if matrix is not None else np.eye(4), dtype=float)

    def _cloth_reset(self, owner, config, matrix, timeline_frame):
        previous = getattr(self, "_cloth_states", {}).get(owner)
        if previous is not None and previous.pending is not None:
            previous.pending.cancel()  # Drop queued work; running work can finish independently.
        settings = ClothSettings.from_dict(config["settings"])
        units = settings.scene_units_per_meter
        rest = transform_points(config["asset"].positions * units, matrix) / units
        state = ClothState(ClothSolver(config["asset"], settings, rest, config.get("pins", [])),
                           cloth_signature(config, matrix), matrix.copy(), settings.start_frame,
                           int(timeline_frame))
        state.cache.put(state.frame, state.solver.x, state.solver.velocity)
        state.display_positions = state.solver.x.copy()
        return state

    def _cloth_schedule(self, state, colliders, kind, frame):
        executor = getattr(self, "_cloth_executor", None)
        if executor is None:
            executor = self._cloth_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cloth-solve")
            # The worker owns only physics arrays. It never touches Qt or OpenGL,
            # and closing the viewport does not wait for an outstanding solve.
            self.destroyed.connect(lambda *_: executor.shutdown(wait=False, cancel_futures=True))
        state.pending_kind, state.pending_frame = kind, int(frame)
        state.pending = executor.submit(state.solver.step, colliders=colliders)

    def _cloth_collect(self, owner, config, state):
        if state.pending is None or not state.pending.done():
            return
        pending = state.pending
        state.pending = None
        pending.result()  # Already complete; propagate failures without blocking Qt.
        state.frame = state.pending_frame
        state.version += 1
        state.display_positions = state.solver.x.copy()
        if state.pending_kind == "bake" and state.bake:
            bake = state.bake
            bake["frames"].append(state.frame)
            bake["positions"].append(state.display_positions.copy())
            bake["next"] += 1
            if state.frame >= state.solver.settings.end_frame:
                self._cloth_finish_bake(owner, config, state)
            else:
                state.note = f"Baking frame {state.frame} / {state.solver.settings.end_frame} (Play / Pause cancels)"
                QtCore.QTimer.singleShot(0, lambda f=bake["next"]: self._cloth_set_timeline(f))
        else:
            state.cache.put(state.frame, state.solver.x, state.solver.velocity)
            state.note = ""

    def _cloth_status(self, model, state):
        if model is None:
            return
        mode = "Baking" if state.bake else "Running" if state.playing else "Paused"
        detail = state.note or state.solver.backend_detail
        if state.pending is not None and not state.solver.last_ms:
            detail = "Preparing solver on the simulation worker…"
        gpu = f" · GPU {state.solver.last_gpu_ms:.1f} ms" if state.solver.backend.startswith("Warp CUDA") else ""
        model._cloth_status = (f"{mode} · {state.solver.backend} · {len(state.solver.x)} vertices · "
                               f"{state.solver.last_ms:.1f} ms/frame{gpu}\n{detail}")

    def _cloth_colliders(self, config, state, items):
        owners = [o.strip() for o in str(config.get("collider_owner", "")).split(",") if o.strip()]
        if not owners:
            owners = [str(c.get("node") or "") for c in config.get("colliders", [])]
        by_owner = {str(item.payload.get("owner") or "").lower(): item for item in items}
        result = []
        units = state.solver.settings.scene_units_per_meter
        for owner in owners:
            item = by_owner.get(owner.lower())
            if item is None:
                raise RuntimeError("Collider not in Scene: " + owner)
            payload = item.payload
            context = payload.get("fbx_rig_context")
            if (isinstance(context, dict) and context.get("skeleton") is not None
                    and self._mgl_fbx_mesh_skinning_enabled(context)):
                # Reuse the app's exact animation/retiming evaluator, not bind geometry.
                bridge = payload.setdefault("_cloth_collider_bridge", {})
                bridge["groom_collider"] = {"fbx_rig_context": context, "node": owner,
                    "sample_owner": payload.get("fbx_sample_owner") or owner}
                prepared = self._mgl_groom_guide_mesh_collider(bridge)
                if prepared is None:
                    raise RuntimeError(bridge.get("_groom_collider_error", "Animated collider could not be evaluated."))
                local = np.asarray(prepared["triangle_points"]).reshape(-1, 3)
                faces = np.arange(len(local)).reshape(-1, 3)
            else:
                entries = self._mgl_mesh_entries_for_payload(payload)
                local_blocks, face_blocks, offset = [], [], 0
                for entry in entries:
                    points = np.asarray(entry.get("points"), dtype=float).reshape(-1, 3)
                    indices = entry.get("indices")
                    faces = np.arange(len(points)).reshape(-1, 3) if indices is None else np.asarray(indices).reshape(-1, 3)
                    local_blocks.append(points)
                    face_blocks.append(faces + offset)
                    offset += len(points)
                if not local_blocks:
                    raise RuntimeError("Collider has no triangle mesh: " + owner)
                local, faces = np.concatenate(local_blocks), np.concatenate(face_blocks)
            world = transform_points(local, self._cloth_matrix(owner)) / units
            previous = state.collider_previous.get(owner)
            collider = MeshCollider(world, faces, previous)
            result.append(collider)
            state.collider_previous[owner] = world.copy()
        return result

    def _cloth_publish(self, item, state):
        key = (id(state), state.version)
        if item.payload.get("_cloth_publish_key") == key:
            return
        asset = state.solver.asset
        units = state.solver.settings.scene_units_per_meter
        # Undo the owner transform because the normal draw pass applies it again.
        local = transform_points(state.display_positions * units, np.linalg.inv(state.matrix)).astype("f4")
        normals = np.zeros_like(local)
        tri = local[asset.triangles]
        face_normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        for corner in range(3):
            np.add.at(normals, asset.triangles[:, corner], face_normals)
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-12)
        for entry in self._mgl_mesh_entries_for_payload(item.payload):
            mapping = entry.get("_cloth_vertices")
            if mapping is None:
                # OBJ readers may expand vertices for normals/UVs; retain authored IDs.
                distance, mapping = cKDTree(asset.positions).query(np.asarray(entry["points"]))
                if np.max(distance, initial=0) > 1e-5:
                    raise RuntimeError("Cloth display mesh differs from its solver topology.")
                entry["_cloth_vertices"] = mapping
                entry["vertex_ids"] = mapping
                entry["topology_mesh"] = asset.signature
            entry["points"] = np.ascontiguousarray(local[mapping])
            entry["normals"] = np.ascontiguousarray(normals[mapping])
            entry["vbo"].write(entry["points"].tobytes())
            entry["nbo"].write(entry["normals"].tobytes())
        owner = str(item.payload.get("owner") or "")
        self._mgl_store_mesh_topology_for_owner(owner, item.payload)
        self._mgl_scene_bounds_by_owner[owner] = (local.min(axis=0), local.max(axis=0))
        self._mgl_scene_mesh_bounds_by_owner[owner] = self._mgl_scene_bounds_by_owner[owner]
        # Update the same wire buffers rather than allocating new VAOs each tick.
        for wire in self._mgl_scene.iter_by_tag("scene-wire"):
            if str(wire.payload.get("owner") or "") != owner:
                continue
            lines = local[asset.edges].reshape(-1, 3)
            if wire.payload.get("wire_instanced"):
                self._mgl_update_instanced_wire_item_from_points(wire, lines)
                continue
            data = self._mgl_wire_vertex_data_from_line_points(lines)
            buffer = wire.payload.get("wire_vbo")
            if buffer is not None:
                if buffer.size != data.nbytes:
                    buffer.orphan(data.nbytes)
                buffer.write(data.tobytes())
                wire.payload["wire_vertex_count"] = int(data.size // 10)
                wire.payload["line_segment_count"] = len(asset.edges)
        self._mgl_clear_mesh_selection_overlay_cache()
        item.payload["_cloth_publish_key"] = key

    def _cloth_set_timeline(self, frame):
        widgets = getattr(self, "_timeline_set_frame_widgets", None)
        if callable(widgets):
            widgets(int(frame))
        setter = getattr(self, "_timeline_on_frame_spin_changed", None)
        if callable(setter):
            setter(int(frame))
        self.update()

    def _cloth_command(self, command, owner, config, state, timeline):
        action = command.get("action")
        if action in {"pin", "unpin", "clear_pins"}:
            selected = getattr(self, "_mesh_selected_elements", lambda: [])()
            indices = {int(e["vertex_index"]) for e in selected if e.get("mode") == "point"
                       and str(e.get("owner", "")).lower() == owner.lower()}
            if action != "clear_pins" and not indices:
                raise RuntimeError("Select cloth vertices in viewport point mode first.")
            pins = set(config.get("pins", []))
            if action == "pin":
                pins.update(indices)
            elif action == "unpin":
                pins.difference_update(indices)
            else:
                pins.clear()
            from nodes.cloth_solver.spec import set_value
            set_value(config["node_item"], "pins", json.dumps(sorted(pins)))
            return state
        if action == "reset":
            if config.get("cache_path"):
                config["cache_path"] = ""
                if config.get("node_item") is not None:
                    from nodes.cloth_solver.spec import set_value
                    set_value(config["node_item"], "cache_path", "")
                elif config.get("node_model") is not None:
                    for parameter in config["node_model"].params:
                        if parameter.get("name") == "cache_path":
                            parameter["value"] = ""
            return self._cloth_reset(owner, config, self._cloth_matrix(owner), timeline)
        if action == "toggle":
            if state.bake:
                state.bake = None
                state.note = "Bake cancelled."
            else:
                state.playing = not state.playing
                state.next_tick = 0
                state.disk_frames.clear()
        elif action == "step":
            state.playing = False
            state.next_tick = -1  # one fixed step, even when the timeline is idle
            state.disk_frames.clear()
        elif action == "bake":
            settings = state.solver.settings
            frames = settings.end_frame - settings.start_frame + 1
            if frames * len(state.solver.x) * 12 > 512 * 1024**2:
                raise RuntimeError("Bake exceeds 512 MB. Shorten the frame range or use a coarser mesh.")
            stop = getattr(self, "_timeline_stop_playback", None)
            if callable(stop):
                stop()
            state = self._cloth_reset(owner, config, self._cloth_matrix(owner), settings.start_frame)
            state.bake = {"path": command["path"], "frames": [state.frame], "positions": [state.solver.x.copy()],
                          "restore_frame": timeline, "next": settings.start_frame + 1, "primed": False}
            # Defer the frame change until the current paint finishes.
            QtCore.QTimer.singleShot(0, lambda f=settings.start_frame: self._cloth_set_timeline(f))
        return state

    def _cloth_finish_bake(self, owner, config, state):
        bake = state.bake
        write_bake(bake["path"], {"signature": state.signature, "owner": owner}, bake["frames"], bake["positions"])
        state.disk_frames = dict(zip(bake["frames"], bake["positions"]))
        state.note = "Baked " + str(len(bake["frames"])) + " frames to " + bake["path"]
        state.bake = None
        QtCore.QTimer.singleShot(0, lambda f=bake["restore_frame"]: self._cloth_set_timeline(f))
        if config.get("node_item") is not None:
            from nodes.cloth_solver.spec import set_value
            set_value(config["node_item"], "cache_path", bake["path"])
        elif config.get("node_model") is not None:
            for parameter in config["node_model"].params:
                if parameter.get("name") == "cache_path":
                    parameter["value"] = bake["path"]
        config["cache_path"] = state.cache_path = bake["path"]

    def _mgl_update_cloth(self):
        items = self._cloth_items()
        states = getattr(self, "_cloth_states", None)
        if states is None:
            states = self._cloth_states = {}
        timeline = int(self._mgl_timeline_frame_index())
        now, active = time.perf_counter(), set()
        for item in items:
            config = item.payload.get("cloth")
            if not isinstance(config, dict):
                continue
            owner = str(item.payload.get("owner") or "")
            active.add(owner)
            model = config.get("node_model")
            state = states.get(owner)
            try:
                matrix = self._cloth_matrix(owner)
                signature = cloth_signature(config, matrix)
                if state is None or state.signature != signature:
                    state = states[owner] = self._cloth_reset(owner, config, matrix, timeline)
                    self._cloth_colliders(config, state, items)
                settings = ClothSettings.from_dict(config["settings"])
                if not settings.enabled:
                    state.playing = False
                commands = getattr(model, "_cloth_commands", []) if model else []
                if model is not None:
                    model._cloth_commands = []
                for command in commands:
                    state = states[owner] = self._cloth_command(command, owner, config, state, timeline)
                    if command.get("action") == "reset":
                        self._cloth_colliders(config, state, items)
                self._cloth_collect(owner, config, state)
                if state.pending is not None:
                    # Draw/pick the last complete frame and keep handling commands.
                    # Reset/disconnection discards old state without waiting for it.
                    self._cloth_publish(item, state)
                    self._cloth_status(model, state)
                    continue
                state.solver.settings = settings
                path = config.get("cache_path", "")
                if path != state.cache_path:
                    if path:
                        _, frames = read_bake(path, state.signature)
                        if any(p.shape != state.solver.x.shape for p in frames.values()):
                            raise RuntimeError("Cache vertex count differs from the cloth mesh.")
                        state.disk_frames = frames
                        state.playing = False
                    else:
                        state.disk_frames.clear()
                    state.cache_path = path
                    state.version += 1
                changed = timeline != state.last_timeline
                stepping = state.next_tick == -1 or (state.playing and now >= state.next_tick)
                if state.disk_frames and not state.bake:
                    frame = state.disk_frames.get(timeline)
                    if frame is not None:
                        state.solver.x[:] = frame
                        state.display_positions = state.solver.x.copy()
                        state.solver.time = max(0, timeline - settings.start_frame) / settings.fps
                        if changed:
                            state.version += 1
                            state.solver.velocity.fill(0)
                        state.frame = timeline
                        state.note = "Baked cache playback"
                    else:
                        state.note = "Frame is outside the baked range."
                elif state.bake:
                    bake = state.bake
                    if not bake["primed"] and timeline == settings.start_frame:
                        self._cloth_colliders(config, state, items)
                        bake["primed"] = True
                        if settings.end_frame == settings.start_frame:
                            self._cloth_finish_bake(owner, config, state)
                        else:
                            QtCore.QTimer.singleShot(0, lambda f=bake["next"]: self._cloth_set_timeline(f))
                    elif bake["primed"] and timeline == bake["next"]:
                        self._cloth_schedule(state, self._cloth_colliders(config, state, items), "bake", timeline)
                elif changed and not state.playing:
                    cached = state.cache.get(timeline)
                    if cached is not None:
                        state.solver.x[:], state.solver.velocity[:] = cached
                        state.display_positions = state.solver.x.copy()
                        state.version += 1
                        state.solver.time = max(0, timeline - settings.start_frame) / settings.fps
                        state.frame = timeline
                        state.note = "Preview cache"
                        state.collider_previous.clear()
                        self._cloth_colliders(config, state, items)
                    elif timeline <= settings.start_frame:
                        state = states[owner] = self._cloth_reset(owner, config, matrix, timeline)
                        self._cloth_colliders(config, state, items)
                    elif timeline == state.frame + 1:
                        self._cloth_schedule(state, self._cloth_colliders(config, state, items), "timeline", timeline)
                    else:
                        state.note = "Uncached frame: play sequentially from Start frame or bake the range."
                elif stepping and not state.disk_frames:
                    self._cloth_schedule(state, self._cloth_colliders(config, state, items), "live", state.frame + 1)
                    interval = 1 / settings.fps
                    state.next_tick = state.next_tick + interval if state.next_tick > 0 else now + interval
                    if state.next_tick < now:
                        state.next_tick = now + interval
                    state.note = ""
                state.last_timeline = timeline
                self._cloth_publish(item, state)
                self._cloth_status(model, state)
            except Exception as exc:
                if state is not None:
                    state.playing = False
                    state.bake = None
                if model is not None:
                    model._cloth_status = "Cloth error: " + str(exc)
        # Keep paused states across graph refreshes, but remove disconnected solvers.
        for owner in set(states) - active:
            disconnected = states.pop(owner, None)
            if disconnected is not None and disconnected.pending is not None:
                disconnected.pending.cancel()
        live = any(s.playing or s.bake or s.pending is not None for s in states.values())
        timer = getattr(self, "_cloth_timer", None)
        if live and timer is None:
            timer = self._cloth_timer = QtCore.QTimer(self)
            timer.setInterval(16)
            timer.timeout.connect(self.update)
        if timer is not None:
            if live and not timer.isActive():
                timer.start()
            elif not live:
                timer.stop()

    def _cloth_mouse_ray(self, event):
        position = event.position()
        dpr = float(self.devicePixelRatioF())
        vw, vh = int(self.width() * dpr), int(self.height() * dpr)
        px, py = float(position.x() * dpr), float(position.y() * dpr)
        p, v, m = self._mgl_pick_proj, self._mgl_pick_view, self._mgl_pick_model
        inverse = np.linalg.inv(np.asarray(p) @ np.asarray(v) @ np.asarray(m))
        active = getattr(self, "_mgl_active_viewport_rect", None)
        if active and len(active) >= 4:
            ax, ay, aw, ah = active[:4]
            py_bottom = vh - py
            if not (ax <= px <= ax + aw and ay <= py_bottom <= ay + ah):
                return None
            nx, ny = 2 * (px - ax) / aw - 1, 2 * (py_bottom - ay) / ah - 1
        else:
            nx, ny = 2 * px / vw - 1, 1 - 2 * py / vh
        near, far = inverse @ np.array([nx, ny, -1, 1]), inverse @ np.array([nx, ny, 1, 1])
        near, far = near[:3] / near[3], far[:3] / far[3]
        direction = far - near
        direction /= max(np.linalg.norm(direction), 1e-12)
        return near, direction

    def _cloth_mouse_press(self, event):
        if event.button() != QtCore.Qt.LeftButton or event.modifiers() & QtCore.Qt.AltModifier:
            return False
        if str(getattr(self, "_mesh_select_mode", "object")) in {"point", "edge", "face"}:
            return False
        states = getattr(self, "_cloth_states", {})
        candidates = [(owner, state) for owner, state in states.items()
                      if state.solver.settings.interactive and state.solver.settings.enabled
                      and self._mgl_scene_owner_visible(owner)]
        if not candidates:
            return False
        ray = self._cloth_mouse_ray(event)
        if ray is None:
            return False
        origin, direction = ray
        hits = []
        for owner, state in candidates:
            units = state.solver.settings.scene_units_per_meter
            local = transform_points(state.display_positions * units, np.linalg.inv(state.matrix))
            topo = {"points": local, "triangles": state.solver.asset.triangles,
                    "bounds_min": local.min(axis=0), "bounds_max": local.max(axis=0)}
            hit = self._mgl_pick_triangles_for_owner(owner, topo, origin, direction)
            if hit:
                hits.append((hit["t_world"], owner, state, hit))
        if not hits:
            return False
        _, owner, state, hit = min(hits, key=lambda h: h[0])
        # A body or another mesh in front of the cloth still receives ordinary picking.
        for other in self._cloth_items():
            other_owner = str(other.payload.get("owner") or "")
            if other_owner == owner or not other.visible:
                continue
            topology = self._mgl_mesh_topology_for_owner(other_owner)
            if topology is not None:
                obstruction = self._mgl_pick_triangles_for_owner(other_owner, topology, origin, direction)
                if obstruction and obstruction["t_world"] < hit["t_world"] - 1e-5:
                    return False
        face = int(hit["face_index"])
        indices = state.solver.asset.triangles[face]
        weights = np.asarray(hit["barycentric"])
        target = weights @ state.display_positions[indices]
        state.solver.set_grab(indices, weights, target)
        self._cloth_drag = (owner, target * state.solver.settings.scene_units_per_meter, direction.copy(), state.playing)
        state.playing = True
        state.disk_frames.clear()
        self._mgl_pick_press_pos = None
        self.update()
        return True

    def _cloth_mouse_move(self, event):
        drag = getattr(self, "_cloth_drag", None)
        if drag is None:
            return False
        state = getattr(self, "_cloth_states", {}).get(drag[0])
        if state is None or state.solver.grab is None:
            self._cloth_drag = None
            return False
        ray = self._cloth_mouse_ray(event)
        if ray is not None:
            origin, direction = ray
            denominator = float(np.dot(direction, drag[2]))
            if abs(denominator) > 1e-8:
                distance = float(np.dot(drag[1] - origin, drag[2]) / denominator)
                target = (origin + direction * distance) / state.solver.settings.scene_units_per_meter
                ids, weights, _ = state.solver.grab
                state.solver.set_grab(ids, weights, target)
        self.update()
        return True

    def _cloth_mouse_release(self, event):
        drag = getattr(self, "_cloth_drag", None)
        if drag is None or event.button() != QtCore.Qt.LeftButton:
            return False
        state = getattr(self, "_cloth_states", {}).get(drag[0])
        if state is not None:
            state.solver.release_grab()
            state.playing = drag[3]
        self._cloth_drag = None
        self.update()
        return True
