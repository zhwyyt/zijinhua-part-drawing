"""Read and check the final boundary representation, without modelling history."""

from collections import defaultdict
from itertools import product
import math

import numpy as np
from shapely.geometry import Polygon
from shapely import union_all

from ..analytic_geometry import frame_axes, project


class EvidenceError(ValueError):
    def __init__(self, code, message, source=""):
        super().__init__(message)
        self.issue = dict(code=code, message=message, sourceRef=source)


def require(condition, code, message, source=""):
    if not condition:
        raise EvidenceError(code, message, source)


def read(node, source=""):
    require(
        isinstance(node, dict) and node.get("status") == "READ",
        "SOURCE_UNAVAILABLE",
        "Required evidence is not READ",
        source,
    )
    return node["value"]


def point(value, source=""):
    require(
        isinstance(value, (list, tuple, np.ndarray))
        and len(value) == 3
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in value),
        "NONFINITE_GEOMETRY",
        "Expected a finite 3D point/vector",
        source,
    )
    return np.array(value, dtype=float)


def make_frame(normal, origin, preferred=None):
    n = point(normal)
    n /= np.linalg.norm(n)
    axes = frame_axes(preferred) if preferred else np.eye(3)
    x = next(np.array(a) - np.dot(a, n) * n for a in axes if abs(np.dot(a, n)) < 0.9)
    x /= np.linalg.norm(x)
    return dict(origin=list(origin), axisX=x.tolist(), axisY=np.cross(n, x).tolist())


def polygon(loops, frame):
    rings = [[project(p, frame)[:2] for p in loop] for loop in loops]
    rings.sort(key=lambda ring: Polygon(ring).area, reverse=True)
    return Polygon(rings[0], rings[1:])


def _signed_area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2


def read_shell(part, tolerance=0.1):
    source = f"parts/{part['id']}/finalSolid"
    solid = read(part.get("finalSolid"), source)
    raw_faces, raw_edges = read(solid.get("faces"), source), read(solid.get("edges"), source)
    require(bool(raw_faces) and bool(raw_edges), "FINAL_SOLID_INCOMPLETE", "Faces or edges missing", source)
    # Stitch only numerical copies, not physical features within the model budget.
    stitch = min(tolerance / 1000, 1e-4)
    vertices, buckets = [], defaultdict(list)

    def vertex(p):
        key = tuple(math.floor(c / stitch) for c in p)
        hits = [
            i
            for delta in product((-1, 0, 1), repeat=3)
            for i in buckets[tuple(a + b for a, b in zip(key, delta))]
            if np.linalg.norm(p - vertices[i]) <= stitch
        ]
        require(len(hits) <= 1, "AMBIGUOUS_VERTEX", "Vertex stitching is ambiguous", source)
        if hits:
            return hits[0]
        i = len(vertices)
        vertices.append(p)
        buckets[key].append(i)
        return i

    faces, incidence, face_signatures = [], defaultdict(list), set()
    for index, raw in enumerate(raw_faces):
        ref = source + f"/faces/{index}"
        n = point(read(raw.get("normal"), ref), ref)
        require(np.linalg.norm(n) > 1e-12, "INVALID_NORMAL", "Zero face normal", ref)
        n /= np.linalg.norm(n)
        loops = [
            [point(p, ref) for p in read(loop.get("vertices"), ref)] for loop in read(raw.get("loops"), ref)
        ]
        require(
            bool(loops) and all(len(loop) >= 3 for loop in loops),
            "DEGENERATE_FACE",
            "Face has missing or degenerate loops",
            ref,
        )
        origin = loops[0][0]
        residual = max(abs(np.dot(p - origin, n)) for loop in loops for p in loop)
        require(residual <= tolerance, "FACE_NORMAL_CONFLICT", "Vertices contradict face plane", ref)
        reported_normal = n.tolist()
        cloud = np.array([p - origin for loop in loops for p in loop])
        _, singular, vectors = np.linalg.svd(cloud, full_matrices=False)
        require(singular[1] > stitch, "DEGENERATE_FACE", "Face vertices are collinear", ref)
        measured = vectors[-1]
        if np.dot(measured, n) < 0:
            measured = -measured
        require(
            max(abs(cloud @ measured)) <= stitch,
            "NONPLANAR_FACE_UNSUPPORTED",
            "A nonplanar surface needs analytic surface parameters",
            ref,
        )
        n = measured
        frame = make_frame(n, origin)
        loops.sort(key=lambda loop: Polygon([project(p, frame)[:2] for p in loop]).area, reverse=True)
        shape = polygon(loops, frame)
        require(
            shape.is_valid and shape.area > stitch * stitch,
            "INVALID_FACE_BOUNDARY",
            "Face loops overlap, self-intersect or degenerate",
            ref,
        )
        ids = []
        for li, loop in enumerate(loops):
            ring = [vertex(p) for p in loop]
            require(len(set(ring)) == len(ring), "DEGENERATE_FACE", "Repeated face vertex", ref)
            area = _signed_area([project(p, frame)[:2] for p in loop])
            if (area > 0) != (li == 0):
                ring.reverse()
            ids.append(ring)
            for a, b in zip(ring, ring[1:] + ring[:1]):
                require(a != b, "DEGENERATE_EDGE", "Zero-length face edge", ref)
                incidence[tuple(sorted((a, b)))].append((index, a, b))
        signature = tuple(sorted(tuple(sorted(r)) for r in ids))
        require(signature not in face_signatures, "DUPLICATE_FACE", "Repeated geometric face", ref)
        face_signatures.add(signature)
        faces.append(
            dict(
                id=raw.get("id", f"F{index}"),
                index=index,
                normal=n.tolist(),
                loops=[[p.tolist() for p in loop] for loop in loops],
                area=shape.area,
                frame=frame,
                sourceRef=ref,
                maxPlaneResidualMm=residual,
                reportedNormal=reported_normal,
                originPartId=raw.get("originPartId", {}).get("value"),
            )
        )
    require(
        all(len(items) == 2 for items in incidence.values()),
        "SHELL_NOT_CLOSED",
        "Each boundary edge must have exactly two incident faces",
        source,
    )
    require(
        all(a[1] == b[2] and a[2] == b[1] for a, b in incidence.values()),
        "NORMAL_ORIENTATION_CONFLICT",
        "Adjacent outward normals disagree",
        source,
    )
    observed = set()
    edge_records = []
    for index, raw in enumerate(raw_edges):
        a, b = vertex(point(raw.get("start"), source)), vertex(point(raw.get("end"), source))
        key = tuple(sorted((a, b)))
        require(
            a != b and key in incidence, "UNEXPLAINED_EDGE", "Edge not represented by face boundary", source
        )
        require(key not in observed, "DUPLICATE_EDGE", "Repeated solid edge", source)
        observed.add(key)
        edge_records.append(
            dict(
                index=index,
                start=vertices[a].tolist(),
                end=vertices[b].tolist(),
                apiType=raw.get("apiType"),
                faces=[v[0] for v in incidence[key]],
            )
        )
    require(observed == set(incidence), "MISSING_EDGE", "Face boundary absent from edge inventory", source)
    # Connected shell and outward orientation. Translation-relative volume avoids cancellation.
    graph = defaultdict(set)
    for records in incidence.values():
        a, b = (r[0] for r in records)
        graph[a].add(b)
        graph[b].add(a)
    visited, pending = set(), [0]
    while pending:
        i = pending.pop()
        if i not in visited:
            visited.add(i)
            pending.extend(graph[i] - visited)
    require(
        len(visited) == len(faces),
        "DISCONNECTED_SHELL",
        "Multiple disconnected shells need explicit support",
        source,
    )
    center = np.mean(vertices, axis=0)
    volume = sum(f["area"] * np.dot(np.array(f["loops"][0][0]) - center, f["normal"]) / 3 for f in faces)
    require(
        volume > stitch**3, "INVERTED_OR_EMPTY_SHELL", "Shell normals are inward or volume is zero", source
    )
    return dict(
        status="MATCHED",
        faces=faces,
        edges=edge_records,
        volume=volume,
        stitchToleranceMm=stitch,
        modelToleranceMm=tolerance,
        sourceRef=source,
    )


