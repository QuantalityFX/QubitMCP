from __future__ import annotations
import json
import datetime
from typing import Optional

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    from PySide2 import QtWidgets, QtCore  # type: ignore

try:
    from pymongo import MongoClient  # type: ignore
except Exception:
    MongoClient = None  # pymongo optional until installed

from nodes.core import Spec

DB_NAME = "my_database"
# The user’s Mongo layout: database = my_database, collection = EchoGragh
COLLECTION = "EchoGragh"


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
        setattr(model, "params", params)
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mongo_uri", "mongodb://localhost:27017")
    _ensure_param(node_item, "collection", COLLECTION)
    _ensure_param(node_item, "project", "")
    _ensure_param(node_item, "note", "")
    for port in ("mongo_uri", "collection", "project", "note"):
        if hasattr(node_item, "ensure_input"):
            node_item.ensure_input(port)


def _client(uri: str) -> MongoClient:
    if MongoClient is None:
        raise RuntimeError("pymongo not installed. Please install pymongo to use Database node.")
    return MongoClient(uri or "mongodb://localhost:27017")


def _list_collections(uri: str):
    if MongoClient is None:
        return []
    try:
        client = _client(uri)
        names = []
        for n in client[DB_NAME].list_collection_names():
            if isinstance(n, str) and n.strip():
                names.append(n)
        return sorted(set(names))
    except Exception:
        return []


def _list_projects(uri: str, collection: str | None = None):
    if MongoClient is None:
        return []
    try:
        client = _client(uri)
        coll_name = (collection or COLLECTION).strip() or COLLECTION
        coll = client[DB_NAME][coll_name]
        names = set()
        for n in coll.distinct("name"):
            if isinstance(n, str) and n.strip():
                names.add(n)
        for n in coll.distinct("project"):  # legacy docs
            if isinstance(n, str) and n.strip():
                names.add(n)
        return sorted(names)
    except Exception:
        return []


