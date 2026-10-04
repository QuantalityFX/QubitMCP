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
                point_indices=np.asarray(list(representatives.values()), dtype=np.int64))


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
