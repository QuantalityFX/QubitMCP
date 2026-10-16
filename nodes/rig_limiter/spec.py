"""Repair an existing collection output; source files are never overwritten."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtWidgets

from nodes.core import Spec
from nodes.mocap_import import spec as mocap
from echograph.motion.conversion import JOINT_NAMES, write_bvh
from echograph.motion.rig_limiter import limit_clip, REPAIR_VERSION
from echograph.motion import rig_repair_cache as repair_files
from echograph.motion.rig_limits import preview_limit_fields, preview_limit_from_fields
from echograph.motion.rig_preview_settings import load_preview_limits, load_preview_volumes, load_preview_hand_limits
from echograph.motion.hand_rig import full_rig_layout, expanded_limits, hand_limits_from_fields
from echograph.motion.rig_volumes import volume_settings
from echograph.rigging.fbx_stage5_evaluator import evaluation_cache_token, evaluate_rig_at_time


def settings(model):
    try:
        saved = json.loads(mocap._param_value(model, 'rig_settings') or '{}')
        if not isinstance(saved, dict):
            saved = {}
    except ValueError:
        saved = {}
    limits = load_preview_limits()
    saved_limits = saved.get('joint_limits')
    saved_limits = saved_limits if isinstance(saved_limits,dict) else {}
    for j in limits:
        fields = saved_limits.get(JOINT_NAMES[j])
        if isinstance(fields, dict):
            limits[j] = preview_limit_from_fields(j, fields)
    volumes = volume_settings(saved['volumes']) if isinstance(saved.get('volumes'), dict) else load_preview_volumes()
    return limits, volumes, mocap._param_value(model, 'collisions') != '0'


def store_settings(item, guide):
    names = guide.get('joint_names',JOINT_NAMES)
    value = dict(joint_limits={names[j]:preview_limit_fields(j,v) for j,v in guide['joint_limits'].items()},
                 volumes=guide['volumes'])
    mocap._set_param_value(item.model,'rig_settings',json.dumps(value,sort_keys=True))
    mocap._set_param_value(item.model,'collisions','1' if guide['collisions'] else '0')
    refresh_repair_status(item.model)
    scene = item.scene()
    if scene is not None:
        scene.set_node_params(item.model.name,list(item.model.params),rebuild=False,emit=True)


def hand_settings(model):
    limits = load_preview_hand_limits()
    try:
        saved = json.loads(mocap._param_value(model,'rig_settings') or '{}').get('joint_limits',{})
        if isinstance(saved,dict):
            parsed = hand_limits_from_fields(saved)
            limits.update({name:parsed[name] for name in limits if name in saved})
    except (ValueError,TypeError,AttributeError):
        pass
    return limits


def ik_repair_enabled(model):
    return mocap._param_value(model,'ik_repair') != '0'


def repair_key(skeleton,clip,limits,volumes,collisions,hands,*,ik_repair=True):
    return (evaluation_cache_token(skeleton),evaluation_cache_token(clip),
            json.dumps(limits,sort_keys=True),json.dumps(volumes,sort_keys=True),collisions,json.dumps(hands,sort_keys=True),
            ik_repair,REPAIR_VERSION)


def restore_repair(model, context, key):
    """Restore a saved result without ever running the repair algorithm."""
    path = Path(context.get('path') or '')
    source = (path if context.get('kind') in ('mocap_collection', 'mocap_import', 'bvh_import')
              and path.is_file() and path.suffix.lower() == '.bvh' else None)
    model._rig_limiter_disk_source = source
    if source is None:
        return
    record = repair_files.find_repair(source, repair_files.options_signature(key[2:]))
    cached = getattr(model, '_rig_limiter_cache', None)
    if record is None or (cached is not None and cached[0] == key
                          and getattr(model, '_rig_limiter_disk_record', None) == record):
        return
    from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
    loaded = ingest_bvh_animation_data(repair_files.repair_path(source, record))
    if loaded.skeleton.joint_names != context['skeleton'].joint_names or not loaded.clips:
        raise ValueError('Saved repair does not match the input skeleton.')
    clip = loaded.clips[0]
    clip.metadata['rig_limiter'] = record['report']
    model._rig_limiter_cache = (key, (clip, record['report']), (context['skeleton'], context['clip']))
    model._rig_limiter_disk_record = record


class _RepairStatus(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.pending = True
        self.summary = 'Press Repair to calculate and save the animation. Preview opens rig settings.'


def repair_status(model):
    status = getattr(model, '_rig_limiter_status', None)
    if status is None:
        status = model._rig_limiter_status = _RepairStatus()
    return status


def refresh_repair_status(model, key=None):
    """Update the indicator without evaluating the graph or repairing a clip."""
    if key is None:
        limits, volumes, collisions = settings(model)
        options = repair_key(None, None, limits, volumes, collisions, hand_settings(model),
                             ik_repair=ik_repair_enabled(model))[2:]
        key = getattr(model, '_rig_limiter_source_key', (None, None)) + options
    cached = getattr(model, '_rig_limiter_cache', None)
    same_source = cached is not None and cached[0][:2] == key[:2]
    pending = cached is None or cached[0] != key
    source = getattr(model, '_rig_limiter_disk_source', None)
    if source is not None and repair_files.find_repair(source, repair_files.options_signature(key[2:])) is None:
        pending = True
    lines = []
    if pending:
        lines.append('Repair needed. Press Repair.')
        lines.append('Showing previous repair.' if same_source else 'Showing input animation until repaired.')
    if same_source and not pending:
        report = cached[1][1]
        lines.extend((f"{report['corrected_frames']} / {report['frame_count']} frames corrected",
                      f"{report['finger_collider_count']} finger capsules; {report['arm_ik_corrected_frames']} frames adjusted by arm IK",
                      f"{report['remaining_proxy_overlap_frames']} frames with remaining capsule overlap"))
    summary = '\n'.join(lines)
    model._rig_limiter_summary = summary
    status = repair_status(model)
    if (pending, summary) != (status.pending, status.summary):
        status.pending, status.summary = pending, summary
        status.changed.emit()
    return status


def input_context(item, errors, warnings, *, _depth=0, _visited=None):
    from nodes.anim_retarget import spec as retarget
    scene = item.scene()
    item = getattr(scene,'_node_items',{}).get(item.model.name,item)
    visited = set(_visited or ())
    if id(item) in visited or _depth > 32:
        errors.append('Rig Limiter: input chain contains a cycle or is too deep.')
        return None
    visited.add(id(item))
    upstream,issue = retarget._connected_item_for_port(scene,item,'animation')
    if issue:
        warnings.append(issue)
    if upstream is None:
        errors.append('Rig Limiter: connect Mocap Collection or Mocap Import to animation.')
        return None
    context = retarget._context_for_source_item(upstream,upstream.model.kind.lower(),errors,warnings,
                                               _depth=_depth+1,_visited=visited)
    if context is None and not errors:
        errors.append('Rig Limiter: unsupported animation input.')
    return context


class _RepairJob(QtCore.QThread):
    progress = QtCore.Signal(int, int)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function, self.result, self.error = function, None, None

    def run(self):
        from time import monotonic
        last_update = 0.
        def progress(frame, total):
            nonlocal last_update
            if self.isInterruptionRequested():
                raise ValueError('Rig Limiter cancelled.')
            now = monotonic()
            if now-last_update >= .05:
                self.progress.emit(frame,total)
                last_update = now
        try:
            self.result = self.function(progress)
            if self.isInterruptionRequested():
                self.result = None
                raise ValueError('Rig Limiter cancelled.')
        except Exception as exc:
            self.error = exc


class _RepairProgressDialog(QtWidgets.QDialog):
    stop_requested = QtCore.Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.label = QtWidgets.QLabel('Correcting joint limits and capsule intersections…',self)
        self.label.setWordWrap(True)
        self.bar = QtWidgets.QProgressBar(self)
        self.stop = QtWidgets.QPushButton('Stop',self)
        self.stop.setObjectName('RigLimiterStopButton')
        self.stop.setStyleSheet('QPushButton { background:#b91c1c; color:white; border:1px solid #ef4444;'
                               ' border-radius:4px; padding:6px 18px; }'
                               'QPushButton:hover { background:#dc2626; }'
                               'QPushButton:disabled { background:#7f1d1d; color:#fecaca; }')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16,16,16,16)
        layout.setSpacing(12)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.stop,0,QtCore.Qt.AlignRight)
        self.stop.clicked.connect(self.stop_requested)
        self.setMinimumWidth(380)

    def update_progress(self,frame,total):
        self.bar.setMaximum(total)
        self.bar.setValue(frame)

    def reject(self):
        # Esc and the title-bar close button request cancellation without hiding
        # the dialog while its worker is still unwinding.
        self.stop_requested.emit()

    def closeEvent(self, event):
        self.stop_requested.emit()
        event.ignore()


def run_repair(function, parent=None):
    if parent is not None and isinstance(parent,QtWidgets.QWidget):
        proxy = parent.graphicsProxyWidget()
        if proxy is not None and proxy.scene() is not None and proxy.scene().views():
            parent = proxy.scene().views()[0].window()
        else:
            parent = parent.window()
    parent = parent or QtWidgets.QApplication.activeWindow()
    dialog = _RepairProgressDialog(parent)
    dialog.setWindowTitle('Rig Limiter')
    # Native window ownership keeps the modal dialog above this app without
    # placing it in Windows' global topmost band over unrelated applications.
    dialog.setWindowModality(QtCore.Qt.ApplicationModal)
    job = _RepairJob(function,dialog)
    job.progress.connect(dialog.update_progress)
    def request_stop():
        job.requestInterruption()
        dialog.stop.setEnabled(False)
        dialog.stop.setText('Stopping…')
        dialog.label.setText('Stopping repair…')
    dialog.stop_requested.connect(request_stop)
    job.finished.connect(dialog.accept)
    job.start()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    dialog.exec()
    if job.isRunning():
        job.requestInterruption()
        loop = QtCore.QEventLoop()
        job.finished.connect(loop.quit)
        if job.isRunning():
            loop.exec()
    job.wait()
    dialog.deleteLater()
    if job.error:
        raise job.error
    return job.result


def resolve_context(item, errors, warnings, *, target=False, _depth=0, _visited=None):
    model = item.model
    if getattr(model,'_rig_limiter_resolving',False):
        errors.append('Rig Limiter: input chain contains a cycle or a repair is already running.')
        return None
    model._rig_limiter_resolving = True
    try:
        context = input_context(item,errors,warnings,_depth=_depth,_visited=_visited)
        if context is None or errors:
            model._rig_limiter_source_key = (None, None)
            refresh_repair_status(model)
            return None
        limits,volumes,collisions = settings(model)
        ik_repair = ik_repair_enabled(model)
        hands = hand_settings(model)
        skeleton,source = context['skeleton'],context.get('clip')
        if source is None:
            raise ValueError('Connect an animated skeleton to Rig Limiter.')
        key = repair_key(skeleton,source,limits,volumes,collisions,hands,ik_repair=ik_repair)
        model._rig_limiter_source_key = key[:2]
        restore_repair(model, context, key)
        cached = getattr(model,'_rig_limiter_cache',None)
        # Graph refreshes and workflow saves must never launch a repair. Keep
        # the last result for this input while settings are pending; a new
        # input passes through until the user explicitly repairs it.
        clip = cached[1][0] if cached is not None and cached[0][:2] == key[:2] else source
        refresh_repair_status(model, key)
        # Do not carry source viewport overrides/capture caches into derived output.
        rig = dict(skeleton=skeleton,clip=clip,clips=[clip],meshes=[],loop=False,
                   source_format='bvh',show_capture_joints=False,show_animated_joints=True,
                   mesh_skinning_enabled=False)
        output = dict(context,name=model.name,kind='rig_limiter',model=model,node_item=item,
                      clip=clip,clips=[clip],meshes=[],rig_context=rig,bind_result=None,animation_result=None)
        output.pop('preview_asset',None)
        model._rig_limiter_error = ''
        return output
    except (ValueError,TypeError,KeyError,RuntimeError,OSError) as exc:
        model._rig_limiter_error = str(exc)
        errors.append('Rig Limiter: '+str(exc))
        return None
    finally:
        model._rig_limiter_resolving = False


def build_scene_asset(item, *, preview=False):
    from nodes.anim_retarget import spec as retarget
    errors,warnings = [],[]
    context = resolve_context(item,errors,warnings)
    if context is None:
        item.model._rig_limiter_error = '\n'.join(errors)
        return None
    asset = retarget._preview_asset_for_context(context,owner=item.model.name,role='source',x_offset=0.,curve_thickness=2.)
    if asset is None:
        return None
    rig = context['rig_context'].copy()
    if preview:
        from echograph.motion.rig_preview import build_guide
        indices,names,parents,offsets = full_rig_layout(context['skeleton'])
        limits,volumes,collisions = settings(item.model)
        limits = expanded_limits(names,limits,hand_settings(item.model))
        _,guide = build_guide(offsets,joint_limits=limits,volumes=volumes,names=names,parents=parents,
                              end_sites=context['skeleton'].metadata.get('end_sites'))
        guide.update(profile='input animation',title='Rig Limiter',joint_indices=indices,
                     collisions=collisions,limits=True,save_settings=lambda guide:store_settings(item,guide))
        rig['priormdm_rig_debug'] = guide
    asset.update(node=item.model.name,kind='rig_limiter',source_kind='rig_limiter',fbx_rig_context=rig,wire_only=True)
    return asset


def prepare_repair(item, parent=None):
    errors,warnings = [],[]
    context = input_context(item,errors,warnings)
    if context is None or errors:
        raise ValueError('\n'.join(errors))
    limits,volumes,collisions = settings(item.model)
    ik_repair = ik_repair_enabled(item.model)
    hands = hand_settings(item.model)
    skeleton,clip = context['skeleton'],context.get('clip')
    key = repair_key(skeleton,clip,limits,volumes,collisions,hands,ik_repair=ik_repair)
    item.model._rig_limiter_source_key = key[:2]
    restore_repair(item.model, context, key)
    refresh_repair_status(item.model, key)
    if repair_status(item.model).pending:
        source = getattr(item.model, '_rig_limiter_disk_source', None)
        source_hash = repair_files.file_signature(source) if source is not None else None
        result = run_repair(lambda progress:limit_clip(skeleton,clip,limits,volumes,
                                                      collisions=collisions,ik_repair=ik_repair,
                                                      progress=progress,hand_limits=hands),parent)
        if source is not None:
            if repair_files.file_signature(source) != source_hash:
                raise ValueError('The source BVH changed during repair. Reload it and retry.')
            record = repair_files.save_repair(source, repair_files.options_signature(key[2:]), result[1],
                lambda path: export_context(dict(skeleton=skeleton, clip=result[0]), path))
            item.model._rig_limiter_disk_record = record
        item.model._rig_limiter_cache = (key,result,(skeleton,clip))
    refresh_repair_status(item.model, key)
    if context.get('kind') == 'mocap_collection':
        from nodes.mocap_collection.spec import refresh_repair_records
        refresh_repair_records(context['model'], item.scene())


def export_context(context, path):
    skeleton,clip = context['skeleton'],context['clip']
    count = max(1,int(round(clip.duration*clip.sample_rate_hz))+1)
    local = [evaluate_rig_at_time(skeleton,clip,float(t)).local_transforms
             for t in np.linspace(clip.start_time,clip.end_time,count)]
    positions = np.array([[j.translation for j in pose] for pose in local])
    offsets = np.array([j.local_bind.translation for j in skeleton.joints])
    rotations = Rotation.from_quat(np.array([[j.rotation for j in pose] for pose in local]).reshape(-1,4)).as_matrix()
    rotations = rotations.reshape(count,len(skeleton.joints),3,3)
    if not np.allclose(positions[:,1:],offsets[None,1:],atol=1e-6,rtol=1e-5):
        raise ValueError('BVH export requires fixed offsets on every non-root joint.')
    if not np.allclose([[j.scale for j in pose] for pose in local],1.,atol=1e-6):
        raise ValueError('BVH export cannot store animated bone scale.')
    # BVH root channels are relative to its OFFSET.
    positions[:,0] -= offsets[0]
    write_bvh(Path(path),positions,clip.sample_rate_hz,rotations,offsets,
              names=skeleton.joint_names,parents=[j.parent_index for j in skeleton.joints],
              end_sites=skeleton.metadata.get('end_sites'))


def build_ports(item):
    for name,default in (('animation',''),('rig_settings','{}'),('collisions','1'),('ik_repair','1')):
        mocap._ensure_param(item,name,default)
    mocap._ensure_hidden_params(item.model,['rig_settings','collisions','ik_repair'])
    item.model._named_inputs = ['animation']
    item._default_named_input = 'animation'
    item._show_default_input_with_named = False
    item.ensure_input('animation')
    item.ensure_output('animation')
    item._hide_default_output_with_named = True


class LimiterControls(QtWidgets.QWidget):
    def __init__(self,item):
        super().__init__()
        self.item = item
        layout = QtWidgets.QVBoxLayout(self)
        self.collisions = QtWidgets.QCheckBox('Correct capsule intersections')
        self.collisions.setChecked(settings(item.model)[2])
        self.collisions.toggled.connect(self.set_collisions)
        layout.addWidget(self.collisions)
        self.ik_repair = QtWidgets.QCheckBox('IK repair')
        self.ik_repair.setToolTip('Use IK to bend fingers and reposition limbs when correcting capsule intersections. '
                                 'Turn off to apply bend and twist limits only.')
        self.ik_repair.setChecked(ik_repair_enabled(item.model))
        self.ik_repair.toggled.connect(self.set_ik_repair)
        layout.addWidget(self.ik_repair)
        for label,callback in (('Preview',self.preview),('Repair',self.repair),('Export corrected BVH…',self.export),
                               ('Export checked collection…',self.export_collection),('Reset limits and volumes',self.reset)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
            if label == 'Preview':
                self.preview_button = button
                button.setObjectName('RigLimiterPreviewButton')
                button.setToolTip('Preview the animation and edit rig limits and colliders without running repair.')
            if label == 'Repair':
                self.repair_button = button
                button.setObjectName('RigLimiterRepairButton')
                button.setMinimumHeight(max(28, button.sizeHint().height()))
                button.setStyleSheet('QPushButton[repairPending="true"] { background-color:#9f2929; '
                                    'color:white; border:1px solid #f87171; border-radius:3px; }'
                                    'QPushButton[repairPending="true"]:hover { background-color:#b73535; }')
        self.status = QtWidgets.QLabel('Connect an existing PriorMDM animation.')
        self.status.setWordWrap(True)
        self.status.setFixedHeight(self.status.fontMetrics().lineSpacing()*6)
        layout.addWidget(self.status)
        scene = item.scene() or getattr(item.model,'_graph_scene',None)
        if scene is not None and hasattr(scene,'paramChanged'):
            scene.paramChanged.connect(self.refresh)
        repair_status(item.model).changed.connect(self._refresh_repair_button)
        self.refresh()
        # Workflow loading creates node widgets before reconnecting their edges.
        QtCore.QTimer.singleShot(0, self.refresh)

    def _refresh_repair_button(self):
        status = repair_status(self.item.model)
        button = self.repair_button
        if button.property('repairPending') != status.pending:
            button.setProperty('repairPending', status.pending)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        button.setToolTip('Create a corrected BVH beside the source using the saved rig settings.'
                          if status.pending else 'A repair for the current source and saved settings is available.')
        self.status.setText(status.summary)

    def refresh(self,*args):
        if getattr(self, '_refreshing', False):
            return
        self._refreshing = True
        try:
            resolve_context(self.item, [], [])
        finally:
            self._refreshing = False
        blocker = QtCore.QSignalBlocker(self.collisions)
        self.collisions.setChecked(settings(self.item.model)[2])
        del blocker
        blocker = QtCore.QSignalBlocker(self.ik_repair)
        self.ik_repair.setChecked(ik_repair_enabled(self.item.model))
        del blocker
        refresh_repair_status(self.item.model)
        self._refresh_repair_button()

    def set_collisions(self,enabled):
        mocap._set_param_value(self.item.model,'collisions','1' if enabled else '0')
        self.changed()

    def set_ik_repair(self,enabled):
        mocap._set_param_value(self.item.model,'ik_repair','1' if enabled else '0')
        self.changed()

    def changed(self):
        refresh_repair_status(self.item.model)
        scene = self.item.scene()
        if scene is not None:
            scene.set_node_params(self.item.model.name,list(self.item.model.params),rebuild=False,emit=True)

    def reset(self):
        from echograph.motion.rig_preview_settings import default_preview_limits
        store_settings(self.item,dict(joint_limits=default_preview_limits(),volumes=volume_settings(),collisions=self.collisions.isChecked()))
        self.status.setText('Defaults restored. Press Repair to apply.')

    def repair(self):
        try:
            prepare_repair(self.item, self.window())
            self.changed()
        except Exception as exc:
            self.status.setText(str(exc))

    def preview(self):
        try:
            asset = build_scene_asset(self.item,preview=True)
            if asset is None:
                raise ValueError(getattr(self.item.model,'_rig_limiter_error','Could not build the preview.'))
            views = self.item.scene().views()
            window = next((v.window() for v in views if callable(getattr(v.window(),'open_scene_assets',None))),None)
            if window is None or window.open_scene_assets([asset],frame=True) is False:
                raise ValueError('Open Rig Limiter from a graph with a 3D viewport.')
            view = getattr(window,'gl_view',None)
            renderer = getattr(view,'_mgl_renderer',None) or view
            request = getattr(renderer,'_mgl_scene_skeleton_request_active',None)
            if callable(request):
                request(self.item.model.name)
            self.status.setText(self.item.model._rig_limiter_summary)
        except Exception as exc:
            self.status.setText(str(exc))

    def export(self):
        try:
            errors,warnings = [],[]
            context = resolve_context(self.item,errors,warnings)
            if context is None:
                raise ValueError('\n'.join(errors))
            if repair_status(self.item.model).pending:
                raise ValueError('Press Repair before exporting the changed animation.')
            directory = QtWidgets.QFileDialog.getExistingDirectory(self,'Save corrected BVH in folder')
            if not directory:
                return
            path = Path(directory)/(Path(context['path']).stem+'_limited_'+uuid4().hex[:8]+'.bvh')
            export_context(context,path)
            self.status.setText('Saved '+str(path))
        except Exception as exc:
            self.status.setText(str(exc))

    def export_collection(self):
        from nodes.anim_retarget import spec as retarget
        from nodes.mocap_collection.spec import checked_paths
        from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
        upstream,_ = retarget._connected_item_for_port(self.item.scene(),self.item,'animation')
        if upstream is None or upstream.model.kind != 'mocap_collection':
            self.status.setText('Connect Mocap Collection directly to export its checked animations.')
            return
        paths = checked_paths(upstream.model)
        if not paths:
            self.status.setText('Check at least one animation in Mocap Collection.')
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self,'Save corrected collection in folder')
        if not directory:
            return
        limits,volumes,collisions = settings(self.item.model)
        ik_repair = ik_repair_enabled(self.item.model)
        hands = hand_settings(self.item.model)
        base = mocap._workflow_dir_for_node(upstream)
        saved = []
        def repair(progress):
            reports = []
            for number,raw in enumerate(paths):
                path = mocap._resolve_existing_path(raw,base)
                if path is None:
                    raise ValueError('Missing animation: '+raw)
                loaded = ingest_bvh_animation_data(path)
                clip,report = limit_clip(loaded.skeleton,loaded.clips[0],limits,volumes,collisions=collisions,hand_limits=hands,
                    ik_repair=ik_repair,
                    progress=lambda f,n:progress(number*100+int(f/max(1,n)*100),len(paths)*100))
                output = Path(directory)/(path.stem+'_limited_'+uuid4().hex[:8]+'.bvh')
                export_context(dict(skeleton=loaded.skeleton,clip=clip),output)
                saved.append(output)
                reports.append(report)
            return reports
        try:
            reports = run_repair(repair,self.window())
            remaining = sum(r['remaining_proxy_overlap_frames'] for r in reports)
            self.status.setText(f'Saved {len(saved)} corrected BVHs. {remaining} frames retain capsule overlap.\n{directory}')
        except Exception as exc:
            self.status.setText(f'{exc}\n{len(saved)} completed files saved in {directory}.')


def render_node_body(item,y_cursor):
    widget = LimiterControls(item)
    widget.ensurePolished()
    item.prepareGeometryChange()
    item.width = max(item.width,widget.minimumSizeHint().width()+16,260)
    widget.setFixedWidth(int(item.width-16))
    height = max(widget.sizeHint().height(),widget.minimumSizeHint().height())
    widget.setFixedHeight(height)
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(widget)
    proxy.setPos(8,y_cursor)
    proxy.resize(item.width-16,height)
    item._plugin_proxies.append(proxy)
    item.height = max(item.height,y_cursor+height+10)
    return item.height


def augment_infocard_footer(card,layout):
    model = getattr(card,'_node_ref',None)
    if model is None or model.kind != 'rig_limiter':
        return False
    scene = getattr(card,'_graph_scene',None)
    layout.addWidget(LimiterControls(SimpleNamespace(model=model,scene=lambda:scene)))
    return True


RIG_LIMITER_SPEC = Spec(stripe_color='#22d3ee',build_ports=build_ports,render_node_body=render_node_body,
                        augment_infocard_footer=augment_infocard_footer)
