"""Authored polygon topology alongside the triangles used by the renderer."""
from collections import defaultdict, deque

import numpy as np


def polygon_topology(triangles, vertex_keys, polygon_keys):
    representatives = {}
    welded = np.asarray([representatives.setdefault(key, i) for i, key in enumerate(vertex_keys)], dtype=np.int64)
    groups = {}
    triangle_polygons = []
    for i, key in enumerate(polygon_keys):
        group = groups.setdefault(key, [])
        group.append(i)
    faces = list(groups.values())
    triangle_polygons = np.zeros(len(triangles), dtype=np.int64)
    polygons, edge_faces = [], defaultdict(list)
    for face_id, members in enumerate(faces):
        triangle_polygons[members] = face_id
        counts = defaultdict(int)
        for tri in welded[triangles[members]]:
            for a, b in zip(tri, np.roll(tri, -1)):
                if a != b:
                    counts[tuple(sorted((int(a), int(b))))] += 1
        boundary = [edge for edge, count in counts.items() if count == 1]
        neighbors = defaultdict(list)
        for a, b in boundary:
            neighbors[a].append(b)
            neighbors[b].append(a)
            edge_faces[(a, b)].append(face_id)
        ordered = []
        if neighbors and all(len(n) == 2 for n in neighbors.values()):
            current, previous = min(neighbors), None
            while current not in ordered:
                ordered.append(current)
                following = next(n for n in neighbors[current] if n != previous)
                previous, current = current, following
        polygons.append(ordered)
    adjacency = [dict() for _ in faces]
    for edge, owners in edge_faces.items():
        if len(owners) == 2:
            a, b = owners
            adjacency[a][b] = edge
            adjacency[b][a] = edge
    return dict(polygon_triangles=faces, triangle_polygons=triangle_polygons,
                polygons=polygons, polygon_adjacency=adjacency,
                edges=np.asarray(list(edge_faces), dtype=np.int64).reshape(-1, 2),
                point_indices=np.asarray(list(representatives.values()), dtype=np.int64),
                vertex_representatives=welded)


def expand_face_elements(topo, elem):
    """Keep triangle IDs for existing tools, but select every triangle of a polygon."""
    if elem.get("mode") != "face" or not topo or "triangle_polygons" not in topo:
        return [elem]
    index = int(elem["face_index"])
    if not 0 <= index < len(topo["triangle_polygons"]):
        return []
    polygon = int(topo["triangle_polygons"][index])
    return [dict(elem, face_index=i, indices=tuple(int(v) for v in topo["triangles"][i]))
            for i in topo["polygon_triangles"][polygon]]


def polygon_path(topo, start_triangle, end_triangle, *, loop=False):
    mapping = topo.get("triangle_polygons", [])
    if not (0 <= start_triangle < len(mapping) and 0 <= end_triangle < len(mapping)):
        return []
    start, end = int(mapping[start_triangle]), int(mapping[end_triangle])
    adjacency = topo["polygon_adjacency"]
    parents, queue = {start: None}, deque([start])
    while queue and end not in parents:
        current = queue.popleft()
        for neighbor in sorted(adjacency[current]):
            if neighbor not in parents:
                parents[neighbor] = current
                queue.append(neighbor)
    if end not in parents:
        return []
    path, current = [], end
    while current is not None:
        path.append(current)
        current = parents[current]
    path.reverse()
    if loop and len(path) >= 2:
        visited = set(path)

        def extend(previous, current):
            result = []
            while True:
                vertices = topo["polygons"][current]
                if len(vertices) != 4:
                    break  # No unambiguous opposite edge on triangles/ngons.
                edges = [tuple(sorted((vertices[i], vertices[(i + 1) % 4]))) for i in range(4)]
                entered = adjacency[current].get(previous)
                if entered not in edges:
                    break
                opposite = edges[(edges.index(entered) + 2) % 4]
                following = next((n for n, edge in adjacency[current].items() if edge == opposite), None)
                if following is None or following in visited:
                    break
                visited.add(following)
                result.append(following)
                previous, current = current, following
            return result

        tail = extend(path[-2], path[-1])
        head = extend(path[1], path[0])
        path = list(reversed(head)) + path + tail
    return [i for polygon in path for i in topo["polygon_triangles"][polygon]]


