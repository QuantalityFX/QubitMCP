from __future__ import annotations

import json
from pathlib import Path
import tempfile
import weakref

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from echograph.image_generation.backend import ComfyUIBackend
from echograph.image_generation.config import storage_root
from echograph.image_generation.qt_job import ImageTask
from echograph.image_generation.runtime import runtime_manager
from echograph.image_generation.schema import ImageRequest

DEFAULTS = {"mode": "text_to_image", "model_variant": "int8", "prompt": "", "image": "", "reference_image": "",
            "seed": "42", "random_seed": "true", "steps": "40", "width": "1024", "height": "1024",
            "edit_resolution": "1024", "cfg": "1.0", "transparent": "false", "low_memory": "false",
            "license_use": "research", "license_accepted": "false", "image_path": "", "path": "",
            "pending_request": "", "pending_output_directory": "",
            "seed_used": "", "job_id": "", "status": "idle", "metadata": "{}", "output_directory": "",
            "backend": "comfyui", "negative_prompt": "", "reference_images": "",
            "edit_guidance": "flexible", "edit_framing": "auto"}


def manager_for(model):
    if value(model, "backend") == "native_diffusers":
        from echograph.image_generation.native_runtime import native_runtime_manager
        return native_runtime_manager()
    return runtime_manager()


def backend_for(model):
    manager = manager_for(model)
    if value(model, "backend") == "native_diffusers":
        from echograph.image_generation.native_backend import NativeBackend
        return NativeBackend(manager.service)
    return ComfyUIBackend(manager.config.url)


def value(model, key):
    return str(next((entry.get("value", "") for entry in model.params if entry.get("name") == key), DEFAULTS.get(key, "")))


def put(model, **values):
    params = [dict(entry) for entry in model.params]
    for key, text in values.items():
        entry = next((entry for entry in params if entry.get("name") == key), None)
        if entry is None:
            params.append({"name": key, "value": str(text)})
        else:
            entry["value"] = str(text)
    model.params = params


def notify(item):
    scene = item.scene()
    if scene is not None and hasattr(scene, "set_node_params"):
        scene.set_node_params(item.model.name, item.model.params, rebuild=False, emit=True)


def note_prompt_values(model):
    checked = getattr(model, "_completed_params", ())
    if isinstance(checked, str):
        checked = (checked,)
    if not isinstance(checked, (set, list, tuple)):
        checked = ()
    names = {str(name) for name in checked}
    hidden = {name.strip() for name in value(model, "__ui_hidden_params").split(",") if name.strip()}
    params = [param for param in model.params if param.get("name")
              and not str(param["name"]).startswith("__") and str(param["name"]) not in hidden]
    values = {}
    for param in params:
        name = str(param["name"])
        raw = param.get("value")
        text = "" if raw is None else str(raw).strip()
        if text and (len(params) == 1 or name in names):
            values[name] = text
    return values


def prompt_text(scene, source):
    if (source.model.kind or "").strip().lower() == "note":
        return "\n\n".join(note_prompt_values(source.model).values())
    flatten = getattr(scene, "_flatten_text_segments", None)
    if callable(flatten):
        texts = []
        for segment in flatten(source):
            model = getattr(scene, "_nodes_by_name", {}).get(segment.get("node"))
            if model is not None and (model.kind or "").strip().lower() == "note":
                text = note_prompt_values(model).get(segment.get("param"), "")
            else:
                text = segment.get("text", "")
            if text:
                texts.append(text)
        return "\n\n".join(texts)
    return scene.resolve_text_value(source)


