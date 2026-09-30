from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid

from echograph.model import GraphNode
from nodes.core import Spec
from nodes.mocap_import import spec as params
from .runtime import session_for, value, wrapper_for


def ensure_wrapper(item, *, force=False):
    scene = item.scene()
    if scene is None or not hasattr(scene, "_add_comment_group_from_data"):
        return
    if value(item.model, "wrapper_created", "0") == "1" and not force:
        return
    try:
        wrapper_for(item)
        params._set_param_value(item.model, "wrapper_created", "1")
        return
    except ValueError:
        pass
    from . import register
    register()
    pos = item.scenePos()
    name = scene._unique_node_name("For_Each_End", "for_each_end")
    end_pos = pos + QtCore.QPointF(920, 580)
    scene.add_node(GraphNode(name, kind="for_each_end", pos_xy=(end_pos.x(), end_pos.y())), end_pos)
    scene._add_comment_group_from_data({"title": "For Each", "body": "Drag nodes inside. Connect Render to End.",
        "members": [item.model.name, name], "color": "#7c3aed",
        "rect": [pos.x() - 30, pos.y() - 80, 1280, 900]})
    params._set_param_value(item.model, "wrapper_created", "1")
    scene.set_node_params(item.model.name, list(item.model.params), rebuild=False, emit=True)


def build_start(item):
    for name, default in (("source", ""), ("mode", "outputs"), ("loop_limit", "0"),
                          ("parameter", "output"), ("count", "1"), ("first", "1"), ("step", "1"),
                          ("iteration", "0"), ("wrapper_created", "0")):
        params._ensure_param(item, name, default)
    params._ensure_hidden_params(item.model, ["mode", "loop_limit", "parameter", "count", "first", "step", "iteration", "wrapper_created"])
    item.ensure_input("source")


def build_end(item):
    params._ensure_param(item, "render", "")
    params._ensure_param(item, "iteration", "0")
    params._ensure_hidden_params(item.model, ["iteration"])
    item.ensure_input("render")


class StartControls(QtWidgets.QWidget):
    def __init__(self, item):
        super().__init__()
        self.item = item
        self.session = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        form = QtWidgets.QFormLayout()
        self.mode = QtWidgets.QComboBox()
        for title, key in (("Collection outputs", "outputs"), ("Checked paths", "checked"), ("Parameter range", "parameter")):
            self.mode.addItem(title, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(value(item.model, "mode", "outputs"))))
        self.mode.setToolTip("Collection outputs follows Checked only. Checked paths always visits just checked files.")
        form.addRow("Iterate", self.mode)
        self.limit = QtWidgets.QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSpecialValueText("All")
        self.limit.setValue(int(value(item.model, "loop_limit", "0")))
        self.limit.setToolTip("0 / All visits every eligible input; a positive value caps the number of renders.")
        form.addRow("Loop Limit", self.limit)
        layout.addLayout(form)
        self.parameter_box = QtWidgets.QWidget()
        fields = QtWidgets.QFormLayout(self.parameter_box)
        fields.setContentsMargins(0, 0, 0, 0)
        self.parameter = QtWidgets.QLineEdit(value(item.model, "parameter", "output"))
        self.count = QtWidgets.QSpinBox()
        self.count.setRange(1, 1_000_000)
        self.count.setValue(int(value(item.model, "count", "1")))
        self.first, self.step = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for control, name in ((self.first, "first"), (self.step, "step")):
            control.setRange(-1_000_000, 1_000_000)
            control.setDecimals(4)
            control.setValue(float(value(item.model, name, "1")))
        for label, widget in (("Parameter", self.parameter), ("Count", self.count), ("First value", self.first), ("Step", self.step)):
            fields.addRow(label, widget)
        layout.addWidget(self.parameter_box)
        self.parameter_box.setVisible(self.mode.currentData() == "parameter")
        self.status = QtWidgets.QLabel("Start OUT → Retarget animation IN. Render OUT → End render IN.")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.setToolTip("Collection OUT → Start collection IN. Start OUT → Retarget animation IN.\nOUT always passes the active animation through; Run advances the collection after each render.")
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton("Run For Each")
        self.run_button.setStyleSheet("QPushButton { background:#2563eb; color:white; padding:6px; border-radius:4px; }")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.wrapper_button = QtWidgets.QPushButton("Create wrapper")
        self.wrapper_button.setToolTip("Recreate the wrapper if it was deleted.")
        layout.addWidget(self.wrapper_button)
        self.run_button.clicked.connect(self.run)
        self.stop_button.clicked.connect(lambda: self.session.stop() if self.session else None)
        self.wrapper_button.clicked.connect(lambda: ensure_wrapper(self.item, force=True))
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.limit.valueChanged.connect(lambda number: self.save("loop_limit", str(number)))
        self.parameter.editingFinished.connect(lambda: self.save("parameter", self.parameter.text().strip()))
        for control, name in ((self.count, "count"), (self.first, "first"), (self.step, "step")):
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda number, name=name: self.save(name, str(number)))
        QtCore.QTimer.singleShot(0, self.attach)

    def attach(self):
        if not isValid(self.item) or self.item.scene() is None:
            return
        ensure_wrapper(self.item)
        self.session = session_for(self.item)
        self.session.changed.connect(self.refresh)
        self.item.scene().paramChanged.connect(self.params_changed)
        self.refresh()

    def save(self, name, raw):
        params._set_param_value(self.item.model, name, raw)
        scene = self.item.scene()
        if scene is not None:
            scene.set_node_params(self.item.model.name, list(self.item.model.params), rebuild=False, emit=True)

    def mode_changed(self):
        self.save("mode", self.mode.currentData())
        self.parameter_box.setVisible(self.mode.currentData() == "parameter")
        fit_body(self)

    def params_changed(self, name, _params):
        if name != self.item.model.name:
            return
        with QtCore.QSignalBlocker(self.mode):
            self.mode.setCurrentIndex(max(0, self.mode.findData(value(self.item.model, "mode", "outputs"))))
        self.parameter_box.setVisible(self.mode.currentData() == "parameter")
        fit_body(self)

    def run(self):
        try:
            if self.session is None:
                self.attach()
            self.session.begin()
        except Exception as exc:
            self.status.setText(str(exc))
            fit_body(self)

    def refresh(self):
        if self.session is None:
            return
        running = self.session.running
        self.status.setText(self.session.status)
        if self.session.manifest_path:
            self.status.setToolTip(str(self.session.manifest_path))
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.mode.setEnabled(not running)
        self.limit.setEnabled(not running)
        self.parameter_box.setEnabled(not running)
        self.wrapper_button.setEnabled(not running)
        fit_body(self)


