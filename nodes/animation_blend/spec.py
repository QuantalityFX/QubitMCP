"""Blend a character's bind pose into a skeletal animation for Scene/cloth."""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from nodes.core import Spec
from nodes.util_graph import param_change_relevant
from echograph.rigging.animation_blend import AnimationBlendError, build_blended_clip
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time, evaluation_cache_token

KIND_ALIASES = ("animation_blend", "animation blend", "anim_blend", "blend_animation")
DEFAULTS = {
    "use_target_frame": "0", "target_frame": "0", "blend_frames": "30",
    "hold_frames": "0", "fps": "24", "blend_curve": "smooth",
}


def values(model):
    return {**DEFAULTS, **{p.get("name"): str(p.get("value", "")) for p in model.params}}


def build_ports(item):
    from nodes.mocap_import.spec import _ensure_param, _ensure_hidden_params

    for name, default in {"bind_pose": "", "animation": "", **DEFAULTS}.items():
        _ensure_param(item, name, default)
    _ensure_hidden_params(item.model, DEFAULTS)
    item.model._named_inputs = ["bind_pose", "animation"]
    item._default_named_input = "bind_pose"
    item._show_default_input_with_named = False
    item.ensure_input("bind_pose")
    item.ensure_input("animation")
    item.ensure_output("animation")
    item._hide_default_output_with_named = True


