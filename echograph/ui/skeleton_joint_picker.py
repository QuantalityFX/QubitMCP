from __future__ import annotations

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.rigging.joint_targets import joint_target_name


class JointTargetTree(QtWidgets.QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabel("Joint")
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setDefaultDropAction(QtCore.Qt.CopyAction)
        self.setExpandsOnDoubleClick(False)

    def mimeTypes(self):
        return ["text/plain"]

    def mimeData(self, items):
        data = QtCore.QMimeData()
        if items:
            data.setText(str(items[0].data(0, QtCore.Qt.UserRole) or ""))
        return data

    def supportedDropActions(self):
        return QtCore.Qt.CopyAction

    def set_skeleton(self,owner,skeleton):
        """Populate the same joint hierarchy in dialogs and embedded cards."""
        self.clear()
        joints = list(getattr(skeleton,'joints',[]) or [])
        items = [QtWidgets.QTreeWidgetItem([joint.name]) for joint in joints]
        parents = [int(joint.parent_index) for joint in joints]
        for index,item in enumerate(items):
            target = joint_target_name(owner,joints[index].name)
            item.setData(0,QtCore.Qt.UserRole,target)
            item.setData(0,QtCore.Qt.UserRole+1,joints[index].name)
            item.setToolTip(0,target)
            ancestors,ancestor = {index},parents[index]
            while 0 <= ancestor < len(items) and ancestor not in ancestors:
                ancestors.add(ancestor)
                ancestor = parents[ancestor]
            if 0 <= parents[index] < len(items) and ancestor not in ancestors:
                items[parents[index]].addChild(item)
            else:
                self.addTopLevelItem(item)
        return items


class SkeletonJointPicker(QtWidgets.QDialog):
    def __init__(self, owner: str, skeleton=None, parent=None):
        super().__init__(parent)
        self.owner = str(owner).strip()
        self.setWindowTitle(f"Skeleton Joints — {self.owner}")
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        self.resize(420, 540)
        layout = QtWidgets.QVBoxLayout(self)
        help_text = QtWidgets.QLabel(
            "Drag a joint into a Look At target, or double-click to copy its target name."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter joints…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.tree = JointTargetTree(self)
        layout.addWidget(self.tree, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter_joints)
        self.tree.itemDoubleClicked.connect(self.copy_joint_target)
        self._copy_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Copy, self.tree)
        self._copy_shortcut.activated.connect(lambda: self.copy_joint_target(self.tree.currentItem()))
        self.set_skeleton(skeleton)

    def set_skeleton(self, skeleton):
        joints = list(getattr(skeleton, "joints", []) or [])
        self.tree.set_skeleton(self.owner,skeleton)
        self.tree.expandAll()
        self._filter_joints(self.search.text())
        self.status.setText(
            f"{len(joints)} joints. Copied names include the model to identify the correct rig."
            if joints else "No joints available. Rebuild the Scene view and reopen this list."
        )

    def _filter_joints(self, text):
        query = str(text).strip().casefold()

        def visit(item):
            child_matches = [visit(item.child(i)) for i in range(item.childCount())]
            visible = not query or query in item.text(0).casefold() or any(child_matches)
            item.setHidden(not visible)
            if query and any(child_matches):
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def copy_joint_target(self, item, _column=0):
        if item is None:
            return
        target = str(item.data(0, QtCore.Qt.UserRole) or "")
        if target:
            QtWidgets.QApplication.clipboard().setText(target)
            self.status.setText(f"Copied: {target}")
