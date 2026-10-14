"""Translucent, camera-projected rig guides, drawn behind joint pick handles."""
import math
import numpy as np
from PySide6 import QtCore, QtGui


def pick_guide_joint(data, handles, px, py, dpr):
    """Small screen-space targets instead of body-sized world-space spheres."""
    if not isinstance(data,dict):
        return None
    x,y = px/max(dpr,1e-6),py/max(dpr,1e-6)
    candidates = [(float(p['x']-x)**2+float(p['y']-y)**2,p) for p in data.get('points',[]) if isinstance(p,dict)]
    candidates = [(distance,p) for distance,p in candidates if distance <= 9.**2 and -1 <= p.get('z',0) <= 1]
    if not candidates:
        guide = data.get('rig_guide') or {}
        if not guide.get('collisions'):
            return None
        volumes = [(depth,name) for poly,depth,name in guide.get('_pick_volumes',[])
                   if poly.containsPoint(QtCore.QPointF(x,y),QtCore.Qt.OddEvenFill)]
        if not volumes:
            return None
        _,name = min(volumes)
        return next((dict(h) for h in handles if h.get('name')==name),None)
    _,point = min(candidates,key=lambda pair:(pair[0],pair[1].get('z',0)))
    return next((dict(h) for h in handles if h.get('name')==point.get('name')),None)


def _draw_guide_panel(renderer, painter, viewport, lines):
    """Rasterize text like the viewport status panel, avoiding GL glyph atlases."""
    x,y,w,h = viewport
    font = QtGui.QFont(painter.font())
    font.setPointSize(9)
    metrics = QtGui.QFontMetrics(font)
    pad, margin = 8, 12
    panel_width = max(1, min(w-2*margin, max(metrics.horizontalAdvance(s) for s in lines)+2*pad))
    panel_height = len(lines)*metrics.height() + max(0,len(lines)-1)*2 + 2*pad
    dpr = max(1., float(painter.device().devicePixelRatioF()))
    key = (tuple(lines), font.toString(), panel_width, panel_height, dpr)
    cached = getattr(renderer, '_priormdm_guide_panel_cache', None)
    if cached is None or cached[0] != key:
        image = QtGui.QImage(max(1,math.ceil(panel_width*dpr)), max(1,math.ceil(panel_height*dpr)),
                             QtGui.QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QtCore.Qt.transparent)
        ip = QtGui.QPainter(image)
        try:
            ip.setRenderHint(QtGui.QPainter.Antialiasing,True)
            ip.setRenderHint(QtGui.QPainter.TextAntialiasing,True)
            ip.setFont(font)
            ip.setPen(QtGui.QColor(51,65,85,160))
            ip.setBrush(QtGui.QColor(15,23,42,180))
            ip.drawRoundedRect(QtCore.QRectF(0,0,panel_width,panel_height),6,6)
            ip.setPen(QtGui.QColor('#e2e8f0'))
            for i,line in enumerate(lines):
                ip.drawText(QtCore.QPointF(pad,pad+metrics.ascent()+i*(metrics.height()+2)),
                            metrics.elidedText(line,QtCore.Qt.ElideRight,max(1,int(panel_width-2*pad))))
        finally:
            ip.end()
        cached = (key,image)
        renderer._priormdm_guide_panel_cache = cached
    bottom = y+h
    overlay_height = getattr(renderer, '_bottom_overlay_height', None)
    view_height = getattr(renderer, 'height', None)
    if callable(overlay_height) and callable(view_height):
        # The timeline, audio panel and playback controls share this bottom
        # space. Clamp against widget coordinates so cropped viewports do not
        # subtract the same occupied space twice.
        bottom = min(bottom, float(view_height())-float(overlay_height() or 0.))
    painter.drawImage(QtCore.QPointF(x+w-panel_width-margin,max(y+margin,bottom-panel_height-margin)),cached[1])