def resolve_context(item, errors, warnings, *, target=False, _depth=0, _visited=None):
    """Return the derived rig without exposing an input's unblended clip."""
    from nodes.anim_retarget import spec as retarget

    model = item.model
    scene = item.scene()
    item = getattr(scene, "_node_items", {}).get(model.name, item)
    visited = set() if _visited is None else set(_visited)
    if id(item) in visited or _depth > 32 or getattr(model, "_animation_blend_resolving", False):
        message = "Animation Blend: input chain contains a cycle or is too deep."
        errors.append(message)
        model._animation_blend_error = message
        return None
    visited.add(id(item))
    model._animation_blend_resolving = True
    try:
        inputs = {}
        for port in ("bind_pose", "animation"):
            upstream, issue = retarget._connected_item_for_port(scene, item, port)
            if issue:
                warnings.append(issue)
            if upstream is None:
                raise AnimationBlendError(f"Connect a {'BVH or FBX skeleton' if port == 'bind_pose' else 'skeletal animation'} to the {port.replace('_', ' ')} input.")
            kind = str(upstream.model.kind).strip().lower()
            resolver = retarget._context_for_target_item if port == "bind_pose" else retarget._context_for_source_item
            local_errors = []
            context = resolver(upstream, kind, local_errors, warnings, _depth=_depth + 1, _visited=set(visited))
            if context is None or local_errors:
                raise AnimationBlendError("\n".join(local_errors) or f"Unsupported {port.replace('_', ' ')} input '{kind}'. Use Mocap Import, FBX Import, FBX Animation, or Anim Retarget.")
            if context.get("skeleton") is None:
                raise AnimationBlendError(f"The {port.replace('_', ' ')} input has no skeleton.")
            inputs[port] = context
        bind, animated = inputs["bind_pose"], inputs["animation"]
        source_clip = animated.get("clip")
        if source_clip is None:
            raise AnimationBlendError("The animation input has no animation. Enable the animation on the connected import node.")
        skeleton = bind["skeleton"]
        if bind.get("source_format") != "bvh":
            # FBX mesh bind matrices are authoritative; its local rest transforms
            # can instead describe a different capture pose or animation frame.
            skeleton = retarget._retarget_eval_target_skeleton(skeleton)
        config = values(model)
        target_frame = config["target_frame"] if config["use_target_frame"].lower() in {"1", "true", "yes"} else "0"
        # BVH offsets describe a static rest skeleton. Its imported pose and
        # root height are stored in the first frame's animation channels.
        reference_clip = bind.get("clip") if bind.get("source_format") == "bvh" else None
        identities = tuple((evaluation_cache_token(value), id(value))
                           for value in (skeleton, reference_clip, animated["skeleton"], source_clip))
        key = (identities, target_frame, config["blend_frames"], config["hold_frames"],
               config["fps"], config["blend_curve"])
        cached = getattr(model, "_animation_blend_clip_cache", None)
        if cached is not None and cached[0] == key:
            clip = cached[1]
        else:
            reference_pose = (evaluate_rig_at_time(skeleton, reference_clip, reference_clip.start_time).local_transforms
                              if reference_clip is not None else None)
            clip = build_blended_clip(skeleton, animated["skeleton"], source_clip,
                                      target_frame=target_frame, blend_frames=config["blend_frames"],
                                      hold_frames=config["hold_frames"], fps=config["fps"],
                                      curve=config["blend_curve"], reference_pose=reference_pose,
                                      name=f"{model.name} Animation")
            model._animation_blend_clip_cache = (key, clip)
        fps = clip.sample_rate_hz
        last_frame = max(0, int(math.floor((source_clip.end_time - source_clip.start_time) * fps + 1e-6)))
        if float(target_frame) > last_frame:
            warnings.append(f"Target frame exceeds the animation; using its final pose (frame {last_frame}).")
        meshes = list(bind.get("meshes") or [])
        rig = dict(bind.get("rig_context") or {})
        for flag in ("retarget_preview_role", "retarget_static_pose", "retarget_source_rest_pose",
                     "retarget_preview_target_animated", "retarget_result"):
            rig.pop(flag, None)
        rig.update(skeleton=skeleton, clip=clip, clips=[clip], meshes=meshes, loop=False,
                   mesh_skinning_enabled=bool(meshes), preview_bind_geometry_only=False,
                   skin_weight_debug=False, show_skin_weights=False, show_capture_joints=False,
                   show_animated_joints=not bool(meshes), animation_blend=True)
        context = dict(bind, name=model.name, kind="animation_blend", node_item=item, model=model,
                       skeleton=skeleton, clip=clip, clips=[clip], meshes=meshes, rig_context=rig,
                       joint_count=len(skeleton.joints), clip_count=1, mesh_count=len(meshes),
                       bind_result=None, animation_result=None)
        if isinstance(bind.get("preview_asset"), dict):
            context["preview_asset"] = dict(bind["preview_asset"], fbx_rig_context=rig)
        model._animation_blend_error = ""
        start_label = "BVH first pose" if reference_clip is not None else "Bind pose"
        model._animation_blend_summary = (
            f"{start_label} -> frame {min(int(float(target_frame)), last_frame)} in {config['blend_frames']} frames\n"
            f"{fps:g} fps | {len(skeleton.joints)} joints | {clip.duration:.2f} s"
        )
        if warnings:
            model._animation_blend_summary += "\n" + warnings[-1]
        return context
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        message = str(exc)
        errors.append(message)
        model._animation_blend_error = message
        return None
    finally:
        model._animation_blend_resolving = False


def build_animation_blend_scene_asset(item):
    from nodes.anim_retarget import spec as retarget

    errors, warnings = [], []
    context = resolve_context(item, errors, warnings)
    if context is None:
        return None
    asset = context.get("preview_asset")
    if not isinstance(asset, dict):
        asset = retarget._preview_asset_for_context(
            context, owner=item.model.name,
            role="source" if context.get("source_format") == "bvh" else "target",
            x_offset=0.0, curve_thickness=2.0,
        )
    if not isinstance(asset, dict):
        item.model._animation_blend_error = "The bind-pose input has no scene asset. Check its source file."
        return None
    asset = dict(asset)
    asset.update(node=item.model.name, kind="animation_blend", source_kind="animation_blend",
                 fbx_rig_context=context["rig_context"], has_skeleton=True, visible=True,
                 xform=retarget._clean_xform(context.get("transform_xform")),
                 ext=Path(str(asset.get("path") or "")).suffix.lower() or ".fbx")
    asset.pop("retarget_preview_role", None)
    if context.get("source_format") == "bvh":
        asset["wire_only"] = True
    return asset


