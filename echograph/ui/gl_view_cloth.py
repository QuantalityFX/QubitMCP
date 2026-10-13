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
from echograph.physics.cloth.placement import (PlacementPose, placed_positions, piece_xform,
                                             transform_points, xform_matrix)
from echograph.rigging.fbx_stage5_evaluator import evaluation_cache_token
from .gl_view_cloth_panels import ClothPanelsMixin


def cloth_signature(config, matrix, *, include_settings=True, include_colliders=True, include_pins=None):
    physical_settings = {k: v for k, v in config["settings"].items()
                         if k not in {"interactive", "enabled", "follow_timeline", "gravity_enabled"}}
    if not include_settings:
        physical_settings = {}
    include_pins = include_settings if include_pins is None else include_pins
    asset = config["asset"]
    value = {"asset": asset.signature if include_settings else (asset.topology_signature or asset.signature),
             "settings": physical_settings, "pins": config.get("pins", []) if include_pins else [],
             "collider_owner": config.get("collider_owner", ""),
             "colliders": [{"node": c.get("node"), "path": c.get("path"), "source_path": c.get("source_path")}
                           for c in config.get("colliders", [])],
             "matrix": np.asarray(matrix).round(7).tolist()}
    if include_pins and config.get("pin_positions"):
        value["pin_positions"] = config["pin_positions"]
    if not include_colliders:
        value.pop("collider_owner")
        value.pop("colliders")
    if config.get("piece_transforms"):
        pieces = config.get("_pieces")
        if pieces is None:
            pieces = config["_pieces"] = config["asset"].pieces()
        placements = {}
        for piece in pieces:
            pose = xform_matrix(piece_xform(piece, config["piece_transforms"], 1))
            default = xform_matrix(piece_xform(piece, {}, 1))
            if not np.allclose(pose, default, rtol=0, atol=1e-7):
                placements[piece["id"]] = pose.round(7).tolist()
        if placements:
            value["piece_transforms"] = placements
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass
class ClothState:
    solver: ClothSolver | PlacementPose
    signature: str
    matrix: np.ndarray
    frame: int
    last_timeline: int
    geometry_signature: str = ""
    placement_signature: str = ""
    pose_revision: int = 0
    playing: bool = False
    follow_timeline: bool = False
    next_tick: float = 0.0
    cache: FrameCache = field(default_factory=FrameCache)
    disk_frames: dict = field(default_factory=dict)
    disk_velocities: dict = field(default_factory=dict)
    disk_metadata: dict = field(default_factory=dict)
    cache_playback: bool = True
    collider_previous: dict = field(default_factory=dict)
    bake: dict | None = None
    cache_path: str = ""
    note: str = ""
    version: int = 0
    pending: Future | None = None
    pending_kind: str = ""
    pending_frame: int = 0
    display_positions: np.ndarray | None = None
    saving: dict | None = None
    save_requested: str = ""
    load_requested: str = ""
    error: str = ""
    initial_pose: tuple | None = None
    animation_signature: str = ""
    seek_start: int | None = None
    seek_target: int | None = None
    timeline_cache_valid: bool = False


