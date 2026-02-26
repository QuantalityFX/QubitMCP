from __future__ import annotations

from .gl_view_timeline_io import GraphGLTimelineIOMixin
from .gl_view_timeline_widgets import GraphGLTimelineWidgetsMixin
from .gl_view_timeline_model import GraphGLTimelineModelMixin


class GraphGLTimelineMixin(GraphGLTimelineWidgetsMixin, GraphGLTimelineModelMixin, GraphGLTimelineIOMixin):
    pass
