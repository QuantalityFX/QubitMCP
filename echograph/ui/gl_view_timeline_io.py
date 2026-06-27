from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Dict


class GraphGLTimelineIOMixin:
    @staticmethod
    def _timeline_safe_name(name: str) -> str:
        raw = str(name or "").strip()
        if not raw:
            return "scene"
        out = []
        for ch in raw:
            if ch.isalnum() or ch in ("_", "-"):
                out.append(ch)
            else:
                out.append("_")
        cleaned = "".join(out).strip("_")
        return cleaned or "scene"

    def _timeline_default_scene_name(self) -> str:
        try:
            win = self.window()
            preview = getattr(win, "_active_scene_preview_context", None) if win is not None else None
            if isinstance(preview, dict):
                nm = str(preview.get("scene_name") or preview.get("name") or "").strip()
                if nm:
                    return nm
            active = getattr(win, "_active_scene_node", None) if win is not None else None
            nm = (getattr(active, "name", "") or "").strip() if active is not None else ""
            if nm:
                return nm
        except Exception:
            pass
        current = str(getattr(self, "_timeline_scene_name", "scene") or "scene").strip()
        return current or "scene"

    def _timeline_project_ref_path(self, project_path: str | None = None) -> Path | None:
        raw = (project_path or "").strip()
        if not raw:
            try:
                win = self.window()
                raw = str(getattr(win, "_current_path", "") or "").strip() if win is not None else ""
            except Exception:
                raw = ""
        if not raw:
            try:
                raw = str(getattr(getattr(self, "_scene", None), "_filename", "") or "").strip()
            except Exception:
                raw = ""
        if not raw:
            return None
        try:
            return Path(raw)
        except Exception:
            return None

    def _timeline_default_project_dir(self, project_path: str | None = None) -> Path:
        p = self._timeline_project_ref_path(project_path=project_path)
        if p is not None:
            try:
                if p.suffix:
                    p = p.parent
                return p
            except Exception:
                pass
        return Path(tempfile.gettempdir()) / "EchoGraph"

    def _timeline_workflow_namespace(self, project_path: str | None = None) -> str:
        p = self._timeline_project_ref_path(project_path=project_path)
        if p is None:
            return ""
        try:
            if not p.suffix:
                return ""
            stem = str(p.stem or "").strip()
        except Exception:
            return ""
        if not stem:
            return ""
        try:
            return self._timeline_safe_name(stem)
        except Exception:
            return stem

    def _timeline_project_sidecar_dir(
        self,
        project_path: str | None = None,
        *,
        legacy: bool = False,
        create: bool = True,
    ) -> Path:
        base_dir = self._timeline_default_project_dir(project_path=project_path)
        out_dir = base_dir / "projects"
        if not bool(legacy):
            namespace = self._timeline_workflow_namespace(project_path=project_path)
            if namespace:
                out_dir = out_dir / namespace
        if bool(create):
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        return out_dir

    def _timeline_anim_file_path(
        self,
        scene_name: str,
        project_path: str | None = None,
        owner_name: str | None = None,
        legacy: bool = False,
        create: bool = True,
    ) -> Path:
        out_dir = self._timeline_project_sidecar_dir(
            project_path=project_path,
            legacy=bool(legacy),
            create=bool(create),
        )
        safe_scene = self._timeline_safe_name(scene_name)
        safe_owner = self._timeline_safe_name(owner_name) if str(owner_name or "").strip() else ""
        if safe_owner:
            return out_dir / f"{safe_scene}__owner_{safe_owner}_timeline.json"
        return out_dir / f"{safe_scene}_timeline.json"

    def _timeline_range_file_path(
        self,
        scene_name: str,
        project_path: str | None = None,
        legacy: bool = False,
        create: bool = True,
    ) -> Path:
        out_dir = self._timeline_project_sidecar_dir(
            project_path=project_path,
            legacy=bool(legacy),
            create=bool(create),
        )
        safe_scene = self._timeline_safe_name(scene_name or "scene")
        return out_dir / f"{safe_scene}_timeline_range.json"

    def _timeline_audio_context_scene_name(self, scene_name: str | None = None) -> str:
        raw = str(scene_name or "").strip()
        if raw:
            return raw
        try:
            win = self.window()
            preview = getattr(win, "_active_scene_preview_context", None) if win is not None else None
            if isinstance(preview, dict):
                name = str(preview.get("scene_name") or preview.get("name") or "").strip()
                if name:
                    return name
        except Exception:
            pass
        try:
            win = self.window()
            active = getattr(win, "_active_scene_node", None) if win is not None else None
            if active is not None:
                name = str(getattr(active, "name", "") or "").strip()
                if name:
                    return name
        except Exception:
            pass
        return "viewport"

    def _timeline_audio_file_path(
        self,
        scene_name: str,
        project_path: str | None = None,
        legacy: bool = False,
        create: bool = True,
    ) -> Path:
        out_dir = self._timeline_project_sidecar_dir(
            project_path=project_path,
            legacy=bool(legacy),
            create=bool(create),
        )
        safe_scene = self._timeline_safe_name(scene_name or "viewport")
        return out_dir / f"{safe_scene}_audio.json"

    @staticmethod
    def _timeline_parse_range_payload(raw) -> tuple[object, object, bool]:
        if not isinstance(raw, dict):
            return (None, None, True)
        in_frame = None
        out_frame = None
        loop_enabled = True
        try:
            raw_in = raw.get("in_frame", None)
            if raw_in is not None:
                cand = int(raw_in)
                if cand >= 0:
                    in_frame = int(cand)
        except Exception:
            in_frame = None
        try:
            raw_out = raw.get("out_frame", raw.get("end_frame", None))
            if raw_out is not None:
                cand = int(raw_out)
                if cand >= 0:
                    out_frame = int(cand)
        except Exception:
            out_frame = None
        if in_frame is not None and out_frame is not None and out_frame < in_frame:
            out_frame = in_frame
        try:
            loop_enabled = bool(raw.get("loop_enabled", True))
        except Exception:
            loop_enabled = True
        return (in_frame, out_frame, loop_enabled)

    def _timeline_range_save_to_disk(self) -> None:
        path = getattr(self, "_timeline_range_cfg_path", None)
        if path is None:
            return
        payload = {
            "scene": str(getattr(self, "_timeline_scene_name", "scene") or "scene"),
            "in_frame": (
                int(getattr(self, "_timeline_in_frame", 0))
                if getattr(self, "_timeline_in_frame", None) is not None
                else None
            ),
            "out_frame": (
                int(getattr(self, "_timeline_out_frame", 0))
                if getattr(self, "_timeline_out_frame", None) is not None
                else None
            ),
            "end_frame": (
                int(getattr(self, "_timeline_out_frame", 0))
                if getattr(self, "_timeline_out_frame", None) is not None
                else None
            ),
            "loop_enabled": bool(getattr(self, "_timeline_loop_enabled", True)),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            try:
                self._timeline_owner_keys_cache = {}
            except Exception:
                pass
        except Exception:
            pass

    def _timeline_range_load_from_disk(self) -> None:
        path = getattr(self, "_timeline_range_cfg_path", None)
        raw = None
        migrated_from_legacy = False
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                loaded = None
            if isinstance(loaded, dict):
                raw = loaded
        if raw is None:
            legacy_candidates = []
            seen = set()
            try:
                scene_name = str(getattr(self, "_timeline_scene_name", "") or "").strip() or "scene"
                legacy_range_path = self._timeline_range_file_path(
                    scene_name,
                    legacy=True,
                    create=False,
                )
                if path is None or str(legacy_range_path) != str(path):
                    try:
                        pkey = str(Path(legacy_range_path).resolve())
                    except Exception:
                        pkey = str(legacy_range_path)
                    seen.add(pkey)
                    legacy_candidates.append(Path(legacy_range_path))
            except Exception:
                pass
            legacy_path = getattr(self, "_timeline_anim_path", None)
            if legacy_path is not None:
                try:
                    pkey = str(Path(legacy_path).resolve())
                except Exception:
                    pkey = str(legacy_path)
                seen.add(pkey)
                legacy_candidates.append(Path(legacy_path))
            try:
                scene_name = str(getattr(self, "_timeline_scene_name", "") or "").strip() or "scene"
                out_dir = self._timeline_project_sidecar_dir(legacy=True, create=False)
                safe_scene = self._timeline_safe_name(scene_name)
                extra = [out_dir / f"{safe_scene}_timeline.json"]
                extra.extend(sorted(out_dir.glob(f"{safe_scene}__owner_*_timeline.json")))
                for item in extra:
                    try:
                        pkey = str(Path(item).resolve())
                    except Exception:
                        pkey = str(item)
                    if pkey in seen:
                        continue
                    seen.add(pkey)
                    legacy_candidates.append(Path(item))
            except Exception:
                pass
            for legacy_path in legacy_candidates:
                if legacy_path is None or not legacy_path.exists():
                    continue
                try:
                    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                except Exception:
                    legacy = None
                if isinstance(legacy, dict) and any(
                    k in legacy for k in ("in_frame", "out_frame", "loop_enabled")
                ):
                    raw = legacy
                    migrated_from_legacy = True
                    break
        in_frame, out_frame, loop_enabled = self._timeline_parse_range_payload(raw or {})
        self._timeline_in_frame = in_frame
        self._timeline_out_frame = out_frame
        self._timeline_loop_enabled = bool(loop_enabled)
        if migrated_from_legacy:
            try:
                self._timeline_range_save_to_disk()
            except Exception:
                pass
        try:
            self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
        except Exception:
            pass
        try:
            self._update_timeline_loop_button()
        except Exception:
            pass
        try:
            self._timeline_update_range_button_tooltips()
        except Exception:
            pass
        try:
            self._timeline_update_range_marker_visuals()
        except Exception:
            pass

    def _timeline_audio_store_path(self, raw_path: str) -> str:
        raw = str(raw_path or "").strip()
        if not raw:
            return ""
        try:
            p = Path(raw).expanduser()
        except Exception:
            return raw
        if not p.is_absolute():
            try:
                return p.as_posix()
            except Exception:
                return str(p)
        try:
            base = self._timeline_default_project_dir()
            rel = p.relative_to(base)
            return rel.as_posix()
        except Exception:
            pass
        try:
            return p.as_posix()
        except Exception:
            return str(p)

    def _timeline_audio_resolve_path(self, stored_path: str) -> str:
        raw = str(stored_path or "").strip()
        if not raw:
            return ""
        try:
            p = Path(raw).expanduser()
        except Exception:
            return raw
        if p.is_absolute():
            try:
                return p.as_posix()
            except Exception:
                return str(p)
        try:
            base = self._timeline_default_project_dir()
            return (base / p).resolve().as_posix()
        except Exception:
            try:
                return (Path.cwd() / p).resolve().as_posix()
            except Exception:
                return raw

    def _timeline_audio_save_to_disk(self) -> None:
        path = getattr(self, "_timeline_audio_cfg_path", None)
        if path is None:
            return
        payload = {
            "scene": str(getattr(self, "_timeline_audio_scene_name", "viewport") or "viewport"),
            "audio_path": self._timeline_audio_store_path(str(getattr(self, "_timeline_audio_path", "") or "")),
            "muted": bool(getattr(self, "_timeline_audio_muted", False)),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _timeline_audio_load_from_disk(self) -> None:
        path = getattr(self, "_timeline_audio_cfg_path", None)
        load_path = path
        loaded_from_legacy = False
        if path is not None and not path.exists():
            try:
                scene_name = str(getattr(self, "_timeline_audio_scene_name", "") or "").strip() or "viewport"
                legacy_path = self._timeline_audio_file_path(
                    scene_name,
                    legacy=True,
                    create=False,
                )
                if str(legacy_path) != str(path) and legacy_path.exists():
                    load_path = legacy_path
                    loaded_from_legacy = True
            except Exception:
                load_path = path
        raw = {}
        if load_path is not None and load_path.exists():
            try:
                raw = json.loads(load_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        stored = str(raw.get("audio_path", "") or "").strip()
        resolved = self._timeline_audio_resolve_path(stored)
        muted = bool(raw.get("muted", False))
        if loaded_from_legacy:
            try:
                self._timeline_audio_path = resolved
                self._timeline_audio_muted = bool(muted)
                self._timeline_audio_save_to_disk()
            except Exception:
                pass
        setter = getattr(self, "_timeline_audio_set_path", None)
        mute_setter = getattr(self, "_timeline_audio_set_muted", None)
        if callable(setter):
            try:
                setter(resolved, save=False)
                if callable(mute_setter):
                    try:
                        mute_setter(muted, save=False)
                    except Exception:
                        pass
                else:
                    try:
                        self._timeline_audio_muted = bool(muted)
                    except Exception:
                        pass
                return
            except Exception:
                pass
        try:
            self._timeline_audio_path = resolved
        except Exception:
            pass
        try:
            self._timeline_audio_muted = bool(muted)
        except Exception:
            pass

    def _timeline_json_safe(self, value):
        if value is None:
            return None
        if isinstance(value, (str, bool)):
            return value
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            if math.isfinite(value):
                return float(value)
            return 0.0
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                out[str(k)] = self._timeline_json_safe(v)
            return out
        if isinstance(value, (list, tuple)):
            return [self._timeline_json_safe(v) for v in value]
        try:
            return float(value)
        except Exception:
            return str(value)

    def _timeline_capture_camera_state(self) -> dict:
        try:
            target_owner = getattr(self, "_timeline_target_owner", None)
            if callable(target_owner) and str(target_owner() or "").strip():
                return {}
            if str(getattr(self, "_timeline_owner_name", "") or "").strip():
                return {}
        except Exception:
            pass
        renderer = getattr(self, "_mgl_renderer", None) or self
        get_state = getattr(renderer, "_mgl_get_camera_state", None)
        if not callable(get_state):
            return {}
        try:
            state = get_state() or {}
        except Exception:
            state = {}
        if not isinstance(state, dict):
            return {}
        state = dict(state)
        try:
            state.pop("scene_xforms", None)
        except Exception:
            pass
        return self._timeline_json_safe(state)

    def _timeline_scene_joint_virtual_keys_map(self, owner: str) -> Dict[int, Dict[str, object]]:
        owner_key = str(owner or "").strip()
        if not owner_key:
            return {}
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            decode_fn = getattr(renderer, "_mgl_scene_skeleton_decode_joint_owner", None)
            map_fn = getattr(renderer, "_mgl_scene_skeleton_joint_keys_map", None)
            if callable(decode_fn) and decode_fn(owner_key) and callable(map_fn):
                virtual_map = map_fn(owner_key)
                if isinstance(virtual_map, dict):
                    return virtual_map
        except Exception:
            pass
        return {}

    def _timeline_scene_joint_source_keys_map(self, owner: str) -> Dict[int, Dict[str, object]]:
        owner_key = str(owner or "").strip()
        if not owner_key:
            return {}
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            decode_fn = getattr(renderer, "_mgl_scene_skeleton_decode_joint_owner", None)
            source_map_fn = getattr(renderer, "_mgl_scene_skeleton_joint_source_keys_map", None)
            if callable(decode_fn) and decode_fn(owner_key) and callable(source_map_fn):
                source_map = source_map_fn(owner_key)
                if isinstance(source_map, dict):
                    return source_map
        except Exception:
            pass
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            map_fn = getattr(renderer, "_mgl_scene_skeleton_joint_keys_map", None)
            if callable(map_fn):
                source_map = map_fn(owner_key, timeline_space=False)
                if isinstance(source_map, dict):
                    return source_map
        except Exception:
            pass
        return {}

    def _timeline_editable_scene_joint_keys(
        self,
        virtual_map: Dict[int, Dict[str, object]],
    ) -> Dict[int, Dict[str, object]]:
        staged: Dict[int, Dict[str, object]] = {}
        for frame_raw, entry in (virtual_map or {}).items():
            try:
                frame = int(frame_raw)
            except Exception:
                continue
            if frame < 0 or not isinstance(entry, dict):
                continue
            item = dict(entry)
            item.pop("fbx_clip_key", None)
            item.pop("joint_key", None)
            staged[int(frame)] = item
        return staged

    def _timeline_migrate_scene_joint_keys_to_timeline_frames(
        self,
        owner: str,
        data: Dict[int, Dict[str, object]],
    ) -> tuple[Dict[int, Dict[str, object]], bool]:
        owner_key = str(owner or "").strip()
        if not owner_key or not isinstance(data, dict) or not data:
            return (data if isinstance(data, dict) else {}, False)
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            decode_fn = getattr(renderer, "_mgl_scene_skeleton_decode_joint_owner", None)
            decoded = decode_fn(owner_key) if callable(decode_fn) else None
        except Exception:
            decoded = None
        if not decoded:
            return (data, False)
        asset_owner = str(decoded[0] or "").strip()
        if not asset_owner:
            return (data, False)
        source_map = self._timeline_scene_joint_source_keys_map(owner_key)
        source_frames = []
        for frame_raw in (source_map or {}).keys():
            try:
                source_frames.append(int(frame_raw))
            except Exception:
                continue
        if not source_frames:
            return (data, False)
        source_min = min(source_frames)
        source_max = max(source_frames)
        map_fn = getattr(self, "_timeline_owner_timeline_frame_from_source_frame", None)
        if not callable(map_fn):
            return (data, False)
        out: Dict[int, Dict[str, object]] = {}
        changed = False

        def _merge_entry(dst_frame: int, incoming: Dict[str, object]) -> None:
            existing = out.get(int(dst_frame))
            if not isinstance(existing, dict):
                out[int(dst_frame)] = incoming
                return
            merged = dict(existing)
            old_mask = existing.get("axis_mask", None)
            new_mask = incoming.get("axis_mask", None)
            merged.update(dict(incoming))
            if isinstance(old_mask, (list, tuple)) and isinstance(new_mask, (list, tuple)) and len(old_mask) >= 6 and len(new_mask) >= 6:
                try:
                    merged["axis_mask"] = [bool(old_mask[i]) or bool(new_mask[i]) for i in range(6)]
                except Exception:
                    pass
            if (
                "source_frame" in existing
                and "source_frame" in incoming
                and str(existing.get("source_frame")) != str(incoming.get("source_frame"))
            ):
                merged.pop("source_frame", None)
            out[int(dst_frame)] = merged

        for frame_raw, entry in data.items():
            try:
                frame = int(frame_raw)
            except Exception:
                continue
            if frame < 0 or not isinstance(entry, dict):
                continue
            item = dict(entry)
            dst = int(frame)
            if int(source_min) <= int(frame) <= int(source_max):
                try:
                    mapped = map_fn(
                        asset_owner,
                        float(frame),
                        allow_owner_key_mode=True,
                    )
                except Exception:
                    mapped = None
                if mapped is not None:
                    try:
                        dst = int(round(float(mapped)))
                        item["source_frame"] = float(frame)
                    except Exception:
                        dst = int(frame)
                if int(dst) != int(frame) or "source_frame" not in entry:
                    changed = True
            else:
                if "source_frame" in item:
                    item.pop("source_frame", None)
                    changed = True
            _merge_entry(int(dst), item)
        return (out, bool(changed))

    def _timeline_scene_joint_owner_is_active(self) -> bool:
        owner = str(getattr(self, "_timeline_owner_name", "") or "").strip()
        if not owner:
            return False
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            decode_fn = getattr(renderer, "_mgl_scene_skeleton_decode_joint_owner", None)
            if callable(decode_fn) and decode_fn(owner):
                return True
        except Exception:
            pass
        return bool(self._timeline_scene_joint_virtual_keys_map(owner))

    def _timeline_save_to_disk(self) -> None:
        path = getattr(self, "_timeline_anim_path", None)
        if path is None:
            return
        scene_joint_active = self._timeline_scene_joint_owner_is_active()
        keys_out = []
        try:
            items = sorted((getattr(self, "_timeline_keys", {}) or {}).items(), key=lambda kv: int(kv[0]))
        except Exception:
            items = []
        for frame, entry in items:
            if not isinstance(entry, dict):
                continue
            if bool(entry.get("fbx_clip_key", False)):
                continue
            row = {"frame": int(frame)}
            xyz = entry.get("xyz", None)
            if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
                try:
                    row["xyz"] = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
                except Exception:
                    pass
            rxyz = entry.get("rxyz", None)
            if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
                try:
                    row["rxyz"] = [float(rxyz[0]), float(rxyz[1]), float(rxyz[2])]
                except Exception:
                    pass
            st = entry.get("camera_state", None)
            if isinstance(st, dict) and st:
                row["camera_state"] = self._timeline_json_safe(st)
            mask = entry.get("axis_mask", None)
            if isinstance(mask, (list, tuple)) and len(mask) >= 6:
                try:
                    row["axis_mask"] = [bool(mask[i]) for i in range(6)]
                except Exception:
                    pass
            ch = entry.get("curve_handles", None)
            if isinstance(ch, dict) and ch:
                ch_out = {}
                for ak, av in ch.items():
                    try:
                        axis_idx = int(str(ak).strip())
                    except Exception:
                        continue
                    if axis_idx < 0 or axis_idx > 5:
                        continue
                    if not isinstance(av, dict):
                        continue
                    mode_raw = str(av.get("mode", "tied") or "tied").strip().lower()
                    if mode_raw == "straight":
                        mode = "straight"
                    elif mode_raw == "untied":
                        mode = "untied"
                    else:
                        mode = "tied"
                    if mode == "straight":
                        ch_out[str(axis_idx)] = {"mode": "straight"}
                        continue
                    in_raw = av.get("in", None)
                    out_raw = av.get("out", None)
                    if not (isinstance(in_raw, (list, tuple)) and len(in_raw) >= 2):
                        continue
                    if not (isinstance(out_raw, (list, tuple)) and len(out_raw) >= 2):
                        continue
                    try:
                        ch_out[str(axis_idx)] = {
                            "mode": mode,
                            "in": [float(in_raw[0]), float(in_raw[1])],
                            "out": [float(out_raw[0]), float(out_raw[1])],
                        }
                    except Exception:
                        continue
                if ch_out:
                    row["curve_handles"] = ch_out
            if bool(scene_joint_active):
                source_frame = entry.get("source_frame", None)
                if source_frame is not None:
                    try:
                        value = float(source_frame)
                        if math.isfinite(float(value)):
                            row["source_frame"] = float(value)
                    except Exception:
                        pass
            if len(row) <= 1:
                continue
            keys_out.append(row)
        payload = {
            "scene": str(getattr(self, "_timeline_scene_name", "scene") or "scene"),
            "owner": str(getattr(self, "_timeline_owner_name", "") or ""),
            "fps": float(getattr(self, "_timeline_fps", 24.0) or 24.0),
            "in_frame": (
                int(getattr(self, "_timeline_in_frame", 0))
                if getattr(self, "_timeline_in_frame", None) is not None
                else None
            ),
            "out_frame": (
                int(getattr(self, "_timeline_out_frame", 0))
                if getattr(self, "_timeline_out_frame", None) is not None
                else None
            ),
            "end_frame": (
                int(getattr(self, "_timeline_out_frame", 0))
                if getattr(self, "_timeline_out_frame", None) is not None
                else None
            ),
            "loop_enabled": bool(getattr(self, "_timeline_loop_enabled", True)),
            "material_live_mode": bool(getattr(self, "_timeline_material_live_mode", True)),
            "fx_instances_enabled": bool(getattr(self, "_timeline_fx_instances_enabled", True)),
            "fx_proxy_enabled": bool(getattr(self, "_timeline_fx_proxy_enabled", True)),
            "keys": keys_out,
        }
        if bool(getattr(self, "_timeline_scene_skeleton_fbx_seeded", False)) or bool(scene_joint_active):
            payload["scene_skeleton_fbx_seeded"] = bool(
                getattr(self, "_timeline_scene_skeleton_fbx_seeded", False)
            )
            if bool(scene_joint_active):
                payload["scene_skeleton_frame_space"] = "timeline"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            try:
                self._timeline_owner_keys_cache = {}
            except Exception:
                pass
        except Exception:
            pass

    def _timeline_load_from_disk(self, *, apply_current_frame: bool = True) -> None:
        path = getattr(self, "_timeline_anim_path", None)
        load_path = path
        loaded_from_legacy = False
        if path is not None and not path.exists():
            try:
                scene_name = str(getattr(self, "_timeline_scene_name", "") or "").strip() or "scene"
                owner_name = str(getattr(self, "_timeline_owner_name", "") or "").strip()
                legacy_path = self._timeline_anim_file_path(
                    scene_name,
                    owner_name=owner_name or None,
                    legacy=True,
                    create=False,
                )
                if str(legacy_path) != str(path) and legacy_path.exists():
                    load_path = legacy_path
                    loaded_from_legacy = True
            except Exception:
                load_path = path
        self._timeline_keys = {}
        self._timeline_curve_selected = set()
        self._timeline_scene_skeleton_fbx_seeded = False
        self._timeline_scene_skeleton_frame_space = ""
        material_live_mode = True
        fx_instances_enabled = True
        fx_proxy_enabled = True
        if load_path is None or not load_path.exists():
            try:
                self._timeline_set_fps(24.0, save=False, sync_ui=True)
            except Exception:
                self._timeline_fps = 24.0
            try:
                self._timeline_set_material_live_mode(bool(material_live_mode), save=False)
            except Exception:
                self._timeline_material_live_mode = bool(material_live_mode)
            try:
                self._timeline_set_fx_instances_enabled(bool(fx_instances_enabled), save=False)
            except Exception:
                self._timeline_fx_instances_enabled = bool(fx_instances_enabled)
            try:
                self._timeline_set_fx_proxy_enabled(bool(fx_proxy_enabled), save=False)
            except Exception:
                self._timeline_fx_proxy_enabled = bool(fx_proxy_enabled)
            try:
                owner = str(getattr(self, "_timeline_owner_name", "") or "").strip()
                if owner:
                    virtual_map = self._timeline_scene_joint_virtual_keys_map(owner)
                    if isinstance(virtual_map, dict) and virtual_map:
                        staged = self._timeline_editable_scene_joint_keys(virtual_map)
                        if staged:
                            self._timeline_keys = staged
                            self._timeline_scene_skeleton_fbx_seeded = True
                            self._timeline_scene_skeleton_frame_space = "timeline"
                            try:
                                self._timeline_save_to_disk()
                            except Exception:
                                pass
            except Exception:
                pass
            self._timeline_total_max = max(240, int(self._timeline_current_frame()))
            try:
                if self._timeline_keys:
                    self._timeline_total_max = max(
                        int(self._timeline_total_max),
                        max(int(k) for k in self._timeline_keys.keys()),
                    )
            except Exception:
                pass
            self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
            self._timeline_update_key_count_label()
            self._timeline_refresh_coord_labels()
            try:
                self._update_timeline_loop_button()
            except Exception:
                pass
            try:
                self._timeline_update_range_button_tooltips()
            except Exception:
                pass
            try:
                self._timeline_update_range_marker_visuals()
            except Exception:
                pass
            return
        try:
            raw = json.loads(load_path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        self._timeline_scene_skeleton_fbx_seeded = bool(raw.get("scene_skeleton_fbx_seeded", False))
        def _bool_value(val, default: bool = True) -> bool:
            if isinstance(val, bool):
                return bool(val)
            if isinstance(val, (int, float)):
                return bool(val)
            txt = str(val or "").strip().lower()
            if txt in {"0", "false", "off", "no"}:
                return False
            if txt in {"1", "true", "on", "yes"}:
                return True
            return bool(default)
        material_live_mode = _bool_value(
            raw.get("material_live_mode", raw.get("material_live_enabled", True)),
            default=True,
        )
        fx_instances_enabled = _bool_value(raw.get("fx_instances_enabled", True), default=True)
        fx_proxy_enabled = _bool_value(raw.get("fx_proxy_enabled", True), default=True)
        try:
            fps = float(raw.get("fps", 24.0))
            self._timeline_set_fps(fps if fps > 0.0 else 24.0, save=False, sync_ui=True)
        except Exception:
            self._timeline_set_fps(24.0, save=False, sync_ui=True)
        try:
            self._timeline_set_material_live_mode(bool(material_live_mode), save=False)
        except Exception:
            self._timeline_material_live_mode = bool(material_live_mode)
        try:
            self._timeline_set_fx_instances_enabled(bool(fx_instances_enabled), save=False)
        except Exception:
            self._timeline_fx_instances_enabled = bool(fx_instances_enabled)
        try:
            self._timeline_set_fx_proxy_enabled(bool(fx_proxy_enabled), save=False)
        except Exception:
            self._timeline_fx_proxy_enabled = bool(fx_proxy_enabled)
        rows = raw.get("keys", []) or []
        data: Dict[int, Dict[str, object]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                frame = int(row.get("frame", 0))
            except Exception:
                continue
            if frame < 0:
                continue
            item: Dict[str, object] = {}
            xyz = row.get("xyz", None)
            if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
                try:
                    item["xyz"] = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
                except Exception:
                    pass
            rxyz = row.get("rxyz", None)
            if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
                try:
                    item["rxyz"] = [float(rxyz[0]), float(rxyz[1]), float(rxyz[2])]
                except Exception:
                    pass
            st = row.get("camera_state", None)
            if isinstance(st, dict):
                item["camera_state"] = st
            mask = row.get("axis_mask", None)
            if isinstance(mask, (list, tuple)) and len(mask) >= 6:
                try:
                    item["axis_mask"] = [bool(mask[i]) for i in range(6)]
                except Exception:
                    pass
            ch = row.get("curve_handles", None)
            if isinstance(ch, dict) and ch:
                ch_out = {}
                for ak, av in ch.items():
                    try:
                        axis_idx = int(str(ak).strip())
                    except Exception:
                        continue
                    if axis_idx < 0 or axis_idx > 5:
                        continue
                    if not isinstance(av, dict):
                        continue
                    mode_raw = str(av.get("mode", "tied") or "tied").strip().lower()
                    if mode_raw == "straight":
                        mode = "straight"
                    elif mode_raw == "untied":
                        mode = "untied"
                    else:
                        mode = "tied"
                    if mode == "straight":
                        ch_out[str(axis_idx)] = {"mode": "straight"}
                        continue
                    in_raw = av.get("in", None)
                    out_raw = av.get("out", None)
                    if not (isinstance(in_raw, (list, tuple)) and len(in_raw) >= 2):
                        continue
                    if not (isinstance(out_raw, (list, tuple)) and len(out_raw) >= 2):
                        continue
                    try:
                        ch_out[str(axis_idx)] = {
                            "mode": mode,
                            "in": [float(in_raw[0]), float(in_raw[1])],
                            "out": [float(out_raw[0]), float(out_raw[1])],
                        }
                    except Exception:
                        continue
                if ch_out:
                    item["curve_handles"] = ch_out
            source_frame = row.get("source_frame", None)
            if source_frame is not None:
                try:
                    value = float(source_frame)
                    if math.isfinite(float(value)):
                        item["source_frame"] = float(value)
                except Exception:
                    pass
            if item:
                data[int(frame)] = item
        seeded_from_fbx = False
        owner_for_virtual = str(getattr(self, "_timeline_owner_name", "") or "").strip()
        frame_space = str(raw.get("scene_skeleton_frame_space", "") or "").strip().lower()
        self._timeline_scene_skeleton_frame_space = frame_space
        try:
            if (
                owner_for_virtual
                and data
                and frame_space not in {"timeline", "timeline_retimed"}
                and not any("source_frame" in entry for entry in data.values() if isinstance(entry, dict))
            ):
                migrated, changed = self._timeline_migrate_scene_joint_keys_to_timeline_frames(
                    owner_for_virtual,
                    data,
                )
                if bool(changed):
                    data = migrated
                    self._timeline_scene_skeleton_fbx_seeded = True
                    self._timeline_scene_skeleton_frame_space = "timeline"
                    seeded_from_fbx = True
        except Exception:
            pass
        try:
            virtual_map = self._timeline_scene_joint_virtual_keys_map(owner_for_virtual)
            if (
                isinstance(virtual_map, dict)
                and virtual_map
                and not bool(getattr(self, "_timeline_scene_skeleton_fbx_seeded", False))
            ):
                merged = self._timeline_editable_scene_joint_keys(virtual_map)
                for frame, entry in data.items():
                    if not isinstance(entry, dict):
                        continue
                    base = dict(merged.get(int(frame), {}) or {})
                    base.update(dict(entry))
                    base.pop("fbx_clip_key", None)
                    base.pop("joint_key", None)
                    merged[int(frame)] = base
                data = merged
                self._timeline_scene_skeleton_fbx_seeded = True
                seeded_from_fbx = True
        except Exception:
            pass
        self._timeline_keys = data
        try:
            if (
                owner_for_virtual
                and any("source_frame" in entry for entry in data.values() if isinstance(entry, dict))
            ):
                reindex_fn = getattr(self, "_timeline_scene_joint_reindex_keys_for_speed", None)
                if callable(reindex_fn) and bool(reindex_fn(owner_for_virtual)):
                    data = getattr(self, "_timeline_keys", {}) or {}
                    seeded_from_fbx = True
                    self._timeline_scene_skeleton_frame_space = "timeline"
        except Exception:
            pass
        if seeded_from_fbx or loaded_from_legacy:
            try:
                self._timeline_save_to_disk()
            except Exception:
                pass
        max_key = 0
        try:
            if data:
                max_key = max(int(k) for k in data.keys())
        except Exception:
            max_key = 0
        self._timeline_total_max = max(240, int(max_key))
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
        self._timeline_update_key_count_label()
        self._timeline_refresh_coord_labels()
        try:
            self._update_timeline_loop_button()
        except Exception:
            pass
        try:
            self._timeline_update_range_button_tooltips()
        except Exception:
            pass
        try:
            self._timeline_update_range_marker_visuals()
        except Exception:
            pass
        if bool(apply_current_frame):
            try:
                self._timeline_apply_frame_if_keyed(self._timeline_current_frame())
            except Exception:
                pass
            try:
                self._timeline_apply_selected_camera_owner_frame(self._timeline_current_frame())
            except Exception:
                pass

    def set_timeline_scene_context(
        self,
        scene_name: str | None = None,
        project_path: str | None = None,
        owner_name: str | None = None,
        apply_current_frame: bool = True,
        load_audio: bool = True,
    ) -> None:
        scene_arg = str(scene_name or "").strip()
        name = str(scene_arg).strip()
        if not name:
            name = self._timeline_default_scene_name()
        if not name:
            name = "scene"
        owner = str(owner_name or "").strip()
        if not owner:
            owner = ""
        anim_path = self._timeline_anim_file_path(
            name,
            project_path=project_path,
            owner_name=owner or None,
        )
        range_cfg_path = self._timeline_range_file_path(
            name,
            project_path=project_path,
        )
        old_path = getattr(self, "_timeline_anim_path", None)
        same = old_path is not None and str(old_path) == str(anim_path)
        old_mode = str(getattr(self, "_timeline_mode", "composition") or "composition").strip().lower()
        requested_mode = "owner_keys" if owner else "composition"
        if old_mode == "owner_keys" and owner:
            requested_mode = "owner_keys"
        self._timeline_mode = requested_mode
        audio_scene_name = self._timeline_audio_context_scene_name(scene_arg)
        audio_cfg_path = self._timeline_audio_file_path(
            audio_scene_name,
            project_path=project_path,
        )
        old_audio_path = getattr(self, "_timeline_audio_cfg_path", None)
        same_audio = old_audio_path is not None and str(old_audio_path) == str(audio_cfg_path)
        self._timeline_scene_name = name
        self._timeline_owner_name = owner or None
        self._timeline_project_dir = anim_path.parent
        self._timeline_anim_path = anim_path
        self._timeline_range_cfg_path = range_cfg_path
        try:
            self._timeline_composition_path = self._timeline_composition_file_path(
                name,
                project_path=project_path,
            )
        except Exception:
            self._timeline_composition_path = None
        self._timeline_audio_scene_name = audio_scene_name
        self._timeline_audio_cfg_path = audio_cfg_path
        try:
            log_fn = getattr(self, "_timeline_scene_view_log", None)
            if callable(log_fn):
                log_fn(
                    "timeline_context "
                    f"scene={name!r} owner={owner!r} "
                    f"apply_current_frame={bool(apply_current_frame)} "
                    f"same_path={bool(same)} path={str(anim_path)!r}",
                    throttle_key=f"timeline_context:{owner}:{name}",
                    interval=0.05,
                )
        except Exception:
            pass
        try:
            debug_fn = getattr(self, "_timeline_camera_key_debug_log", None)
            if callable(debug_fn):
                debug_fn(
                    "timeline_context_set",
                    owner=owner,
                    frame=self._timeline_current_frame(),
                    extra={
                        "scene": name,
                        "project_path": project_path,
                        "apply_current_frame": bool(apply_current_frame),
                        "load_audio": bool(load_audio),
                        "same_path": bool(same),
                        "anim_path": str(anim_path),
                    },
                )
        except Exception:
            pass
        # Range markers are scene-level settings; reload on every context switch
        # so owner/outliner timeline changes inherit the same in/out + loop state.
        self._timeline_range_load_from_disk()
        if str(getattr(self, "_timeline_mode", "") or "").strip().lower() == "composition":
            self._timeline_load_composition(apply_current_frame=bool(apply_current_frame))
        elif not same or old_mode == "composition":
            self._timeline_load_from_disk(apply_current_frame=bool(apply_current_frame))
        else:
            self._timeline_refresh_coord_labels()
        if not same_audio and bool(load_audio):
            self._timeline_audio_load_from_disk()
        try:
            self._timeline_update_target_label()
        except Exception:
            pass
        try:
            self._timeline_refresh_speed_control()
        except Exception:
            pass
        try:
            self._timeline_update_mode_controls()
        except Exception:
            pass
