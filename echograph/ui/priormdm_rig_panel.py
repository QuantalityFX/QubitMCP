"""Interactive limit editor embedded in the Preview Rig viewport."""
from pathlib import Path
from html import escape

from PySide6 import QtCore, QtGui, QtWidgets

from echograph.motion.conversion import JOINT_NAMES
from echograph.motion.rig_limits import PREVIEW_JOINT_LIMITS, preview_limit_fields, preview_limit_from_fields
from echograph.motion.rig_preview_settings import save_preview_limits

PANEL_WIDTH = 320
PANEL_HEIGHT = 290


class RigPreviewPanel(QtWidgets.QFrame):
    def __init__(self, renderer):
        super().__init__(renderer)
        self.renderer = renderer
        self.data = None
        self._binding = None
        self._error = ''
        self.setObjectName('PreviewRigPanel')
        self.setAttribute(QtCore.Qt.WA_StyledBackground,True)
        self.setStyleSheet('''
            QFrame#PreviewRigPanel { background:#0f172a; border:1px solid #334155; border-radius:6px; }
            QScrollArea, QWidget#PreviewRigContent { background:transparent; border:0; }
            QLabel { color:#e2e8f0; font-size:11px; }
            QLabel#PreviewRigTitle { font-size:13px; font-weight:600; }
            QDoubleSpinBox { color:#e2e8f0; background:#1e293b; border:1px solid #475569;
                             border-radius:3px; padding:3px; min-height:20px; }
            QDoubleSpinBox:disabled { color:#64748b; border-color:#334155; }
            QToolButton { background:#273548; border:1px solid #475569; border-radius:4px; padding:3px; }
            QToolButton:hover { background:#3b4e67; }
            QToolButton:disabled { background:#182131; border-color:#263246; }
        ''')
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(1,1,1,1)
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.content = QtWidgets.QWidget()
        self.content.setObjectName('PreviewRigContent')
        layout = QtWidgets.QVBoxLayout(self.content)
        layout.setContentsMargins(10,8,10,8)
        layout.setSpacing(6)
        layout.setAlignment(QtCore.Qt.AlignTop)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel('Preview Rig')
        self.title.setObjectName('PreviewRigTitle')
        self.title.setTextFormat(QtCore.Qt.RichText)
        header.addWidget(self.title,1)
        icons = Path(__file__).resolve().parents[2]/'icons'
        self.reset_button = self._button(icons/'Refresh_Icon.png','Reset selected joint to defaults')
        self.save_button = self._button(icons/'save_preset_icon.png','Save all Preview Rig joint limits to application settings')
        self.reset_button.clicked.connect(self.reset_joint)
        self.save_button.clicked.connect(self.save)
        header.addWidget(self.reset_button)
        header.addWidget(self.save_button)
        layout.addLayout(header)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.detail.ensurePolished()
        # Reserve readout space so different names/ranges cannot move the rows.
        self.detail.setFixedHeight(self.detail.fontMetrics().lineSpacing()*4)
        layout.addWidget(self.detail)
        self.legend = QtWidgets.QLabel('Gold: bend  ·  Purple: twist  ·  Red: at limit')
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setAlignment(QtCore.Qt.AlignLeft)
        grid.setColumnMinimumWidth(0,64)
        grid.addWidget(QtWidgets.QLabel('Minimum'),0,1)
        grid.addWidget(QtWidgets.QLabel('Maximum'),0,2)
        self.bend_label = QtWidgets.QLabel('Bend cone')
        self.bend_label.setToolTip('Angle away from the resting bone. A positive minimum excludes the inner cone.')
        grid.addWidget(self.bend_label,1,0)
        grid.addWidget(QtWidgets.QLabel('Twist circle'),2,0)
        self.fields = {}
        for row,kind in ((1,'bend'),(2,'twist')):
            for column,end in ((1,'min'),(2,'max')):
                key = kind+'_'+end
                spin = QtWidgets.QDoubleSpinBox()
                spin.setObjectName('PreviewRig_'+key)
                spin.setAccessibleName(kind.title()+' '+end+' degrees')
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
        note = QtWidgets.QLabel('Changes apply immediately. Save to reuse in future previews.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        scroll.setWidget(self.content)
        outer.addWidget(scroll)

    def _button(self, path, tooltip):
        button = QtWidgets.QToolButton()
        button.setIcon(QtGui.QIcon(str(path)))
        button.setIconSize(QtCore.QSize(18,18))
        button.setFixedSize(28,28)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFocusPolicy(QtCore.Qt.StrongFocus)
        return button

    def _joint(self):
        name = (self.data or {}).get('selected','')
        return JOINT_NAMES.index(name) if name in JOINT_NAMES else None

    def _snapshot(self):
        return {j:preview_limit_fields(j,limit) for j,limit in self.data['rig_guide']['joint_limits'].items()}

    def sync(self, data, detail):
        if self.data is None or self.data.get('rig_guide') is not data['rig_guide']:
            self._error = ''
        self.data = data
        guide = data['rig_guide']
        guide.setdefault('joint_limits',{j:dict(v) for j,v in PREVIEW_JOINT_LIMITS.items()})
        guide.setdefault('_saved_limit_fields',{j:preview_limit_fields(j,v) for j,v in
                         guide.get('saved_joint_limits',guide['joint_limits']).items()})
        joint = self._joint()
        editable = joint in guide['joint_limits']
        binding = (id(guide),joint,guide.get('limits_revision',0))
        if binding != self._binding:
            self._binding = binding
            limit = guide['joint_limits'].get(joint,{})
            values = preview_limit_fields(joint,limit) if editable else dict.fromkeys(self.fields,0.)
            hinge = 'flex' in limit
            self.bend_label.setText('Hinge bend' if hinge else 'Bend cone')
            for key,spin in self.fields.items():
                blocker = QtCore.QSignalBlocker(spin)
                spin.setRange(0. if key.startswith('bend') else -179.,
                              (179. if hinge else 89.) if key.startswith('bend') else 179.)
                spin.setValue(values[key])
                spin.setEnabled(editable and (not hinge or key.startswith('bend')))
                del blocker
            self.reset_button.setEnabled(editable)
        selected = data.get('selected','')
        title = ('Preview Rig · <span style="color:#7dd3fc">'+escape(selected.replace('_',' ').title())+'</span>'
                 if selected else 'Preview Rig')
        if self.title.text() != title:
            self.title.setText(title)
        if selected and detail.startswith(selected+': '):
            detail = detail[len(selected)+2:]
        if self.detail.text() != detail:
            self.detail.setText(detail)
        self._update_status()

    def _update_status(self):
        dirty = self._snapshot() != self.data['rig_guide']['_saved_limit_fields']
        text = self._error or ('Unsaved changes' if dirty else 'Preview Rig settings')
        if self.status.text() != text:
            self.status.setText(text)

    def _edited(self, key, value):
        joint = self._joint()
        if joint not in PREVIEW_JOINT_LIMITS:
            return
        values = {k:spin.value() for k,spin in self.fields.items()}
        kind,end = key.split('_')
        other = kind+('_max' if end=='min' else '_min')
        if values[kind+'_min'] > values[kind+'_max']:
            values[other] = value
        self._apply_limit(joint,preview_limit_from_fields(joint,values))

    def _apply_limit(self, joint, limit):
        from echograph.motion.rig_preview import build_guide
        guide = self.data['rig_guide']
        guide['joint_limits'][joint] = limit
        guide['limits_revision'] = guide.get('limits_revision',0)+1
        guide.pop('_posed_shapes',None)
        _,base = build_guide(guide['offsets'],joint_limits=guide['joint_limits'])
        guide.update(shapes=base['shapes'],labels=base['labels'])
        self._error = ''
        try:
            self._constrain_pose(joint)
        except Exception as exc:
            self._error = 'Could not update pose: '+str(exc)
        self.sync(self.data,guide['labels'].get(self.data.get('selected',''),''))
        self.renderer.update()

    def _constrain_pose(self, joint):
        context = self.data.get('rig_context')
        if not context:
            return
        renderer = self.renderer
        token = renderer._mgl_scene_skeleton_joint_owner_token(self.data['owner'],JOINT_NAMES[joint])
        values = renderer._mgl_scene_skeleton_joint_values(token)
        keys = (context.get('_scene_skeleton_joint_overrides') or {}).get(JOINT_NAMES[joint])
        if keys:
            renderer._mgl_scene_skeleton_apply_timeline_keys(token,keys,getattr(renderer,'_timeline_fps',20))
        if values is not None:
            # Apply even an unkeyed rest pose when a new minimum excludes it.
            renderer._set_owner_rot_deg(token,values[1],False)

    def reset_joint(self):
        joint = self._joint()
        if joint in PREVIEW_JOINT_LIMITS:
            self._apply_limit(joint,preview_limit_from_fields(joint,{}))

    def save(self):
        if not self.data:
            return
        for spin in self.fields.values():
            spin.interpretText()
        try:
            save_preview_limits(self.data['rig_guide']['joint_limits'])
            self.data['rig_guide']['_saved_limit_fields'] = self._snapshot()
            self.data['rig_guide']['saved_joint_limits'] = {j:dict(v) for j,v in self.data['rig_guide']['joint_limits'].items()}
            self._error = 'Saved Preview Rig settings'
        except (OSError,ValueError,TypeError) as exc:
            self._error = 'Could not save: '+str(exc)
        self._update_status()

    # Blank panel areas must not pass clicks, wheels, or double-clicks into
    # joint selection or the camera. Editors/buttons retain their own events.
    def mousePressEvent(self, event): event.accept()
    def mouseReleaseEvent(self, event): event.accept()
    def mouseDoubleClickEvent(self, event): event.accept()
    def mouseMoveEvent(self, event): event.accept()
    def wheelEvent(self, event): event.accept()


def show_rig_panel(renderer, data, detail):
    """Use native controls on a real viewport; image-only renders use text."""
    if not isinstance(renderer,QtWidgets.QWidget):
        return False
    panel = getattr(renderer,'_priormdm_rig_panel',None)
    if panel is None:
        panel = RigPreviewPanel(renderer)
        renderer._priormdm_rig_panel = panel
    panel.sync(data,detail)
    x,y,w,h = data['rig_viewport']
    bottom = y+h
    overlay_height = getattr(renderer,'_bottom_overlay_height',None)
    if callable(overlay_height):
        bottom = min(bottom,renderer.height()-float(overlay_height() or 0.))
    width = max(1,min(PANEL_WIDTH,int(w)-24))
    height = max(1,min(PANEL_HEIGHT,int(bottom-y)-24))
    panel.setGeometry(int(max(x+12,x+w-width-12)),int(max(y+12,bottom-height-12)),width,height)
    if panel.isHidden():
        panel.show()
        panel.raise_()
    return True