def view_result(item):
    scene = item.scene()
    views = scene.views() if scene is not None else []
    window = views[0].window() if views else None
    asset = build_animation_blend_scene_asset(item)
    if asset is None:
        QtWidgets.QMessageBox.warning(window, "Animation Blend", getattr(item.model, "_animation_blend_error", "Connect both inputs first."))
        return False
    handler = getattr(window, "open_scene_assets", None)
    if not callable(handler):
        QtWidgets.QMessageBox.warning(window, "Animation Blend", "The main 3D viewport is not available.")
        return False
    handler([asset], frame=True)
    return True


def build_transformed_blend_scene_asset(item, *, _depth=0, _visited=None):
    """Keep the derived animation when a Transform wraps the blend output."""
    from nodes.anim_retarget import spec as retarget

    visited = set() if _visited is None else set(_visited)
    if id(item) in visited or _depth > 32:
        return None
    visited.add(id(item))
    upstream, _issue = retarget._connected_item_for_ports(item.scene(), item, ("mesh", "path", "source"))
    if upstream is None:
        return None
    kind = str(upstream.model.kind).strip().lower()
    if kind in KIND_ALIASES:
        asset = build_animation_blend_scene_asset(upstream)
    elif kind == "rig_limiter":
        from nodes.rig_limiter.spec import build_scene_asset
        asset = build_scene_asset(upstream)
    elif kind in retarget.TRANSFORM_KIND_ALIASES:
        asset = build_transformed_blend_scene_asset(upstream, _depth=_depth + 1, _visited=visited)
    else:
        return None
    if asset is None:
        return None
    asset = dict(asset)
    asset["node"] = item.model.name
    asset["xform"] = retarget._combine_xforms(asset.get("xform"), retarget._xform_from_transform_node(item))
    return asset


