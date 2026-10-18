"""Interactive limit editor embedded in the Preview Rig viewport."""
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.motion.conversion import JOINT_NAMES, PARENTS
from echograph.motion.rig_limits import PREVIEW_JOINT_LIMITS, preview_limit_fields, preview_limit_from_fields
from echograph.motion.rig_preview_settings import save_preview_limits
from echograph.motion.rig_volumes import volume_settings, rig_scale
from echograph.motion.hand_rig import named_limit_default

PANEL_WIDTH = 320
PANEL_HEIGHT = 320
JOINT_SECTION_HEIGHT = 285
SECTION_SPACING = 8


def _icon_button(path, tooltip):
    button = QtWidgets.QToolButton()
    button.setIcon(QtGui.QIcon(str(path)))
    button.setIconSize(QtCore.QSize(18,18))
    button.setFixedSize(28,28)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setFocusPolicy(QtCore.Qt.StrongFocus)
    return button


class _LimitSpinBox(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event):
        # Scrolling over another joint's fields should scroll the cards, not
        # silently change its limits. Focused fields still support wheel edits.
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class RigJointSection(QtWidgets.QFrame):
    def __init__(self, panel, name):
        super().__init__(panel.content)
        self.panel = panel
        self.name = name
        names = panel.data['rig_guide'].get('joint_names',JOINT_NAMES)
        self.joint = names.index(name) if name in names else None
        self._binding = None
        self.setObjectName('PreviewRigJoint')
        self.setFixedHeight(JOINT_SECTION_HEIGHT)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,6)
        layout.setSpacing(6)
        layout.setAlignment(QtCore.Qt.AlignTop)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(name.replace('_',' ').title() or 'Joint settings')
        self.title.setObjectName('PreviewRigJointTitle')
        header.addWidget(self.title,1)
        icons = Path(__file__).resolve().parents[2]/'icons'
        self.reset_button = _icon_button(icons/'Refresh_Icon.png','Reset this joint to defaults')
        self.reset_button.clicked.connect(self.reset_joint)
        header.addWidget(self.reset_button)
        layout.addLayout(header)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.detail.ensurePolished()
        self.detail.setFixedHeight(self.detail.fontMetrics().lineSpacing()*4)
        layout.addWidget(self.detail)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setAlignment(QtCore.Qt.AlignLeft)
        grid.setColumnMinimumWidth(0,64)
        grid.addWidget(QtWidgets.QLabel('Minimum'),0,1)
        grid.addWidget(QtWidgets.QLabel('Maximum'),0,2)
        self.bend_label = QtWidgets.QLabel('Bend cone')
        self.bend_label.setToolTip('Angle away from the resting bone. A positive minimum excludes the inner cone.')
        grid.addWidget(self.bend_label,1,0)
        self.twist_label = QtWidgets.QLabel('Twist circle')
        grid.addWidget(self.twist_label,2,0)
        self.fields = {}
        for row,kind in ((1,'bend'),(2,'twist')):
            for column,end in ((1,'min'),(2,'max')):
                key = kind+'_'+end
                spin = _LimitSpinBox()
                spin.setObjectName('PreviewRig_'+name+'_'+key)
                spin.setAccessibleName((name.replace('_',' ')+' '+kind+' '+end+' degrees').strip())
                spin.setDecimals(1)
                spin.setSingleStep(1.)
                spin.setSuffix('°')
                spin.setKeyboardTracking(False)
                spin.setFixedWidth(78)
                spin.setToolTip(self.bend_label.toolTip() if kind=='bend' else 'Signed rotation around the bone axis.')
                spin.valueChanged.connect(lambda value,k=key:self._edited(k,value))
                self.fields[key] = spin
                grid.addWidget(spin,row,column)
        layout.addLayout(grid)
        volume_grid = QtWidgets.QGridLayout()
        volume_grid.addWidget(QtWidgets.QLabel('Capsule radius'),0,0,1,2)
        self.volume_fields = []
        for column, label in enumerate(('Start end', 'Far end')):
            volume_grid.addWidget(QtWidgets.QLabel(label),1,column)
            spin = _LimitSpinBox()
            spin.setDecimals(4)
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(name+' capsule '+label+' radius')
            spin.setToolTip('Radius in input rig units. Each capsule end can be sized independently.')
            spin.valueChanged.connect(lambda value,index=column:self._volume_edited(index,value))
            volume_grid.addWidget(spin,2,column)
            self.volume_fields.append(spin)
        layout.addLayout(volume_grid)

    def sync(self, guide, detail):
        editable = self.joint in guide['joint_limits']
        binding = (id(guide),guide.get('limits_revision',0))
        if binding != self._binding:
            self._binding = binding
            limit = guide['joint_limits'].get(self.joint,{})
            values = preview_limit_fields(self.joint,limit) if editable else dict.fromkeys(self.fields,0.)
            hinge = 'flex' in limit
            self.twist_label.setText('Spread' if 'spread_axis' in limit else 'Twist circle')
            self.bend_label.setText('Hinge bend' if hinge else 'Bend cone')
            for key,spin in self.fields.items():
                blocker = QtCore.QSignalBlocker(spin)
                spin.setRange(0. if key.startswith('bend') else -179.,
                              (179. if hinge else 89.) if key.startswith('bend') else 179.)
                spin.setValue(values[key])
                spin.setEnabled(editable and (not hinge or 'spread_axis' in limit or key.startswith('bend')))
                del blocker
            scale = rig_scale(guide['offsets'])
            radii = guide.get('volumes', {}).get(self.name)
            for index,spin in enumerate(self.volume_fields):
                blocker = QtCore.QSignalBlocker(spin)
                spin.setRange(.001*scale,scale)
                spin.setSingleStep(.005*scale)
                spin.setValue(radii[index]*scale if radii else .001*scale)
                spin.setEnabled(radii is not None)
                del blocker
            self.reset_button.setEnabled(editable or radii is not None)
        if self.name and detail.startswith(self.name+': '):
            detail = detail[len(self.name)+2:]
        if self.detail.text() != detail:
            self.detail.setText(detail)

    def _edited(self, key, value):
        default = named_limit_default(self.name)
        if not default:
            return
        values = {k:spin.value() for k,spin in self.fields.items()}
        kind,end = key.split('_')
        other = kind+('_max' if end=='min' else '_min')
        if values[kind+'_min'] > values[kind+'_max']:
            values[other] = value
        self.panel._apply_limit(self.joint,preview_limit_from_fields(self.joint,values,defaults=default))

    def reset_joint(self):
        defaults = volume_settings()
        if self.name in defaults:
            self.panel._apply_volume(self.name,defaults[self.name])
        default = named_limit_default(self.name)
        if default and self.joint in self.panel.data['rig_guide']['joint_limits']:
            self.panel._apply_limit(self.joint,preview_limit_from_fields(self.joint,{},defaults=default))

    def _volume_edited(self,index,value):
        guide = self.panel.data['rig_guide']
        radii = list(guide['volumes'][self.name])
        radii[index] = value/rig_scale(guide['offsets'])
        self.panel._apply_volume(self.name,radii)


