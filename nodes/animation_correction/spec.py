"""Manual animation overrides bound to the incoming collection take."""
from pathlib import Path
from types import SimpleNamespace
import json

from PySide6 import QtCore, QtWidgets
from nodes.core import Spec
from nodes.mocap_import import spec as mocap
from echograph.motion import animation_corrections as corrections
from echograph.rigging.fbx_stage5_evaluator import evaluation_cache_token


def input_context(item, errors, warnings, *, _depth=0, _visited=None):
    from nodes.rig_limiter.spec import input_context as upstream
    context = upstream(item,errors,warnings,_depth=_depth,_visited=_visited)
    errors[:] = [message.replace('Rig Limiter:', 'Animation Correction:') for message in errors]
    if context is not None:
        path = Path(context.get('path') or '')
        if not path.is_file() or path.suffix.lower() not in ('.bvh','.fbx') or context.get('clip') is None:
            errors.append('Animation Correction needs an animated BVH or FBX input.')
            return None
    return context


def signature(model,clip,context=None):
    if context and context.get('kind') == 'rig_limiter':
        upstream = context['model']
        record = getattr(upstream,'_rig_limiter_disk_record',None)
        cached = getattr(upstream,'_rig_limiter_cache',None)
        if record and cached and cached[1][0] is clip:
            # A saved repair is re-ingested from BVH on reload. Its clip name
            # and floating-point keys differ from the in-memory repair.
            return record['options_sha'] + ':' + record['output_sha']
    token = evaluation_cache_token(clip)
    cached = getattr(model,'_animation_correction_signature',None)
    if cached is None or cached[0] != token:
        cached = (token,corrections.clip_signature(clip))
        model._animation_correction_signature = cached
    return cached[1]


def saved_document(item,context):
    document = corrections.read_document(context['path'])
    if document and document.get('base_clip_sha') != signature(item.model,context['clip'],context):
        return None
    return document


def resolve_context(item,errors,warnings,*,target=False,_depth=0,_visited=None):
    if getattr(item.model,'_animation_correction_resolving',False):
        errors.append('Animation Correction: animation input contains a cycle.')
        return None
    item.model._animation_correction_resolving = True
    try:
        context = input_context(item,errors,warnings,_depth=_depth,_visited=_visited)
        if context is None or errors:
            return None
        document = saved_document(item,context)
        token = (evaluation_cache_token(context['skeleton']),evaluation_cache_token(context['clip']),json.dumps(document,sort_keys=True))
        cached = getattr(item.model,'_animation_correction_clip',None)
        if cached is None or cached[0] != token:
            cached = (token,corrections.apply_document(context['skeleton'],context['clip'],document))
            item.model._animation_correction_clip = cached
        clip = cached[1]
        rig = dict(context.get('rig_context') or {})
        rig.update(skeleton=context['skeleton'],clip=clip,clips=[clip],loop=False,
                   source_format=Path(context['path']).suffix.lower().lstrip('.'),show_capture_joints=False,show_animated_joints=True)
        output = dict(context,name=item.model.name,kind='animation_correction',model=item.model,node_item=item,
                      clip=clip,clips=[clip],rig_context=rig,bind_result=None,animation_result=None)
        output.pop('preview_asset',None)
        return output
    except (OSError,ValueError,TypeError,KeyError) as exc:
        errors.append('Animation Correction: '+str(exc))
        return None
    finally:
        item.model._animation_correction_resolving = False


def asset_for_context(item,context,clip=None):
    from nodes.anim_retarget import spec as retarget
    clip = clip or context['clip']
    rig = dict(context.get('rig_context') or {})
    rig.update(skeleton=context['skeleton'],clip=clip,clips=[clip],loop=False,
               source_format=Path(context['path']).suffix.lower().lstrip('.'),show_capture_joints=False,show_animated_joints=True)
    output = dict(context,name=item.model.name,kind='animation_correction',model=item.model,node_item=item,
                  clip=clip,clips=[clip],rig_context=rig,bind_result=None,animation_result=None)
    output.pop('preview_asset',None)
    asset = retarget._preview_asset_for_context(output,owner=item.model.name,role='source',x_offset=0.,curve_thickness=2.)
    if asset is not None:
        asset.update(node=item.model.name,kind='animation_correction',source_kind='animation_correction',
                     fbx_rig_context=rig,wire_only=True)
    return asset


def build_scene_asset(item):
    errors,warnings = [],[]
    context = resolve_context(item,errors,warnings)
    return asset_for_context(item,context) if context is not None else None


def build_ports(item):
    mocap._ensure_param(item,'animation','')
    mocap._ensure_hidden_params(item.model,['animation'])
    item.model._named_inputs = ['animation']
    item._default_named_input = 'animation'
    item._show_default_input_with_named = False
    item.ensure_input('animation')
    item.ensure_output('animation')
    item._hide_default_output_with_named = True