def draw_rig_guide(renderer, painter, data):
    guide = data.get('rig_guide')
    if not guide:
        return
    context = data.get('rig_context')
    shapes = guide['shapes']
    labels = guide['labels']
    if context is not None:
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        from echograph.motion.rig_preview import build_guide
        from echograph.motion.conversion import JOINT_NAMES, PARENTS
        seconds = renderer._mgl_fbx_context_timeline_sample_seconds(context,data['owner'])
        evaluation = evaluate_rig_at_time(context['skeleton'],context.get('clip'),float(seconds),
                                          loop=bool(context.get('loop',False)))
        matrices = np.asarray(evaluation.global_matrices,dtype=float).reshape(-1,4,4)
        if 'joint_indices' in guide:
            matrices = matrices[guide['joint_indices']]
        pose_key = (matrices.tobytes(),guide.get('limits_revision',0))
        cached = guide.get('_posed_shapes')
        if cached is None or cached[0] != pose_key:
            _, posed = build_guide(guide['offsets'],matrices,joint_limits=guide.get('joint_limits'),volumes=guide.get('volumes'),
                                   names=guide.get('joint_names',JOINT_NAMES),parents=guide.get('parents',PARENTS),
                                   end_sites=guide.get('end_sites'))
            cached = (pose_key,posed)
            guide['_posed_shapes'] = cached
        shapes = cached[1]['shapes']
        labels = cached[1]['labels']
    matrix = data['rig_projection']
    x,y,w,h = data['rig_viewport']
    selected = data.get('selected','')
    selected_joints = set(data.get('selected_joints') or [])
    if selected:
        selected_joints.add(selected)
    colors = {'collision':(65,210,235), 'limit':(250,185,65), 'twist':(185,120,255),
              'pointer':(125,255,165),'limit_hit':(255,90,90)}

    def project_sets(sets):
        if not sets:
            return []
        rows = np.concatenate(sets)
        rows, _ = renderer._mgl_scene_skeleton_display_positions(data['owner'], rows)
        clip = np.column_stack((rows,np.ones(len(rows)))) @ matrix.T
        ndc = clip[:,:3]/np.maximum(clip[:,3,None],1e-8)
        invalid = (~np.isfinite(ndc).all(axis=1) | (clip[:,3]<=1e-8) | (abs(ndc[:,2])>1)).astype(int)
        invalid_sum = np.r_[0,np.cumsum(invalid)]
        depth_sum = np.r_[0,np.cumsum(ndc[:,2])]
        pixels = np.column_stack((x+(ndc[:,0]+1)*w/2,y+(1-ndc[:,1])*h/2))
        qt_points = [QtCore.QPointF(float(px),float(py)) for px,py in pixels]
        result, start = [], 0
        for vertices in sets:
            end = start+len(vertices)
            valid = invalid_sum[end] == invalid_sum[start]
            poly = QtGui.QPolygonF(qt_points[start:end]) if valid else None
            result.append((poly,float((depth_sum[end]-depth_sum[start])/len(vertices))) if valid else None)
            start = end
        return result

    painter.save()
    try:
        painter.setClipRect(QtCore.QRectF(x,y,w,h),QtCore.Qt.IntersectClip)
        painter.setRenderHint(QtGui.QPainter.Antialiasing,True)
        faces, wires = [], []
        guide['_pick_volumes'] = []
        for shape in shapes:
            kind = shape['kind']
            if kind == 'collision' and not guide['collisions']:
                continue
            if kind != 'collision' and (not guide['limits'] or (selected_joints and shape['joint'] not in selected_joints)):
                continue
            projected_sets = project_sets(shape['faces']+shape['wires'])
            for projected in projected_sets[:len(shape['faces'])]:
                if projected:
                    poly, depth = projected
                    faces.append((depth,poly,kind))
                    if kind == 'collision':
                        guide['_pick_volumes'].append((poly,depth,shape['joint']))
            for projected in projected_sets[len(shape['faces']):]:
                if projected:
                    wires.append((projected[0],kind))
        painter.setPen(QtCore.Qt.NoPen)
        for _,poly,kind in sorted(faces,key=lambda f:f[0],reverse=True):
            painter.setBrush(QtGui.QColor(*colors[kind],20 if kind=='collision' else 32))
            painter.drawPolygon(poly)
        painter.setBrush(QtCore.Qt.NoBrush)
        for poly,kind in sorted(wires,key=lambda row: row[1] in ('pointer','limit_hit')):
            is_pointer = kind in ('pointer','limit_hit')
            color = QtGui.QColor(*colors[kind],255 if is_pointer else 150)
            painter.setPen(QtGui.QPen(color,3. if is_pointer else 1.))
            painter.drawPolyline(poly)
            if is_pointer and len(poly):
                painter.setBrush(color)
                painter.drawEllipse(poly[-1],3.5,3.5)
                painter.setBrush(QtCore.Qt.NoBrush)
        title = f"PriorMDM rig · {guide['profile']} · pose inspection"
        detail = labels.get(selected,'Select a joint to isolate its limits and read the values.')
        lines = [title,'Cyan: volumes   Gold: bend   Purple: twist   Pointer: green / red at limit',*detail.splitlines(),
                 'Pose-editor limits; automatic generation cleanup is more conservative.']
        from .priormdm_rig_panel import show_rig_panel
        if not show_rig_panel(renderer,data,detail,labels=labels):
            _draw_guide_panel(renderer,painter,(x,y,w,h),lines)
    finally:
        painter.restore()