def polygon_selection(topo, anchor, target, *, loop=False):
    """One source for the hover preview and committed polygon selection."""
    if not isinstance(target, dict):
        return []
    if not isinstance(anchor, dict) or anchor.get('owner') != target.get('owner'):
        return expand_face_elements(topo, target)
    indices = polygon_path(topo, int(anchor['face_index']), int(target['face_index']), loop=loop)
    return [dict(target, face_index=i, indices=tuple(int(v) for v in topo['triangles'][i])) for i in indices]


def edge_loop(topo, edge):
    """Walk opposite edges at regular quad vertices; stop at ambiguous poles."""
    edge = tuple(sorted(map(int, edge)))
    return extend_edge_path(topo, [edge])


def edge_connectivity(topo):
    cached = topo.get('_selection_connectivity')
    signature = (id(topo.get('polygons')), len(topo.get('polygons', [])), id(topo.get('edges')))
    if cached and cached[0] == signature:
        return cached[1]
    polygons = topo.get('polygons', topo.get('triangles', []))
    edge_faces, vertex_edges = defaultdict(set), defaultdict(set)
    for face, vertices in enumerate(polygons):
        for a, b in zip(vertices, np.roll(vertices, -1)):
            pair = tuple(sorted((int(a), int(b))))
            edge_faces[pair].add(face)
            vertex_edges[int(a)].add(pair)
            vertex_edges[int(b)].add(pair)
    for a, b in topo.get('edges', []):
        pair = tuple(sorted((int(a), int(b))))
        vertex_edges[int(a)].add(pair)
        vertex_edges[int(b)].add(pair)
    result = (polygons, edge_faces, vertex_edges)
    topo['_selection_connectivity'] = (signature, result)
    return result


def extend_edge_path(topo, path):
    if not path:
        return []
    polygons, edge_faces, vertex_edges = edge_connectivity(topo)
    result, visited = list(path), set(path)
    ends = [(path[0], v) for v in path[0] if len(path) == 1 or v not in path[1]]
    if len(path) > 1:
        ends += [(path[-1], v) for v in path[-1] if v not in path[-2]]
    for incoming, vertex in ends:
        while True:
            incident = vertex_edges[vertex]
            if any(len(edge_faces[e]) > 2 for e in incident):
                break
            if not edge_faces[incoming] and len(incident) == 2:
                candidates = [e for e in incident if e != incoming]
            elif len(edge_faces[incoming]) == 1:
                candidates = [e for e in incident if e != incoming and len(edge_faces[e]) == 1]
            elif len(incident) == 4 and all(len(polygons[f]) == 4 for e in incident for f in edge_faces[e]):
                candidates = [e for e in incident if not (edge_faces[e] & edge_faces[incoming])]
            else:
                break
            if len(candidates) != 1 or candidates[0] in visited:
                break
            incoming = candidates[0]
            result.append(incoming)
            visited.add(incoming)
            vertex = incoming[1] if incoming[0] == vertex else incoming[0]
    return result


def shortest_path(start, end, neighbors):
    parents, queue = {start: None}, deque([start])
    while queue and end not in parents:
        current = queue.popleft()
        for neighbor in sorted(neighbors(current)):
            if neighbor not in parents:
                parents[neighbor] = current
                queue.append(neighbor)
    if end not in parents:
        return []
    path, current = [], end
    while current is not None:
        path.append(current)
        current = parents[current]
    return list(reversed(path))


def component_selection(topo, anchor, target, *, loop=False):
    """Resolve paths/loops on the same authored topology for preview and click."""
    if not topo or not isinstance(target, dict):
        return []
    mode = target.get('mode')
    same_anchor = (isinstance(anchor, dict) and anchor.get('owner') == target.get('owner')
                   and anchor.get('mode', mode) == mode)
    if mode == 'face':
        return polygon_selection(topo, anchor if same_anchor else None, target, loop=loop)
    _, _, vertex_edges = edge_connectivity(topo)
    if mode == 'edge':
        end = tuple(sorted(map(int, target['edge'])))
        start = tuple(sorted(map(int, anchor['edge']))) if same_anchor else end
        path = shortest_path(start, end, lambda e: (vertex_edges[e[0]] | vertex_edges[e[1]]) - {e}) or [end]
        edges = extend_edge_path(topo, path) if loop else path
        return [dict(target, edge=e) for e in edges]
    if mode == 'point':
        representatives = topo.get('vertex_representatives')
        def canonical(index):
            index = int(index)
            return int(representatives[index]) if representatives is not None else index
        end = canonical(target['vertex_index'])
        start = canonical(anchor['vertex_index']) if same_anchor else end
        path = shortest_path(start, end, lambda v: {b if a == v else a for a,b in vertex_edges[v]}) or [end]
        if loop and len(path) > 1:
            edges = extend_edge_path(topo, [tuple(sorted(e)) for e in zip(path, path[1:])])
            path = list(dict.fromkeys(path + [v for e in edges for v in e]))
        points = topo.get('points')
        return [dict(target, vertex_index=v, position=tuple(points[v]) if points is not None else None) for v in path]
    return [target]