class BlendControls(QtWidgets.QWidget):
    def __init__(self, item):
        super().__init__()
        self.item = item
        config = values(item.model)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        self.override = QtWidgets.QCheckBox("Use target frame")
        self.override.setChecked(config["use_target_frame"].lower() in {"1", "true", "yes"})
        self.override.setToolTip("Blend to a later animation frame. Frame 0 is the first frame.")
        form.addRow(self.override)
        self.spins = {}
        for name, label in (("target_frame", "Target frame"), ("blend_frames", "Blend frames"),
                            ("hold_frames", "Hold pose frames")):
            spin = QtWidgets.QSpinBox()
            spin.setObjectName(f"AnimationBlend_{name}")
            spin.setRange(0, 100000)
            try:
                spin.setValue(int(float(config[name])))
            except (ValueError, OverflowError):
                spin.setValue(int(DEFAULTS[name]))
            spin.valueChanged.connect(lambda value, key=name: self.set_value(key, str(value)))
            form.addRow(label, spin)
            self.spins[name] = spin
        self.spins["target_frame"].setEnabled(self.override.isChecked())
        self.spins["target_frame"].setToolTip("Frame counted from the animation start at this node's frame rate.")
        self.spins["blend_frames"].setToolTip("Frames spent transitioning to the target pose. Zero skips the transition.")
        self.spins["hold_frames"].setToolTip("Hold the starting pose before blending so cloth can settle.")
        self.override.toggled.connect(self._override_changed)
        self.fps = QtWidgets.QDoubleSpinBox()
        self.fps.setObjectName("AnimationBlend_fps")
        self.fps.setRange(1, 240)
        self.fps.setDecimals(3)
        try:
            self.fps.setValue(float(config["fps"]))
        except ValueError:
            self.fps.setValue(24)
        self.fps.setSuffix(" fps")
        self.fps.setToolTip("Frame rate for blend duration and target frame. Match the Scene timeline (default 24 fps).")
        self.fps.valueChanged.connect(lambda value: self.set_value("fps", str(value)))
        form.addRow("Frame rate", self.fps)
        self.curve = QtWidgets.QComboBox()
        self.curve.addItem("Smooth", "smooth")
        self.curve.addItem("Linear", "linear")
        self.curve.setCurrentIndex(max(0, self.curve.findData(config["blend_curve"])))
        self.curve.currentIndexChanged.connect(lambda _: self.set_value("blend_curve", self.curve.currentData()))
        form.addRow("Blend curve", self.curve)
        button = QtWidgets.QPushButton("View")
        button.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        button.clicked.connect(lambda: view_result(self.item))
        form.addRow("", button)
        self.status = QtWidgets.QLabel("Connect a bind-pose rig and an animation.")
        self.status.setWordWrap(True)
        self.status.setFixedHeight(60)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.status)
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self.refresh)
        scene = item.scene() or getattr(item.model, "_graph_scene", None)
        if scene is not None:
            if hasattr(scene, "linksChanged"):
                scene.linksChanged.connect(self.schedule_refresh)
            if hasattr(scene, "paramChanged"):
                scene.paramChanged.connect(self._param_changed)
        self.schedule_refresh()

    def _override_changed(self, enabled):
        self.spins["target_frame"].setEnabled(enabled)
        self.set_value("use_target_frame", "1" if enabled else "0")

    def set_value(self, name, value):
        item = self.item
        # Scene updates can destroy InfoCard controls; finish their Qt event first.
        def apply():
            if isinstance(item, QtCore.QObject) and not isValid(item):
                return
            actual = getattr(item.scene(), "_node_items", {}).get(item.model.name, item)
            if hasattr(actual, "_set_param_value"):
                actual._set_param_value(name, value, rebuild=False)
        QtCore.QTimer.singleShot(0, apply)
        self.schedule_refresh()

    def _param_changed(self, name, *_args):
        if param_change_relevant(self.item, name):
            self.schedule_refresh()

    def schedule_refresh(self, *_args):
        self._refresh_timer.start()

    def refresh(self):
        if isinstance(self.item, QtCore.QObject) and not isValid(self.item):
            return
        config = values(self.item.model)
        controls = {**self.spins, "fps": self.fps, "use_target_frame": self.override, "blend_curve": self.curve}
        for name, control in controls.items():
            blocker = QtCore.QSignalBlocker(control)
            try:
                if name == "use_target_frame":
                    control.setChecked(config[name].lower() in {"1", "true", "yes"})
                elif name == "blend_curve":
                    control.setCurrentIndex(max(0, control.findData(config[name])))
                else:
                    control.setValue(float(config[name]) if name == "fps" else int(float(config[name])))
            except (ValueError, OverflowError):
                pass
            del blocker
        self.spins["target_frame"].setEnabled(self.override.isChecked())
        errors, warnings = [], []
        resolve_context(self.item, errors, warnings)
        message = "\n".join(errors) if errors else self.item.model._animation_blend_summary
        self.status.setText(message if len(message) <= 160 else message[:157] + "...")
        self.status.setToolTip(message)


def render_node_body(item, y_cursor):
    widget = BlendControls(item)
    widget.ensurePolished()
    item.prepareGeometryChange()
    item.width = max(item.width, 280, widget.minimumSizeHint().width() + 16)
    widget.setFixedWidth(int(item.width - 16))
    height = widget.sizeHint().expandedTo(widget.minimumSizeHint()).height()
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(widget)
    proxy.setPos(8, y_cursor)
    proxy.resize(item.width - 16, height)
    item._plugin_proxies.append(proxy)
    item.height = max(item.height, y_cursor + height + 10)
    return y_cursor + height


def augment_infocard_footer(card, layout):
    model = getattr(card, "_node_ref", None)
    if model is None or model.kind.lower() not in KIND_ALIASES:
        return False
    scene = getattr(card, "_graph_scene", None)
    item = getattr(scene, "_node_items", {}).get(model.name) or SimpleNamespace(model=model, scene=lambda: scene)
    layout.addWidget(BlendControls(item))
    return True


ANIMATION_BLEND_SPEC = Spec(stripe_color="#c084fc", build_ports=build_ports,
                            render_node_body=render_node_body, augment_infocard_footer=augment_infocard_footer)
