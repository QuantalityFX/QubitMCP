"""Repair an existing collection output; source files are never overwritten."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from nodes.core import Spec
from nodes.mocap_import import spec as mocap
from echograph.motion.conversion import JOINT_NAMES, write_bvh_frames
from echograph.motion.rig_limiter import limit_clip, REPAIR_VERSION
from echograph.motion import rig_repair_cache as repair_files
from echograph.motion.rig_limits import preview_limit_fields, preview_limit_from_fields
from echograph.motion.rig_preview_settings import load_preview_limits, load_preview_volumes, load_preview_hand_limits
from echograph.motion.hand_rig import full_rig_layout, expanded_limits, hand_limits_from_fields
from echograph.motion.rig_volumes import volume_settings
from echograph.rigging.fbx_stage5_evaluator import evaluation_cache_token, evaluate_rig_at_time

_repair_active = False
_fault_log = None


def _enable_repair_fault_log():
    """Retain native fault traces, which cannot be caught as Python errors."""
    import faulthandler
    global _fault_log
    if not faulthandler.is_enabled():
        try:
            path = Path(__file__).resolve().parents[2]/'logs'/'rig_limiter_native_fault.log'
            path.parent.mkdir(parents=True,exist_ok=True)
            _fault_log = path.open('a',encoding='utf-8')
            faulthandler.enable(_fault_log,all_threads=True)
        except OSError:
            if _fault_log is not None:
                _fault_log.close()
                _fault_log = None


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


def thorough_repair_enabled(model):
    return mocap._param_value(model,'thorough_repair') == '1'


def repair_key(skeleton,clip,limits,volumes,collisions,hands,*,ik_repair=True,thorough_repair=False):
    return (evaluation_cache_token(skeleton),evaluation_cache_token(clip),
            json.dumps(limits,sort_keys=True),json.dumps(volumes,sort_keys=True),collisions,json.dumps(hands,sort_keys=True),
            ik_repair,thorough_repair,REPAIR_VERSION)


def repair_option_signatures(options):
    """Keep verified older BVHs usable when their actual settings still match."""
    signatures = [repair_files.options_signature(options)]
    # Versions 2 and 3 used these same user settings, before Thorough repair
    # was introduced. They cannot satisfy a request for Thorough repair.
    if not options[-2]:
        signatures.extend(repair_files.options_signature(tuple(options[:-2])+(version,))
                          for version in (3,2))
    return tuple(signatures)


def restore_repair(model, context, key):
    """Restore a saved result without ever running the repair algorithm."""
    path = Path(context.get('path') or '')
    source = (path if context.get('kind') in ('mocap_collection', 'mocap_import', 'bvh_import')
              and path.is_file() and path.suffix.lower() == '.bvh' else None)
    model._rig_limiter_disk_source = source
    if source is None:
        return
    record = repair_files.find_repair(source, repair_option_signatures(key[2:]))
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
                             ik_repair=ik_repair_enabled(model),thorough_repair=thorough_repair_enabled(model))[2:]
        key = getattr(model, '_rig_limiter_source_key', (None, None)) + options
    cached = getattr(model, '_rig_limiter_cache', None)
    same_source = cached is not None and cached[0][:2] == key[:2]
    pending = cached is None or cached[0] != key
    source = getattr(model, '_rig_limiter_disk_source', None)
    record = repair_files.find_repair(source, repair_option_signatures(key[2:])) if source is not None else None
    if source is not None and record is None:
        pending = True
    report = cached[1][1] if same_source and not pending else None
    earlier = record is not None and record['options_sha'] != repair_files.options_signature(key[2:])
    return _publish_repair_status(model,pending,report,same_source=same_source,saved_earlier=earlier)


def _publish_repair_status(model,pending,report=None,*,same_source=False,saved_earlier=False):
    lines = []
    if pending:
        lines.append('Repair needed. Press Repair.')
        lines.append('Showing previous repair.' if same_source else 'Showing input animation until repaired.')
    if report is not None and not pending:
        lines.extend((f"{report['corrected_frames']} / {report['frame_count']} source frames corrected",
                      f"{report['finger_collider_count']} finger capsules; {report['arm_ik_corrected_frames']} frames adjusted by arm IK",
                      f"{report.get('output_remaining_proxy_overlap_frames',report['remaining_proxy_overlap_frames'])} output samples with remaining capsule overlap"))
        if report.get('repair_substeps',1) > 1:
            lines.append(f"{report['repair_substeps']} repair substeps; output baked at {report['sample_rate_hz']:g} fps")
            lines.append(f"{report.get('interpolated_samples_repaired',0)} between-frame samples repaired")
        if report.get('remaining_interpolated_contact_samples',0):
            lines.append(f"{report['remaining_interpolated_contact_samples']} checks still show added between-frame overlap")
        lines.append('Thorough repair' if report.get('thorough_repair') else 'Fast repair')
        if saved_earlier:
            lines.append('Using saved repair from an earlier version.')
    summary = '\n'.join(lines)
    model._rig_limiter_summary = summary
    status = repair_status(model)
    if (pending, summary) != (status.pending, status.summary):
        status.pending, status.summary = pending, summary
        status.changed.emit()
    return status


def _status_input(item):
    """Follow For Each pass-throughs without evaluating their animations."""
    from nodes.anim_retarget import spec as retarget
    scene = item.scene() or getattr(item.model,'_graph_scene',None)
    port,seen,watched = 'animation',{id(item)},set()
    for _ in range(32):
        upstream,_ = retarget._connected_item_for_port(scene,item,port)
        if upstream is None or id(upstream) in seen:
            return None,watched
        seen.add(id(upstream))
        watched.add(upstream.model.name)
        if upstream.model.kind.lower() != 'for_each':
            return upstream,watched
        item,port = upstream,'source'
    return None,watched


def _status_source(item):
    """Read the selected BVH path without evaluating/loading animation."""
    upstream,_ = _status_input(item)
    if upstream is None:
        return True,'',None
    kind = upstream.model.kind.lower()
    if kind == 'mocap_collection':
        from nodes.mocap_collection.spec import entries,output_number
        rows,number = entries(upstream.model),output_number(upstream.model)
        raw = rows[number-1]['path'] if number else ''
    elif kind in ('mocap_import','bvh_import'):
        raw = mocap._param_value(upstream.model,'path')
    else:
        return False,'',None
    return True,raw,mocap._workflow_dir_for_node(item)


class _StatusLookupSignals(QtCore.QObject):
    finished = QtCore.Signal(object,object)


class _StatusLookup(QtCore.QRunnable):
    def __init__(self,request):
        super().__init__()
        self.request = request
        self.signals = _StatusLookupSignals()

    def run(self):
        raw,base,options = self.request
        record = None
        try:
            path = mocap._resolve_existing_path(raw,base)
            if path is not None and path.suffix.lower() == '.bvh':
                record = repair_files.find_repair(path,options)
        except (OSError,ValueError,TypeError):
            pass
        self.signals.finished.emit(self,record)


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
    stage = QtCore.Signal(str)

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
        progress.set_stage = self.stage.emit
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

    @QtCore.Slot(int,int)
    def update_progress(self,frame,total):
        self.bar.setMaximum(total)
        self.bar.setValue(frame)

    @QtCore.Slot(str)
    def update_stage(self,label):
        if self.stop.isEnabled():
            self.label.setText(label)

    def reject(self):
        # Esc and the title-bar close button request cancellation without hiding
        # the dialog while its worker is still unwinding.
        self.stop_requested.emit()

    def closeEvent(self, event):
        self.stop_requested.emit()
        event.ignore()


def run_repair(function, parent=None):
    global _repair_active
    if _repair_active:
        raise ValueError('A Rig Limiter repair is already running.')
    _repair_active = True
    try:
        _enable_repair_fault_log()
        return _run_repair_dialog(function,parent)
    finally:
        _repair_active = False


def _run_repair_dialog(function,parent):
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
    job.progress.connect(dialog.update_progress,QtCore.Qt.QueuedConnection)
    job.stage.connect(dialog.update_stage,QtCore.Qt.QueuedConnection)
    def request_stop():
        job.requestInterruption()
        dialog.stop.setEnabled(False)
        dialog.stop.setText('Stopping…')
        dialog.label.setText('Stopping repair…')
    dialog.stop_requested.connect(request_stop)
    job.finished.connect(dialog.accept,QtCore.Qt.QueuedConnection)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    job.start()
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
        thorough_repair = thorough_repair_enabled(model)
        hands = hand_settings(model)
        skeleton,source = context['skeleton'],context.get('clip')
        if source is None:
            raise ValueError('Connect an animated skeleton to Rig Limiter.')
        key = repair_key(skeleton,source,limits,volumes,collisions,hands,ik_repair=ik_repair,thorough_repair=thorough_repair)
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
    thorough_repair = thorough_repair_enabled(item.model)
    hands = hand_settings(item.model)
    skeleton,clip = context['skeleton'],context.get('clip')
    key = repair_key(skeleton,clip,limits,volumes,collisions,hands,ik_repair=ik_repair,thorough_repair=thorough_repair)
    item.model._rig_limiter_source_key = key[:2]
    restore_repair(item.model, context, key)
    refresh_repair_status(item.model, key)
    if repair_status(item.model).pending:
        source = getattr(item.model, '_rig_limiter_disk_source', None)
        source_hash = repair_files.file_signature(source) if source is not None else None
        def repair(progress):
            def solving(f,n):
                progress(int(850*f/max(1,n)),1000)
            solving.set_stage = getattr(progress,'set_stage',lambda label:None)
            result = limit_clip(skeleton,clip,limits,volumes,collisions=collisions,ik_repair=ik_repair,
                                progress=solving,hand_limits=hands,thorough_repair=thorough_repair)
            record = None
            if source is not None:
                solving.set_stage('Saving corrected BVH...')
                if repair_files.file_signature(source) != source_hash:
                    raise ValueError('The source BVH changed during repair. Reload it and retry.')
                record = repair_files.save_repair(source,repair_files.options_signature(key[2:]),result[1],
                    lambda path:export_context(dict(skeleton=skeleton,clip=result[0]),path,
                        progress=lambda f,n:progress(850+int(150*f/max(1,n)),1000)))
            return result,record
        result,record = run_repair(repair,parent)
        if record is not None:
            item.model._rig_limiter_disk_record = record
        item.model._rig_limiter_cache = (key,result,(skeleton,clip))
    refresh_repair_status(item.model, key)
    if context.get('kind') == 'mocap_collection':
        from nodes.mocap_collection.spec import refresh_repair_records
        refresh_repair_records(context['model'], item.scene())


def export_context(context, path, *, progress=None):
    skeleton,clip = context['skeleton'],context['clip']
    count = max(1,int(round(clip.duration*clip.sample_rate_hz))+1)
    offsets = np.array([j.local_bind.translation for j in skeleton.joints])
    def frames():
        for frame in range(count):
            if progress is not None:
                progress(frame,count)
            time = clip.start_time+clip.duration*frame/max(1,count-1)
            local = evaluate_rig_at_time(skeleton,clip,time).local_transforms
            positions = np.array([j.translation for j in local])
            if not np.allclose(positions[1:],offsets[1:],atol=1e-6,rtol=1e-5):
                raise ValueError('BVH export requires fixed offsets on every non-root joint.')
            if not np.allclose([j.scale for j in local],1.,atol=1e-6):
                raise ValueError('BVH export cannot store animated bone scale.')
            # BVH root channels are relative to its OFFSET.
            yield positions[0]-offsets[0],Rotation.from_quat([j.rotation for j in local]).as_matrix()
    write_bvh_frames(Path(path),frames(),count,clip.sample_rate_hz,offsets,
                     names=skeleton.joint_names,parents=[j.parent_index for j in skeleton.joints],
                     end_sites=skeleton.metadata.get('end_sites'),progress=progress)


def build_ports(item):
    for name,default in (('animation',''),('rig_settings','{}'),('collisions','1'),('ik_repair','1'),('thorough_repair','0')):
        mocap._ensure_param(item,name,default)
    mocap._ensure_hidden_params(item.model,['rig_settings','collisions','ik_repair','thorough_repair'])
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
        self._status_job = None
        self._status_request = None
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(40)
        self._status_timer.timeout.connect(self._start_status_lookup)
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
        self.thorough = QtWidgets.QCheckBox('Thorough repair (slower)')
        self.thorough.setToolTip('Spend more time resolving between-frame contacts. '
                                 'Leave off for fast repairs. Unresolved contacts are reported in either mode.')
        self.thorough.setChecked(thorough_repair_enabled(item.model))
        self.thorough.toggled.connect(self.set_thorough_repair)
        layout.addWidget(self.thorough)
        action_buttons = []
        for label,callback in (('Preview',self.preview),('Repair',self.repair),('Export corrected BVH…',self.export),
                               ('Export checked collection…',self.export_collection),('Reset limits and volumes',self.reset)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
            action_buttons.append(button)
            if label == 'Preview':
                self.preview_button = button
                button.setObjectName('RigLimiterPreviewButton')
                button.setStyleSheet(
                    "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
                    "QPushButton:hover{background:#1d4ed8;}"
                    "QPushButton:disabled{background:#334155;color:#94a3b8;}"
                )
                button.setToolTip('Preview the animation and edit rig limits and colliders without running repair.')
            if label == 'Repair':
                self.repair_button = button
                button.setObjectName('RigLimiterRepairButton')
                button.setStyleSheet('QPushButton[repairPending="true"] { background-color:#9f2929; '
                                    'color:white; border:1px solid #f87171; border-radius:3px; }'
                                    'QPushButton[repairPending="true"]:hover { background-color:#b73535; }')
        button_height = action_buttons[-1].sizeHint().height()
        for button in action_buttons:
            button.setFixedHeight(button_height)
        self.status = QtWidgets.QLabel('Connect an existing PriorMDM animation.')
        self.status.setWordWrap(True)
        self.status.setFixedHeight(self.status.fontMetrics().lineSpacing()*10)
        layout.addWidget(self.status)
        scene = item.scene() or getattr(item.model,'_graph_scene',None)
        if scene is not None and hasattr(scene,'paramChanged'):
            scene.paramChanged.connect(self._params_changed)
        if scene is not None and hasattr(scene,'linksChanged'):
            scene.linksChanged.connect(self.refresh)
        repair_status(item.model).changed.connect(self._refresh_repair_button)
        self.refresh()
        # Workflow loading creates node widgets before reconnecting their edges.
        QtCore.QTimer.singleShot(0,self,self.refresh)

    @QtCore.Slot()
    def _refresh_repair_button(self):
        if not isValid(self) or not isValid(self.repair_button) or not isValid(self.status):
            return
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

    @QtCore.Slot(str,list)
    def _params_changed(self,name,_params):
        if name != self.item.model.name:
            upstream,watched = _status_input(self.item)
            if upstream is None or (upstream.model.kind.lower() in ('mocap_collection','mocap_import','bvh_import')
                                    and name not in watched):
                return
        self.refresh()

    @QtCore.Slot()
    def refresh(self,*args):
        if not isValid(self) or getattr(self, '_refreshing', False):
            return
        self._refreshing = True
        try:
            direct,raw,base = _status_source(self.item)
            if direct:
                limits,volumes,collisions = settings(self.item.model)
                options = repair_key(None,None,limits,volumes,collisions,hand_settings(self.item.model),
                    ik_repair=ik_repair_enabled(self.item.model),
                    thorough_repair=thorough_repair_enabled(self.item.model))[2:]
                # Replacing this tuple also invalidates an in-flight lookup,
                # including when a repair was just saved for the same path.
                self._status_request = (raw,base,repair_option_signatures(options))
                self._status_timer.start()
            else:
                self._status_request = None
                self._status_timer.stop()
                resolve_context(self.item, [], [])
        finally:
            self._refreshing = False
        if not isValid(self) or not isValid(self.collisions) or not isValid(self.ik_repair) or not isValid(self.thorough):
            return
        blocker = QtCore.QSignalBlocker(self.collisions)
        self.collisions.setChecked(settings(self.item.model)[2])
        del blocker
        blocker = QtCore.QSignalBlocker(self.ik_repair)
        self.ik_repair.setChecked(ik_repair_enabled(self.item.model))
        del blocker
        blocker = QtCore.QSignalBlocker(self.thorough)
        self.thorough.setChecked(thorough_repair_enabled(self.item.model))
        del blocker
        if not direct:
            refresh_repair_status(self.item.model)
        self._refresh_repair_button()

    @QtCore.Slot()
    def _start_status_lookup(self):
        if self._status_job is not None or self._status_request is None:
            return
        job = self._status_job = _StatusLookup(self._status_request)
        job.signals.finished.connect(self._status_lookup_finished)
        QtCore.QThreadPool.globalInstance().start(job)

    @QtCore.Slot(object,object)
    def _status_lookup_finished(self,job,record):
        if not isValid(self):
            return
        self._status_job = None
        if job.request is self._status_request:
            earlier = record is not None and record['options_sha'] != job.request[2][0]
            _publish_repair_status(self.item.model,record is None,record['report'] if record else None,
                                   saved_earlier=earlier)
        elif self._status_request is not None:
            self._status_timer.start()

    def set_collisions(self,enabled):
        mocap._set_param_value(self.item.model,'collisions','1' if enabled else '0')
        self.changed()

    def set_ik_repair(self,enabled):
        mocap._set_param_value(self.item.model,'ik_repair','1' if enabled else '0')
        self.changed()

    def set_thorough_repair(self,enabled):
        mocap._set_param_value(self.item.model,'thorough_repair','1' if enabled else '0')
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
            # Preview resolves the saved result itself. A lookup started before
            # that must not overwrite its authoritative status afterward.
            self._status_request = None
            self._status_timer.stop()
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
        thorough_repair = thorough_repair_enabled(self.item.model)
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
                def solving(f,n):
                    progress(number*100+int(f/max(1,n)*85),len(paths)*100)
                solving.set_stage = getattr(progress,'set_stage',lambda label:None)
                clip,report = limit_clip(loaded.skeleton,loaded.clips[0],limits,volumes,collisions=collisions,hand_limits=hands,
                    ik_repair=ik_repair,progress=solving,thorough_repair=thorough_repair)
                output = Path(directory)/(path.stem+'_limited_'+uuid4().hex[:8]+'.bvh')
                stage = getattr(progress,'set_stage',lambda label:None)
                stage('Saving corrected BVH...')
                export_context(dict(skeleton=loaded.skeleton,clip=clip),output,
                    progress=lambda f,n:progress(number*100+85+int(f/max(1,n)*15),len(paths)*100))
                saved.append(output)
                reports.append(report)
            return reports
        try:
            reports = run_repair(repair,self.window())
            remaining = sum(r.get('output_remaining_proxy_overlap_frames',r['remaining_proxy_overlap_frames']) for r in reports)
            between = sum(r.get('remaining_interpolated_contact_samples',0) for r in reports)
            self.status.setText(f'Saved {len(saved)} corrected BVHs. {remaining} output samples retain capsule overlap; '
                                f'{between} between-frame checks remain unresolved.\n{directory}')
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