def _create_project(uri: str, collection: str, name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Project name cannot be empty."
    if MongoClient is None:
        return "Install pymongo to create projects."
    try:
        client = _client(uri)
        coll_name = (collection or COLLECTION).strip() or COLLECTION
        coll = client[DB_NAME][coll_name]
        if coll.find_one({"$or": [{"name": name}, {"project": name}]}):
            return f"Project '{name}' already exists."
        coll.insert_one({
            "name": name,
            "project": name,
            "type": "project",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "history": [],
        })
        return f"Created project '{name}'."
    except Exception as exc:
        return f"Failed to create: {exc}"


def _delete_project(uri: str, collection: str, name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Select a project to delete."
    if MongoClient is None:
        return "Install pymongo to delete projects."
    try:
        client = _client(uri)
        coll_name = (collection or COLLECTION).strip() or COLLECTION
        coll = client[DB_NAME][coll_name]
        res = coll.delete_many({"$or": [{"name": name}, {"project": name}]})
        if res.deleted_count:
            return f"Deleted project '{name}' (and related history)."
        return "Project not found."
    except Exception as exc:
        return f"Failed to delete: {exc}"


def _save_note(uri: str, collection: str, project: str, note_text: str) -> str:
    if MongoClient is None:
        return "Install pymongo to save notes."
    project = (project or "").strip()
    collection = (collection or COLLECTION).strip() or COLLECTION
    if not project:
        return "Select or enter a project name first."
    if not note_text:
        return "Note is empty; nothing to save."
    try:
        client = _client(uri)
        coll = client[DB_NAME][collection]
        filt = {"$or": [{"name": project}, {"project": project}]}
        coll.update_one(
            filt,
            {
                "$setOnInsert": {
                    "name": project,
                    "project": project,
                    "type": "project",
                    "created_at": datetime.datetime.utcnow().isoformat(),
                    "history": [],
                }
            },
            upsert=True,
        )
        entry = {
            "role": "note",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "content": note_text,
        }
        coll.update_one({"$or": [{"name": project}, {"project": project}]}, {"$push": {"history": entry}})
        return f"Saved note to project '{project}'."
    except Exception as exc:
        return f"Failed to save note: {exc}"


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if node is None or (getattr(node, "kind", "").lower() != "database"):
        return False

    def _param_val(key: str) -> str:
        for p in getattr(node, "params", []) or []:
            if (p.get("name") or "").strip().lower() == key:
                return p.get("value") or ""
        return ""

    def _set_param(key: str, value: str):
        params = list(getattr(node, "params", []) or [])
        found = False
        for p in params:
            if (p.get("name") or "").strip().lower() == key:
                p["value"] = value
                found = True
                break
        if not found:
            params.append({"name": key, "value": value})
        node.params = params
        sc = getattr(card, "_graph_scene", None)
        if sc:
            try:
                sc.set_node_params(node.name, params)
            except Exception:
                pass

    uri_edit = QtWidgets.QLineEdit(_param_val("mongo_uri") or "mongodb://localhost:27017")
    uri_edit.setPlaceholderText("mongodb://localhost:27017")
    uri_edit.editingFinished.connect(lambda: _set_param("mongo_uri", uri_edit.text()))

    collection_combo = QtWidgets.QComboBox()
    collection_combo.setEditable(False)

    collection_edit = QtWidgets.QLineEdit(_param_val("collection") or COLLECTION)
    collection_edit.setPlaceholderText("New or existing collection name")

    project_combo = QtWidgets.QComboBox()
    project_combo.setEditable(False)

    project_edit = QtWidgets.QLineEdit()
    project_edit.setPlaceholderText("New or existing project name")

    note_edit = QtWidgets.QLineEdit(_param_val("note"))
    note_edit.setPlaceholderText("Enter note text to save")
    note_edit.textEdited.connect(lambda txt: _set_param("note", txt))

    status_lbl = QtWidgets.QTextEdit()
    status_lbl.setReadOnly(True)
    status_lbl.setMaximumHeight(64)
    status_lbl.setStyleSheet("QTextEdit{color:#9ca3af;background:#0f1216;border:1px solid #334;border-radius:4px;padding:4px;}")
    status_lbl.setTextInteractionFlags(
        QtCore.Qt.TextSelectableByMouse
        | QtCore.Qt.TextSelectableByKeyboard
        | QtCore.Qt.LinksAccessibleByMouse
    )

    def _current_collection() -> str:
        text = collection_edit.text().strip()
        if text:
            return text
        combo_text = collection_combo.currentText()
        if combo_text and not combo_text.startswith("Select"):
            return combo_text
        return _param_val("collection") or COLLECTION

    def _refresh_collections():
        uri = uri_edit.text().strip()
        names = _list_collections(uri)
        collection_combo.clear()
        collection_combo.addItem("Select collection...")
        for n in names:
            collection_combo.addItem(n)
        current = _param_val("collection")
        if current:
            idx = collection_combo.findText(current)
            if idx >= 0:
                collection_combo.setCurrentIndex(idx)
        if not collection_edit.text().strip() and current:
            collection_edit.setText(current)
        return names

    def _refresh_projects():
        uri = uri_edit.text().strip()
        collection = _current_collection()
        names = _list_projects(uri, collection)
        project_combo.clear()
        project_combo.addItem("Select project...")
        for n in names:
            project_combo.addItem(n)
        current = _param_val("project")
        if current:
            idx = project_combo.findText(current)
            if idx >= 0:
                project_combo.setCurrentIndex(idx)
        return names

    def _refresh_list():
        _refresh_collections()
        return _refresh_projects()

    def _select_collection_from_combo(idx: int):
        if idx <= 0:
            return
        name = collection_combo.currentText()
        if not name:
            return
        collection_edit.setText(name)
        _set_param("collection", name)
        status_lbl.setText(f"Using collection '{name}'.")
        _refresh_projects()

    def _collection_edit_finished():
        name = collection_edit.text().strip() or COLLECTION
        _set_param("collection", name)
        _refresh_projects()

    def _select_from_combo(idx: int):
        if idx <= 0:
            return
        name = project_combo.currentText()
        _set_param("project", name)
        project_edit.setText(name)
        status_lbl.setText(f"Using project '{name}'.")

    def _create_clicked():
        name = project_edit.text().strip()
        uri = uri_edit.text().strip()
        collection = _current_collection()
        msg = _create_project(uri, collection, name)
        status_lbl.setText(msg)
        _refresh_list()
        if "Created" in msg or "exists" in msg:
            _set_param("project", name)
            _set_param("collection", collection)

    def _delete_clicked():
        name = project_combo.currentText()
        uri = uri_edit.text().strip()
        collection = _current_collection()
        msg = _delete_project(uri, collection, name)
        status_lbl.setText(msg)
        _refresh_list()

    def _save_note_clicked():
        uri = uri_edit.text().strip()
        collection = _current_collection()
        project_name = (project_edit.text().strip() or project_combo.currentText()).strip()
        note_text = note_edit.text()
        msg = _save_note(uri, collection, project_name, note_text)
        status_lbl.setText(msg)
        if msg.lower().startswith("saved note"):
            _set_param("project", project_name)
            _set_param("collection", collection)
            _set_param("note", note_text)

    refresh_btn = QtWidgets.QPushButton("Refresh")
    refresh_btn.clicked.connect(_refresh_list)

    create_btn = QtWidgets.QPushButton("Create/Use")
    create_btn.clicked.connect(_create_clicked)

    delete_btn = QtWidgets.QPushButton("Delete")
    delete_btn.clicked.connect(_delete_clicked)

    save_note_btn = QtWidgets.QPushButton("Save Note")
    save_note_btn.clicked.connect(_save_note_clicked)

    form = QtWidgets.QFormLayout()
    form.addRow("Mongo URI", uri_edit)
    form.addRow("Collections", collection_combo)
    form.addRow("Collection name", collection_edit)
    form.addRow("Projects", project_combo)
    form.addRow("Project name", project_edit)
    form.addRow("Note text", note_edit)

    btn_row = QtWidgets.QHBoxLayout()
    btn_row.addWidget(refresh_btn)
    btn_row.addWidget(create_btn)
    btn_row.addWidget(delete_btn)
    btn_row.addWidget(save_note_btn)
    btn_row.addStretch(1)

    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    v.addLayout(form)
    v.addLayout(btn_row)
    v.addWidget(status_lbl)
    footer_layout.addWidget(container)

    _refresh_list()
    collection_combo.currentIndexChanged.connect(_select_collection_from_combo)
    collection_edit.editingFinished.connect(_collection_edit_finished)
    project_combo.currentIndexChanged.connect(_select_from_combo)

    if MongoClient is None:
        status_lbl.setText("Install pymongo to enable Database node controls.")

    return True

DATABASE_SPEC = Spec(
    stripe_color="#16a34a",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)
