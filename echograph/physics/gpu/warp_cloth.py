"""GPU-resident XPBD stepping with CUDA graphs and one viewport transfer per frame."""
import numpy as np
import warp as wp
from . import cloth_kernels as kernels


@wp.kernel
def project_constraints(x: wp.array(dtype=wp.vec3), inverse_mass: wp.array(dtype=float),
                        ids: wp.array2d(dtype=int), weights: wp.array2d(dtype=float),
                        rest: wp.array(dtype=float), lambdas: wp.array(dtype=float),
                        rows: wp.array(dtype=int), stiffness: float, clock: wp.array(dtype=float),
                        progress: wp.array(dtype=float),
                        gap: float, sewing: int):
    row = rows[wp.tid()]
    alpha = 1.0 / (stiffness * clock[0] * clock[0])
    value = wp.vec3(0.0)
    denominator = alpha
    for column in range(4):
        index = ids[row, column]
        w = weights[row, column]
        value += x[index] * w
        denominator += inverse_mass[index] * w * w
    length = wp.length(value)
    target = rest[row]
    if sewing == 1:
        target = target * (1.0 - progress[0]) + gap * progress[0]
    dl = (-(length - target) - alpha * lambdas[row]) / denominator
    lambdas[row] += dl
    if length > 1.0e-12:
        direction = value / length
        for column in range(4):
            w = weights[row, column]
            if wp.abs(w) > 1.0e-12:
                index = ids[row, column]
                x[index] += direction * (inverse_mass[index] * w * dl)


class WarpCollider:
    """Persistent collider buffers; update motion without rebuilding topology/BVH."""
    def __init__(self, collider, device):
        self.topology = collider.triangles.copy()
        previous = collider.previous if collider.previous is not None and collider.previous.shape == collider.vertices.shape else collider.vertices
        self.start = wp.array(np.asarray(previous, dtype=np.float32), dtype=wp.vec3, device=device)
        self.end = wp.array(np.asarray(collider.vertices, dtype=np.float32), dtype=wp.vec3, device=device)
        self.current = wp.empty_like(self.end)
        self.previous = wp.empty_like(self.end)
        self.faces = wp.array(self.topology, dtype=int, device=device)
        self.lower = wp.empty(len(self.topology), dtype=wp.vec3, device=device)
        self.upper = wp.empty_like(self.lower)
        self.last_start = np.array(previous, copy=True)
        self.last_end = collider.vertices.copy()
        wp.launch(kernels.triangle_bounds, dim=len(self.topology), inputs=[self.end, self.start,
                  self.faces, self.lower, self.upper, 1], device=device)
        self.bvh = wp.Bvh(self.lower, self.upper)

    def update(self, collider):
        previous = collider.previous if collider.previous is not None and collider.previous.shape == collider.vertices.shape else collider.vertices
        if not np.array_equal(previous, self.last_start):
            self.start.assign(np.asarray(previous, dtype=np.float32))
            self.last_start = np.array(previous, copy=True)
        if not np.array_equal(collider.vertices, self.last_end):
            self.end.assign(np.asarray(collider.vertices, dtype=np.float32))
            self.last_end = collider.vertices.copy()