def component_gesture_bindings():
    from . import hotkeys_config
    return [(operation, *hotkeys_config.parse_key_binding(hotkeys_config.keyseq(action, fallback)))
            for operation, action, fallback in (
                ('subtract', 'gl_mesh_deselect_path_loop', 'Ctrl+A'),
                ('add', 'gl_mesh_select_path_loop', 'Shift+A'))]


def component_gesture_operation(view, modifiers):
    from . import hotkeys_config
    from echograph.qt_compat import QtCore
    if (getattr(view, '_mesh_select_mode', '') not in ('point', 'edge', 'face')
            or getattr(view, '_fly_mode_enabled', False) or getattr(view, '_fps_nav_active', False)):
        return None
    held = getattr(view, '_mesh_component_held_keys', None)
    if held is None:
        held = {QtCore.Qt.Key_A} if getattr(view, '_mesh_polygon_a_held', False) else set()
    for operation, key, required in component_gesture_bindings():
        if key in held and (modifiers & hotkeys_config._MOD_MASK) == required:
            return operation
    return None


def handle_component_gesture_key(view, event, *, pressed):
    """Track held keys, including modifier changes without moving the mouse."""
    from echograph.qt_compat import QtCore
    if (getattr(view, '_mesh_select_mode', '') not in ('point', 'edge', 'face')
            or getattr(view, '_fly_mode_enabled', False) or getattr(view, '_fps_nav_active', False)):
        return False
    key = event.key()
    activation_keys = {key for _, key, _ in component_gesture_bindings() if key is not None}
    modifiers = {QtCore.Qt.Key_Shift: QtCore.Qt.ShiftModifier,
                 QtCore.Qt.Key_Control: QtCore.Qt.ControlModifier,
                 QtCore.Qt.Key_Alt: QtCore.Qt.AltModifier,
                 QtCore.Qt.Key_Meta: QtCore.Qt.MetaModifier}
    held = set(getattr(view, '_mesh_component_held_keys', set()))
    if key not in activation_keys and key not in held and key not in modifiers:
        return False
    if event.isAutoRepeat():
        return key in activation_keys
    consumed = key in activation_keys or key in held
    if consumed:
        if pressed:
            held.add(key)
        else:
            held.discard(key)
    view._mesh_component_held_keys = held
    view._mesh_polygon_a_held = bool(held)
    mods = event.modifiers()
    if key in modifiers:
        mods = mods | modifiers[key] if pressed else mods & ~modifiers[key]
    view._mesh_hover_subtract = bool(mods & QtCore.Qt.ControlModifier) and not bool(mods & QtCore.Qt.AltModifier)
    pending = getattr(view, '_mesh_hover_pending_event', None)
    if pending is not None:
        pending.setModifiers(mods)
    operation = component_gesture_operation(view, mods)
    update_polygon_preview(view, getattr(view, '_mesh_polygon_hover_target', None),
                           operation is not None, selection_op=operation)
    if consumed:
        event.accept()
    return consumed


def update_polygon_preview(view, target, active, *, selection_op='add'):
    preview = {}
    if active and isinstance(target, dict) and target.get('mode') in ('point', 'edge', 'face'):
        renderer = getattr(view, '_mgl_renderer', None) or view
        topo = renderer._mgl_mesh_topology_for_owner(target['owner'])
        if topo:
            anchor = getattr(view, '_mesh_polygon_anchor', None)
            preview = {name: component_selection(topo, anchor, target, loop=loop)
                       for name, loop in (('path', False), ('loop', True))}
    view._mesh_polygon_preview = preview
    view._mesh_polygon_preview_operation = selection_op if preview else None
    view.update()