class EndControls(QtWidgets.QWidget):
    def __init__(self, item):
        super().__init__()
        self.item = item
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel("Connect Render here. The next iteration waits for completion.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def refresh(self):
        session = getattr(self.item.model, "_foreach_session", None)
        if session is not None and isValid(session):
            self.status.setText(session.status)
            fit_body(self)


def body_height(body):
    body.layout().invalidate()
    height = max(body.sizeHint().height(), body.minimumSizeHint().height())
    if body.layout().hasHeightForWidth():
        height = max(body.minimumSizeHint().height(), body.layout().totalHeightForWidth(body.width()))
    return height


def fit_body(body):
    """Keep the node border fitted to the controls that are currently visible."""
    body.updateGeometry()
    proxy = body.graphicsProxyWidget()
    if proxy is None:  # Info-card layouts manage their own widget height.
        return
    item = body.item
    height = body_height(body)
    bottom = proxy.pos().y() + height + 10
    if body.height() != height or item.height != bottom:
        item.prepareGeometryChange()
        body.setFixedHeight(height)
        proxy.resize(body.width(), height)
        item.height = bottom
        item.update()


def render_body(item, y_cursor):
    start = item.model.kind == "for_each"
    body = StartControls(item) if start else EndControls(item)
    body.ensurePolished()
    item.prepareGeometryChange()
    item.width = max(item.width, 340, body.minimumSizeHint().width() + 16)
    body.setFixedWidth(int(item.width - 16))
    height = body_height(body)
    body.setFixedHeight(height)
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(body)
    proxy.setPos(8, y_cursor)
    proxy.resize(item.width - 16, height)
    item._plugin_proxies.append(proxy)
    item.height = y_cursor + height + 10
    return item.height


def footer(card, layout):
    model = getattr(card, "_node_ref", None)
    scene = getattr(card, "_graph_scene", None)
    item = getattr(scene, "_node_items", {}).get(getattr(model, "name", ""))
    if item is None:
        return False
    layout.addWidget(StartControls(item) if model.kind == "for_each" else EndControls(item))
    return True


START_SPEC = Spec(stripe_color="#7c3aed", build_ports=build_start, render_node_body=render_body, augment_infocard_footer=footer)
END_SPEC = Spec(stripe_color="#7c3aed", build_ports=build_end, render_node_body=render_body, augment_infocard_footer=footer)