class ClothViewportMixin(ClothPanelsMixin):
    def _cloth_items(self):
        scene = getattr(self, "_mgl_scene", None)
        return list(scene.iter_by_tag("scene-model")) if scene is not None else []

    def _cloth_matrix(self, owner):
        matrix = self._mgl_build_scene_asset_model_matrix(owner)
        return np.asarray(matrix if matrix is not None else np.eye(4), dtype=float)

    def _cloth_reset(self, owner, config, matrix, timeline_frame):
        config.pop("_pieces", None)
        previous = getattr(self, "_cloth_states", {}).get(owner)
        if previous is not None and previous.pending is not None:
            previous.pending.cancel()  # Drop queued work; running work can finish independently.
        settings = ClothSettings.from_dict(config["settings"])
        units = settings.scene_units_per_meter
        rest = placed_positions(config["asset"], config.get("piece_transforms", {}), units, matrix)
        solver = (PlacementPose(config["asset"], settings, rest) if config.get("placement_only") else
                  ClothSolver(config["asset"], settings, rest, config.get("pins", []), defer_backend=True))
        if not config.get("placement_only") and config.get("pin_positions"):
            solver.update_pins(config.get("pins", []), config["pin_positions"])
        frame = int(timeline_frame) if settings.follow_timeline else settings.start_frame
        state = ClothState(solver, cloth_signature(config, matrix), matrix.copy(), frame,
                           int(timeline_frame), follow_timeline=settings.follow_timeline,
                           geometry_signature=cloth_signature(config, matrix, include_settings=False),
                           placement_signature=cloth_signature(config, matrix, include_settings=False, include_colliders=False),
                           pose_revision=(previous.pose_revision + 1) if previous is not None else 0)
        state.cache.put(state.frame, state.solver.x, state.solver.velocity, simulation_time=getattr(solver, "time", 0.0))
        state.display_positions = state.solver.x.copy()
        if not config.get("placement_only"):
            self._cloth_mark_initial_pose(state)
        return state

    @staticmethod
    def _cloth_mark_initial_pose(state):
        # Keep this independently of the bounded preview cache. A saved, sewn
        # pose must survive both eviction and an animation change.
        state.initial_pose = (state.frame, state.solver.x.copy(), state.solver.velocity.copy(),
                              float(state.solver.time))
        state.animation_signature = ""
        state.seek_start = state.seek_target = None
        state.timeline_cache_valid = state.follow_timeline
        state.pose_revision += 1

    def _cloth_rig_content_signature(self, value):
        if value is None:
            return ""
        cache = getattr(self, "_cloth_rig_signatures", None)
        if cache is None:
            cache = self._cloth_rig_signatures = {}
        token = evaluation_cache_token(value)
        if token not in cache:
            # Canonical clips/skeletons are immutable after ingestion. Hash
            # their content once, so an identical rig rebuilt by a node does
            # not discard the preview merely because its Python id changed.
            data = {key: getattr(value, key) for key in
                    ("start_time", "end_time", "sample_rate_hz") if hasattr(value, key)}
            data["tracks"] = [track.to_dict() for track in getattr(value, "tracks", [])]
            data["joints"] = [joint.to_dict() for joint in getattr(value, "joints", [])]
            cache[token] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            if len(cache) > 64:
                del cache[next(iter(cache))]
        return cache[token]

    def _cloth_animation_signature(self, config, items, visiting=()):
        by_owner = {str(item.payload.get("owner") or "").casefold(): item for item in items}
        sources = []
        for owner in self._cloth_collider_owners(config):
            item = by_owner.get(owner.casefold())
            context = item.payload.get("fbx_rig_context", {}) if item is not None else {}
            context = context if isinstance(context, dict) else {}
            sample_owner = str(item.payload.get("fbx_sample_owner") or owner) if item is not None else owner
            block_fn = getattr(self, "_timeline_find_composition_block", None)
            block = block_fn(owner=sample_owner) if callable(block_fn) else {}
            block = block if isinstance(block, dict) else {}
            timing = {key: block.get(key) for key in ("enabled", "clip_start_frame", "clip_end_frame",
                      "source_start_frame", "source_end_frame", "speed_percent", "hold_before", "hold_after")}
            speed_fn = getattr(self, "_timeline_owner_speed_factor", None)
            keys_fn = getattr(self, "_timeline_keys_map_for_owner", None)
            sources.append((owner.casefold(), self._cloth_rig_content_signature(context.get("clip")),
                            self._cloth_rig_content_signature(context.get("skeleton")),
                            bool(context.get("loop", True)), timing,
                            speed_fn(sample_owner) if callable(speed_fn) else
                            getattr(self, "_timeline_owner_speed_percent_by_owner", {}).get(sample_owner, 100),
                            keys_fn(owner) if callable(keys_fn) else {}))
            nested = item.payload.get("cloth") if item is not None else None
            if isinstance(nested, dict):
                key = owner.casefold()
                if key in visiting:
                    raise RuntimeError("Cloth collider loop: " + " → ".join((*visiting, key)))
                other = getattr(self, "_cloth_states", {}).get(str(item.payload.get("owner") or ""))
                sources.append(("cloth", key,
                    cloth_signature(nested, self._cloth_matrix(owner), include_settings=False, include_colliders=False),
                    other.pose_revision if other is not None else 0,
                    self._cloth_animation_signature(nested, items, (*visiting, key))
                    if not nested.get("placement_only") else ""))
        return hashlib.sha256(json.dumps((self._mgl_timeline_fps_value(), sources), sort_keys=True).encode()).hexdigest()

    def _cloth_order_items(self, items):
        """Solve cloth colliders before the garments that depend on them."""
        by_owner = {str(item.payload.get("owner") or "").casefold(): item for item in items
                    if isinstance(item.payload.get("cloth"), dict)}
        ordered, complete = [], set()

        def visit(key, path):
            if key in path:
                raise RuntimeError("Cloth collider loop: " + " → ".join((*path, key))
                                   + ". Remove one of the reciprocal collider assignments.")
            if key in complete:
                return
            item = by_owner[key]
            config = item.payload["cloth"]
            if not config.get("placement_only"):
                for owner in self._cloth_collider_owners(config):
                    dependency = owner.casefold()
                    if dependency in by_owner:
                        visit(dependency, (*path, key))
            complete.add(key)
            ordered.append(item)

        for key in by_owner:
            visit(key, ())
        return ordered

    def _cloth_work_frame(self, items, timeline):
        # Scrubbing can request many frames at once. Keep uncached garments on
        # the same solve frame, just as sequential rendering does. A fast solver
        # must not run ahead and evict poses needed by a slower dependent cloth.
        frames = [timeline]
        states = getattr(self, "_cloth_states", {})
        for item in items:
            config = item.payload.get("cloth")
            if not isinstance(config, dict) or config.get("placement_only"):
                continue
            settings = ClothSettings.from_dict(config["settings"])
            state = states.get(str(item.payload.get("owner") or ""))
            if (state is None or not settings.enabled or not settings.follow_timeline
                    or state.initial_pose is None or state.cache.get(timeline) is not None
                    or (state.cache_playback and timeline in state.disk_frames)
                    or timeline <= state.initial_pose[0]):
                continue
            if state.pending is not None and state.pending_kind == "timeline":
                frames.append(state.pending_frame)
            elif state.seek_target == timeline:
                frames.append(state.frame + 1)
            else:
                earlier = [frame for frame in state.cache.frames if state.initial_pose[0] <= frame < timeline]
                frames.append(max(earlier, default=state.initial_pose[0]) + 1)
        return min(frames)

    def _cloth_collider_pose(self, item, frame):
        """Immutable world-space physics pose, independent of the displayed mesh."""
        owner = str(item.payload.get("owner") or "")
        other = getattr(self, "_cloth_states", {}).get(owner)
        if other is None:
            return None
        if item.payload["cloth"].get("placement_only") or not other.follow_timeline or not other.solver.settings.enabled:
            return other.display_positions
        if other.cache_playback and frame in other.disk_frames:
            return other.disk_frames[frame]
        if other.initial_pose is not None and other.initial_pose[0] > max(0, int(frame)):
            # A snapshot loaded at a later playhead is not a simulated pose for
            # the preceding animation. Request that source's early frames.
            return None
        cached = other.cache.get(frame)
        if cached is not None:
            return cached[0]
        if other.initial_pose is not None and frame <= other.initial_pose[0]:
            return other.initial_pose[1]
        return None

    def _cloth_dependencies_ready(self, config, state, items, frame, *, previous_frame=None):
        by_owner = {str(item.payload.get("owner") or "").casefold(): item for item in items}
        for owner in self._cloth_collider_owners(config):
            item = by_owner.get(owner.casefold())
            if item is not None and isinstance(item.payload.get("cloth"), dict):
                previous = state.frame if previous_frame is None else previous_frame
                samples = (previous, frame) if owner not in state.collider_previous else (frame,)
                for sample in samples:
                    if self._cloth_collider_pose(item, sample) is None:
                        # A bounded source cache may have evicted this frame.
                        # Ask that solver to rebuild it without publishing the
                        # internal pose at the user's current playhead.
                        requests = getattr(self, "_cloth_dependency_requests", None)
                        if requests is None:
                            requests = self._cloth_dependency_requests = {}
                        source = str(item.payload.get("owner") or "")
                        requests[source] = min(requests.get(source, sample), sample)
                        state.note = f"Waiting for {owner}: frame {sample}"
                        return False
        return True

    def _cloth_restore_preview(self, state, frame, cached, clock, *, publish=True):
        state.solver.x[:], state.solver.velocity[:] = cached
        state.solver.time = clock
        state.frame = int(frame)
        state.collider_previous.clear()
        if publish:
            state.display_positions = state.solver.x.copy()
            state.version += 1

    def _cloth_follow_frame(self, config, state, items, timeline, *, publish=True):
        if state.initial_pose[0] > max(0, int(timeline)):
            _, positions, velocity, clock = state.initial_pose
            start = min(state.solver.settings.start_frame, max(0, int(timeline)))
            # Loading/settling at frame 30 must not freeze frames 0–29 when
            # rewinding or rendering from the start. Preserve the exact saved
            # positions, velocity and sewing clock, and discard held previews.
            self._cloth_restore_preview(state, start, (positions, velocity), clock, publish=False)
            state.cache.clear()
            state.cache.put(start, positions, velocity, simulation_time=clock)
            self._cloth_mark_initial_pose(state)
        signature = self._cloth_animation_signature(config, items)
        if state.animation_signature and state.animation_signature != signature:
            frame, positions, velocity, clock = state.initial_pose
            state.cache.clear()
            state.cache.put(frame, positions, velocity, simulation_time=clock)
            self._cloth_restore_preview(state, frame, (positions, velocity), clock, publish=False)
            state.seek_start, state.seek_target = frame, timeline
        state.animation_signature = signature
        cached = state.cache.get(timeline)
        if cached is not None:
            if state.frame != timeline or state.seek_target is not None or state.last_timeline != timeline:
                if state.frame != timeline:
                    self._cloth_restore_preview(state, timeline, cached, state.cache.times.get(timeline,
                        state.initial_pose[3] + max(0, timeline - state.initial_pose[0]) / self._mgl_timeline_fps_value()),
                        publish=publish)
                elif publish:
                    state.display_positions = state.solver.x.copy()
                    state.version += 1
                if any(owner not in state.collider_previous for owner in self._cloth_collider_owners(config)):
                    self._cloth_colliders(config, state, items, frame=timeline)
            state.seek_start = state.seek_target = None
            state.note = "Preview cache"
            return
        initial_frame, positions, velocity, clock = state.initial_pose
        if timeline <= initial_frame:
            # Before the loaded starting frame, hold the saved pose; never
            # substitute the unsimulated pattern or integrate backwards.
            self._cloth_restore_preview(state, timeline, (positions, velocity), clock, publish=publish)
            state.cache.put(timeline, positions, velocity, simulation_time=clock)
            self._cloth_colliders(config, state, items, frame=timeline)
            state.seek_start = state.seek_target = None
            state.note = "Starting pose"
            return
        if state.seek_target != timeline:
            earlier = [frame for frame in state.cache.frames if initial_frame <= frame < timeline]
            frame = max(earlier, default=initial_frame)
            if state.frame != frame:
                cached = state.cache.get(frame) or (positions, velocity)
                self._cloth_restore_preview(state, frame, cached, state.cache.times.get(frame, clock), publish=False)
            state.seek_start, state.seek_target = frame, timeline
        next_frame = state.frame + 1
        if next_frame > getattr(self, "_cloth_sync_frame", timeline):
            state.note = f"Waiting for the other cloth solvers: frame {state.frame}"
            return
        if not self._cloth_dependencies_ready(config, state, items, next_frame):
            return
        if any(owner not in state.collider_previous for owner in self._cloth_collider_owners(config)):
            self._cloth_colliders(config, state, items, frame=state.frame)
        state.note = f"Calculating cloth: frame {next_frame} / {timeline}"
        self._cloth_schedule(state, self._cloth_colliders(config, state, items, frame=next_frame), "timeline", next_frame)

    def _cloth_worker(self):
        executor = getattr(self, "_cloth_executor", None)
        if executor is None:
            executor = self._cloth_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cloth-solve")
            # The worker owns only physics arrays. It never touches Qt or OpenGL,
            # and closing the viewport does not wait for an outstanding solve.
            self.destroyed.connect(lambda *_: executor.shutdown(wait=False, cancel_futures=True))
        return executor

    def _cloth_schedule(self, state, colliders, kind, frame):
        state.pending_kind, state.pending_frame = kind, int(frame)
        dt = 1 / self._mgl_timeline_fps_value() if kind == "timeline" else None
        state.pending = self._cloth_worker().submit(state.solver.step, dt=dt, colliders=colliders)

    def _cloth_collect(self, owner, config, state):
        if state.pending is None or not state.pending.done():
            return
        pending = state.pending
        state.pending = None
        try:
            pending.result()  # Already complete; never block Qt on a solve or write.
        except Exception:
            state.saving = None
            raise
        if state.pending_kind == "save":
            self._cloth_saved(owner, config, state)
            return
        state.frame = state.pending_frame
        if state.pending_kind == "live":
            state.timeline_cache_valid = False
        if state.pending_kind != "timeline" or state.seek_target is None:
            state.version += 1
            state.display_positions = state.solver.x.copy()
        if state.pending_kind == "bake" and state.bake:
            bake = state.bake
            bake["frames"].append(state.frame)
            bake["positions"].append(state.display_positions.copy())
            bake["velocities"].append(state.solver.velocity.copy())
            state.cache.put(state.frame, state.solver.x, state.solver.velocity, simulation_time=state.solver.time)
            bake["next"] += 1
            if bake.get("stop") or state.frame >= state.solver.settings.end_frame:
                self._cloth_finish_bake(owner, config, state)
            else:
                state.note = f"Baking frame {state.frame} / {state.solver.settings.end_frame}. Stop & save keeps completed frames."
                self._cloth_queue_bake_frame(owner, state, bake["next"])
        else:
            state.cache.put(state.frame, state.solver.x, state.solver.velocity, simulation_time=state.solver.time)
            state.note = ""

    def _cloth_status(self, model, state):
        if model is None:
            return
        model._cloth_baking = bool(state.bake)
        model._cloth_saving = bool(state.saving)
        model._cloth_seek_progress = ((state.seek_start, state.frame, state.seek_target)
                                     if state.seek_target is not None else None)
        model._cloth_playing = bool(state.playing and not state.follow_timeline and state.solver.settings.enabled)
        mode = ("Saving" if state.saving else "Baking" if state.bake else "Calculating" if state.seek_target is not None else
                f"Timeline frame {state.frame}" if state.follow_timeline else "Running" if state.playing else "Paused")
        backend_detail = state.solver.backend_detail
        if state.solver.settings.device == "cpu" and not backend_detail:
            backend_detail = "CPU backend selected. Choose auto or cuda to use the GPU."
        detail = "\n".join(dict.fromkeys(d for d in (state.note, backend_detail) if d))
        if state.pending is not None and not state.solver.last_ms:
            detail = "Preparing solver on the simulation worker…"
        gpu = f" · GPU {state.solver.last_gpu_ms:.1f} ms" if state.solver.backend.startswith("Warp CUDA") else ""
        model._cloth_status = (f"{mode} · {state.solver.backend} · {len(state.solver.x)} vertices · "
                               f"{state.solver.last_ms:.1f} ms/frame{gpu}\n{detail}")

    @staticmethod
    def _cloth_collider_owners(config):
        owners = [o.strip() for o in str(config.get("collider_owner", "")).split(",") if o.strip()]
        if not owners:
            owners = [str(c.get("node") or "") for c in config.get("colliders", [])]
        return list(dict.fromkeys(owners))

    def _cloth_colliders(self, config, state, items, *, frame=None):
        visible_frame = int(self._mgl_timeline_frame_index())
        if frame is None or int(frame) == visible_frame:
            return self._cloth_colliders_current_frame(config, state, items)
        apply_frame = getattr(self, "_timeline_apply_scene_frame", None)
        previous = getattr(self, "_cloth_sample_frame", None)
        self._cloth_sample_frame = int(frame)
        try:
            if callable(apply_frame):
                apply_frame(int(frame))
            return self._cloth_colliders_current_frame(config, state, items)
        finally:
            self._cloth_sample_frame = previous
            if callable(apply_frame):
                apply_frame(visible_frame)

    def _cloth_colliders_current_frame(self, config, state, items):
        owners = self._cloth_collider_owners(config)
        by_owner = {str(item.payload.get("owner") or "").lower(): item for item in items}
        result = []
        units = state.solver.settings.scene_units_per_meter
        for owner in owners:
            item = by_owner.get(owner.lower())
            if item is None:
                raise RuntimeError("Collider not in Scene: " + owner)
            payload = item.payload
            surface_thickness = 0.0
            context = payload.get("fbx_rig_context")
            if isinstance(payload.get("cloth"), dict):
                frame = int(self._mgl_timeline_frame_index())
                positions = self._cloth_collider_pose(item, frame)
                if positions is None:
                    raise RuntimeError(f"Waiting for cloth collider {owner}: frame {frame}")
                other = self._cloth_states[str(payload.get("owner") or "")]
                # Solvers store world metres; the target may use a different
                # scene-unit conversion. Use every panel in the parent garment.
                world = np.asarray(positions, dtype=float).copy() * (other.solver.settings.scene_units_per_meter / units)
                surface_thickness = other.solver.settings.thickness * other.solver.settings.scene_units_per_meter / units
                faces = other.solver.asset.triangles
            elif (isinstance(context, dict) and context.get("skeleton") is not None
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
            if not isinstance(payload.get("cloth"), dict):
                world = transform_points(local, self._cloth_matrix(owner)) / units
            previous = state.collider_previous.get(owner)
            collider = MeshCollider(world, faces, previous, two_sided=isinstance(payload.get("cloth"), dict),
                                    surface_thickness=surface_thickness)
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
        topology = self._mgl_mesh_topology_for_owner(owner)
        if isinstance(topology, dict):
            mapping = np.concatenate([entry["_cloth_vertices"] for entry in self._mgl_mesh_entries_for_payload(item.payload)])
            if len(mapping) == len(topology["points"]):
                topology["vertex_ids"] = mapping
                topology["source_owner"] = owner
        self._cloth_publish_panels(owner, item.payload["cloth"], state)
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

    def _cloth_queue_bake_frame(self, owner, state, frame):
        def advance():
            if (getattr(self, "_cloth_states", {}).get(owner) is state and state.bake
                    and not state.bake.get("stop")):
                self._cloth_set_timeline(frame)
        QtCore.QTimer.singleShot(0, advance)

    def _cloth_queue_pose_frame(self, owner, state, frame):
        def seek():
            if (getattr(self, "_cloth_states", {}).get(owner) is state and state.frame == frame
                    and not state.playing and not state.bake):
                self._cloth_set_timeline(frame)
        QtCore.QTimer.singleShot(0, seek)

    def _cloth_stop(self, owner, config, state):
        state.playing = False
        state.next_tick = 0
        drag = getattr(self, "_cloth_drag", None)
        if drag is not None and drag[0] == owner:
            release = getattr(state.solver, "release_grab", None)
            if callable(release):
                release()
            self._cloth_drag = None
        if state.bake:
            state.bake["stop"] = True
            state.note = "Stopping bake; saving completed frames…"
            if state.pending is None:
                self._cloth_finish_bake(owner, config, state)

    def _cloth_timeline_play_toggled(self, checked):
        stopped_bake = False
        for item in self._cloth_items():
            owner = str(item.payload.get("owner") or "")
            state = getattr(self, "_cloth_states", {}).get(owner)
            if state is not None and (state.bake or (not checked and state.playing)):
                stopped_bake = stopped_bake or bool(state.bake)
                self._cloth_stop(owner, item.payload["cloth"], state)
        if checked and stopped_bake:
            stop = getattr(self, "_timeline_stop_playback", None)
            if callable(stop):
                QtCore.QTimer.singleShot(0, stop)
        self.update()

    def _cloth_timeline_active(self):
        for item in self._cloth_items():
            config = item.payload.get("cloth")
            if isinstance(config, dict) and not config.get("placement_only"):
                settings = ClothSettings.from_dict(config["settings"])
                if settings.enabled and settings.follow_timeline:
                    return True
        return False

    def _cloth_timeline_ready(self, frame):
        """Hold the playhead until every timeline cloth has published this frame."""
        states = getattr(self, "_cloth_states", {})
        items = self._cloth_items()
        for item in items:
            config = item.payload.get("cloth")
            if not isinstance(config, dict) or config.get("placement_only"):
                continue
            settings = ClothSettings.from_dict(config["settings"])
            if not settings.enabled or not settings.follow_timeline:
                continue
            owner = str(item.payload.get("owner") or "")
            state = states.get(owner)
            if (state is None or state.pending is not None or not state.follow_timeline
                    or state.last_timeline != int(frame)
                    or state.frame != int(frame) or state.error
                    or (state.initial_pose is not None and state.initial_pose[0] > max(0, int(frame))
                        and not (state.cache_playback and int(frame) in state.disk_frames))
                    or any(owner not in state.collider_previous for owner in self._cloth_collider_owners(config))
                    or state.geometry_signature != cloth_signature(config, self._cloth_matrix(owner), include_settings=False)
                    or state.solver.settings != settings):
                return False
            if state.animation_signature:
                try:
                    if state.animation_signature != self._cloth_animation_signature(config, items):
                        return False
                except RuntimeError:
                    return False  # The viewport reports collider loops on its next update.
        return True

    def _cloth_render_ready(self, frame):
        return (self._cloth_timeline_ready(frame)
                and all(state.pending is None for state in getattr(self, "_cloth_states", {}).values()))

    def _cloth_render_error(self, frame):
        for item in self._cloth_items():
            config = item.payload.get("cloth")
            if not isinstance(config, dict) or config.get("placement_only"):
                continue
            owner = str(item.payload.get("owner") or "")
            state = getattr(self, "_cloth_states", {}).get(owner)
            if state is not None and state.error:
                return f"{owner}: {state.error}"
            settings = ClothSettings.from_dict(config["settings"])
            if (state is not None and state.pending is None and settings.enabled and settings.follow_timeline
                    and state.seek_target is None
                    and state.frame != int(frame) and state.last_timeline == int(frame)):
                return f"{owner}: {state.note or 'No cloth pose available for this timeline frame.'}"
        return ""

    def _cloth_render_preroll_start(self, target):
        """Continue sequentially from cached cloth instead of skipping solve frames."""
        start = int(target)
        for item in self._cloth_items():
            config = item.payload.get("cloth")
            if not isinstance(config, dict) or config.get("placement_only"):
                continue
            settings = ClothSettings.from_dict(config["settings"])
            if not settings.enabled or not settings.follow_timeline:
                continue
            state = getattr(self, "_cloth_states", {}).get(str(item.payload.get("owner") or ""))
            frames = set(state.cache.frames) if state is not None else set()
            if state is not None and state.cache_playback:
                frames.update(state.disk_frames)
            if state is not None:
                frames.add(state.frame)
            if int(target) not in frames:
                earlier = [frame for frame in frames if frame <= int(target)]
                start = min(start, max(earlier) if earlier else settings.start_frame)
        return max(0, start)

    def _cloth_queue_timeline_step(self, owner, state, frame):
        def advance():
            if (getattr(self, "_cloth_states", {}).get(owner) is state and state.follow_timeline
                    and state.solver.settings.enabled and not state.bake and not state.saving):
                stop = getattr(self, "_timeline_stop_playback", None)
                if callable(stop):
                    stop()
                self._cloth_set_timeline(frame)
        QtCore.QTimer.singleShot(0, advance)

    @staticmethod
    def _cloth_cache_path(config, path):
        config["cache_path"] = path
        if config.get("node_item") is not None:
            from nodes.cloth_solver.spec import set_value
            set_value(config["node_item"], "cache_path", path)
        elif config.get("node_model") is not None:
            model = config["node_model"]
            parameter = next((p for p in model.params if p.get("name") == "cache_path"), None)
            if parameter is None:
                model.params.append({"name": "cache_path", "value": path})
            else:
                parameter["value"] = path

    def _cloth_command(self, command, owner, config, state, timeline):
        action = command.get("action")
        if action in {"pin", "unpin", "clear_pins"}:
            selected = getattr(self, "_mesh_selected_elements", lambda: [])()
            indices = set()
            for element in selected:
                if element.get("mode") != "point":
                    continue
                selected_owner = str(element.get("owner", ""))
                topology = self._mgl_mesh_topology_for_owner(selected_owner) or {}
                source_owner = str(topology.get("source_owner", selected_owner))
                if source_owner.casefold() != owner.casefold():
                    continue
                index = int(element["vertex_index"])
                vertex_ids = topology.get("vertex_ids")
                if vertex_ids is not None:
                    if not 0 <= index < len(vertex_ids):
                        continue
                    index = int(vertex_ids[index])
                if 0 <= index < len(state.solver.x):
                    indices.add(index)
            if action != "clear_pins" and not indices:
                raise RuntimeError("Select cloth vertices in viewport point mode first.")
            pins = set(config.get("pins", []))
            positions = dict(config.get("pin_positions", {}))
            if action == "pin":
                for index in indices - pins:
                    positions[str(index)] = state.display_positions[index].tolist()
                pins.update(indices)
            elif action == "unpin":
                pins.difference_update(indices)
            else:
                pins.clear()
            config["pins"] = sorted(pins)
            config["pin_positions"] = {key: value for key, value in positions.items() if int(key) in pins}
            # Write both parameters in one notification. Scene refresh must see
            # the new anchors with their IDs, and physics waits for an idle worker.
            def persist():
                node = config.get("node_item")
                if node is not None:
                    from shiboken6 import isValid
                    if isValid(node):
                        node._set_param_value("pin_positions", json.dumps(config["pin_positions"]),
                                              rebuild=False, notify_scene=False)
                        node._set_param_value("pins", json.dumps(config["pins"]), rebuild=False)
                elif config.get("node_model") is not None:
                    model = config["node_model"]
                    for name in ("pins", "pin_positions"):
                        parameter = next((p for p in model.params if p.get("name") == name), None)
                        if parameter is None:
                            model.params.append({"name": name, "value": json.dumps(config[name])})
                        else:
                            parameter["value"] = json.dumps(config[name])
            QtCore.QTimer.singleShot(0, persist)
            state.note = f"{len(pins)} pinned point(s) · Current cloth pose kept"
            return state
        if action == "reset":
            if config.get("cache_path"):
                self._cloth_cache_path(config, "")
            return self._cloth_reset(owner, config, self._cloth_matrix(owner), timeline)
        if state.saving and action in {"toggle", "step", "bake", "save"}:
            state.note = "Finishing cache save…"
            return state
        if action == "toggle":
            if state.bake:
                self._cloth_stop(owner, config, state)
            elif ClothSettings.from_dict(config["settings"]).follow_timeline:
                state.note = "Use the viewport timeline controls to play or pause the simulation."
            else:
                state.playing = not state.playing
                state.next_tick = 0
                state.cache_playback = False
        elif action in {"pause", "stop"}:
            self._cloth_stop(owner, config, state)
            stop = getattr(self, "_timeline_stop_playback", None)
            if callable(stop):
                stop()
        elif action == "save":
            self._cloth_stop(owner, config, state)
            state.save_requested = command["path"]
        elif action == "load_cache":
            self._cloth_stop(owner, config, state)
            state.load_requested = command["path"]
        elif action == "step":
            if ClothSettings.from_dict(config["settings"]).follow_timeline and not state.bake:
                self._cloth_queue_timeline_step(owner, state, timeline + 1)
            else:
                state.playing = False
                state.next_tick = -1  # one fixed step, even when the timeline is idle
                state.cache_playback = False
        elif action == "bake":
            settings = state.solver.settings
            frames = settings.end_frame - settings.start_frame + 1
            if frames * len(state.solver.x) * 24 > 512 * 1024**2:
                raise RuntimeError("Bake exceeds 512 MB. Shorten the frame range or use a coarser mesh.")
            stop = getattr(self, "_timeline_stop_playback", None)
            if callable(stop):
                stop()
            state = self._cloth_reset(owner, config, self._cloth_matrix(owner), settings.start_frame)
            state.bake = {"path": command["path"], "frames": [state.frame], "positions": [state.solver.x.copy()],
                          "velocities": [state.solver.velocity.copy()],
                          "next": settings.start_frame + 1, "primed": False, "stop": False}
            # Defer the frame change until the current paint finishes.
            self._cloth_queue_bake_frame(owner, state, settings.start_frame)
        return state

    def _cloth_finish_bake(self, owner, config, state):
        bake = state.bake
        state.bake = None
        self._cloth_save(owner, state, bake["path"], bake["frames"], bake["positions"], bake["velocities"],
                         cache_type="bake")

    def _cloth_save(self, owner, state, path, frames, positions, velocities, *, cache_type="state"):
        state.playing = False
        state.saving = {"path": path, "frames": frames, "positions": positions, "velocities": velocities}
        metadata = {"signature": state.signature, "owner": owner, "resume_frame": int(frames[-1]),
                    "solver_time": state.solver.time, "cache_type": cache_type,
                    "geometry_signature": state.geometry_signature, "placement_signature": state.placement_signature,
                    "settings": state.solver.settings.to_dict()}
        state.saving["metadata"] = metadata
        state.note = f"Saving {len(frames)} frame(s)…"
        state.pending_kind = "save"
        state.pending = self._cloth_worker().submit(write_bake, path, metadata, frames, positions, velocities)

    def _cloth_saved(self, owner, config, state):
        saved = state.saving
        state.saving = None
        state.playing = False
        state.disk_metadata = saved["metadata"]
        if state.disk_metadata["cache_type"] == "bake":
            state.cache_playback = True
            state.disk_frames = dict(zip(saved["frames"], saved["positions"]))
            state.disk_velocities = dict(zip(saved["frames"], saved["velocities"]))
            # Keep the last completed pose visible when a bake is stopped.
            self._cloth_queue_pose_frame(owner, state, state.frame)
        else:
            # A snapshot is an initial condition, not animation cache playback.
            state.disk_frames.clear()
            state.disk_velocities.clear()
        state.note = f"Saved {len(saved['frames'])} frame(s) to {saved['path']}"
        self._cloth_cache_path(config, saved["path"])
        state.cache_path = saved["path"]

    @staticmethod
    def _cloth_cache_is_state(metadata, frames):
        # Older Save state files have no type and only one recorded pose.
        return len(frames) == 1 and metadata.get("cache_type", "state") == "state"

    def _cloth_restore_saved_state(self, state, metadata, frames, velocities, timeline):
        saved_frame = next(iter(frames))
        state.solver.x[:] = frames[saved_frame]
        state.solver.velocity[:] = velocities[saved_frame]
        state.solver.time = float(metadata.get("solver_time",
            max(0, saved_frame - state.solver.settings.start_frame) / state.solver.settings.fps))
        state.display_positions = state.solver.x.copy()
        # The real-time step count must never seek the animation playhead.
        state.frame = int(timeline) if state.follow_timeline else saved_frame
        state.last_timeline = int(timeline)
        state.cache.put(state.frame, state.solver.x, state.solver.velocity, simulation_time=state.solver.time)
        state.disk_frames.clear()
        state.disk_velocities.clear()
        self._cloth_mark_initial_pose(state)
        state.note = "Saved state loaded. Play continues from this pose."

    def _cloth_apply_settings(self, state, settings):
        previous = state.solver.settings
        if previous == settings:
            return
        state.solver.update_settings(settings)
        controls = {k for k in settings.to_dict() if k not in {"enabled", "interactive", "follow_timeline"}}
        if any(getattr(previous, key) != getattr(settings, key) for key in controls):
            # Preview frames remain reusable until the collider animation changes.
            # Edited controls apply to frames that have not yet been calculated.
            state.cache_playback = False
            state.cache.put(state.frame, state.solver.x, state.solver.velocity, simulation_time=state.solver.time)
            state.note = "Settings updated. Cached frames retained; new frames use these settings."
        if previous.scene_units_per_meter != settings.scene_units_per_meter:
            state.version += 1
            state.collider_previous.clear()

    def _cloth_apply_constraints(self, state, config):
        elastic_changed = state.solver.update_elastic(config["asset"])
        pins_changed = state.solver.update_pins(config.get("pins", []), config.get("pin_positions", {}))
        if elastic_changed or pins_changed:
            # Retain the loaded initial condition and completed history. Only
            # future previews must be solved again with the edited constraints.
            state.cache.truncate_after(state.frame)
            state.cache.put(state.frame, state.solver.x, state.solver.velocity, simulation_time=state.solver.time)
            state.cache_playback = False
            state.display_positions = state.solver.x.copy()
            state.version += 1
            state.pose_revision += 1
            state.seek_start = state.seek_target = None
            state.note = (f"{len(state.solver.pins)} pinned point(s) · Current cloth pose kept" if pins_changed else
                          "Elastic updated · Current cloth pose kept")
        state.signature = cloth_signature(config, state.matrix)

    def _cloth_pinned_elements(self):
        elements = []
        for owner, state in getattr(self, "_cloth_states", {}).items():
            pins = getattr(state.solver, "pins", ())
            if not len(pins) or not self._mgl_scene_owner_visible(owner):
                continue
            topology = self._mgl_mesh_topology_for_owner(owner) or {}
            vertex_ids = topology.get("vertex_ids")
            if vertex_ids is None:
                indices = pins
            else:
                ids, first = np.unique(vertex_ids, return_index=True)
                indices = first[np.isin(ids, pins)]
            elements.extend({"mode": "point", "owner": owner, "vertex_index": int(index)} for index in indices)
        return elements

    def _cloth_draw_pins(self, mvp):
        if getattr(self, "_mgl_render_output_active", False):
            return
        self._mgl_draw_component_preview("cloth-pins", self._cloth_pinned_elements(),
                                         (1.0, .08, .06, 1.0), mvp, point_size=8.0)

    def _mgl_update_cloth(self):
        items = self._cloth_items()
        states = getattr(self, "_cloth_states", None)
        if states is None:
            states = self._cloth_states = {}
        timeline = int(self._mgl_timeline_frame_index())
        requested_timeline = timeline
        dependency_requests = getattr(self, "_cloth_dependency_requests", {})
        self._cloth_dependency_requests = {}
        now, active = time.perf_counter(), set()
        try:
            ordered = self._cloth_order_items(items)
        except RuntimeError as exc:
            for state in states.values():
                state.error = str(exc)
                state.playing = False
            for item in items:
                config = item.payload.get("cloth")
                if isinstance(config, dict) and config.get("node_model") is not None:
                    config["node_model"]._cloth_status = "Cloth error: " + str(exc)
            stop = getattr(self, "_timeline_stop_playback", None)
            if callable(stop):
                stop()
            return
        self._cloth_sync_frame = self._cloth_work_frame(items, timeline)
        for item in ordered:
            # Loading a baked range can resume this owner at its recorded
            # frame. Never let that temporary frame seed another solver.
            timeline = requested_timeline
            config = item.payload.get("cloth")
            if not isinstance(config, dict):
                continue
            owner = str(item.payload.get("owner") or "")
            active.add(owner)
            model = config.get("node_model")
            state = states.get(owner)
            try:
                matrix = self._cloth_matrix(owner)
                signature = cloth_signature(config, matrix, include_settings=False)
                placement_signature = cloth_signature(config, matrix, include_settings=False, include_colliders=False)
                if state is None or state.placement_signature != placement_signature:
                    state = states[owner] = self._cloth_reset(owner, config, matrix, timeline)
                    self._cloth_colliders(config, state, items)
                if config.get("placement_only"):
                    self._cloth_publish(item, state)
                    continue
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
                if state.geometry_signature != signature:
                    # Adding/removing a collider changes preview animation, not
                    # the garment's loaded, sewn initial condition.
                    state.geometry_signature = signature
                    state.collider_previous.clear()
                self._cloth_apply_settings(state, settings)
                self._cloth_apply_constraints(state, config)
                if state.follow_timeline != settings.follow_timeline:
                    state.follow_timeline = settings.follow_timeline
                    state.playing, state.next_tick = False, 0
                    if (settings.follow_timeline and not state.timeline_cache_valid
                            and not (state.disk_frames and state.cache_playback) and not state.bake):
                        # The live clock's frame count is unrelated to the playhead.
                        # Start here from the settled pose and its simulation time.
                        state.frame = state.last_timeline = timeline
                        state.cache.clear()
                        state.cache.put(timeline, state.solver.x, state.solver.velocity,
                                        simulation_time=state.solver.time)
                        self._cloth_mark_initial_pose(state)
                        state.collider_previous.clear()
                        self._cloth_colliders(config, state, items)
                        state.note = "Timeline mode. Play or scrub to simulate the cloth."
                if not settings.follow_timeline or not settings.enabled:
                    if state.seek_target is not None:
                        state.display_positions = state.solver.x.copy()
                        state.version += 1
                    state.seek_start = state.seek_target = None
                if state.load_requested:
                    self._cloth_cache_path(config, state.load_requested)
                    state.load_requested = ""
                    state.cache_path = ""  # Choosing the same file again reloads it.
                if (state.save_requested and state.seek_target is None
                        and (not settings.follow_timeline or state.frame == timeline)):
                    path, state.save_requested = state.save_requested, ""
                    self._cloth_save(owner, state, path, [state.frame], [state.display_positions.copy()],
                                     [state.solver.velocity.copy()])
                    self._cloth_status(model, state)
                    continue
                path = config.get("cache_path", "")
                if path != state.cache_path:
                    if path:
                        legacy_geometry = {cloth_signature({**config, "collider_owner": value}, matrix,
                                           include_settings=False) for value in
                                           ("", str(config.get("collider_owner", "")).split(",")[0].strip())}
                        metadata, frames, velocities = read_bake(path, state.signature, include_velocities=True,
                            geometry_signature=state.geometry_signature, placement_signature=state.placement_signature,
                            legacy_geometry_signatures=legacy_geometry, allow_legacy_state=True)
                        if any(p.shape != state.solver.x.shape for p in frames.values()):
                            raise RuntimeError("Cache vertex count differs from the cloth mesh.")
                        state.disk_metadata = metadata
                        state.cache.clear()
                        state.collider_previous.clear()
                        state.playing, state.next_tick = False, 0
                        if self._cloth_cache_is_state(metadata, frames):
                            self._cloth_restore_saved_state(state, metadata, frames, velocities, timeline)
                        else:
                            state.cache_playback = True
                            state.disk_frames = frames
                            state.disk_velocities = velocities
                            if "resume_frame" in metadata or timeline not in frames:
                                timeline = int(metadata.get("resume_frame", max(frames)))
                                if timeline not in frames:
                                    timeline = max(frames)
                                self._cloth_queue_pose_frame(owner, state, timeline)
                    else:
                        state.disk_frames.clear()
                        state.disk_velocities.clear()
                    state.cache_path = path
                    state.version += 1
                # Scene assembly/cache loading can precede collider availability.
                # Prime their current pose before allowing the playhead to advance.
                if any(owner not in state.collider_previous for owner in self._cloth_collider_owners(config)):
                    if self._cloth_dependencies_ready(config, state, items, state.frame):
                        self._cloth_colliders(config, state, items, frame=state.frame if state.follow_timeline else None)
                changed = timeline != state.last_timeline
                stepping = settings.enabled and not settings.follow_timeline and not getattr(self, "_mgl_render_output_active", False) and (
                    state.next_tick == -1 or (state.playing and now >= state.next_tick))
                if state.disk_frames and state.cache_playback and not state.bake:
                    frame = state.disk_frames.get(timeline)
                    preview = state.cache.get(timeline) if frame is None and settings.follow_timeline else None
                    if preview is not None:
                        frame = preview[0]
                    if frame is not None:
                        state.solver.x[:] = frame
                        state.display_positions = state.solver.x.copy()
                        state.solver.velocity[:] = (preview[1] if preview is not None else
                                                    state.disk_velocities.get(timeline, np.zeros_like(frame)))
                        state.solver.time = (state.cache.times[timeline] if preview is not None else
                                             float(state.disk_metadata["solver_time"])
                                             if timeline == state.disk_metadata.get("resume_frame")
                                             else max(0, timeline - settings.start_frame) / settings.fps)
                        if changed:
                            state.version += 1
                            state.collider_previous.clear()
                        state.frame = timeline
                        if state.cache.get(timeline) is None:
                            state.cache.put(timeline, state.solver.x, state.solver.velocity,
                                            simulation_time=state.solver.time)
                        state.note = f"Cache frame {timeline}. Cloth Play continues from this pose."
                    elif settings.enabled and settings.follow_timeline and timeline == state.frame + 1:
                        if self._cloth_dependencies_ready(config, state, items, timeline):
                            self._cloth_schedule(state, self._cloth_colliders(config, state, items), "timeline", timeline)
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
                            self._cloth_queue_bake_frame(owner, state, bake["next"])
                    elif (bake["primed"] and timeline == bake["next"]
                          and self._cloth_dependencies_ready(config, state, items, timeline)):
                        self._cloth_schedule(state, self._cloth_colliders(config, state, items), "bake", timeline)
                elif settings.enabled and settings.follow_timeline:
                    target = dependency_requests.get(owner, timeline)
                    if target != timeline and self._cloth_collider_pose(item, target) is not None:
                        target = timeline
                    self._cloth_follow_frame(config, state, items, target, publish=target == timeline)
                elif (stepping and not (state.disk_frames and state.cache_playback)
                      and self._cloth_dependencies_ready(config, state, items, timeline, previous_frame=timeline)):
                    self._cloth_schedule(state, self._cloth_colliders(config, state, items), "live", state.frame + 1)
                    interval = 1 / settings.fps
                    state.next_tick = state.next_tick + interval if state.next_tick > 0 else now + interval
                    if state.next_tick < now:
                        state.next_tick = now + interval
                    state.note = ""
                state.last_timeline = timeline
                if state.frame == timeline:
                    state.error = ""
                self._cloth_publish(item, state)
                self._cloth_status(model, state)
            except Exception as exc:
                if state is not None:
                    state.playing = False
                    state.bake = None
                    state.error = str(exc)
                if model is not None:
                    model._cloth_baking = False
                    model._cloth_playing = False
                    model._cloth_saving = False
                    model._cloth_seek_progress = None
                    model._cloth_status = "Cloth error: " + str(exc)
                if ClothSettings.from_dict(config.get("settings", {})).follow_timeline:
                    stop = getattr(self, "_timeline_stop_playback", None)
                    if callable(stop):
                        stop()
        # Keep paused states across graph refreshes, but remove disconnected solvers.
        for owner in set(states) - active:
            disconnected = states.pop(owner, None)
            if disconnected is not None and disconnected.pending is not None:
                disconnected.pending.cancel()
        live = any(s.playing or s.bake or s.pending is not None or s.save_requested
                   or s.seek_target is not None for s in states.values())
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
        state.playing = not state.follow_timeline
        state.disk_frames.clear()
        self._mgl_pick_press_pos = None
        self.update()
        return True

    def _cloth_mouse_move(self, event):
        drag = getattr(self, "_cloth_drag", None)
        if drag is None:
            return False
        state = getattr(self, "_cloth_states", {}).get(drag[0])
        if state is None or getattr(state.solver, "grab", None) is None:
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
            release = getattr(state.solver, "release_grab", None)
            if callable(release):
                release()
            state.playing = drag[3] and state.solver.settings.enabled
        self._cloth_drag = None
        self.update()
        return True
