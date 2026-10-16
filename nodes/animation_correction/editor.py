"""Pose directly in the viewport; edit source keys in the existing timeline."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from echograph.motion import animation_corrections as corrections
from echograph.motion.rig_repair_cache import file_signature
from echograph.rigging.fbx_stage5_evaluator import override_rotation_keys
from . import spec


def ik_control_radius(joint_radius):
    return float(joint_radius)*(2.5/3.)


def preview_guide(context):
    """Use the same body/hand guides as Rig Limiter and PriorMDM previews."""
    from echograph.ui.scene_rig_overlay import collider_guide
    from echograph.motion.rig_preview import build_guide
    from echograph.motion.rig_preview_settings import load_preview_limits, load_preview_hand_limits
    from echograph.motion.hand_rig import expanded_limits
    guide = collider_guide(context)
    limits,hands = load_preview_limits(),load_preview_hand_limits()
    volumes = guide['volumes']
    if context.get('kind') == 'rig_limiter':
        from nodes.rig_limiter.spec import settings,hand_settings
        limits,volumes,_ = settings(context['model'])
        hands = hand_settings(context['model'])
    names = guide['joint_names']
    _,posed = build_guide(guide['offsets'],joint_limits=expanded_limits(names,limits,hands),
        volumes=volumes,names=names,parents=guide['parents'],end_sites=guide.get('end_sites'))
    indices = guide['joint_indices']
    posed.update(joint_indices=indices,collisions=True,limits=True,read_only=True,
        title='Animation Correction',profile='input animation',
        joint_aliases={name:context['skeleton'].joints[index].name for name,index in zip(names,indices)})
    return posed


def ik_control_basis(axes=None):
    """Displayed joint-local XYZ directions, stored as rows."""
    basis = np.asarray(axes,dtype=float) if axes is not None else np.eye(3)
    if basis.shape != (3,3) or not np.all(np.isfinite(basis)) or abs(np.linalg.det(basis)) < 1e-10:
        return np.eye(3)
    return basis


def cube_curves(center, radius, axes=None):
    """Twelve line curves aligned to the driving joint's displayed local axes."""
    corners = np.asarray([(x,y,z) for x in (-1,1) for y in (-1,1) for z in (-1,1)],dtype=float)
    corners = np.asarray(center)+(corners*radius)@ik_control_basis(axes)
    return np.asarray([corners[index] for a in range(8) for b in range(a+1,8)
                       if (a ^ b) in (1,2,4) for index in (a,b)],dtype=np.float32)


