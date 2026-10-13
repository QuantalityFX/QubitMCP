"""Shared elastic edge controls for the node and full pattern editor."""
from PySide6 import QtCore, QtWidgets

from echograph.patterns.elastic import DEFAULT_REST_RATIO, DEFAULT_STRENGTH, elastic_for_edge


class ElasticEdgeControls(QtWidgets.QWidget):
    edited = QtCore.Signal(bool, float, float)

    def __init__(self, parent=None, *, compact=True):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self) if compact else QtWidgets.QFormLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.enabled = QtWidgets.QCheckBox("Elastic")
        self.strength = QtWidgets.QDoubleSpinBox()
        self.strength.setRange(1, 1000000)
        self.strength.setDecimals(1)
        self.strength.setValue(DEFAULT_STRENGTH)
        self.strength.setSuffix(" N/m")
        self.strength.setToolTip("Elastic strength. Higher values pull harder and resist stretching.")
        self.rest_length = QtWidgets.QDoubleSpinBox()
        self.rest_length.setRange(1, 100)
        self.rest_length.setDecimals(1)
        self.rest_length.setValue(DEFAULT_REST_RATIO * 100)
        self.rest_length.setSuffix(" %")
        self.rest_length.setToolTip("Relaxed length as a percentage of the pattern edge. 80% tightens by 20%.")
        if compact:
            row.addWidget(self.enabled)
            row.addWidget(QtWidgets.QLabel("Strength"))
            row.addWidget(self.strength)
            row.addWidget(QtWidgets.QLabel("Rest length"))
            row.addWidget(self.rest_length)
        else:
            row.addRow(self.enabled)
            row.addRow("Strength", self.strength)
            row.addRow("Rest length", self.rest_length)
            self.setMaximumWidth(330)
        for spin in (self.strength, self.rest_length):
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._edited)
        self.enabled.toggled.connect(self._edited)
        self.setEnabled(False)

    def _edited(self, *_args):
        if self.sender() is self.enabled or self.enabled.isChecked():
            self.edited.emit(self.enabled.isChecked(), self.strength.value(), self.rest_length.value() / 100)

    def show_selection(self, document, selection):
        available = {(piece.id, edge.id) for piece in document.pieces for edge in piece.segments}
        selection = sorted(set(selection) & available)
        blockers = [QtCore.QSignalBlocker(widget) for widget in
                    (self.enabled, self.strength, self.rest_length)]
        self.setEnabled(bool(selection))
        rows = [elastic_for_edge(document, *edge) for edge in selection]
        self.enabled.setChecked(bool(rows) and all(row is not None for row in rows))
        first = next((row for row in rows if row is not None), None)
        if first:
            self.strength.setValue(first["strength"])
            self.rest_length.setValue(first["rest_ratio"] * 100)
        del blockers