class RigPreviewPanel(QtWidgets.QFrame):
    def __init__(self, renderer):
        super().__init__(renderer)
        self.renderer = renderer
        self.data = None
        self.sections = {}
        self._selection = ()
        self._error = ''
        self.setObjectName('PreviewRigPanel')
        self.setAttribute(QtCore.Qt.WA_StyledBackground,True)
        self.setStyleSheet('''
            QFrame#PreviewRigPanel { background:#0f172a; border:1px solid #334155; border-radius:6px; }
            QScrollArea, QWidget#PreviewRigContent { background:transparent; border:0; }
            QFrame#PreviewRigJoint { border:0; border-bottom:1px solid #334155; }
            QLabel { color:#e2e8f0; font-size:11px; }
            QCheckBox { color:#e2e8f0; font-size:11px; }
            QLabel#PreviewRigTitle { font-size:13px; font-weight:600; }
            QLabel#PreviewRigJointTitle { color:#7dd3fc; font-size:13px; font-weight:600; }
            QDoubleSpinBox { color:#e2e8f0; background:#1e293b; border:1px solid #475569;
                             border-radius:3px; padding:3px; min-height:20px; }
            QDoubleSpinBox:disabled { color:#64748b; border-color:#334155; }
            QToolButton { background:#273548; border:1px solid #475569; border-radius:4px; padding:3px; }
            QToolButton:hover { background:#3b4e67; }
            QToolButton:disabled { background:#182131; border-color:#263246; }
        ''')
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10,8,10,8)
        outer.setSpacing(6)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel('Preview Rig')
        self.title.setObjectName('PreviewRigTitle')
        header.addWidget(self.title,1)
        icons = Path(__file__).resolve().parents[2]/'icons'
        self.save_button = _icon_button(icons/'save_preset_icon.png','Save all joint limits and capsule radii')
        self.save_button.clicked.connect(self.save)
        header.addWidget(self.save_button)
        outer.addLayout(header)
        self.collision_check = QtWidgets.QCheckBox('Collision preview (blocks rotations)')
        self.collision_check.setToolTip('Show capsules and stop manual rotations at contact with another body volume.')
        self.collision_check.toggled.connect(self._collision_toggled)
        outer.addWidget(self.collision_check)
        self.legend = QtWidgets.QLabel('Gold: bend  ·  Purple: twist  ·  Red: at limit')
        self.legend.setWordWrap(True)
        outer.addWidget(self.legend)
        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.content = QtWidgets.QWidget()
        self.content.setObjectName('PreviewRigContent')
        self.sections_layout = QtWidgets.QVBoxLayout(self.content)
        self.sections_layout.setContentsMargins(0,0,8,0)
        self.sections_layout.setSpacing(SECTION_SPACING)
        self.sections_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll,1)
        note = QtWidgets.QLabel('Edits apply immediately. Save to reuse.')
        note.setWordWrap(True)
        outer.addWidget(note)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def _active_section(self):
        return self.sections.get((self.data or {}).get('selected','')) or next(iter(self.sections.values()))

    @property
    def fields(self): return self._active_section().fields
    @property
    def reset_button(self): return self._active_section().reset_button

    def _snapshot(self):
        return {j:preview_limit_fields(j,limit) for j,limit in self.data['rig_guide']['joint_limits'].items()}

    def sync(self, data, detail, *, labels=None):
        guide_changed = self.data is None or self.data.get('rig_guide') is not data['rig_guide']
        if guide_changed:
            self._error = ''
        self.data = data
        guide = data['rig_guide']
        guide.setdefault('volumes',volume_settings())
        guide.setdefault('_saved_volumes',{n:list(r) for n,r in guide.get('saved_volumes',guide['volumes']).items()})
        guide.setdefault('_saved_collisions',bool(guide.get('collisions',True)))
        blocker = QtCore.QSignalBlocker(self.collision_check)
        self.collision_check.setChecked(bool(guide.get('collisions',True)))
        del blocker
        guide.setdefault('joint_limits',{j:dict(v) for j,v in PREVIEW_JOINT_LIMITS.items()})
        guide.setdefault('_saved_limit_fields',{j:preview_limit_fields(j,v) for j,v in
                         guide.get('saved_joint_limits',guide['joint_limits']).items()})
        joint_names = guide.get('joint_names',JOINT_NAMES)
        names = list(dict.fromkeys(name for name in (data.get('selected_joints') or []) if name in joint_names))
        active = data.get('selected','')
        if active in joint_names and active not in names:
            names.append(active)
        selection = tuple(reversed(names)) or ('',)
        selection_changed = selection != self._selection
        if selection_changed:
            while self.sections_layout.count():
                self.sections_layout.takeAt(0)
            for name in tuple(self.sections):
                if name not in selection:
                    section = self.sections.pop(name)
                    section.hide()
                    section.deleteLater()
            self.sections = {name:self.sections.get(name) or RigJointSection(self,name) for name in selection}
            for section in self.sections.values():
                self.sections_layout.addWidget(section)
                section.show()
            self._selection = selection
            self.content.setMinimumHeight(len(selection)*JOINT_SECTION_HEIGHT+(len(selection)-1)*SECTION_SPACING)
        labels = dict(guide['labels'] if labels is None else labels)
        labels[active] = detail
        for name,section in self.sections.items():
            section.sync(guide,labels.get(name,'No editable limits for this joint.'))
        title = guide.get('title','Preview Rig')+(f' · {len(names)} joints' if len(names)>1 else '')
        if self.title.text() != title:
            self.title.setText(title)
        if guide_changed or selection_changed:
            self.scroll.verticalScrollBar().setValue(0)
        self._update_status()

    def preferred_height(self, width=PANEL_WIDTH):
        self.ensurePolished()
        visible = min(2,max(1,len(self.sections)))
        cards = visible*JOINT_SECTION_HEIGHT+(visible-1)*SECTION_SPACING
        layout = self.layout()
        margins,frame = layout.contentsMargins(),self.contentsMargins()
        inner_width = max(1,width-margins.left()-margins.right()-frame.left()-frame.right())
        chrome = margins.top()+margins.bottom()+frame.top()+frame.bottom()+layout.spacing()*(layout.count()-1)
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is self.scroll:
                continue
            wrapped = item.heightForWidth(inner_width)
            chrome += max(item.minimumSize().height(),wrapped if wrapped >= 0 else item.sizeHint().height())
        # Account for font/style wrapping without making joint readouts resize
        # the panel. One or two complete cards must fit whenever the viewport can.
        return max(PANEL_HEIGHT+(visible-1)*(JOINT_SECTION_HEIGHT+SECTION_SPACING),cards+chrome+4)

    def place(self):
        if self.data is None:
            return
        renderer = self.renderer
        x,y,w,h = self.data['rig_viewport']
        right, bottom = min(x+w,renderer.width()), y+h
        overlay_height = getattr(renderer,'_bottom_overlay_height',None)
        if callable(overlay_height):
            bottom = min(bottom,renderer.height()-float(overlay_height() or 0.))
        width = max(1,min(PANEL_WIDTH,int(right-x)-24))
        available = max(1,int(bottom-y)-24)
        self.setMaximumHeight(available)
        height = min(self.preferred_height(width),available)
        rect = QtCore.QRect(int(max(x+12,right-width-12)),int(max(y+12,bottom-height-12)),width,height)
        if self.geometry() != rect:
            self.setGeometry(rect)

    def _update_status(self):
        guide = self.data['rig_guide']
        dirty = (self._snapshot() != guide['_saved_limit_fields'] or guide['volumes'] != guide['_saved_volumes']
                 or (callable(guide.get('save_settings')) and guide['collisions'] != guide['_saved_collisions']))
        text = self._error or ('Unsaved changes' if dirty else 'Preview Rig settings')
        if self.status.text() != text:
            self.status.setText(text)

    def _apply_limit(self, joint, limit):
        from echograph.motion.rig_preview import build_guide
        guide = self.data['rig_guide']
        guide['joint_limits'][joint] = limit
        guide['limits_revision'] = guide.get('limits_revision',0)+1
        guide.pop('_posed_shapes',None)
        _,base = build_guide(guide['offsets'],joint_limits=guide['joint_limits'],volumes=guide['volumes'],
                           names=guide.get('joint_names',JOINT_NAMES),parents=guide.get('parents',PARENTS),end_sites=guide.get('end_sites'))
        guide.update(shapes=base['shapes'],labels=base['labels'])
        self._error = ''
        try:
            self._constrain_pose(joint)
        except Exception as exc:
            self._error = 'Could not update pose: '+str(exc)
        self.sync(self.data,guide['labels'].get(self.data.get('selected',''),''))
        self.renderer.update()

    def _apply_volume(self, name, radii):
        guide = self.data['rig_guide']
        guide['volumes'][name] = volume_settings({name:radii})[name]
        guide['limits_revision'] = guide.get('limits_revision',0)+1
        guide.pop('_posed_shapes',None)
        from echograph.motion.rig_preview import build_guide
        _,base = build_guide(guide['offsets'],joint_limits=guide['joint_limits'],volumes=guide['volumes'],
                           names=guide.get('joint_names',JOINT_NAMES),parents=guide.get('parents',PARENTS),end_sites=guide.get('end_sites'))
        guide.update(shapes=base['shapes'],labels=base['labels'])
        self._error = ''
        self.sync(self.data,guide['labels'].get(self.data.get('selected',''),''))
        self.renderer.update()

    def _collision_toggled(self, enabled):
        if self.data:
            self.data['rig_guide']['collisions'] = bool(enabled)
            self._error = ''
            self._update_status()
            self.renderer.update()

    def _constrain_pose(self, joint):
        context = self.data.get('rig_context')
        if not context:
            return
        renderer = self.renderer
        name = self.data['rig_guide'].get('joint_names',JOINT_NAMES)[joint]
        token = renderer._mgl_scene_skeleton_joint_owner_token(self.data['owner'],name)
        values = renderer._mgl_scene_skeleton_joint_values(token)
        keys = {f:dict(row) for f,row in (context.get('_scene_skeleton_joint_overrides') or {}).get(name,{}).items()}
        fps = getattr(renderer,'_timeline_fps',20)
        if name == self.data.get('selected'):
            if keys:
                renderer._mgl_scene_skeleton_apply_timeline_keys(token,keys,fps)
            if values is not None:
                renderer._set_owner_rot_deg(token,values[1],False)
        elif values is not None:
            # Editing another card must not replace the active joint's timeline
            # or move its gizmo. Author only this joint's pose directly.
            frame = max(0,int(round(renderer._timeline_current_frame())))
            entry = dict(keys.get(frame,{}))
            mask = list(entry.get('axis_mask',[bool(entry.get('xyz'))]*3+[False]*3))
            entry.update(rxyz=list(values[1]),axis_mask=mask[:3]+[True]*3,scene_skeleton_authored=True)
            map_frame = getattr(renderer,'_timeline_owner_source_frame_from_timeline_frame',None)
            source = map_frame(self.data['owner'],float(frame),allow_owner_key_mode=True) if callable(map_frame) else float(frame)
            if source is not None:
                entry['source_frame'] = max(0.,float(source))
            keys[frame] = entry
            renderer._mgl_scene_skeleton_apply_timeline_keys(token,keys,fps)

    def save(self):
        if not self.data:
            return
        for section in self.sections.values():
            for spin in [*section.fields.values(),*section.volume_fields]:
                spin.interpretText()
        try:
            guide = self.data['rig_guide']
            callback = guide.get('save_settings')
            if callable(callback):
                callback(guide)
            else:
                save_preview_limits(guide['joint_limits'],volumes=guide['volumes'],names=guide.get('joint_names',JOINT_NAMES))
            guide['_saved_volumes'] = {n:list(r) for n,r in guide['volumes'].items()}
            guide['_saved_collisions'] = guide['collisions']
            self.data['rig_guide']['_saved_limit_fields'] = self._snapshot()
            self.data['rig_guide']['saved_joint_limits'] = {j:dict(v) for j,v in self.data['rig_guide']['joint_limits'].items()}
            self._error = 'Saved '+guide.get('title','Preview Rig')+' settings'
        except (OSError,ValueError,TypeError) as exc:
            self._error = 'Could not save: '+str(exc)
        self._update_status()

    # Blank panel areas must not pass input into joint selection or the camera.
    def mousePressEvent(self, event): event.accept()
    def mouseReleaseEvent(self, event): event.accept()
    def mouseDoubleClickEvent(self, event): event.accept()
    def mouseMoveEvent(self, event): event.accept()
    def wheelEvent(self, event): event.accept()


def show_rig_panel(renderer, data, detail, *, labels=None):
    """Use native controls on a real viewport; image-only renders use text."""
    if not isinstance(renderer,QtWidgets.QWidget):
        return False
    panel = getattr(renderer,'_priormdm_rig_panel',None)
    if panel is None:
        panel = RigPreviewPanel(renderer)
        renderer._priormdm_rig_panel = panel
    panel.sync(data,detail,labels=labels)
    panel.place()
    if panel.isHidden():
        panel.show()
        panel.raise_()
    return True