def incoming(item, port):
    scene = item.scene()
    if scene is None:
        return None
    edges = scene._ordered_in_edges(item) if hasattr(scene, "_ordered_in_edges") else (
        scene._in_edges(item) if hasattr(scene, "_in_edges") else [])
    for edge in edges:
        name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", "")
        if str(name).lower() != port:
            continue
        source = edge.src
        model = source.model
        if port in ("prompt", "negative_prompt"):
            return prompt_text(scene, source)
        output = getattr(model, "_qwen_outputs", {})
        if output.get("image") is not None:
            return output["image"].copy()
        for attr in ("_output_image", "_image", "_preview_image"):
            image = getattr(model, attr, None)
            if image is not None:
                # Transfer QPixmap to a thread-safe QImage on the GUI thread.
                return image.toImage() if isinstance(image, QtGui.QPixmap) else image
        paths = getattr(model, "_image_collection_state", {}).get("paths", [])
        if paths:
            return paths[0]
        thumbnail = value(model, "thumbnail").strip()
        if thumbnail:
            path = resolve_path(item, thumbnail)
            selected = value(model, "thumbnail_choice").strip()
            if selected:
                selection = Path(selected)
                path = selection if selection.is_absolute() else path.parent / selection.name
            return path
        for key in ("image_path", "path", "source_image", "preview_image", "texture", "output"):
            text = value(model, key).strip()
            if text:
                return resolve_path(item, text)
        raise ValueError(f"The node connected to {port} has no image result.")
    return None


def workflow_directory(item):
    scene = item.scene()
    if scene:
        for view in scene.views():
            window = view.window()
            for key in ("_pending_project_save_dir", "_current_project_dir"):
                text = str(getattr(window, key, "") or "")
                if text:
                    return Path(text)
            current = str(getattr(window, "_current_path", "") or "")
            if current:
                return Path(current).parent
    return None


def resolve_path(item, text):
    path = Path(text).expanduser()
    return path if path.is_absolute() else (workflow_directory(item) or storage_root()) / path


def output_directory_for(item):
    directory = value(item.model, "output_directory").strip()
    return resolve_path(item, directory) if directory else (
        (workflow_directory(item) or Path(tempfile.gettempdir()) / "QubitMCP") / "qwen_image")