class CorrectionControls(QtWidgets.QWidget):
    def __init__(self,item):
        super().__init__()
        self.item = item
        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(QtCore.Qt.AlignTop)
        button = QtWidgets.QPushButton('Preview / edit animation')
        self.preview_button = button
        button.setObjectName('AnimationCorrectionPreviewButton')
        # Rig Limiter fixes its action buttons to an ordinary button's height.
        button.setFixedHeight(button.sizeHint().height())
        button.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        button.clicked.connect(self.edit)
        layout.addWidget(button)
        self.status = QtWidgets.QLabel('Scrub the timeline. Click joints to rotate.\nDrag pelvis, chest, wrist / ankle cubes for IK. Edits save per source take.')
        self.status.setWordWrap(True)
        self.status.setFixedHeight(self.status.fontMetrics().lineSpacing()*4)
        layout.addWidget(self.status)

    def edit(self):
        from .editor import CorrectionSession
        try:
            errors,warnings = [],[]
            context = input_context(self.item,errors,warnings)
            if context is None or errors:
                raise ValueError('\n'.join(errors))
            window = next((view.window() for view in self.item.scene().views()
                           if callable(getattr(view.window(),'open_scene_assets',None))),None)
            if window is None:
                raise ValueError('Open Animation Correction from a graph with a 3D viewport.')
            previous = getattr(window.gl_view,'_animation_correction_session',None)
            if previous is not None:
                from shiboken6 import isValid
                if isValid(previous):
                    if previous.dirty and not previous.save():
                        raise ValueError('Save the current take before changing correction previews.')
                    previous.finish()
            session = CorrectionSession(self.item,context,window.gl_view)
            session.open(window)
        except (ValueError,RuntimeError,OSError,TypeError,KeyError) as exc:
            self.status.setText(str(exc))


def render_node_body(item,y_cursor):
    widget = CorrectionControls(item)
    widget.ensurePolished()
    item.prepareGeometryChange()
    item.width = max(item.width,widget.minimumSizeHint().width()+16,280)
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


class CorrectionHierarchy(QtWidgets.QWidget):
    """The Scene joint browser, embedded in the correction info card."""
    def __init__(self,item,controls):
        super().__init__()
        from echograph.ui.skeleton_joint_picker import JointTargetTree
        self.item,self.controls = item,controls
        self._binding = None
        self._session = None
        self._items = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setAlignment(QtCore.Qt.AlignTop)
        self.expand = QtWidgets.QToolButton()
        self.expand.setText('Rig hierarchy')
        self.expand.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.expand.setArrowType(QtCore.Qt.RightArrow)
        self.expand.setCheckable(True)
        self.expand.setAutoRaise(True)
        self.expand.setObjectName('AnimationCorrectionHierarchyButton')
        self.expand.toggled.connect(self.toggle)
        layout.addWidget(self.expand)
        self.tree = JointTargetTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(210)
        self.tree.setMaximumHeight(340)
        self.tree.setObjectName('AnimationCorrectionJointTree')
        self.tree.itemClicked.connect(self.select_joint)
        self.tree.hide()
        layout.addWidget(self.tree)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(lambda:self.refresh() if self.isVisible() else None)

    def toggle(self,expanded):
        self.expand.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self.tree.setVisible(expanded)
        if expanded:
            self.refresh()
            self.timer.start()
        else:
            self.timer.stop()

    def refresh(self):
        if not self.expand.isChecked():
            return
        errors = []
        context = input_context(self.item,errors,[])
        if context is None or errors:
            self.tree.clear()
            self._items = {}
            self._binding = None
            self.expand.setToolTip('\n'.join(errors))
            return
        binding = (str(Path(context['path']).resolve()),id(context['skeleton']))
        if binding != self._binding:
            self._binding = binding
            items = self.tree.set_skeleton(self.item.model.name,context['skeleton'])
            self._items = {row.text(0):row for row in items}
            self.tree.expandToDepth(0)
            self.expand.setText(f'Rig hierarchy ({len(items)} joints)')
        session = getattr(self.item.model,'_animation_correction_session',None)
        if session is not self._session:
            from shiboken6 import isValid
            if self._session is not None and isValid(self._session):
                self._session.selectionChanged.disconnect(self.sync_selection)
            self._session = session
            if session is not None:
                session.selectionChanged.connect(self.sync_selection)
        if session is not None:
            self.sync_selection(session.selected)

    def sync_selection(self,name):
        row = self._items.get(name)
        if row is not None:
            self.tree.setCurrentItem(row)
            parent = row.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()

    def select_joint(self,row,_column=0):
        session = getattr(self.item.model,'_animation_correction_session',None)
        # Opening the preview is the same action as its existing preview button.
        if session is None or str(session.path) != self._binding[0] or id(session.skeleton) != self._binding[1]:
            self.controls.edit()
            session = getattr(self.item.model,'_animation_correction_session',None)
        if session is not None:
            session.select_joint(row.text(0))
            self.refresh()


def augment_infocard_footer(card,layout):
    model = getattr(card,'_node_ref',None)
    if model is None or model.kind != 'animation_correction':
        return False
    scene = getattr(card,'_graph_scene',None)
    item = SimpleNamespace(model=model,scene=lambda:scene)
    controls = CorrectionControls(item)
    layout.addWidget(controls)
    layout.addWidget(CorrectionHierarchy(item,controls))
    return True


ANIMATION_CORRECTION_SPEC = Spec(stripe_color='#38bdf8',build_ports=build_ports,
    render_node_body=render_node_body,augment_infocard_footer=augment_infocard_footer)
