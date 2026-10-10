"""Scene objects for panel selection and placement; all panels share one solve."""
import json
import numpy as np
from shiboken6 import isValid

from echograph.physics.cloth.placement import (piece_owner, split_piece_owner, piece_xform,
                                             xform_matrix, matrix_xform, transform_points, validate_placements)


class ClothPanelsMixin:
    def _cloth_panel_info(self, owner):
        token = split_piece_owner(owner)
        if token is None:
            return None
        parent, piece_id = token
        for item in self._cloth_items():
            if str(item.payload.get("owner", "")).casefold() != parent.casefold():
                continue
            config = item.payload.get("cloth")
            if not isinstance(config, dict):
                continue
            pieces = config.get("_pieces")
            if pieces is None:
                pieces = config["_pieces"] = config["asset"].pieces()
            for piece in pieces:
                if piece["id"] == piece_id:
                    return parent, config, piece
        return None

    def _cloth_panel_pose(self, info):
        parent, config, piece = info
        units = float(config["settings"]["scene_units_per_meter"])
        local = piece_xform(piece, config.get("piece_transforms", {}), units)
        return xform_matrix(local) @ self._cloth_matrix(parent)

    def _cloth_panel_get_xform(self, owner):
        info = self._cloth_panel_info(owner)
        return matrix_xform(self._cloth_panel_pose(info)) if info is not None else None

    def _cloth_panel_set_xform(self, owner, pos=None, rot=None, scl=None):
        info = self._cloth_panel_info(owner)
        if info is None:
            return False
        parent, config, piece = info
        units = float(config["settings"]["scene_units_per_meter"])
        state = getattr(self, "_cloth_states", {}).get(parent)
        if state is not None and (state.bake or state.saving):
            state.note = "Stop & save the bake before changing pattern placement."
            self.update()
            return True
        current = self._cloth_panel_get_xform(owner)
        for name, value in (("pos", pos), ("rot", rot), ("scl", scl)):
            if value is not None:
                current[name] = [float(v) for v in value]
        inverse = np.linalg.inv(self._cloth_matrix(parent))
        placements = config.setdefault("piece_transforms", {})
        # A translation should preserve the authored orientation/scale exactly,
        # including when a parent has a nonuniform scale.
        if rot is None and scl is None:
            local = dict(piece_xform(piece, placements, units))
            local["pos"] = transform_points(np.asarray([current["pos"]]), inverse)[0].tolist()
            local_pose = xform_matrix(local)
            local_pose[3, :3] = local["pos"]
        else:
            local_pose = xform_matrix(current) @ inverse
            local = matrix_xform(local_pose)
        # Store positions in meters, independent of the Scene's display units.
        local["pos"] = (np.asarray(local["pos"]) / units).tolist()
        # Keep the exact local affine transform when a nonuniformly scaled
        # parent would otherwise introduce shear during world-space rotation.
        local_pose[3, :3] /= units
        local["matrix"] = local_pose.tolist()
        validate_placements({piece["id"]: local})
        placements[piece["id"]] = local
        model = config.get("node_model")
        if model is not None:
            value = json.dumps(placements, sort_keys=True)
            parameter = next((p for p in model.params if p.get("name") == "piece_transforms"), None)
            if parameter is None:
                model.params.append({"name": "piece_transforms", "value": value})
            else:
                parameter["value"] = value
        node_item = config.get("node_item")
        if node_item is not None and isValid(node_item) and node_item.scene() is not None:
            scene = node_item.scene()
            scene._scene_asset_revision = int(getattr(scene, "_scene_asset_revision", 0)) + 1
        config["cache_path"] = ""
        if model is not None:
            for parameter in model.params:
                if parameter.get("name") == "cache_path":
                    parameter["value"] = ""
        if state is not None:
            state.playing = False
        self.update()
        return True

    def _cloth_publish_panels(self, owner, config, state):
        topology = getattr(self, "_mgl_scene_mesh_topology_by_owner", None)
        if not isinstance(topology, dict):
            topology = self._mgl_scene_mesh_topology_by_owner = {}
        asset = config["asset"]
        units = float(config["settings"]["scene_units_per_meter"])
        pieces = config.get("_pieces")
        if pieces is None:
            pieces = config["_pieces"] = asset.pieces()
        for piece in pieces:
            token = piece_owner(owner, piece["id"])
            pose = self._cloth_panel_pose((owner, config, piece))
            points = transform_points(state.display_positions[piece["vertices"]] * units, np.linalg.inv(pose)).astype("f4")
            if "triangles" not in piece:
                lookup = np.full(len(asset.positions), -1, dtype=np.int32)
                lookup[piece["vertices"]] = np.arange(len(piece["vertices"]))
                piece["triangles"] = lookup[asset.triangles[piece["faces"]]]
                piece["edges"] = np.unique(np.sort(piece["triangles"][:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1), axis=0)
            triangles, edges = piece["triangles"], piece["edges"]
            topology[token] = {"points": points, "triangles": triangles, "edges": edges,
                               "bounds_min": points.min(axis=0), "bounds_max": points.max(axis=0),
                               "model": pose, "vertex_ids": piece["vertices"], "source_owner": owner,
                               "pattern_piece_id": piece["id"], "pattern_piece_name": piece["name"]}

    def _cloth_piece_for_face(self, owner, face_index):
        for item in self._cloth_items():
            if str(item.payload.get("owner", "")) != owner:
                continue
            config = item.payload.get("cloth")
            if not isinstance(config, dict):
                break
            asset = config["asset"]
            if 0 <= face_index < len(asset.triangles):
                return piece_owner(owner, asset.vertex_pieces[asset.triangles[face_index, 0]])
        return owner
