"""Move complete motion folders into named archives without overwriting files."""
import copy
import json
from pathlib import Path
import shutil
import uuid

from PySide6 import QtWidgets

from . import approvals, review_data as data


def archive_rows(model, scene, selected, parent, name):
    from . import spec
    data.ensure_idle(scene)
    name = data.validate_folder_name(name)
    parent = Path(parent).expanduser().resolve()
    destination = parent / name
    if destination.exists() and (not destination.is_dir() or destination.is_symlink()):
        raise ValueError("Archive destination must be a regular folder.")
    rows = spec.entries(model)
    original_params = copy.deepcopy(model.params)
    selected = set(selected)
    if not selected or any(i < 0 or i >= len(rows) for i in selected):
        raise ValueError("Select paths to archive first.")
    base_file = approvals.metadata_path(scene)
    original_approvals = base_file.read_bytes() if base_file and base_file.exists() else None
    base = base_file.parent if base_file else None
    chosen = [copy.deepcopy(row) for i, row in enumerate(rows) if i in selected]
    sources = set()
    documents = set()
    for row in chosen:
        source = data.resolved_path(row["path"], base)
        if not source.is_file():
            raise ValueError(f"BVH is missing: {source}")
        sources.add(source.parent)
        render = row.get("render", {})
        if render.get("folder"):
            sources.add(data.resolved_path(render["folder"], base))
        for raw in render.get("manifests", []):
            path = data.resolved_path(raw, base)
            if path.is_file():
                documents.add(path)
                # Include every recorded take of the selected BVH, not only
                # the most recently assigned preview.
                for take in data.read_json(path).get("takes", []):
                    if take.get("source_path") and data.resolved_path(take["source_path"], base) == source:
                        if take.get("take_folder"):
                            sources.add(data.resolved_path(take["take_folder"], base))
    for folder in sources:
        if (not folder.is_dir() or folder == folder.parent or folder.is_symlink()
                or getattr(folder.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError(f"Cannot archive missing, root, or linked folder: {folder}")
        if base and base.resolve().is_relative_to(folder):
            raise ValueError("Cannot move the project folder itself. Place BVHs in their own run folders first.")
        if destination.is_relative_to(folder) or folder.is_relative_to(destination):
            raise ValueError("The archive must be separate from the source folders.")
    # Keep nested render folders inside their enclosing motion folder.
    sources = {p for p in sources if not any(p != other and p.is_relative_to(other) for other in sources)}
    # Shared runs can move, provided every open collection reference follows.
    models = {id(model): model}
    for item in getattr(scene, "_node_items", {}).values():
        if item.model.kind == "mocap_collection":
            models[id(item.model)] = item.model
    states = [(current, copy.deepcopy(current.params), spec.entries(current), spec.output_number(current))
              for current in models.values()]
    # Separate batches permit appending to existing archives without collisions.
    batch = destination / ("batch_" + uuid.uuid4().hex[:12])
    moves = [(old, batch / f"{i + 1:03d}_{old.name}") for i, old in enumerate(sorted(sources))]
    for old, _ in moves:
        documents.update(old.rglob("take.json"))
        documents.update(old.rglob("*_manifest.json"))
    manifest = destination / "archive.json"
    original_manifest = manifest.read_bytes() if manifest.exists() else None
    catalog = json.loads(original_manifest) if original_manifest else {"schema_version": 1, "entries": []}
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("entries"), list):
        raise ValueError(f"Invalid archive index: {manifest}")
    rewrites = []
    for path in documents:
        original = path.read_bytes()
        record = json.loads(original)
        rewritten = data.rewrite_record(record, moves, base)
        if "takes" in rewritten:
            rewritten["takes"] = [data.rewrite_record(take, moves, base) for take in rewritten["takes"]]
        rewrites.append((path, original, rewritten))
    for row in chosen:
        row["original_path"] = row["path"]
        row["path"] = data.remap_path(row["path"], moves, base)
        if row.get("render"):
            row["render"] = data.rewrite_record(row["render"], moves, base)
            row["render"]["manifests"] = [data.remap_path(p, moves, base) for p in row["render"].get("manifests", [])]
        catalog["entries"].append(row)
    completed, written = [], []
    destination.mkdir(parents=True, exist_ok=True)
    batch.mkdir(exist_ok=False)
    try:
        for old, new in moves:
            if new.exists():
                raise FileExistsError(str(new))
            shutil.move(str(old), str(new))
            completed.append((old, new))
        for old, original, record in rewrites:
            path = Path(data.remap_path(str(old), moves))
            written.append((path, original))
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary = batch / "archive-index.tmp"
        temporary.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(manifest)
        for current, _, current_rows, active in states:
            remaining = []
            for i, row in enumerate(current_rows):
                if current is model and i in selected:
                    continue
                row["path"] = data.remap_path(row["path"], moves, base)
                if row.get("render"):
                    row["render"] = data.rewrite_record(row["render"], moves, base)
                    row["render"]["manifests"] = [data.remap_path(p, moves, base) for p in row["render"].get("manifests", [])]
                remaining.append(row)
            if current is model:
                active = sum(i < active for i in range(len(current_rows)) if i not in selected)
            spec.save_state(current, remaining, max(1, active) if remaining else 0, scene)
    except Exception as exc:
        failures = []
        model.params = original_params
        for current, params, _, _ in states:
            current.params = params
        try:
            if base_file:
                if original_approvals is None:
                    base_file.unlink(missing_ok=True)
                else:
                    base_file.write_bytes(original_approvals)
            for current, _, _, _ in states:
                spec.sync_output(current, scene)
        except (OSError, ValueError) as error:
            failures.append(str(error))
        for path, original in reversed(written):
            try:
                path.write_bytes(original)
            except OSError as error:
                failures.append(str(error))
        for old, new in reversed(completed):
            try:
                if old.exists():
                    raise FileExistsError(f"Cannot restore over existing folder: {old}")
                shutil.move(str(new), str(old))
            except OSError as error:
                failures.append(str(error))
        try:
            if original_manifest is None:
                manifest.unlink(missing_ok=True)
            else:
                manifest.write_bytes(original_manifest)
        except OSError as error:
            failures.append(str(error))
        if failures:
            raise OSError(f"Archive failed: {exc}. Recovery needs attention: {'; '.join(failures)}") from exc
        raise
    return len(chosen), destination


def choose_archive(scene, parent_widget):
    path = approvals.metadata_path(scene)
    dialog = QtWidgets.QDialog(parent_widget)
    dialog.setWindowTitle("Archive selected paths")
    dialog.setMinimumWidth(640)
    layout = QtWidgets.QFormLayout(dialog)
    folder = QtWidgets.QLineEdit(str(path.parent / "archives") if path else "")
    browse = QtWidgets.QPushButton("Browse…")
    row = QtWidgets.QHBoxLayout()
    row.addWidget(folder)
    row.addWidget(browse)
    layout.addRow("Archive parent folder", row)
    names = QtWidgets.QComboBox()
    layout.addRow("Archive", names)
    new_name = QtWidgets.QLineEdit()
    new_name.setPlaceholderText("Archive folder name")
    layout.addRow("New folder name", new_name)
    destination = QtWidgets.QLabel()
    destination.setWordWrap(True)
    layout.addRow("Destination", destination)
    def refresh():
        current = names.currentData()
        names.clear()
        names.addItem("Create new archive…", "")
        root = Path(folder.text())
        if folder.text() and root.is_dir():
            for name in sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "archive.json").is_file()):
                names.addItem(name, name)
        names.setCurrentIndex(max(0, names.findData(current)))
    def browse_folder():
        chosen = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Archive parent folder", folder.text())
        if chosen:
            folder.setText(chosen)
            refresh()
    browse.clicked.connect(browse_folder)
    folder.editingFinished.connect(refresh)
    refresh()
    description = QtWidgets.QLabel("Moves the complete BVH run folders and their assigned render folders, then removes the selected paths from this collection.")
    description.setWordWrap(True)
    layout.addRow(description)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Archive")
    def update_destination():
        existing = names.currentData() or ""
        new_name.setEnabled(not bool(existing))
        name = existing or new_name.text().strip()
        try:
            data.validate_folder_name(name)
            valid = bool(folder.text().strip())
        except ValueError:
            valid = False
        destination.setText(str(Path(folder.text().strip()) / name) if valid else "Choose a parent folder and enter a folder name.")
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(valid)
    names.currentIndexChanged.connect(update_destination)
    new_name.textChanged.connect(update_destination)
    folder.textChanged.connect(update_destination)
    update_destination()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)
    dialog.adjustSize()
    dialog.resize(680, dialog.sizeHint().height())
    if dialog.exec() == QtWidgets.QDialog.Accepted:
        if not folder.text().strip():
            raise ValueError("Choose an archive parent folder.")
        return folder.text().strip(), data.validate_folder_name(names.currentData() or new_name.text())
    return None
