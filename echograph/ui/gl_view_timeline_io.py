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
            active = getattr(win, "_active_scene_node", None) if win is not None else None
            nm = (getattr(active, "name", "") or "").strip() if active is not None else ""
            if nm:
                return nm
        except Exception:
            pass
        current = str(getattr(self, "_timeline_scene_name", "scene") or "scene").strip()
        return current or "scene"

    def _timeline_default_project_dir(self, project_path: str | None = None) -> Path:
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
        if raw:
            try:
                p = Path(raw)
                if p.suffix:
                    p = p.parent
                return p
            except Exception:
                pass
        return Path(tempfile.gettempdir()) / "EchoGraph"

    def _timeline_anim_file_path(
        self,
        scene_name: str,
        project_path: str | None = None,
        owner_name: str | None = None,
    ) -> Path:
        base_dir = self._timeline_default_project_dir(project_path=project_path)
        out_dir = base_dir / "projects"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_scene = self._timeline_safe_name(scene_name)
        safe_owner = self._timeline_safe_name(owner_name) if str(owner_name or "").strip() else ""
        if safe_owner:
            return out_dir / f"{safe_scene}__owner_{safe_owner}_timeline.json"
        return out_dir / f"{safe_scene}_timeline.json"

    def _timeline_range_file_path(
        self,
        scene_name: str,
        project_path: str | None = None,
    ) -> Path:
        base_dir = self._timeline_default_project_dir(project_path=project_path)
        out_dir = base_dir / "projects"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_scene = self._timeline_safe_name(scene_name or "scene")
        return out_dir / f"{safe_scene}_timeline_range.json"

    def _timeline_audio_context_scene_name(self, scene_name: str | None = None) -> str:
        raw = str(scene_name or "").strip()
        if raw:
            return raw
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
    ) -> Path:
        base_dir = self._timeline_default_project_dir(project_path=project_path)
        out_dir = base_dir / "projects"
        out_dir.mkdir(parents=True, exist_ok=True)
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
            raw_out = raw.get("out_frame", None)
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
            "loop_enabled": bool(getattr(self, "_timeline_loop_enabled", True)),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
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
                base_dir = self._timeline_default_project_dir()
                out_dir = Path(base_dir) / "projects"
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
        raw = {}
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        stored = str(raw.get("audio_path", "") or "").strip()
        resolved = self._timeline_audio_resolve_path(stored)
        muted = bool(raw.get("muted", False))
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

    def _timeline_save_to_disk(self) -> None:
        path = getattr(self, "_timeline_anim_path", None)
        if path is None:
            return
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
            "loop_enabled": bool(getattr(self, "_timeline_loop_enabled", True)),
            "material_live_mode": bool(getattr(self, "_timeline_material_live_mode", True)),
            "fx_instances_enabled": bool(getattr(self, "_timeline_fx_instances_enabled", True)),
            "fx_proxy_enabled": bool(getattr(self, "_timeline_fx_proxy_enabled", True)),
            "keys": keys_out,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _timeline_load_from_disk(self) -> None:
        path = getattr(self, "_timeline_anim_path", None)
        self._timeline_keys = {}
        self._timeline_curve_selected = set()
        material_live_mode = True
        fx_instances_enabled = True
        fx_proxy_enabled = True
        if path is None or not path.exists():
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
            self._timeline_total_max = max(240, int(self._timeline_current_frame()))
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
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
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
            if fps > 0.0:
                self._timeline_fps = fps
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
            if item:
                data[int(frame)] = item
        if not data:
            owner = str(getattr(self, "_timeline_owner_name", "") or "").strip()
            if owner:
                try:
                    renderer = getattr(self, "_mgl_renderer", None) or self
                    map_fn = getattr(renderer, "_mgl_timeline_owner_keys_map", None)
                    if callable(map_fn):
                        virtual_map = map_fn(owner)
                    else:
                        virtual_map = {}
                except Exception:
                    virtual_map = {}
                if isinstance(virtual_map, dict) and virtual_map:
                    staged: Dict[int, Dict[str, object]] = {}
                    for frame_raw, entry in virtual_map.items():
                        try:
                            frame = int(frame_raw)
                        except Exception:
                            continue
                        if frame < 0:
                            continue
                        if isinstance(entry, dict):
                            staged[int(frame)] = dict(entry)
                    if staged:
                        data = staged
        self._timeline_keys = data
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
        try:
            self._timeline_apply_frame_if_keyed(self._timeline_current_frame())
        except Exception:
            pass

    def set_timeline_scene_context(
        self,
        scene_name: str | None = None,
        project_path: str | None = None,
        owner_name: str | None = None,
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
        self._timeline_audio_scene_name = audio_scene_name
        self._timeline_audio_cfg_path = audio_cfg_path
        # Range markers are scene-level settings; reload on every context switch
        # so owner/outliner timeline changes inherit the same in/out + loop state.
        self._timeline_range_load_from_disk()
        if not same:
            self._timeline_load_from_disk()
        else:
            self._timeline_refresh_coord_labels()
        if not same_audio:
            self._timeline_audio_load_from_disk()
        try:
            self._timeline_update_target_label()
        except Exception:
            pass

