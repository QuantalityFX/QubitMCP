from __future__ import annotations

from pathlib import Path

from .spec import PLY_SEQUENCE_SPEC

_PATCH_KEY = "_ply_sequence_create_dialog_patch_v1"
_DEBUG_PATCH_KEY = "_ply_sequence_header_debug_patch_v1"
_SCENE_ASSET_PATCH_KEY = "_ply_sequence_scene_asset_patch_v1"
_RENDERER_PATCH_KEY = "_ply_sequence_renderer_patch_v1"


def _patch_create_node_dialog() -> None:
    try:
        from echograph.ui import dialogs
    except Exception:
        return

    cls = getattr(dialogs, "CreateNodeDialog", None)
    if cls is None or getattr(cls, _PATCH_KEY, False):
        return

    original_init = getattr(cls, "__init__", None)
    if not callable(original_init):
        return

    def wrapped_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            combo = getattr(self, "kind_edit", None)
            if combo is None:
                return
            if combo.findText("ply_sequence") < 0:
                combo.addItem("ply_sequence")
        except Exception:
            pass

    try:
        cls.__init__ = wrapped_init
        setattr(cls, _PATCH_KEY, True)
    except Exception:
        return


def _patch_header_debug_button() -> None:
    try:
        from echograph.ui import node_item as node_item_mod
    except Exception:
        return

    cls = getattr(node_item_mod, "NodeItem", None)
    if cls is None or getattr(cls, _DEBUG_PATCH_KEY, False):
        return

    original = getattr(cls, "_header_debug_button_kind", None)
    if not callable(original):
        return

    def wrapped(self, *args, **kwargs):
        try:
            kind = str(getattr(getattr(self, "model", None), "kind", "") or "").strip().lower()
            if kind in ("ply_sequence", "ply sequence"):
                return "ply_sequence"
        except Exception:
            pass
        try:
            return original(self, *args, **kwargs)
        except Exception:
            return None

    try:
        cls._header_debug_button_kind = wrapped
        setattr(cls, _DEBUG_PATCH_KEY, True)
    except Exception:
        return


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return (entry.get("value") or "").strip()
    return ""