class WarpProjector:
    def __init__(self, solver, require_cuda=False):
        wp.init()
        if not wp.is_cuda_available():
            raise RuntimeError("No supported Warp CUDA device; using NumPy CPU XPBD.")
        self.device = wp.get_device("cuda:0")
        self.solver = solver
        self.label = "Warp CUDA (GPU solve)"
        self.x = wp.array(solver.x.astype(np.float32), dtype=wp.vec3, device=self.device)
        self.old = wp.empty_like(self.x)
        self.rest = wp.array(solver.rest.astype(np.float32), dtype=wp.vec3, device=self.device)
        self.velocity = wp.zeros_like(self.x)
        self.mass = wp.array(solver.inverse_mass.astype(np.float32), dtype=float, device=self.device)
        self.clock = wp.zeros(2, dtype=float, device=self.device)
        self.progress = wp.zeros(1, dtype=float, device=self.device)
        self.grab_ids = wp.zeros(max(1, len(solver.x)), dtype=int, device=self.device)
        self.grab_weights = wp.zeros(len(self.grab_ids), dtype=float, device=self.device)
        self.grab_target = wp.zeros(1, dtype=wp.vec3, device=self.device)
        self.grab_count = wp.zeros(1, dtype=int, device=self.device)
        neighbors = [sorted(n) for n in solver._neighbors]
        offsets = np.r_[0, np.cumsum([len(n) for n in neighbors])].astype(np.int32)
        self.offsets = wp.array(offsets, dtype=int, device=self.device)
        self.neighbors = wp.array(np.concatenate(neighbors).astype(np.int32), dtype=int, device=self.device)
        self.faces = wp.array(solver.asset.triangles, dtype=int, device=self.device)
        surface = solver.x[solver.asset.triangles]
        self.lower = wp.array(surface.min(axis=1).astype(np.float32), dtype=wp.vec3, device=self.device)
        self.upper = wp.array(surface.max(axis=1).astype(np.float32), dtype=wp.vec3, device=self.device)
        self.bvh = wp.Bvh(self.lower, self.upper)
        self.grid = wp.HashGrid(64, 64, 64, device=self.device)
        self.grid.reserve(len(solver.x))
        self.correction = wp.zeros_like(self.x)
        self.counts = wp.zeros(len(solver.x), dtype=float, device=self.device)
        self.colliders = []
        self.graph = None
        self._graph_settings = None
        self.groups = []
        for group in solver.groups:
            indices, weights = group["indices"], group["weights"]
            padded_ids = np.zeros((len(indices), 4), dtype=np.int32)
            padded_weights = np.zeros((len(indices), 4), dtype=np.float32)
            padded_ids[:, :indices.shape[1]] = indices
            padded_weights[:, :indices.shape[1]] = weights
            self.groups.append((group,
                wp.array(padded_ids, dtype=int, device=self.device),
                wp.array(padded_weights, dtype=float, device=self.device),
                wp.array(group["rest"].astype(np.float32), dtype=float, device=self.device),
                wp.zeros(len(indices), dtype=float, device=self.device),
                [wp.array(rows, dtype=int, device=self.device) for rows in group["colors"]]))
        # Worker stepping uses an independent CUDA stream, including graph replay
        # and readback. Never share a default stream across viewport/worker threads.
        wp.synchronize_stream(wp.get_stream(self.device))
        self.stream = wp.Stream(self.device)
        self.started = wp.Event(self.device, enable_timing=True)
        self.finished = wp.Event(self.device, enable_timing=True)

    def _launch(self, kernel, dim, inputs):
        wp.launch(kernel, dim=dim, inputs=inputs, device=self.device)

    def _self_contact(self):
        n = len(self.x)
        thickness = 2 * self.solver.settings.thickness
        self._launch(kernels.triangle_bounds, len(self.lower), [self.x, self.old,
                     self.faces, self.lower, self.upper, 0])
        self.bvh.refit()
        self.correction.zero_()
        self.counts.zero_()
        self._launch(kernels.self_face_contact, n, [self.x, self.old, self.mass,
                     self.faces, self.bvh.id, thickness, self.offsets, self.neighbors,
                     self.correction, self.counts])
        self._launch(kernels.apply_contacts, n, [self.x, self.correction, self.counts])
        self.grid.build(self.x, thickness)
        self.correction.zero_()
        self.counts.zero_()
        self._launch(kernels.self_point_contact, n, [self.x, self.mass, self.grid.id,
                     thickness, self.offsets, self.neighbors, self.correction, self.counts])
        self._launch(kernels.apply_contacts, n, [self.x, self.correction, self.counts])

    def _body_contact(self, friction):
        settings = self.solver.settings
        margin = max(settings.thickness * (2 + settings.collision_quality), .02)
        for collider in self.colliders:
            self._launch(kernels.mesh_contact, len(self.x), [self.x, self.old, self.mass,
                         collider.current, collider.previous, collider.faces, collider.bvh.id,
                         settings.thickness, margin, friction])

    def _record_frame(self):
        settings = self.solver.settings
        n = len(self.x)
        for substep in range(settings.substeps):
            self._launch(kernels.integrate, n, [self.x, self.velocity, self.old, self.rest,
                         self.mass, self.clock, settings.gravity_y, settings.damping,
                         settings.seam_seconds, self.progress])
            for _, _, _, _, lambdas, _ in self.groups:
                lambdas.zero_()
            for collider in self.colliders:
                self._launch(kernels.sample_collider, len(collider.end), [collider.start, collider.end,
                             collider.current, collider.previous, (substep + 1) / settings.substeps,
                             substep / settings.substeps])
                self._launch(kernels.triangle_bounds, len(collider.lower), [collider.current,
                             collider.previous, collider.faces, collider.lower, collider.upper, 1])
                collider.bvh.refit()
            for iteration in range(settings.iterations):
                for group, ids, weights, rest, lambdas, colors in self.groups:
                    for rows in colors:
                        self._launch(project_constraints, len(rows), [self.x, self.mass, ids, weights,
                                     rest, lambdas, rows, group["stiffness"], self.clock, self.progress,
                                     settings.seam_gap, int(group["kind"] == "seam")])
                self._launch(kernels.grab, 1, [self.x, self.mass, self.grab_ids, self.grab_weights,
                             self.grab_target, self.grab_count, self.clock])
                quality = settings.collision_quality
                passes = (iteration + 1) * quality // settings.iterations - iteration * quality // settings.iterations
                for _ in range(passes):
                    self._body_contact(settings.friction)
                    if settings.self_collision:
                        self._self_contact()
                    if settings.ground:
                        self._launch(kernels.ground_contact, n, [self.x, self.old, self.mass,
                                     settings.ground_height + settings.thickness, settings.friction])
            if settings.self_collision or settings.ground:
                self._body_contact(0.0)
            self._launch(kernels.finish_substep, n, [self.x, self.old, self.velocity, self.mass, self.clock])

    def step(self, dt, colliders=()):
        solver = self.solver
        with wp.ScopedStream(self.stream):
            # Public CPU arrays can be changed by cache playback, reset and callers.
            # Upload once per frame; never cross the bridge inside a solve iteration.
            self.x.assign(np.asarray(solver.x, dtype=np.float32))
            self.velocity.assign(np.asarray(solver.velocity, dtype=np.float32))
            self.clock.assign(np.asarray([dt / solver.settings.substeps, solver.time], dtype=np.float32))
            grab = solver.grab
            count = len(grab[0]) if grab is not None else 0
            if count > len(self.grab_ids):
                self.grab_ids = wp.zeros(count, dtype=int, device=self.device)
                self.grab_weights = wp.zeros(count, dtype=float, device=self.device)
                self.graph = None
            if grab is not None:
                ids = np.zeros(len(self.grab_ids), dtype=np.int32)
                weights = np.zeros(len(ids), dtype=np.float32)
                ids[:count], weights[:count] = grab[0], grab[1]
                self.grab_ids.assign(ids)
                self.grab_weights.assign(weights)
                self.grab_target.assign(np.asarray([grab[2]], dtype=np.float32))
            self.grab_count.assign(np.asarray([count], dtype=np.int32))
            colliders = [c for c in colliders if len(c.triangles)]
            if len(colliders) != len(self.colliders):
                self.colliders = [WarpCollider(c, self.device) for c in colliders]
                self.graph = None
            else:
                for index, collider in enumerate(colliders):
                    cached = self.colliders[index]
                    if len(cached.end) != len(collider.vertices) or not np.array_equal(cached.topology, collider.triangles):
                        self.colliders[index] = WarpCollider(collider, self.device)
                        self.graph = None
                    else:
                        cached.update(collider)
            graph_settings = tuple((key, value) for key, value in solver.settings.to_dict().items()
                                   if key not in {"enabled", "interactive"})
            if self.graph is None or self._graph_settings != graph_settings:
                # Load before capture so first-use compilation and allocations do
                # not interfere with CUDA capture. Capture does not advance state.
                wp.load_module(module=__name__, device=self.device)
                wp.load_module(module=kernels, device=self.device)
                with wp.ScopedCapture(device=self.device) as capture:
                    self._record_frame()
                self.graph = capture.graph
                self._graph_settings = graph_settings
            self.stream.record_event(self.started)
            wp.capture_launch(self.graph)
            self.stream.record_event(self.finished)
            positions, velocity = self.x.numpy(), self.velocity.numpy()
            gpu_ms = wp.get_event_elapsed_time(self.started, self.finished)
        if not np.isfinite(positions).all() or not np.isfinite(velocity).all():
            raise RuntimeError("CUDA cloth became unstable; reset and increase substeps.")
        solver.x[:] = positions
        solver.velocity[:] = velocity
        solver.x[solver.pins] = solver.rest[solver.pins]
        solver.velocity[solver.pins] = 0
        solver.last_gpu_ms = gpu_ms
        solver.time += dt