class CorrectionPanel(QtWidgets.QFrame):
    def __init__(self,session,viewport):
        super().__init__(viewport)
        self.session = session
        self.setObjectName('AnimationCorrectionPanel')
        self.setStyleSheet('QFrame#AnimationCorrectionPanel { background:#16212e; border:1px solid #475569; '
                           'border-radius:6px; } QLabel,QCheckBox { color:#e2e8f0; }')
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('Animation Correction — '+session.path.name)
        title.setWordWrap(True)
        layout.addWidget(title)
        self.ik = QtWidgets.QCheckBox('IK Controls')
        self.ik.setChecked(session.ik_enabled)
        self.ik.toggled.connect(session.set_ik)
        self.lock_limbs = QtWidgets.QCheckBox('Lock arm/leg IK')
        self.lock_limbs.setToolTip('Keep hands and feet in world space while moving or rotating the pelvis or chest. '
                                  'Turn off to let them follow the body. Targets beyond limb reach stop at full extension.')
        self.lock_limbs.setChecked(session.lock_limbs)
        self.lock_limbs.setEnabled(session.ik_enabled)
        self.lock_limbs.toggled.connect(session.set_lock_limbs)
        ik_row = QtWidgets.QHBoxLayout()
        ik_row.addWidget(self.ik)
        ik_row.addWidget(self.lock_limbs)
        layout.addLayout(ik_row)
        self.colliders = QtWidgets.QCheckBox('Show colliders')
        self.colliders.setToolTip('Display the same body and hand capsules as the Rig Limiter preview.')
        self.colliders.setChecked(session.show_colliders)
        self.colliders.setEnabled(session.guide is not None)
        if session.guide_error:
            self.colliders.setToolTip(session.guide_error)
        self.colliders.toggled.connect(session.set_colliders)
        self.limits = QtWidgets.QCheckBox('Show limits')
        self.limits.setToolTip('Display joint-limit cones, circles and direction markers.')
        self.limits.setChecked(session.show_limits)
        self.limits.setEnabled(session.guide is not None)
        if session.guide_error:
            self.limits.setToolTip(session.guide_error)
        self.limits.toggled.connect(session.set_limits)
        visibility = QtWidgets.QHBoxLayout()
        visibility.addWidget(self.colliders)
        visibility.addWidget(self.limits)
        layout.addLayout(visibility)
        note = QtWidgets.QLabel('Scrub the timeline. Click joints to rotate.\nDrag blue pole circles to steer elbow / knee / spine bends.\nPose IK, then press Start IK / End IK or Set key.\nSelect Px / Py / Pz to limit IK target axes.\nYellow bands connect paired IK keys.\nSettings > Joint names shows all names.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.ik_range = QtWidgets.QLabel()
        self.ik_range.setWordWrap(True)
        layout.addWidget(self.ik_range)
        row = QtWidgets.QHBoxLayout()
        self.ik_buttons = []
        for text,mode in (('Start IK','start'),('End IK','end')):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(lambda _checked=False,m=mode:session.key_ik(mode=m,save=True))
            row.addWidget(button)
            self.ik_buttons.append(button)
        layout.addLayout(row)
        self.reload = QtWidgets.QPushButton('Reload selected joint')
        icons = Path(__file__).resolve().parents[2]/'icons'
        refresh = icons/'Refresh_Icon_L.png'
        if not refresh.is_file():
            refresh = icons/'Refresh_Icon.png'
        self.reload.setIcon(QtGui.QIcon(str(refresh)))
        self.reload.setIconSize(QtCore.QSize(20,20))
        self.reload.setToolTip('Restore this joint’s input BVH / FBX keys. If IK drives it, remove that limb’s IK ranges too.')
        self.reload.clicked.connect(session.reload_joint)
        layout.addWidget(self.reload)
        self.status = QtWidgets.QLabel('Edits save as a draft for this take.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for text,callback,icon in (('Save draft',session.save,'save_preset_icon.png'),
                                   ('Approve & export BVH',session.approve,'Out_Node_Icon.png')):
            button = QtWidgets.QPushButton(text)
            button.setIcon(QtGui.QIcon(str(icons/icon)))
            button.setIconSize(QtCore.QSize(20,20))
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.ensurePolished()
        self.setFixedWidth(max(265,self.minimumSizeHint().width()))
        self.adjustSize()
        viewport.installEventFilter(self)
        self.place()
        self.sync_selection()

    def sync_selection(self):
        session = self.session
        self.reload.setText('Reload '+session.selected)
        pending = any(span['joint'] == session.selected and 'end' not in span for span in session.ik_spans)
        for button in self.ik_buttons:
            button.setEnabled(session.ik_enabled and session.selected in session.endpoints)
        self.ik_range.setText('IK start set. Scrub to the end and key its pose.' if pending else
                              ('Selected pole: ' if session.selected_pole else 'Selected: ')+session.selected)
        self.adjustSize()
        self.place()

    def place(self):
        view = self.parentWidget()
        bottom = getattr(view,'_bottom_overlay_height',lambda:0)() or 0
        self.move(max(4,view.width()-self.width()-12),max(4,view.height()-int(bottom)-self.height()-12))
        self.raise_()

    def eventFilter(self,obj,event):
        if event.type() in (QtCore.QEvent.Resize,QtCore.QEvent.Show,QtCore.QEvent.LayoutRequest):
            self.place()
        return False

    def mousePressEvent(self,event): event.accept()
    def mouseReleaseEvent(self,event): event.accept()
    def mouseDoubleClickEvent(self,event): event.accept()
    def mouseMoveEvent(self,event): event.accept()
    def wheelEvent(self,event): event.accept()


class CorrectionSession(QtCore.QObject):
    selectionChanged = QtCore.Signal(str)
    def __init__(self,item,context,viewport):
        super().__init__(viewport)
        self.item,self.context,self.viewport = item,context,viewport
        self.renderer = getattr(viewport,'_mgl_renderer',None) or viewport
        self.skeleton,self.base = context['skeleton'],context['clip']
        self.path = Path(context['path']).resolve()
        self.source_sha = file_signature(self.path)
        self.base_sha = spec.signature(item.model,self.base,context)
        document = spec.saved_document(item,context) or {}
        # Repairs may add subframe samples. Their sampling density is not the
        # viewport's playback clock: keep the frame numbering already in use.
        self.fps = float(getattr(viewport,'_timeline_fps',self.base.sample_rate_hz))
        if not np.isfinite(self.fps) or self.fps <= 0:
            self.fps = float(self.base.sample_rate_hz)
        self._ik_bake_cache = {}
        self.draft = corrections.apply_document(self.skeleton,self.base,document,ik_cache=self._ik_bake_cache)
        self.tracks = deepcopy(document.get('tracks',{}))
        for joint in document.get('keys',{}):
            track = next((track for track in self.draft.tracks if track.joint_name == joint),None)
            if track is not None:
                self.tracks[joint] = track.to_dict()
        self.rows = deepcopy(document.get('timeline_rows',{}))
        self.ik_spans = corrections.document_ik_spans(document)
        self.fk_keys = deepcopy(document.get('fk_keys',{}))
        self._committed_clip = self.draft
        self._preview_time = None
        self._preview_locks = None
        self._preview_poles = {}
        self._timeline_dirty = False
        self._undo = []
        self._redo = []
        self._gesture_before = None
        self._finished = False
        self._frame_signal_connected = False
        self.manual = (self._ik_bake_cache['clip'] if self._ik_bake_cache and not document.get('keys') else
                       corrections.apply_document(self.skeleton,self.base,dict(tracks=self.tracks)))
        for saved in self.rows.values():
            saved['keys'] = {int(frame):row for frame,row in saved['keys'].items()}
        self.approved = deepcopy(document.get('approved'))
        self.exports = deepcopy(document.get('exports',[]))
        self.ik_enabled = document.get('ik_enabled',True)
        self.lock_limbs = document.get('lock_limbs',False)
        self.show_colliders = document.get('show_colliders',True)
        self.show_limits = document.get('show_limits',True)
        self.timeline_range = (corrections.read_timeline_range(self.path,self.source_sha)
                               or deepcopy(document.get('timeline_range') or {}))
        self._saved_timeline_range = deepcopy(self.timeline_range)
        self.guide_error = ''
        try:
            self.guide = preview_guide(context)
            self.guide['collisions'] = self.show_colliders
            self.guide['limits'] = self.show_limits
        except (ValueError,KeyError,IndexError) as exc:
            self.guide = None
            self.guide_error = 'Collider preview unavailable: '+str(exc)
        self.selected = self.skeleton.joint_names[0]
        self.selected_pole = None
        self.pole_handles = []
        self.dirty = False
        self._graph_update_pending = False
        self.panel = self.rig = self.drag = None
        self.endpoints = set(corrections.limb_endpoints(self.skeleton))
        for name in self.skeleton.joint_names:
            if not corrections.body_control(self.skeleton,name):
                continue
            try:
                corrections.ik_chain(self.skeleton,name)
                self.endpoints.add(name)
            except ValueError:
                pass

    def open(self,window):
        self.window = window
        previous = getattr(self.viewport,'_animation_correction_session',None)
        if previous is not None and previous is not self and isValid(previous):
            if previous.dirty and not previous.save():
                raise ValueError('Save the previous take before changing correction previews.')
            previous.finish()
        asset = spec.asset_for_context(self.item,self.context,self.draft)
        if asset is None:
            raise ValueError('Could not build the animation preview.')
        self.rig = asset['fbx_rig_context']
        self.rig['_animation_correction_session'] = self
        if self.guide is not None:
            self.rig['priormdm_rig_debug'] = self.guide
        self.viewport._animation_correction_session = self
        self.item.model._animation_correction_session = self
        if window.open_scene_assets([asset],frame=True) is False:
            self.finish()
            raise ValueError('Could not open the animation preview.')
        self.renderer._mgl_scene_skeleton_request_active(self.item.model.name)
        show_timeline = getattr(self.viewport,'set_timeline_visible',None)
        if callable(show_timeline):
            show_timeline(True)
        self.panel = CorrectionPanel(self,self.viewport)
        self.panel.show()
        self.select(self.selected)
        token = self.renderer._mgl_scene_skeleton_joint_owner_token(self.item.model.name,self.selected)
        self.viewport.set_timeline_scene_context(owner_name=token,apply_current_frame=False,load_audio=False)
        self.viewport._timeline_load_from_disk(apply_current_frame=False)
        spin = getattr(self.viewport,'_timeline_frame_spin',None)
        if spin is not None:
            spin.valueChanged.connect(self.clear_preview)
            self._frame_signal_connected = True
        if self.panel is not None:
            self.panel.place()

    def initialize_timeline_range(self,fps):
        """Restore the display window independently of the current pose and joint."""
        total = max(1,round(self.draft.duration*fps))
        start,end,view_start,view_end = 1,total,1,total
        try:
            saved = self.timeline_range
            scale = fps/float(saved.get('fps',fps))
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError('Invalid range frame rate')
            start = max(0,min(100000,round(saved.get('start',1/scale)*scale)))
            end = max(start,min(100000,round(saved.get('end',total/scale)*scale)))
            view_start = max(start,min(end,round(saved.get('view_start',start/scale)*scale)))
            view_end = max(view_start,min(end,round(saved.get('view_end',end/scale)*scale)))
        except (AttributeError,TypeError,ValueError,ZeroDivisionError,OverflowError):
            start,end,view_start,view_end = 1,total,1,total
        self.viewport._timeline_in_frame = start
        self.viewport._timeline_out_frame = end
        self.viewport._timeline_view_start = view_start
        self.viewport._timeline_view_span = view_end-view_start
        self.viewport._timeline_display_range_locked = True

    def time(self):
        context = self.renderer._mgl_scene_owner_fbx_rig_context(self.item.model.name) or self.rig
        if context is not None:
            return self.renderer._mgl_fbx_context_timeline_sample_seconds(context,self.item.model.name)
        return self.base.start_time+float(self.viewport._timeline_current_frame())/self.fps

    def set_ik(self,enabled):
        self.clear_preview()
        self.ik_enabled = bool(enabled)
        if self.panel is not None:
            self.panel.lock_limbs.setEnabled(self.ik_enabled)
        self.dirty = True
        self.renderer._mgl_scene_skeleton_update_owner_handles(self.item.model.name,force=True,upload=False)
        self.select(self.selected)
        self.save()

    def set_lock_limbs(self,enabled):
        self.clear_preview()
        self.lock_limbs = bool(enabled)
        self.dirty = True
        self.save()

    def select(self,name,*,pole=False):
        self.selected = name
        self.selected_pole = name if pole else None
        self.viewport._set_xform_gizmo_mode('translate' if self.ik_enabled and name in self.endpoints else 'rotate')
        self.viewport.set_gizmo_visible(True)
        if self.panel is not None:
            self.panel.sync_selection()
        self.selectionChanged.emit(name)

    def select_pole(self,handle):
        self.select(handle['name'],pole=True)
        self.viewport._xform_gizmo_pos = tuple(handle['position'])
        self.viewport._xform_gizmo_pos_locked = True
        self.renderer._mgl_scene_skeleton_invalidate_owner(self.item.model.name)

    def ik_pole(self,name,*,pose=None):
        time = self.time()
        if self._preview_time is not None and abs(self._preview_time-time)<1e-8 and name in self._preview_poles:
            return np.asarray(self._preview_poles[name],dtype=float)
        return corrections.ik_pole_at_time(self.skeleton,self.manual,self.draft,self.ik_spans,name,time,pose=pose)

    def update_pole_handles(self,handles):
        self.pole_handles = []
        if not self.ik_enabled:
            return
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        pose = evaluate_rig_at_time(self.skeleton,self.draft,self.time(),loop=False)
        by_name = {handle['name']:handle for handle in handles}
        matrix = self.renderer._mgl_scene_skeleton_display_model_matrix(self.item.model.name)
        for name in sorted(self.endpoints):
            if corrections.body_control(self.skeleton,name) == 'pelvis' or name not in by_name:
                continue
            _,middle,_ = corrections.ik_chain(self.skeleton,name)
            pole = self.ik_pole(name,pose=pose)
            if matrix is not None:
                point = np.r_[pole,1.]@np.asarray(matrix)
                pole = point[:3]/point[3]
            control = by_name[name]
            bend = by_name[self.skeleton.joints[middle].name]
            radius = float(bend['radius'])*1.25
            self.pole_handles.append(dict(role='scene_skeleton',owner=self.item.model.name,name=name,
                index=middle,position=tuple(pole),axes=bend.get('axes'),radius=radius,control_radius=radius,
                ik_control=True,ik_pole=True,bend_position=bend['position']))

    def select_joint(self,name):
        """Use the shared viewport picker for info-card hierarchy selections."""
        self.renderer._mgl_scene_skeleton_update_owner_handles(self.item.model.name,force=True,upload=False)
        handles = getattr(self.renderer,'_mgl_scene_skeleton_handles_by_owner',{}).get(self.item.model.name,[])
        handle = next((entry for entry in handles if entry.get('name') == name),None)
        if handle is None:
            from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
            index = self.skeleton.joint_names.index(name)
            pose = evaluate_rig_at_time(self.skeleton,self.draft,self.time(),loop=False)
            point = np.asarray(pose.global_matrices[index]).reshape(4,4)[:3,3]
            matrix = self.renderer._mgl_scene_skeleton_display_model_matrix(self.item.model.name)
            if matrix is not None:
                point = (np.r_[point,1.]@np.asarray(matrix))[:3]
            handle = dict(role='scene_skeleton',owner=self.item.model.name,name=name,index=index,position=point)
        return self.renderer._mgl_scene_skeleton_handle_click(handle)

    def set_colliders(self,enabled):
        self.show_colliders = bool(enabled)
        if self.guide is not None:
            self.guide['collisions'] = self.show_colliders
        self.dirty = True
        self.renderer._mgl_scene_skeleton_invalidate_owner(self.item.model.name)
        self.save()

    def set_limits(self,enabled):
        self.show_limits = bool(enabled)
        if self.guide is not None:
            self.guide['limits'] = self.show_limits
        self.dirty = True
        self.renderer._mgl_scene_skeleton_invalidate_owner(self.item.model.name)
        self.save()

    def keys_map(self,name,*,timeline_space=True,fps=None,include_ik=True):
        fps = float(fps or getattr(self.viewport,'_timeline_fps',self.fps))
        saved = self.rows.get(name)
        if saved is not None:
            rows = {}
            for frame,entry in saved['keys'].items():
                row = deepcopy(entry)
                source_frame = float(row.get('source_frame',frame))*fps/float(saved['fps'])
                row['source_frame'] = source_frame
                rows[round(source_frame)] = row
        else:
            rows = corrections.joint_timeline_keys(self.manual,name,fps)
        if include_ik:
            for span in self.ik_spans:
                chain = corrections.ik_span_joints(self.skeleton,span)
                if name not in chain:
                    continue
                start,end = span['start'],span.get('end',span['start'])
                ik_mask = ([bool(value) and name == span['joint'] for value in span['position_mask']]
                           +[name != span['joint']]*3 if 'position_mask' in span
                           else [name == span['joint']]*3+[True]*3)
                kept = {}
                for frame,row in rows.items():
                    if start['time']-1e-8 <= self.base.start_time+float(row.get('source_frame',frame))/fps <= end['time']+1e-8:
                        if 'position_mask' not in span:
                            continue
                        row = deepcopy(row)
                        row['axis_mask'] = [bool(value) and not keyed for value,keyed
                                            in zip(row.get('axis_mask',[True]*6),ik_mask)]
                        if not any(row['axis_mask']):
                            continue
                    kept[frame] = row
                rows = kept
                for boundary in ('start','end'):
                    key = span.get(boundary)
                    if key is None:
                        continue
                    source_frame = (key['time']-self.base.start_time)*fps
                    from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
                    pose = evaluate_rig_at_time(self.skeleton,self.draft,key['time'],loop=False)
                    quaternion = pose.local_transforms[self.skeleton.joint_names.index(name)].rotation
                    ik_row = dict(axis_mask=ik_mask,ik_axis_mask=list(ik_mask),
                        source_frame=source_frame,source_time=key['time'],scene_skeleton_authored=True,
                        animation_correction_key=True,ik_key=key['id'],ik_control=span['joint'],
                        xyz=list(key['target']),rotation_quaternion=list(quaternion),
                        rxyz=Rotation.from_quat(quaternion).as_euler('xyz',degrees=True).tolist())
                    frame = round(source_frame)
                    existing = rows.get(frame)
                    if existing is not None and 'position_mask' in span:
                        for field,offset in (('xyz',0),('rxyz',3)):
                            ik_row[field] = [ik_row[field][axis] if ik_mask[offset+axis]
                                             else existing.get(field,ik_row[field])[axis] for axis in range(3)]
                        ik_row['axis_mask'] = [a or b for a,b in zip(existing['axis_mask'],ik_mask)]
                    rows[frame] = ik_row
        for stamp,quaternion in self.fk_keys.get(name,{}).items():
            source_frame = (float(stamp)-self.base.start_time)*fps
            row = rows.setdefault(round(source_frame),dict(axis_mask=[False]*6))
            row.update(source_frame=source_frame,source_time=float(stamp),fk_key=True,
                       scene_skeleton_authored=True,animation_correction_key=True,
                       rxyz=Rotation.from_quat(quaternion).as_euler('xyz',degrees=True).tolist(),
                       rotation_quaternion=list(quaternion))
            row['axis_mask'][3:6] = [True]*3
        if timeline_space:
            mapper = getattr(self.viewport,'_timeline_owner_timeline_frame_from_source_frame',None)
            if callable(mapper):
                mapped = {}
                for frame,row in rows.items():
                    stamp = mapper(self.item.model.name,float(row.get('source_frame',frame)),allow_owner_key_mode=True)
                    mapped[round(stamp if stamp is not None else frame)] = row
                rows = mapped
        return rows

    def apply_keys(self,name,rows,fps):
        try:
            return self._apply_keys(name,rows,fps)
        except (ValueError,RuntimeError) as exc:
            if self.panel is not None:
                self.panel.status.setText(str(exc))
            self.refresh_timeline()
            return False

    def _apply_keys(self,name,rows,fps):
        rows = deepcopy(rows)
        expected = self.keys_map(name,fps=fps)
        if rows == expected:
            return True
        underlying = self.keys_map(name,fps=fps,include_ik=False)
        original_underlying = deepcopy(underlying)
        spans = deepcopy(self.ik_spans)
        incoming = {row['ik_key']:row for row in rows.values() if row.get('ik_key')}
        for frame,original in expected.items():
            key_id = original.get('ik_key')
            if key_id:
                row = incoming.get(key_id)
                # A control boundary is atomic across the whole limb.
                ik_mask = original.get('ik_axis_mask',[True]*6)
                removed = row is None or any(keyed and not row.get('axis_mask',[False]*6)[axis]
                                             for axis,keyed in enumerate(ik_mask))
                for span in spans:
                    for boundary in ('start','end'):
                        key = span.get(boundary)
                        if key is None or key['id'] != key_id:
                            continue
                        if removed:
                            span.pop(boundary)
                        elif row == original:
                            continue
                        else:
                            key['time'] = self.base.start_time+float(row['source_frame'])/fps
                            if name == span['joint']:
                                mask = span.get('position_mask',[True]*3)
                                key['target'] = [row['xyz'][axis] if mask[axis] else key['target'][axis]
                                                 for axis in range(3)]
                            key['rotations'][name] = Rotation.from_euler('xyz',row['rxyz'],degrees=True).as_quat().tolist()
            elif frame not in rows:
                underlying.pop(frame,None)
        for frame,row in rows.items():
            if not row.get('ik_key'):
                underlying[frame] = row
            elif 'ik_axis_mask' in row:
                # A source key can share a frame with a boundary on another axis.
                source = deepcopy(underlying.get(frame,dict(source_frame=row['source_frame'],
                    xyz=list(row['xyz']),rxyz=list(row['rxyz']),axis_mask=[False]*6)))
                for axis,keyed in enumerate(row['ik_axis_mask']):
                    if keyed:
                        continue
                    source['axis_mask'][axis] = row['axis_mask'][axis]
                    field = 'xyz' if axis < 3 else 'rxyz'
                    source.setdefault(field,list(row[field]))
                    source[field][axis%3] = row[field][axis%3]
                underlying[frame] = source
        spans = [span for span in spans if 'start' in span or 'end' in span]
        for span in spans:
            if 'start' not in span:
                span['start'] = span.pop('end')
            if 'end' in span and span['start']['time'] > span['end']['time']:
                span['start'],span['end'] = span['end'],span['start']
        corrections.validate_ik_spans(self.skeleton,self.base,spans)
        # Keep the original rows underneath IK ranges, so deleting a control
        # or reloading a joint restores the source instead of baked samples.
        rows = underlying
        source_changed = rows != original_underlying
        for row in rows.values():
            row.pop('fbx_clip_key',None)
            row.pop('joint_key',None)
            row['scene_skeleton_authored'] = True
            row['animation_correction_key'] = True
        tracks,saved_rows = dict(self.tracks),dict(self.rows)
        fk_keys = dict(self.fk_keys)
        # Removing/retiming an IK boundary must not reconstruct every source
        # key or invalidate the manual clip used by unchanged IK ranges.
        if source_changed:
            track = corrections.track_from_timeline_keys(self.skeleton,self.base,name,rows,float(fps))
            tracks[name] = track.to_dict()
            saved_rows[name] = dict(fps=float(fps),keys=rows)
            fk_keys[name] = {format(self.base.start_time+float(row.get('source_frame',frame))/fps,'.12g'):
                            Rotation.from_euler('xyz',row['rxyz'],degrees=True).as_quat().tolist()
                            for frame,row in rows.items() if row.get('fk_key')}
            if not fk_keys[name]:
                fk_keys.pop(name)
        self.rebuild(tracks=tracks,rows=saved_rows,spans=spans,fk_keys=fk_keys)
        return True

    def rebuild(self,*,tracks=None,rows=None,spans=None,fk_keys=None):
        before = self._state()
        tracks = self.tracks if tracks is None else tracks
        rows = self.rows if rows is None else rows
        spans = self.ik_spans if spans is None else spans
        fk_keys = self.fk_keys if fk_keys is None else fk_keys
        manual = (self.manual if tracks == self.tracks else
                  corrections.apply_document(self.skeleton,self.base,dict(tracks=tracks)))
        draft = corrections.preview_clip(self.skeleton,manual,spans,self.fps,
                                         fk_keys=fk_keys,cache=self._ik_bake_cache)
        self.tracks,self.rows,self.ik_spans = tracks,rows,spans
        self.fk_keys = fk_keys
        self.manual,self.draft = manual,draft
        self._committed_clip = draft
        self._preview_time = None
        self._preview_locks = None
        self._preview_poles = {}
        self.approved = None
        self.dirty = True
        self.sync()
        if self.panel is not None:
            self.panel.sync_selection()
        self._record(before)

    def rotate(self,name,angles):
        if self.ik_enabled and name in self.endpoints:
            self.preview_ik(name,rotations={name:Rotation.from_euler('xyz',angles,degrees=True).as_quat()})
            return
        self.key_rotations({name:Rotation.from_euler('xyz',angles,degrees=True).as_quat()})

    def key_ik(self,name=None,*,mode=None,target=None,rotations=None,pole=None,save=False):
        name = name or self.selected
        if not self.ik_enabled or name not in self.endpoints:
            if self.panel is not None:
                self.panel.status.setText('Enable IK and select a pelvis, chest, wrist or ankle control.')
            return False
        try:
            from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
            time = self.time()
            pose = evaluate_rig_at_time(self.skeleton,self.draft,time,loop=False)
            chain = corrections.ik_chain(self.skeleton,name)
            end = chain[-1]
            target = np.asarray(target if target is not None else np.asarray(pose.global_matrices[end]).reshape(4,4)[:3,3])
            body = corrections.body_control(self.skeleton,name)
            if body != 'pelvis':
                pole = np.asarray(pole if pole is not None else self.ik_pole(name,pose=pose),dtype=float)
            locked = bool(body and self.lock_limbs)
            span_options = dict(joint=name,lock_limbs=locked,
                                keyframe_fps=float(getattr(self.viewport,'_timeline_fps',self.fps)))
            matching = [span for span in self.ik_spans if span['joint'] == name and (
                'end' not in span or (mode is None and span['start']['time'] <= time <= span['end']['time'])
                or any(abs(span[boundary]['time']-time)<1e-7
                                       for boundary in ('start','end') if boundary in span))]
            # A range keeps its Start IK channel selection when its end is keyed
            # or a boundary is refined, even if the timeline filter has changed.
            if matching and (mode != 'start' or any(
                    abs(span['start']['time']-time)<1e-7 for span in matching)):
                if 'position_mask' in matching[0]:
                    span_options['position_mask'] = list(matching[0]['position_mask'])
            else:
                axes = getattr(self.viewport,'_timeline_curve_axes_filter',set()) or set()
                if axes:
                    mask = [axis in axes for axis in range(3)]
                    if not any(mask):
                        raise ValueError('Select Px, Py or Pz for an axis IK range, or clear the channel selection for full IK.')
                    span_options['position_mask'] = mask
            names = corrections.ik_span_joints(self.skeleton,span_options)
            seed = {joint:list(pose.local_transforms[self.skeleton.joint_names.index(joint)].rotation) for joint in names}
            if rotations:
                seed.update({joint:list(value) for joint,value in rotations.items()})
            translations = {self.skeleton.joints[chain[0]].name:pose.local_transforms[chain[0]].translation} if body == 'pelvis' else {}
            locks = (self._preview_locks if self._preview_time is not None and abs(self._preview_time-time)<1e-8 and self._preview_locks is not None else
                     corrections.limb_world_targets(self.skeleton,pose)) if locked else {}
            if 'position_mask' in span_options:
                animated = evaluate_rig_at_time(self.skeleton,self._committed_clip,time,loop=False)
                animated_target = np.asarray(animated.global_matrices[end]).reshape(4,4)[:3,3]
                target = np.where(span_options['position_mask'],target,animated_target)
                seed = {joint:list(animated.local_transforms[self.skeleton.joint_names.index(joint)].rotation) for joint in names}
                translations = {joint:animated.local_transforms[self.skeleton.joint_names.index(joint)].translation
                                for joint in translations}
            solved,translations,error = corrections.solve_ik_pose(self.skeleton,self.draft,name,time,target,
                seed=seed,translations=translations,locks=locks,pole=pole)
            seed.update({joint:np.asarray(value).tolist() for joint,value in solved.items()})
            key = dict(id=uuid4().hex,time=float(time),target=target.tolist(),rotations=seed)
            key['body_frames'] = corrections.body_reference_frames(self.skeleton,self.manual,pose,time)
            if pole is not None:
                key['pole'] = pole.tolist()
            if body:
                key.update(translations={joint:np.asarray(value).tolist() for joint,value in translations.items()},locks=deepcopy(locks))
            spans = deepcopy(self.ik_spans)
            # A changed checkbox also applies when refining or completing a
            # saved body range. Keep its boundary data consistent with the mode.
            for span in spans:
                if span['joint'] != name or span.get('lock_limbs',False) == locked:
                    continue
                span['lock_limbs'] = locked
                for boundary in ('start','end'):
                    old = span.get(boundary)
                    if old is None:
                        continue
                    original = evaluate_rig_at_time(self.skeleton,self._committed_clip,old['time'],loop=False)
                    old['rotations'] = {joint:list(original.local_transforms[self.skeleton.joint_names.index(joint)].rotation)
                                        for joint in names}
                    old['locks'] = corrections.limb_world_targets(self.skeleton,original) if locked else {}
            same = [(span,boundary) for span in spans if span['joint']==name for boundary in ('start','end')
                    if boundary in span and abs(span[boundary]['time']-time)<1e-7]
            pending = next((span for span in spans if span['joint']==name and 'end' not in span),None)
            if same:
                if mode == 'end' and pending is not None and abs(pending['start']['time']-time)<1e-7:
                    raise ValueError('Scrub to a different frame before setting the IK end.')
                key['id'] = same[0][0][same[0][1]]['id']
                for span,boundary in same:
                    span[boundary] = deepcopy(key)
            elif mode == 'start':
                if pending is not None:
                    spans.remove(pending)
                spans.append(dict(span_options,start=key))
            elif pending is not None:
                pending['end'] = key
                if time < pending['start']['time']:
                    pending['start'],pending['end'] = pending['end'],pending['start']
            elif mode == 'end':
                raise ValueError('Set an IK start pose first, then scrub to its end.')
            else:
                containing = next((span for span in spans if span['joint']==name and
                    span['start']['time'] < time < span.get('end',span['start'])['time']),None)
                if containing is not None:
                    # Refining an existing range creates two adjacent ranges.
                    spans.append(dict(span_options,start=deepcopy(key),end=containing['end']))
                    containing['end'] = key
                else:
                    spans.append(dict(span_options,start=key))
            corrections.validate_ik_spans(self.skeleton,self.base,spans)
            affected = [span for span in spans if span['joint']==name and any(
                span.get(boundary,{}).get('id') == key['id'] for boundary in ('start','end'))]
            fk_keys = dict(self.fk_keys)
            for joint in names:
                if joint == name and affected and 'position_mask' in affected[0]:
                    continue
                if joint in fk_keys:
                    fk_keys[joint] = {stamp:value for stamp,value in fk_keys[joint].items() if not any(
                        span['start']['time']-1e-8 <= float(stamp) <= span.get('end',span['start'])['time']+1e-8
                        for span in affected)}
                    if not fk_keys[joint]:
                        fk_keys.pop(joint)
            self.rebuild(spans=spans,fk_keys=fk_keys)
            self.refresh_timeline()
            if self.panel is not None:
                mask = affected[0].get('position_mask') if affected else None
                channels = ' ('+', '.join(axis for axis,keyed in zip(('Px','Py','Pz'),mask) if keyed)+')' if mask else ''
                self.panel.status.setText(f'IK control keyed{channels}. Target error: {error:.4g}.')
            if save:
                return self.save(notify=False)
            return True
        except (ValueError,RuntimeError) as exc:
            if self.panel is not None:
                self.panel.status.setText(str(exc))
            return False

    def reload_joint(self):
        name = self.selected
        removed = [span for span in self.ik_spans if name in
            corrections.ik_span_joints(self.skeleton,span)]
        spans = [span for span in self.ik_spans if span not in removed]
        tracks,rows = dict(self.tracks),dict(self.rows)
        tracks.pop(name,None)
        rows.pop(name,None)
        fk_keys = dict(self.fk_keys)
        fk_keys.pop(name,None)
        self.rebuild(tracks=tracks,rows=rows,spans=spans,fk_keys=fk_keys)
        self.refresh_timeline()
        saved = self.save()
        if saved and self.panel is not None:
            self.panel.status.setText('Reloaded input keys for '+name+('. Removed its limb’s IK ranges.' if removed else '.'))
        return saved

    def key_rotations(self,rotations):
        before = self._state()
        time = self.time()
        keys = dict(self.fk_keys)
        for name,quaternion in rotations.items():
            keys[name] = dict(keys.get(name,{}))
            keys[name][format(time,'.12g')] = list(quaternion)
        # A local FK key wins at this frame, including inside a saved IK range.
        # Changing an elbow must not immediately be overwritten by the solver.
        if self.ik_spans:
            self.draft = corrections.preview_clip(self.skeleton,self.manual,self.ik_spans,self.fps,
                                                 fk_keys=keys,cache=self._ik_bake_cache)
        else:
            self.draft = override_rotation_keys(self.skeleton,self._committed_clip,
                {name:{time:quaternion} for name,quaternion in rotations.items()})
        self.fk_keys = keys
        self._committed_clip = self.draft
        self._preview_time = None
        self._preview_locks = None
        self._preview_poles = {}
        self.approved = None
        self.dirty = self._timeline_dirty = True
        self.sync()
        self._record(before)

    def preview_ik(self,name,*,target=None,rotations=None,pole=None):
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        self.begin_edit()
        time = self.time()
        if self._preview_time is not None and abs(self._preview_time-time)>1e-8:
            self.clear_preview()
        pose = evaluate_rig_at_time(self.skeleton,self.draft,time,loop=False)
        chain = corrections.ik_chain(self.skeleton,name)
        end = chain[-1]
        target = np.asarray(target if target is not None else np.asarray(pose.global_matrices[end]).reshape(4,4)[:3,3])
        body = corrections.body_control(self.skeleton,name)
        if body != 'pelvis':
            pole = np.asarray(pole if pole is not None else self.ik_pole(name,pose=pose),dtype=float)
        names = corrections.ik_span_joints(self.skeleton,dict(joint=name,lock_limbs=bool(body and self.lock_limbs)))
        seed = {joint:pose.local_transforms[self.skeleton.joint_names.index(joint)].rotation for joint in names}
        seed.update(rotations or {})
        if body and self.lock_limbs and self._preview_locks is None:
            self._preview_locks = corrections.limb_world_targets(self.skeleton,pose)
        translations = {self.skeleton.joints[chain[0]].name:pose.local_transforms[chain[0]].translation} if body == 'pelvis' else {}
        source = self.draft if body else self._committed_clip
        solved,translations,error = corrections.solve_ik_pose(self.skeleton,source,name,time,target,seed=seed,
            translations=translations,locks=self._preview_locks if body and self.lock_limbs else None,pole=pole)
        seed.update(solved)
        self.draft = corrections.override_pose(self.skeleton,source,time,seed,translations)
        self._preview_time = time
        if pole is not None:
            self._preview_poles = dict(self._preview_poles,**{name:pole.tolist()})
        self.sync()
        if self.panel is not None:
            self.panel.status.setText(f'IK preview only. Press Start IK / End IK or Set key. Error: {error:.4g}.')

    def clear_preview(self,*_args):
        if self._preview_time is not None:
            self.draft = self._committed_clip
            self._preview_time = None
            self._preview_locks = None
            self._preview_poles = {}
            self.sync()

    def move(self,name,point):
        if self.selected_pole == name:
            return self.move_pole(name,point)
        if not self.ik_enabled or name not in self.endpoints:
            return
        try:
            matrix = self.renderer._mgl_scene_skeleton_display_model_matrix(self.item.model.name)
            target = np.asarray(point,dtype=float)
            if matrix is not None:
                target = np.r_[target,1.] @ np.linalg.inv(np.asarray(matrix))
                target = target[:3]/target[3]
            self.preview_ik(name,target=target)
        except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
            if self.panel is not None:
                self.panel.status.setText(str(exc))

    def move_pole(self,name,point):
        if not self.ik_enabled or name not in self.endpoints or corrections.body_control(self.skeleton,name) == 'pelvis':
            return
        try:
            matrix = self.renderer._mgl_scene_skeleton_display_model_matrix(self.item.model.name)
            pole = np.asarray(point,dtype=float)
            if matrix is not None:
                pole = np.r_[pole,1.]@np.linalg.inv(np.asarray(matrix))
                pole = pole[:3]/pole[3]
            self.preview_ik(name,pole=pole)
        except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
            if self.panel is not None:
                self.panel.status.setText(str(exc))

    def ray(self,event):
        dpr = self.viewport.devicePixelRatioF()
        point = event.position()
        return self.renderer._mgl_retarget_pick_ray(point.x()*dpr,point.y()*dpr,
            round(self.viewport.width()*dpr),round(self.viewport.height()*dpr))

    def start_drag(self,handle,event):
        ray = self.ray(event)
        if ray is None:
            return False
        origin,direction = (np.asarray(value,dtype=float) for value in ray)
        center = np.asarray(handle['position'],dtype=float)
        normal = direction/np.linalg.norm(direction)
        hit = origin+direction*np.dot(center-origin,normal)/np.dot(direction,normal)
        self.begin_edit()
        self.drag = dict(name=handle['name'],pole=bool(handle.get('ik_pole')),center=center,normal=normal,hit=hit,
                         start_pixel=QtCore.QPointF(event.position()),moved=False)
        self.viewport._animation_correction_drag = self
        return True

    def drag_to(self,event):
        if self.drag is None:
            return
        delta = event.position()-self.drag['start_pixel']
        if not self.drag['moved'] and delta.x()**2+delta.y()**2 < 9.:
            return
        ray = self.ray(event)
        if ray is None:
            return
        self.drag['moved'] = True
        origin,direction = (np.asarray(value,dtype=float) for value in ray)
        denominator = np.dot(direction,self.drag['normal'])
        if abs(denominator) > 1e-8:
            hit = origin+direction*np.dot(self.drag['center']-origin,self.drag['normal'])/denominator
            move = self.move_pole if self.drag['pole'] else self.move
            move(self.drag['name'],self.drag['center']+hit-self.drag['hit'])

    def commit(self,*_args):
        self.drag = None
        self.viewport._animation_correction_drag = None
        if self._gesture_before is not None:
            before,self._gesture_before = self._gesture_before,None
            self._record(before)
        if self._timeline_dirty:
            self.refresh_timeline()
        if self.dirty:
            self.save(notify=False)

    def _state(self):
        # Revisions share immutable clips/channels; never copy a whole take.
        return dict(tracks=self.tracks,rows=self.rows,ik_spans=self.ik_spans,fk_keys=self.fk_keys,
                    manual=self.manual,draft=self.draft,_committed_clip=self._committed_clip,
                    _preview_time=self._preview_time,_preview_locks=self._preview_locks,
                    _preview_poles=self._preview_poles,approved=self.approved)

    def begin_edit(self):
        if self._gesture_before is None:
            self._gesture_before = self._state()

    def _record(self,before):
        if self._gesture_before is not None:
            return
        after = self._state()
        if before['draft'] is after['draft'] and before['ik_spans'] is after['ik_spans']:
            return
        self._undo.append((before,after))
        del self._undo[:-100]
        self._redo.clear()

    def _restore(self,state):
        for name,value in state.items():
            setattr(self,name,value)
        self.dirty = True
        self.sync()
        self.refresh_timeline()
        if self.panel is not None:
            self.panel.sync_selection()
        self.save(notify=False)

    def undo(self):
        self.commit()
        if not self._undo:
            return False
        entry = self._undo.pop()
        self._restore(entry[0])
        self._redo.append(entry)
        return True

    def redo(self):
        self.commit()
        if not self._redo:
            return False
        entry = self._redo.pop()
        self._restore(entry[1])
        self._undo.append(entry)
        return True

    def sync(self):
        for context in (self.rig,self.renderer._mgl_scene_owner_fbx_rig_context(self.item.model.name)):
            if context is not None and context.get('skeleton') is self.skeleton:
                context.update(clip=self.draft,clips=[self.draft],_animation_correction_session=self)
        self.renderer._mgl_scene_skeleton_invalidate_owner(self.item.model.name)

    def refresh_timeline(self):
        self._timeline_dirty = False
        decoded = self.renderer._mgl_scene_skeleton_decode_joint_owner(getattr(self.viewport,'_timeline_owner_name',''))
        if decoded and decoded[0] == self.item.model.name:
            self.viewport._timeline_keys = self.keys_map(decoded[1])
            self.viewport._timeline_update_key_markers()
            self.viewport._timeline_update_key_count_label()
            self.viewport._timeline_refresh_coord_labels()

    def document(self):
        return dict(version=2,source_sha=self.source_sha,base_clip_sha=self.base_sha,
            fps=self.fps,keys={},tracks=self.tracks,timeline_rows=self.rows,ik_enabled=self.ik_enabled,
            lock_limbs=self.lock_limbs,
            ik_spans=self.ik_spans,fk_keys=self.fk_keys,show_colliders=self.show_colliders,
            show_limits=self.show_limits,
            timeline_range=self.timeline_range,
            approved=self.approved,exports=self.exports)

    def save_timeline_range(self):
        if self.timeline_range == self._saved_timeline_range:
            return True
        try:
            corrections.save_timeline_range(self.path,self.source_sha,self.timeline_range)
            self._saved_timeline_range = deepcopy(self.timeline_range)
            return True
        except (OSError,ValueError) as exc:
            if self.panel is not None:
                self.panel.status.setText(str(exc))
            return False

    def save(self,*,notify=True):
        try:
            if self._timeline_dirty:
                self.refresh_timeline()
            corrections.save_document(self.path,self.document())
            if not self.save_timeline_range():
                return False
            self.dirty = False
            self._graph_update_pending = True
            if notify and not self._notify_saved_changes():
                return False
            if self.panel is not None:
                self.panel.status.setText('Saved JSON draft for '+self.path.name)
            return True
        except (OSError,ValueError,RuntimeError) as exc:
            if self.panel is not None:
                self.panel.status.setText(str(exc))
            return False

    def _notify_saved_changes(self):
        # The viewport already uses the current draft. Publish to the rest of
        # the graph at explicit save/approval or when the editing session closes.
        self._graph_update_pending = False
        try:
            scene = self.item.scene()
            if scene is not None:
                from nodes.mocap_collection import spec as collection
                for item in list(getattr(scene,'_node_items',{}).values()):
                    if item.model.kind == 'mocap_collection':
                        collection.refresh_repair_records(item.model,scene)
                scene.set_node_params(self.item.model.name,list(self.item.model.params),rebuild=False,emit=True)
            return True
        except (OSError,ValueError,RuntimeError) as exc:
            self._graph_update_pending = True
            if self.panel is not None and isValid(self.panel):
                self.panel.status.setText(str(exc))
            return False

    def approve(self):
        if not self.save(notify=False):
            return False
        output = self.path.with_name(self.path.stem+'_corrected_'+uuid4().hex[:8]+'.bvh')
        previous = self.approved
        try:
            from nodes.rig_limiter.spec import export_context
            clip = corrections.apply_ik_spans(self.skeleton,self.manual,self.ik_spans,self.fps,cache=self._ik_bake_cache)
            if self.fk_keys:
                clip = override_rotation_keys(self.skeleton,clip,self.fk_keys)
            export_context(corrections.bvh_export_context(self.skeleton,clip),output)
            self.approved = dict(file=output.name,output_sha=file_signature(output),source_sha=self.source_sha)
            self.exports.append(dict(self.approved))
            if not self.save():
                self.exports.pop()
                self.approved = previous
                output.unlink(missing_ok=True)
                return False
            if self.panel is not None:
                self.panel.status.setText('Approved and exported: '+output.name)
            return output
        except (OSError,ValueError,RuntimeError) as exc:
            self.approved = previous
            output.unlink(missing_ok=True)
            if self.panel is not None:
                self.panel.status.setText(str(exc))
            return False

    def finish(self):
        if not isValid(self) or self._finished:
            return
        self._finished = True
        self.commit()
        self.save_timeline_range()
        spin = getattr(self.viewport,'_timeline_frame_spin',None)
        if spin is not None and self._frame_signal_connected:
            spin.valueChanged.disconnect(self.clear_preview)
            self._frame_signal_connected = False
        for context in (self.rig,self.renderer._mgl_scene_owner_fbx_rig_context(self.item.model.name)):
            if context is not None and context.get('_animation_correction_session') is self:
                context.pop('_animation_correction_session',None)
                context.pop('priormdm_rig_debug',None)
        if self.panel is not None and isValid(self.panel):
            self.panel.hide()
            self.panel.deleteLater()
        if getattr(self.viewport,'_animation_correction_session',None) is self:
            self.viewport._animation_correction_session = None
        sync_range = getattr(self.viewport,'_timeline_sync_range_editor',None)
        if callable(sync_range):
            sync_range()
        if getattr(self.renderer,'_mgl_scene_skeleton_active_owner','') == self.item.model.name:
            self.renderer._mgl_scene_skeleton_request_active('')
        if getattr(self.item.model,'_animation_correction_session',None) is self:
            self.item.model._animation_correction_session = None
        if self._graph_update_pending:
            self._notify_saved_changes()
        self.deleteLater()