def plane_regions(shell):
    """Merge adjacent coplanar patches; keep every original face in the mapping."""
    faces = shell["faces"]
    tol = shell["stitchToleranceMm"]
    plane_tol = shell["modelToleranceMm"]
    adjacent = {tuple(sorted(edge["faces"])) for edge in shell["edges"]}
    groups = []
    for face in faces:
        n, p = np.array(face["normal"]), np.array(face["loops"][0][0])
        group = next(
            (
                g
                for g in groups
                if np.dot(g[0]["normal"], n) > 1 - 1e-8
                and any(tuple(sorted((face["index"], other["index"]))) in adjacent for other in g)
                and all(abs(np.dot(np.array(q) - p, n)) <= plane_tol for loop in g[0]["loops"] for q in loop)
                and all(
                    abs(np.dot(np.array(q) - g[0]["loops"][0][0], g[0]["normal"])) <= plane_tol
                    for loop in face["loops"]
                    for q in loop
                )
            ),
            None,
        )
        if group is None:
            groups.append([face])
        else:
            group.append(face)
    regions = []
    for group in groups:
        frame = group[0]["frame"]
        merged = union_all([polygon(f["loops"], frame) for f in group], grid_size=tol / 10)
        require(merged.is_valid, "COPLANAR_UNION_INVALID", "Coplanar union is invalid")
        pieces = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
        for shape in pieces:
            members = [f for f in group if shape.intersection(polygon(f["loops"], frame)).area > tol**2]
            axes = frame_axes(frame)

            def lift(coords):
                return [
                    (np.array(frame["origin"]) + x * np.array(axes[0]) + y * np.array(axes[1])).tolist()
                    for x, y in list(coords)[:-1]
                ]

            regions.append(
                dict(
                    id=f"R{len(regions)}",
                    faces=[f["index"] for f in members],
                    sourceRefs=[f["sourceRef"] for f in members],
                    normal=group[0]["normal"],
                    frame=frame,
                    area=shape.area,
                    mergeGridMm=tol / 10,
                    maxMergePlaneResidualMm=max(
                        abs(np.dot(np.array(p) - group[0]["loops"][0][0], group[0]["normal"]))
                        for f in members
                        for loop in f["loops"]
                        for p in loop
                    ),
                    loops=[lift(shape.exterior.coords)] + [lift(r.coords) for r in shape.interiors],
                )
            )
    return regions
