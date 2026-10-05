"""Open a PriorMDM rest-rig guide in the existing application viewport."""
from pathlib import Path
from uuid import uuid4
from PySide6 import QtWidgets


def open_rig_preview(node, scene, parent=None):
    from .spec import value
    from echograph.motion.rig_preview import create_rig_asset
    views = scene.views() if scene is not None and hasattr(scene,'views') else []
    windows = [v.window() for v in views]
    widget = parent
    while widget is not None:
        windows.append(widget)
        widget = widget.parentWidget()
    window = next((w for w in windows if callable(getattr(w,'open_scene_assets',None))),None)
    if window is None:
        raise ValueError('Open Preview Rig from a graph with a 3D viewport.')
    directory = Path(__file__).resolve().parents[2]/'logs'/'motion'/'rig_previews'/uuid4().hex[:12]
    owner = f'{node.name}_RigPreview'
    asset = create_rig_asset(value(node,'preview_profile'),value(node,'last_archive'),directory,owner,
                            collisions=value(node,'rig_show_collisions') != 'false',
                            limits=value(node,'rig_show_limits') != 'false')
    if window.open_scene_assets([asset],frame=True) is False:
        raise RuntimeError('The viewport could not open the rig preview.')
    view = getattr(window,'gl_view',None)
    renderer = getattr(view,'_mgl_renderer',None) or view
    request = getattr(renderer,'_mgl_scene_skeleton_request_active',None)
    if callable(request):
        request(owner)
    if view is not None:
        view.update()
    return asset


def preview_from_node(item):
    try:
        open_rig_preview(item.model,item.scene())
    except Exception as exc:
        views = item.scene().views() if item.scene() else []
        QtWidgets.QMessageBox.warning(views[0].window() if views else None,'PriorMDM Preview Rig',str(exc))
