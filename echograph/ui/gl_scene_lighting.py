"""Two scene lights sharing the renderer's existing single-light shadow code."""
from contextlib import contextmanager

from echograph.services.profiler import profile_scope

LIGHT_FIELDS = (
    "owner", "type", "dir", "pos", "intensity", "range", "shadow_strength",
    "shadow_range", "shadow_fov", "shadow_near", "shadow_bias",
    "shadow_resolution", "width", "height",
)
SHADOW_DEFAULTS = {
    "fbo": None, "depth_tex": None, "id_tex": None, "size_current": (),
    "light_mvp": None, "light_mvp_faces": None, "point_faces": None,
    "area_faces": None,
    "signature": None, "dirty": True, "valid": False, "last_update_ts": 0.0,
}


class SceneLightingMixin:
    @contextmanager
    def _mgl_scene_bounds_scope(self):
        """Reuse bounds during one render only; poses may change before the next."""
        if getattr(self, '_mgl_scene_bounds_read_cache', None) is not None:
            yield
            return
        self._mgl_scene_bounds_read_cache = {}
        try:
            yield
        finally:
            self._mgl_scene_bounds_read_cache = None

    def _mgl_set_light_config(self, owner, config):
        self._mgl_scene_light_owner = owner
        self._mgl_scene_light_type = self._mgl_normalize_light_type(config.get("type", "directional"))
        self._mgl_scene_light_dir = None
        self._mgl_scene_light_pos = None
        for name in LIGHT_FIELDS[4:]:
            default = 1.0 if name in {"intensity", "shadow_strength", "width", "height"} else 0.0
            try:
                value = max(0.0, float(config.get(name, default)))
            except (TypeError, ValueError):
                value = default
            if name == "shadow_strength":
                value = min(1.0, value)
            elif name == "shadow_fov" and value > 0.0:
                value = max(1.0, min(179.0, value))
            setattr(self, "_mgl_scene_light_" + name, value)

    @contextmanager
    def _mgl_second_light_context(self):
        """Swap only per-light state; always restore the primary, even on failure."""
        state = self._mgl_second_light_state
        names = ["_mgl_scene_light_" + name for name in LIGHT_FIELDS]
        names += ["_mgl_shadow_" + name for name in SHADOW_DEFAULTS]
        saved = {name: getattr(self, name, None) for name in names}
        try:
            self._mgl_set_light_config(state["owner"], state["config"])
            for name, default in SHADOW_DEFAULTS.items():
                setattr(self, "_mgl_shadow_" + name, state.get(name, default))
            yield
        finally:
            for name in SHADOW_DEFAULTS:
                state[name] = getattr(self, "_mgl_shadow_" + name)
            for name, value in saved.items():
                setattr(self, name, value)

    def _mgl_release_second_light(self):
        state = getattr(self, "_mgl_second_light_state", None)
        if state:
            for name in ("fbo", "depth_tex", "id_tex"):
                resource = state.get(name)
                if resource is not None:
                    try:
                        resource.release()
                    except Exception:
                        pass
        self._mgl_second_light_state = None

    def _mgl_render_scene_light_shadows(self):
        scene = getattr(self, "_mgl_scene", None)
        items = list(scene.items()) if scene is not None else []
        lights = [item for item in items if item.tag == "scene-light"]
        visibility = getattr(self, "_mgl_scene_visibility", {}) or {}
        active = []
        for item in lights:
            payload = item.payload or {}
            owner = payload.get("owner") or payload.get("node") or item.name
            if item.visible and visibility.get(owner, True):
                active.append((owner, payload.get("light") or {}))
        if active:
            self._mgl_set_light_config(*active[0])
        elif lights or getattr(self, "_mgl_scene_light_owner", None):
            # Hidden authored lights should not become an implicit work light.
            self._mgl_set_light_config(None, {"intensity": 0.0 if lights else 1.0})

        dirty = bool(getattr(self, "_mgl_shadow_dirty", True))
        if len(active) > 1:
            owner, config = active[1]
            state = getattr(self, "_mgl_second_light_state", None)
            if state is None:
                state = dict(SHADOW_DEFAULTS)
                self._mgl_second_light_state = state
            if state.get("owner") != owner or state.get("config") != config or dirty:
                state["dirty"] = True
            state.update(owner=owner, config=dict(config))
        else:
            self._mgl_release_second_light()

        with profile_scope("render.3d.mgl.shadow.primary"):
            self._mgl_render_shadow_map()
        if getattr(self, "_mgl_second_light_state", None) is not None:
            with self._mgl_second_light_context():
                with profile_scope("render.3d.mgl.shadow.secondary"):
                    self._mgl_render_shadow_map()

    def _mgl_apply_shadow_uniforms(self, prog):
        self._mgl_apply_one_light_uniforms(prog)
        enabled = (
            getattr(self, "_mgl_second_light_state", None) is not None
            and not getattr(self, "_mgl_work_light_enabled", False)
            and not getattr(self, "_mgl_unlit_view_enabled", False)
        )
        if prog is None or "UseSecondLight" not in prog:
            return
        prog["UseSecondLight"].value = int(enabled)
        if enabled:
            with self._mgl_second_light_context():
                self._mgl_apply_one_light_uniforms(prog, suffix="2", texture_unit=9)
