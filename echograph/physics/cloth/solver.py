"""Colored XPBD cloth with stretch, curvature, sewing, pins and temporary grabs."""
import time
import numpy as np
from scipy.spatial import cKDTree
from .settings import ClothSettings
from .collisions import friction_correction


def constraint_colors(indices, weights):
    """No two constraints in a batch write the same particle (CPU/CUDA safe)."""
    batches, used = [], []
    for row, (vertices, coeffs) in enumerate(zip(indices, weights)):
        active = {int(v) for v, w in zip(vertices, coeffs) if abs(w) > 1e-12}
        for batch, occupied in zip(batches, used):
            if not active & occupied:
                batch.append(row)
                occupied.update(active)
                break
        else:
            batches.append([row])
            used.append(active)
    return [np.asarray(batch, dtype=np.int32) for batch in batches]


class ClothSolver:
    def __init__(self, asset, settings=None, world_positions=None, pins=(), *, defer_backend=False):
        self.asset = asset
        self.settings = settings or ClothSettings()
        self.rest = np.array(asset.positions if world_positions is None else world_positions, dtype=float, copy=True)
        self.pin_positions = self.rest.copy()
        self.x = self.rest.copy()
        self.velocity = np.zeros_like(self.x)
        tri = self.rest[asset.triangles]
        area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) * 0.5
        self._particle_areas = np.zeros(len(self.x))
        np.add.at(self._particle_areas, asset.triangles.ravel(), np.repeat(area / 3, 3))
        self.inverse_mass = 1 / np.maximum(self._particle_areas * self.settings.density, 1e-8)
        self.pins = np.asarray(sorted(set(int(i) for i in pins if 0 <= int(i) < len(self.x))), dtype=np.int32)
        self.inverse_mass[self.pins] = 0
        self._mass_revision = 0
        self.groups = []
        self._add_stretch_elastic_groups()
        self._add_group(asset.bends, asset.bend_weights, self.settings.bend, "bend")
        self._add_group(asset.seam_indices, asset.seam_weights, self.settings.seam_stiffness, "seam")
        self.time = 0.0
        self.grab = None
        self.last_ms = 0.0
        self.last_gpu_ms = 0.0
        self.backend = "CPU XPBD"
        self.backend_detail = ""
        self._accelerator = None
        self._excluded = {tuple(sorted(map(int, edge))) for edge in asset.edges}
        # Sewn samples and immediate triangle neighbors may touch by design.
        for row in asset.seam_indices:
            for a in row[:2]:
                for b in row[2:]:
                    self._excluded.add(tuple(sorted((int(a), int(b)))))
        self._neighbors = [{i} for i in range(len(self.x))]
        for a, b in self._excluded:
            self._neighbors[a].add(b)
            self._neighbors[b].add(a)
        self._neighbor_keys = np.asarray(sorted(vertex * len(self.x) + neighbor
            for vertex, neighbors in enumerate(self._neighbors) for neighbor in neighbors), dtype=np.int64)
        self._backend_device = None
        if not defer_backend:
            self._configure_backend()
        elif self.settings.device != "cpu":
            self.backend = "CUDA pending"
            self.backend_detail = "GPU initialization starts with the first solve."

    def _configure_backend(self):
        self._backend_device = self.settings.device
        self._accelerator = None
        self.backend, self.backend_detail, self.last_gpu_ms = "CPU XPBD", "", 0.0
        if self.settings.device != "cpu":
            try:
                from echograph.physics.gpu.warp_cloth import WarpProjector
                self._accelerator = WarpProjector(self, require_cuda=self.settings.device == "cuda")
                self.backend = self._accelerator.label
            except Exception as exc:
                if isinstance(exc, ModuleNotFoundError) and exc.name == "warp":
                    self.backend_detail = "Warp is not installed in the app environment. Run setup.bat core and restart QubitMCP."
                else:
                    self.backend_detail = "Warp unavailable; CPU fallback: " + str(exc)

    def update_settings(self, settings):
        """Apply controls between solves, preserving positions, velocity and clock."""
        previous = self.settings
        if settings == previous:
            return
        self.settings = settings
        stiffness = {"stretch": settings.stretch, "bend": settings.bend, "seam": settings.seam_stiffness}
        if settings.density != previous.density:
            self.inverse_mass[:] = 1 / np.maximum(self._particle_areas * settings.density, 1e-8)
            self.inverse_mass[self.pins] = 0
            self._mass_revision += 1
        for group in self.groups:
            group["stiffness"] = stiffness.get(group["kind"], group["stiffness"])
            group["lambda"].fill(0)
            if settings.density != previous.density:
                batches = []
                for rows, ids, weights, _, _, active in group["batches"]:
                    mass = self.inverse_mass[ids]
                    batches.append((rows, ids, weights, mass, np.sum(weights * weights * mass, axis=1), active))
                group["batches"] = batches
        if settings.device != previous.device:
            # Build the new GPU backend on the solve worker, not the Qt thread.
            self._backend_device = None
            self._accelerator = None
            self.backend, self.backend_detail, self.last_gpu_ms = "CPU XPBD", "", 0.0

    def _refresh_constraint_mass(self):
        for group in self.groups:
            group["lambda"].fill(0)
            group["batches"] = [(rows, ids, weights, self.inverse_mass[ids],
                                np.sum(weights * weights * self.inverse_mass[ids], axis=1), active)
                               for rows, ids, weights, _, _, active in group["batches"]]

    def update_pins(self, pins, positions=None):
        """Pin the current pose without changing fabric rest lengths or resetting the solve."""
        pins = np.asarray(sorted(set(int(i) for i in pins)), dtype=np.int32)
        if np.any(pins < 0) or np.any(pins >= len(self.x)):
            raise ValueError("Invalid cloth pin vertices.")
        anchors = self.pin_positions.copy()
        added = np.setdiff1d(pins, self.pins)
        anchors[added] = self.x[added]
        for index in pins:
            value = (positions or {}).get(str(index), (positions or {}).get(int(index)))
            if value is not None:
                value = np.asarray(value, dtype=float)
                if value.shape != (3,) or not np.isfinite(value).all():
                    raise ValueError("Cloth pin positions must be finite 3D points.")
                anchors[index] = value
        if np.array_equal(pins, self.pins) and np.array_equal(anchors[pins], self.pin_positions[pins]):
            return False
        self.pins = pins
        self.pin_positions[:] = anchors
        self.inverse_mass[:] = 1 / np.maximum(self._particle_areas * self.settings.density, 1e-8)
        self.inverse_mass[pins] = 0
        self.x[pins] = anchors[pins]
        self.velocity[pins] = 0
        self._mass_revision += 1
        self._refresh_constraint_mass()
        return True

    def _add_stretch_elastic_groups(self):
        asset = self.asset
        elastic = {tuple(sorted(edge)) for edge in asset.elastic_indices}
        ordinary = np.asarray([edge for edge in asset.edges if tuple(edge) not in elastic], dtype=np.int32).reshape(-1, 2)
        self._add_group(ordinary, np.tile([1.0, -1.0], (len(ordinary), 1)), self.settings.stretch, "stretch")
        for rows in asset.elastic_bands:
            indices = asset.elastic_indices[rows]
            vertices = np.unique(indices)
            roots = vertices.copy()
            for weld in asset.elastic_welds:
                mask = np.isin(vertices, weld)
                if np.any(mask):
                    roots[mask] = weld[0]
            _, aliases = np.unique(roots, return_inverse=True)
            order = np.argsort(aliases, kind="stable")
            lengths = np.linalg.norm(self.rest[indices[:, 0]] - self.rest[indices[:, 1]], axis=1)
            segment_rest = lengths * asset.elastic_ratios[rows]
            self.groups.append({"kind": "elastic", "indices": indices, "vertices": vertices,
                "aliases": aliases, "segment_aliases": aliases[np.searchsorted(vertices, indices)],
                "members": vertices[order], "member_offsets": np.r_[0, np.cumsum(np.bincount(aliases))],
                "rest": np.array([segment_rest.sum()]), "segment_rest": segment_rest,
                "segment_stiffness": asset.elastic_strengths[rows],
                "stiffness": float(1 / np.sum(1 / asset.elastic_strengths[rows])),
                "lambda": np.zeros(len(indices)), "batches": [],
                "colors": constraint_colors(indices, np.tile([1.0, -1.0], (len(indices), 1)))})

    def update_elastic(self, asset):
        """Apply edge material edits to an existing settled garment with identical topology."""
        if asset.signature == self.asset.signature:
            return False
        if ((asset.topology_signature or asset.signature) !=
                (self.asset.topology_signature or self.asset.signature)):
            raise ValueError("Elastic updates require unchanged cloth topology.")
        self.asset = asset
        others = [group for group in self.groups if group["kind"] not in {"stretch", "elastic"}]
        self.groups = []
        self._add_stretch_elastic_groups()
        self.groups.extend(others)
        # New constraint buffers are built on the solve worker on its next step.
        self._backend_device = None
        self._accelerator = None
        return True

    def _add_group(self, indices, weights, stiffness, kind):
        if not len(indices):
            return
        # Duplicate indices at sample endpoints must be merged before computing
        # effective mass or allocating colored kernels.
        indices, weights = np.array(indices, copy=True), np.array(weights, copy=True)
        for column in range(indices.shape[1]):
            for earlier in range(column):
                same = indices[:, column] == indices[:, earlier]
                weights[same, earlier] += weights[same, column]
                weights[same, column] = 0
        value = np.einsum("ni,nij->nj", weights, self.rest[indices])
        rest = np.linalg.norm(value, axis=1)
        if kind == "bend":
            rest[:] = 0  # Patterns have planar rest curvature.
        colors = constraint_colors(indices, weights)
        batches = []
        for rows in colors:
            ids, w = indices[rows], weights[rows]
            mass = self.inverse_mass[ids]
            batches.append((rows, ids, w, mass, np.sum(w * w * mass, axis=1), np.abs(w) > 1e-12))
        self.groups.append({"indices": indices, "weights": weights, "rest": rest,
                            "stiffness": stiffness, "kind": kind,
                            "colors": colors, "batches": batches,
                            "lambda": np.zeros_like(rest)})

    def set_grab(self, indices, weights, target):
        indices = np.asarray(indices, dtype=np.int32)
        weights = np.asarray(weights, dtype=float)
        if len(indices) != len(weights) or np.any(indices < 0) or np.any(indices >= len(self.x)):
            raise ValueError("Invalid grab vertices.")
        if not np.isfinite(weights).all() or abs(weights.sum() - 1) > 1e-6:
            raise ValueError("Grab weights must sum to one.")
        target = np.asarray(target, dtype=float).reshape(3)
        if not np.isfinite(target).all():
            raise ValueError("Invalid grab target.")
        self.grab = (indices, weights, target)

    def release_grab(self):
        self.grab = None

    def _project(self, h):
        for group in self.groups:
            if group["stiffness"] <= 0:
                continue
            if group["kind"] == "elastic":
                continue
            alpha = 1 / (group["stiffness"] * h * h)
            target = group["rest"]
            if group["kind"] == "seam":
                progress = min(1.0, (self.time + h) / max(self.settings.seam_seconds, h))
                target = target * (1 - progress) + self.settings.seam_gap * progress
            for rows, ids, w, mass, effective_mass, active in group["batches"]:
                value = np.einsum("ni,nij->nj", w, self.x[ids])
                length = np.linalg.norm(value, axis=1)
                direction = value / np.maximum(length[:, None], 1e-12)
                denominator = effective_mass + alpha
                dl = (-(length - target[rows]) - alpha * group["lambda"][rows]) / denominator
                group["lambda"][rows] += dl
                correction = mass[:, :, None] * w[:, :, None] * dl[:, None, None] * direction[:, None]
                # Coloring guarantees unique active indices in this batch.
                self.x[ids[active]] += correction[active]
        for group in self.groups:
            if group["kind"] == "elastic":
                self._project_elastic_band(group, h)

    def _project_elastic_band(self, group, h):
        """Local elastic strain plus a coarse solve for tension around sewn bands."""
        indices, aliases = group["indices"], group["segment_aliases"]
        compliance = 1 / (group["segment_stiffness"] * h * h)
        for rows in group["colors"]:
            a, b = indices[rows].T
            delta = self.x[a] - self.x[b]
            lengths = np.linalg.norm(delta, axis=1)
            direction = delta / np.maximum(lengths[:, None], 1e-12)
            wa, wb = self.inverse_mass[a], self.inverse_mass[b]
            dl = (-(lengths - group["segment_rest"][rows]) - compliance[rows] * group["lambda"][rows]) / (
                wa + wb + compliance[rows])
            group["lambda"][rows] += dl
            self.x[a] += direction * (wa * dl)[:, None]
            self.x[b] -= direction * (wb * dl)[:, None]
        delta = self.x[indices[:, 0]] - self.x[indices[:, 1]]
        lengths = np.linalg.norm(delta, axis=1)
        direction = delta / np.maximum(lengths[:, None], 1e-12)
        count = len(group["member_offsets"]) - 1
        gradient = np.zeros((count, 3))
        np.add.at(gradient, aliases[:, 0], direction)
        np.add.at(gradient, aliases[:, 1], -direction)
        inverse = self.inverse_mass[group["vertices"]]
        mass = np.divide(1.0, inverse, out=np.full_like(inverse, np.inf), where=inverse > 0)
        effective_inverse = 1 / np.bincount(group["aliases"], weights=mass, minlength=count)
        alpha = 1 / (group["stiffness"] * h * h)
        denominator = np.sum(effective_inverse[:, None] * gradient * gradient) + alpha
        # The coarse correction updates the SAME multipliers as the short
        # springs. This propagates tension without adding a second stiffness.
        dl = (-(lengths.sum() - group["rest"][0]) - np.dot(compliance, group["lambda"])) / denominator
        group["lambda"] += dl
        local = group["aliases"]
        self.x[group["vertices"]] += effective_inverse[local, None] * gradient[local] * dl

    def _project_grab(self, h):
        if self.grab is None:
            return
        ids, weights, target = self.grab
        mass = self.inverse_mass[ids]
        alpha = 1 / (50000 * h * h)
        correction = (target - weights @ self.x[ids]) / (np.sum(mass * weights**2) + alpha)
        np.add.at(self.x, ids, (mass * weights)[:, None] * correction)

    def _self_contact(self, old=None):
        self._self_face_contact(old)
        radius = 2 * self.settings.thickness
        pairs = cKDTree(self.x).query_pairs(radius, output_type="ndarray")
        if not len(pairs):
            return
        pairs = np.asarray([p for p in pairs if tuple(map(int, p)) not in self._excluded], dtype=np.int32).reshape(-1, 2)
        if not len(pairs):
            return
        a, b = pairs.T
        delta = self.x[a] - self.x[b]
        length = np.linalg.norm(delta, axis=1)
        direction = delta / np.maximum(length[:, None], 1e-12)
        zero = length < 1e-12
        direction[zero] = np.array([0, 0, 1.0])
        denominator = self.inverse_mass[a] + self.inverse_mass[b]
        amount = np.maximum(0, radius - length) / np.maximum(denominator, 1e-12)
        correction = np.zeros_like(self.x)
        counts = np.zeros(len(self.x))
        np.add.at(correction, a, direction * (amount * self.inverse_mass[a])[:, None])
        np.add.at(correction, b, -direction * (amount * self.inverse_mass[b])[:, None])
        np.add.at(counts, a, 1)
        np.add.at(counts, b, 1)
        self.x += correction / np.maximum(counts[:, None], 1)

    def _self_face_contact(self, old):
        """Vertex/triangle contacts prevent sparse vertices passing through a face."""
        from .collisions import closest_points, TriangleBroadphase
        faces = self.asset.triangles
        triangles = self.x[faces]
        thickness = 2 * self.settings.thickness
        rows, triangle_ids = TriangleBroadphase(triangles).query_pairs(self.x, thickness)
        if not len(rows):
            return
        keys = rows[:, None] * len(self.x) + faces[triangle_ids]
        lookup = np.searchsorted(self._neighbor_keys, keys)
        lookup = np.minimum(lookup, len(self._neighbor_keys) - 1)
        adjacent = np.any(self._neighbor_keys[lookup] == keys, axis=1)
        rows, triangle_ids = rows[~adjacent], triangle_ids[~adjacent]
        if not len(rows):
            return
        nearest, normal, _ = closest_points(self.x[rows], triangles[triangle_ids])
        delta = self.x[rows] - nearest
        distance = np.linalg.norm(delta, axis=1)
        active = distance < thickness
        rows, triangle_ids = rows[active], triangle_ids[active]
        nearest, normal, delta, distance = nearest[active], normal[active], delta[active], distance[active]
        if not len(rows):
            return
        direction = delta / np.maximum(distance[:, None], 1e-12)
        zero = distance < 1e-10
        direction[zero] = normal[zero]
        if old is not None:
            previous_delta = old[rows] - old[faces[triangle_ids]].mean(axis=1)
            side = np.sign(np.sum(previous_delta * normal, axis=1))
            direction[zero] *= np.where(side[zero] < 0, -1, 1)[:, None]
        tri = triangles[triangle_ids]
        ab, ac, ap = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0], nearest - tri[:, 0]
        aa, bb, cc = np.sum(ab*ab, axis=1), np.sum(ab*ac, axis=1), np.sum(ac*ac, axis=1)
        denominator = np.maximum(aa*cc - bb*bb, 1e-30)
        u = (cc*np.sum(ap*ab, axis=1) - bb*np.sum(ap*ac, axis=1)) / denominator
        v = (aa*np.sum(ap*ac, axis=1) - bb*np.sum(ap*ab, axis=1)) / denominator
        bary = np.clip(np.c_[1-u-v, u, v], 0, 1)
        bary /= np.maximum(bary.sum(axis=1)[:, None], 1e-12)
        ids = faces[triangle_ids]
        mass = self.inverse_mass[rows] + np.sum(bary*bary*self.inverse_mass[ids], axis=1)
        amount = (thickness - distance) / np.maximum(mass, 1e-12)
        correction, counts = np.zeros_like(self.x), np.zeros(len(self.x))
        np.add.at(correction, rows, direction * (amount*self.inverse_mass[rows])[:, None])
        np.add.at(counts, rows, 1)
        for corner in range(3):
            index = ids[:, corner]
            np.add.at(correction, index, -direction * (amount*bary[:, corner]*self.inverse_mass[index])[:, None])
            np.add.at(counts, index, (bary[:, corner] > 1e-12).astype(float))
        self.x += correction / np.maximum(counts[:, None], 1)

    def step(self, dt=None, colliders=()):
        if not self.settings.enabled:
            return self.x
        started = time.perf_counter()
        dt = 1 / self.settings.fps if dt is None else float(dt)
        if not np.isfinite(dt) or dt <= 0 or dt > 1:
            raise ValueError("Cloth timestep must be between zero and one second.")
        if self._backend_device != self.settings.device:
            self._configure_backend()
        if self._accelerator is not None:
            try:
                self._accelerator.step(dt, colliders)
                self.last_ms = (time.perf_counter() - started) * 1000
                return self.x
            except Exception as exc:
                # GPU state is committed to public arrays only after a successful
                # complete frame. Resume the CPU reference from the original state.
                self._accelerator = None
                self.backend = "CPU XPBD"
                self.last_gpu_ms = 0.0
                self.backend_detail = "Warp failed; CPU fallback: " + str(exc)
        h = dt / self.settings.substeps
        for substep in range(self.settings.substeps):
            old = self.x.copy()
            self.velocity[:, 1] += self.settings.effective_gravity_y * h
            self.velocity *= np.exp(-self.settings.damping * h)
            self.x += self.velocity * h
            self.x[self.pins] = self.pin_positions[self.pins]
            for group in self.groups:
                group["lambda"].fill(0)
            sampled = [c.interpolate((substep + 1) / self.settings.substeps,
                                     substep / self.settings.substeps) for c in colliders]
            for iteration in range(self.settings.iterations):
                self._project(h)
                self._project_grab(h)
                # Contacts are interleaved with elastic constraints on CPU.
                quality = self.settings.collision_quality
                passes = ((iteration + 1) * quality // self.settings.iterations
                          - iteration * quality // self.settings.iterations)
                if passes:
                    for _ in range(passes):
                        for collider in sampled:
                            collider.project(self.x, old, self.inverse_mass, self.settings.thickness,
                                             self.settings.friction, self.settings.collision_quality)
                        if self.settings.self_collision:
                            self._self_contact(old)
                        if self.settings.ground:
                            active = (self.x[:, 1] < self.settings.ground_height + self.settings.thickness) & (self.inverse_mass > 0)
                            depth = self.settings.ground_height + self.settings.thickness - self.x[active, 1]
                            self.x[active, 1] = self.settings.ground_height + self.settings.thickness
                            self.x[active] -= friction_correction(self.x[active] - old[active],
                                np.array([[0.0, 1.0, 0.0]]), depth, self.settings.friction)
                self.x[self.pins] = self.pin_positions[self.pins]
            if self.settings.self_collision or self.settings.ground:
                # Finish the substep with body clearance after all constraints.
                # One guard pass avoids doubling the configured contact budget
                # or applying friction a second time.
                for collider in sampled:
                    collider.project(self.x, old, self.inverse_mass, self.settings.thickness,
                                     0.0, self.settings.collision_quality)
            self.velocity = (self.x - old) / h
            self.velocity[self.pins] = 0
            self.time += h
        if not np.isfinite(self.x).all():
            self.x[:] = self.rest
            self.velocity.fill(0)
            raise RuntimeError("Cloth became unstable; reset and increase substeps.")
        self.last_ms = (time.perf_counter() - started) * 1000
        return self.x
