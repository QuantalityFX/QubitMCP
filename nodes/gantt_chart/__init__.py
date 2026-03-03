from .spec import GANTT_CHART_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("gantt_chart", GANTT_CHART_SPEC)
    _core.register_spec("gantt chart", GANTT_CHART_SPEC)
    _core.register_spec("gant_chart", GANTT_CHART_SPEC)
    _core.register_spec("gant chart", GANTT_CHART_SPEC)