class NodeSession(QtCore.QObject):
    changed = QtCore.Signal()
    image_selected = QtCore.Signal(str, str)

    def __init__(self, item):
        super().__init__(QtCore.QCoreApplication.instance())
        self.item_ref = weakref.ref(item)
        self.task = None
        self.check_task = None
        self.inventory = None
        self._image_picker_active = False
        self._image_directory = None
        self.message = value(item.model, "status")
        if self.message in ("running", "queued"):
            self.message = "Previous job detached. Check its history before generating again: " + value(item.model, "job_id")
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._watch_item)
        self._managers = set()
        self.bind_manager()

    def bind_manager(self):
        item = self.item_ref()
        manager = manager_for(item.model)
        if manager not in self._managers:
            manager.server_stopped.connect(lambda m=manager: self._backend_stopped()
                if self.item() is not None and manager_for(self.item().model) is m else None)
            self._managers.add(manager)

    def item(self):
        item = self.item_ref()
        return item if item is not None and isValid(item) and item.scene() is not None else None

    @property
    def running(self):
        return self.task is not None

    def _watch_item(self):
        if self.item() is None:
            if self.task:
                self.task.cancel()
            self.timer.stop()

    def choose_image(self, key, caption):
        item = self.item()
        if item is None or self.running or self._image_picker_active:
            return
        # Parent the native picker to the real application window, never to the
        # embedded QGraphicsProxyWidget, which can be destroyed during its event loop.
        views = item.scene().views()
        parent = views[0].window() if views else None
        current = value(item.model, key).strip()
        initial = str(resolve_path(item, current)) if current else str(
            self._image_directory or workflow_directory(item) or Path.home())
        self._image_picker_active = True
        try:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(parent, caption, initial,
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        except (OSError, RuntimeError) as exc:
            self.message = "Could not open the image picker: " + str(exc)
            self.changed.emit()
            return
        finally:
            self._image_picker_active = False
        item = self.item()
        if path and item is not None:
            put(item.model, **{key: path})
            self._image_directory = Path(path).parent
            notify(item)
            self.image_selected.emit(key, path)
            self.changed.emit()

    def check_backend(self):
        if self.check_task is not None:
            return
        item = self.item()
        if item is None:
            return
        backend = backend_for(item.model)
        native = value(item.model, "backend") == "native_diffusers"
        def check(cancel, progress):
            backend.cancel_event = cancel
            try:
                if native:
                    return backend.inventory(cancel, progress)
                return backend.inventory()
            finally:
                backend.close()
        self.check_task = ImageTask(check)
        self.check_task.progress.connect(self._check_progress)
        self.check_task.result.connect(self._checked)
        self.check_task.failed.connect(self._check_failed)
        self.check_task.finished.connect(self._check_finished)
        self.message = "Checking local backend..."
        self.changed.emit()
        self.check_task.start()

    def _check_progress(self, progress):
        if self.check_task and not self.check_task.cancel_event.is_set() and not self.running:
            self.message = progress["message"]
            self.changed.emit()

    def _checked(self, inventory):
        if self.check_task and self.check_task.cancel_event.is_set():
            return
        self.inventory = inventory
        item = self.item()
        if item is not None:
            try:
                request = self.request(validate=False)
                if request.backend == "native_diffusers":
                    if request.model_variant not in inventory.get("variants", []):
                        raise ValueError("The selected native model is not installed or validated.")
                    if not self.running:
                        self.message = "Native backend ready: official BF16 with CPU offloading."
                    self.changed.emit()
                    return
                # Validate the actual selected workflow and all model choices.
                from dataclasses import replace
                request = replace(request, prompt=request.prompt or "Backend check",
                                  image="check" if request.mode == "image_edit" else None)
                backend = ComfyUIBackend(runtime_manager().config.url)
                try:
                    graph, _ = backend.build_graph(request, 42)
                    backend.check_graph(graph, inventory)
                finally:
                    backend.close()
                self.message = "Backend ready for " + ("INT8" if request.model_variant == "int8" else "GGUF Q4_K_M")
            except Exception as exc:
                self.message = str(exc)
        self.changed.emit()

    def _check_failed(self, error):
        if self.check_task and self.check_task.cancel_event.is_set():
            return
        self.inventory = None
        self.message = "Backend unavailable: " + error
        self.changed.emit()

    def _check_finished(self):
        self.check_task = None
        self.changed.emit()

    def _backend_stopped(self):
        self.inventory = None
        if self.check_task:
            self.check_task.cancel()
        self.message = "Local backend stopped. Start it again to generate images."
        self.changed.emit()

    def request(self, validate=True):
        item = self.item()
        if item is None:
            raise RuntimeError("The Qwen Image node is no longer in the graph.")
        model = item.model
        mode = value(model, "mode")
        prompt = incoming(item, "prompt")
        negative_prompt = incoming(item, "negative_prompt")
        native = value(model, "backend") == "native_diffusers"
        if not native and negative_prompt is not None:
            raise ValueError("Negative prompt connections require the native Diffusers backend.")
        image = reference = None
        if mode == "image_edit":
            image = incoming(item, "image")
            reference = incoming(item, "reference_image")
            if image is None and value(model, "image").strip():
                image = resolve_path(item, value(model, "image"))
            if reference is None and value(model, "reference_image").strip():
                reference = resolve_path(item, value(model, "reference_image"))
        if validate and prompt is not None and not prompt.strip():
            raise ValueError("Connected positive prompt is empty. Check the source node's selected values.")
        request = ImageRequest(prompt=value(model, "prompt") if prompt is None else prompt, mode=mode,
            model_variant=value(model, "model_variant"), image=image, reference_image=reference,
            seed=None if value(model, "random_seed") == "true" else int(value(model, "seed")),
            steps=int(value(model, "steps")), width=int(value(model, "width")), height=int(value(model, "height")),
            edit_resolution=int(value(model, "edit_resolution")), cfg=float(value(model, "cfg")),
            transparent=value(model, "transparent") == "true", low_memory=value(model, "low_memory") == "true",
            backend=value(model, "backend"),
            negative_prompt=(value(model, "negative_prompt") if negative_prompt is None else negative_prompt) if native else "",
            edit_guidance=value(model, "edit_guidance") if value(model, "backend") == "native_diffusers" else "flexible",
            edit_framing=value(model, "edit_framing") if value(model, "backend") == "native_diffusers" else "auto",
            reference_images=tuple(resolve_path(item, line.strip()) for line in value(model, "reference_images").splitlines()
                                   if line.strip()) if mode == "image_edit" and value(model, "backend") == "native_diffusers" else ())
        return request.validate() if validate else request

    def generate(self, recover=False):
        if self.running:
            self.task.cancel()
            self.message = "Cancelling this job..."
            self.changed.emit()
            return
        item = self.item()
        if item is None:
            return
        try:
            manager = manager_for(item.model)
            if manager.installing:
                raise ValueError("Wait for Setup to finish.")
            if manager.stopping:
                raise ValueError("Wait for the local backend to stop before starting another job.")
            if value(item.model, "license_accepted") != "true":
                raise ValueError("Open Setup and accept the Qwen model license before generating.")
            if value(item.model, "license_use") not in ("research", "separately_licensed"):
                raise ValueError("Select a permitted model use in Setup.")
            if recover:
                data = json.loads(value(item.model, "pending_request"))
                request = ImageRequest(**data)
                if request.backend != value(item.model, "backend"):
                    raise ValueError("Select the backend used by the saved job before recovering it.")
                from dataclasses import replace
                request = replace(request, seed=int(value(item.model, "seed_used")))
                job_id = value(item.model, "job_id")
                if not job_id:
                    raise ValueError("There is no saved job ID to recover.")
            else:
                request = self.request()
                job_id = None
            directory = output_directory_for(item)
            if recover and value(item.model, "pending_output_directory"):
                directory = Path(value(item.model, "pending_output_directory"))
            if not recover:
                from dataclasses import fields
                snapshot = {field.name: getattr(request, field.name) for field in fields(request)
                            if field.name not in ("image", "reference_image", "reference_images")}
                snapshot.update(image="uploaded" if request.image is not None else None,
                                reference_image="uploaded" if request.reference_image is not None else None,
                                reference_images=["uploaded"] * len(request.reference_images))
                put(item.model, pending_request=json.dumps(snapshot), pending_output_directory=directory,
                    job_id="", status="running")
            backend = backend_for(item.model)
            def generate(cancel, progress):
                try:
                    return backend.generate(request, directory, cancel, progress, resume_job_id=job_id)
                finally:
                    backend.close()
            self.task = ImageTask(generate)
            self.task.progress.connect(self._progress)
            self.task.result.connect(self._result)
            self.task.failed.connect(self._failed)
            self.task.finished.connect(self._finished)
            manager.track_job(self.task)
            self.message = "Preparing image generation..."
            self.timer.start()
            self.task.start()
        except Exception as exc:
            self.message = str(exc)
        self.changed.emit()

    def _progress(self, progress):
        item = self.item()
        if item is None:
            return
        old_job_id = value(item.model, "job_id")
        self.message = progress["message"]
        put(item.model, **{key: progress[key] for key in ("status", "job_id", "seed_used") if key in progress})
        if progress.get("job_id") and progress["job_id"] != old_job_id:
            notify(item)
        self.changed.emit()

    def _result(self, result):
        item = self.item()
        if item is None:
            return
        put(item.model, image_path=result.image_path, path=result.image_path, seed_used=result.seed_used,
            job_id=result.job_id, status="success", metadata=json.dumps(result.metadata), pending_request="")
        # Pillow keeps alpha in memory; path is the existing disk-backed port convention.
        item.model._qwen_outputs = {"image": result.image, "image_path": str(result.image_path),
            "seed_used": result.seed_used, "job_id": result.job_id, "status": "success", "metadata": result.metadata}
        self.message = result.metadata.get("warning") or f"Complete - seed {result.seed_used}"
        notify(item)
        self.changed.emit()

    def _failed(self, error):
        item = self.item()
        if item:
            put(item.model, status="cancelled" if self.task and self.task.cancel_event.is_set() else "failed")
            notify(item)
        self.message = error
        self.changed.emit()

    def _finished(self):
        self.task = None
        self.timer.stop()
        self.changed.emit()


def session_for(item):
    session = getattr(item.model, "_qwen_session", None)
    if session is None:
        session = NodeSession(item)
        item.model._qwen_session = session
    else:
        session.item_ref = weakref.ref(item)
    return session