def _bool_param(raw: str, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _patch_scene_asset_flags() -> None:
    try:
        from echograph.ui import node_item as node_item_mod
    except Exception:
        return

    cls = getattr(node_item_mod, "NodeItem", None)
    if cls is None or getattr(cls, _SCENE_ASSET_PATCH_KEY, False):
        return

    original = getattr(cls, "_collect_scene_assets", None)
    if not callable(original):
        return

    def wrapped(self, *args, **kwargs):
        try:
            assets = list(original(self, *args, **kwargs) or [])
        except Exception:
            return []

        try:
            scene = self.scene()
        except Exception:
            scene = None
        node_items = getattr(scene, "_node_items", None) if scene is not None else None
        if not isinstance(node_items, dict):
            return assets

        for asset in assets:
            if not isinstance(asset, dict):
                continue
            ext = str(asset.get("ext") or "").strip().lower()
            if not ext:
                try:
                    ext = Path(str(asset.get("path") or "").strip()).suffix.lower()
                except Exception:
                    ext = ""
            if ext != ".ply":
                continue

            owner = str(asset.get("node") or "").strip()
            if not owner:
                continue
            owner_item = node_items.get(owner)
            owner_model = getattr(owner_item, "model", None) if owner_item is not None else None
            if owner_model is None:
                continue
            owner_kind = str(getattr(owner_model, "kind", "") or "").strip().lower()
            if owner_kind not in {"ply_sequence", "ply sequence", "plysequence"}:
                continue

            follow_camera = _bool_param(_param_value(owner_model, "follow_camera"), default=False)
            # Keep sequence rooted in scene view unless Follow Cam is enabled.
            lock_root = not bool(follow_camera)
            seed = (
                _param_value(owner_model, "source")
                or _param_value(owner_model, "mesh")
                or _param_value(owner_model, "path")
            )
            anchor_key = str(seed or asset.get("path") or owner).strip()

            asset["splat_anchor_lock"] = bool(lock_root)
            asset["splat_anchor_key"] = anchor_key

        return assets

    try:
        cls._collect_scene_assets = wrapped
        setattr(cls, _SCENE_ASSET_PATCH_KEY, True)
    except Exception:
        return


def _patch_renderer_splat_anchor() -> None:
    try:
        from echograph.ui import gl_mgl_renderer as renderer_mod
    except Exception:
        return

    cls = getattr(renderer_mod, "MGLRendererMixin", None)
    if cls is None or getattr(cls, _RENDERER_PATCH_KEY, False):
        return

    original_load = getattr(cls, "_mgl_load_scene_assets", None)
    original_rebuild = getattr(cls, "_mgl_rebuild_scene_splats", None)
    if not callable(original_load) or not callable(original_rebuild):
        return

    def wrapped_load(self, assets, *args, **kwargs):
        anchor_cfg = {}
        for entry in assets or []:
            if not isinstance(entry, dict):
                continue
            ext = str(entry.get("ext") or "").strip().lower()
            if not ext:
                try:
                    ext = Path(str(entry.get("path") or "").strip()).suffix.lower()
                except Exception:
                    ext = ""
            if ext != ".ply":
                continue
            owner = str(entry.get("node") or "").strip()
            if not owner:
                try:
                    owner = Path(str(entry.get("path") or "").strip()).name
                except Exception:
                    owner = ""
            if not owner:
                continue
            anchor_cfg[owner] = {
                "lock": bool(entry.get("splat_anchor_lock", False)),
                "key": str(entry.get("splat_anchor_key") or entry.get("path") or owner).strip(),
            }
        try:
            setattr(self, "_ply_sequence_anchor_cfg", anchor_cfg)
        except Exception:
            pass
        return original_load(self, assets, *args, **kwargs)

    def wrapped_rebuild(self, *args, **kwargs):
        np_mod = getattr(renderer_mod, "np", None)
        splat_map = getattr(self, "_mgl_scene_splats", None)
        if np_mod is not None and isinstance(splat_map, dict) and splat_map:
            cfg = getattr(self, "_ply_sequence_anchor_cfg", None)
            if not isinstance(cfg, dict):
                cfg = {}
            cfg_norm = {}
            for key, value in cfg.items():
                name = str(key or "").strip()
                if not name:
                    continue
                cfg_norm[name] = value
                cfg_norm[name.lower()] = value

            centers = getattr(self, "_ply_sequence_anchor_centers", None)
            if not isinstance(centers, dict):
                centers = {}

            bounds_local = getattr(self, "_mgl_scene_splats_bounds_local", None)
            if not isinstance(bounds_local, dict):
                bounds_local = {}
                try:
                    setattr(self, "_mgl_scene_splats_bounds_local", bounds_local)
                except Exception:
                    pass

            pivots = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
            if not isinstance(pivots, dict):
                pivots = {}
                try:
                    setattr(self, "_mgl_scene_pivot_local_by_owner", pivots)
                except Exception:
                    pass
            seq_pivot_owners = getattr(self, "_ply_sequence_anchor_pivot_owners", None)
            if not isinstance(seq_pivot_owners, set):
                seq_pivot_owners = set()

            active = {str(owner).strip().lower() for owner in splat_map.keys()}
            for stale in list(centers.keys()):
                if str(stale).strip().lower() not in active:
                    try:
                        del centers[stale]
                    except Exception:
                        pass
            for stale in list(seq_pivot_owners):
                if str(stale).strip().lower() not in active:
                    try:
                        seq_pivot_owners.discard(stale)
                    except Exception:
                        pass
                    try:
                        if stale in pivots:
                            del pivots[stale]
                    except Exception:
                        pass

            for owner, arr in list(splat_map.items()):
                owner_name = str(owner or "").strip()
                if not owner_name:
                    continue
                conf = cfg_norm.get(owner_name) or cfg_norm.get(owner_name.lower()) or {}
                lock_root = bool(conf.get("lock", False))
                if not lock_root:
                    if owner_name in centers:
                        try:
                            del centers[owner_name]
                        except Exception:
                            pass
                    if owner_name in seq_pivot_owners:
                        try:
                            seq_pivot_owners.discard(owner_name)
                        except Exception:
                            pass
                        try:
                            if owner_name in pivots:
                                del pivots[owner_name]
                        except Exception:
                            pass
                    continue

                try:
                    a = np_mod.array(np_mod.asarray(arr, dtype=np_mod.float32), dtype=np_mod.float32, copy=True)
                except Exception:
                    continue
                if a.ndim != 2 or a.shape[0] <= 0 or a.shape[1] < 3:
                    continue

                try:
                    cur_center = ((a[:, :3].min(axis=0) + a[:, :3].max(axis=0)) * 0.5).astype(np_mod.float32)
                except Exception:
                    continue

                anchor_key = str(conf.get("key") or owner_name).strip()
                rec = centers.get(owner_name)
                if not isinstance(rec, dict) or str(rec.get("key") or "") != anchor_key:
                    rec = {"key": anchor_key, "center": cur_center}
                    centers[owner_name] = rec

                anchor_center = rec.get("center")
                try:
                    anchor_center = np_mod.asarray(anchor_center, dtype=np_mod.float32)
                    if anchor_center.shape[0] != 3:
                        anchor_center = cur_center
                except Exception:
                    anchor_center = cur_center
                rec["center"] = anchor_center

                shift = (anchor_center - cur_center).astype(np_mod.float32)
                if float(np_mod.linalg.norm(shift)) > 1.0e-12:
                    a[:, :3] = a[:, :3] + shift[None, :]
                splat_map[owner] = a
                try:
                    mins = a[:, :3].min(axis=0).astype(np_mod.float32)
                    maxs = a[:, :3].max(axis=0).astype(np_mod.float32)
                    bounds_local[owner] = (mins, maxs)
                except Exception:
                    pass
                try:
                    pivots[owner_name] = (
                        float(anchor_center[0]),
                        float(anchor_center[1]),
                        float(anchor_center[2]),
                    )
                    seq_pivot_owners.add(owner_name)
                except Exception:
                    pass

            try:
                setattr(self, "_ply_sequence_anchor_centers", centers)
            except Exception:
                pass
            try:
                setattr(self, "_ply_sequence_anchor_pivot_owners", seq_pivot_owners)
            except Exception:
                pass

        return original_rebuild(self, *args, **kwargs)

    try:
        cls._mgl_load_scene_assets = wrapped_load
        cls._mgl_rebuild_scene_splats = wrapped_rebuild
        setattr(cls, _RENDERER_PATCH_KEY, True)
    except Exception:
        return


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("ply_sequence", PLY_SEQUENCE_SPEC)
    _core.register_spec("ply sequence", PLY_SEQUENCE_SPEC)
    _core.register_spec("plysequence", PLY_SEQUENCE_SPEC)
    _patch_create_node_dialog()
    _patch_header_debug_button()
    _patch_scene_asset_flags()
    _patch_renderer_splat_anchor()
