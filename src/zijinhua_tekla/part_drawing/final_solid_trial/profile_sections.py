"""Measured cross-sections and rectangular wall partitions of straight sections."""

import math

import numpy as np
from shapely import union_all
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import polygonize

from ..analytic_geometry import project, frame_axes
from .shell import require, read


def local_faces(part, shell):
    frame = read(part.get("coordinateSystem"), "coordinateSystem")
    axes = frame_axes(frame)
    return frame, [
        dict(
            face,
            localLoops=[[project(p, frame) for p in ring] for ring in face["loops"]],
            localNormal=[float(np.dot(face["normal"], a)) for a in axes],
        )
        for face in shell["faces"]
    ]


def section_at(faces, station, grid=1e-5):
    lines = []
    for face in faces:
        for ring in face["localLoops"]:
            hits = []
            for a, b in zip(ring, ring[1:] + ring[:1]):
                if (a[0] < station < b[0]) or (b[0] < station < a[0]):
                    t = (station - a[0]) / (b[0] - a[0])
                    hits.append(np.array(a[1:]) + t * (np.array(b[1:]) - a[1:]))
            if not hits:
                continue
            n = face["localNormal"]
            direction = np.array([-n[2], n[1]])
            hits.sort(key=lambda p: float(np.dot(p, direction)))
            require(
                len(hits) % 2 == 0, "PROFILE_SECTION_OPEN", "Odd face intersections in cross-section"
            )
            lines.extend(
                LineString([a, b]) for a, b in zip(hits[::2], hits[1::2]) if np.linalg.norm(a - b) > grid
            )
    network = union_all(lines, grid_size=grid)
    cells = list(polygonize(network))
    # Polygonize produces a cell for a tube's void too. Classify cells by
    # parity of the actual boundary crossings along a transverse ray.
    material = []
    segments = [list(line.coords) for line in lines]
    for cell in cells:
        p = cell.representative_point()
        count = 0
        for a, b in segments:
            if (a[1] > p.y) != (b[1] > p.y):
                x = a[0] + (p.y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                count += x > p.x
        if count % 2:
            material.append(cell)
    return union_all(material, grid_size=grid)


def _levels(values, tolerance):
    groups = []
    for v in sorted(values):
        if not groups or v - groups[-1][0] > tolerance:
            groups.append([])
        groups[-1].append(v)
    return [sum(g) / len(g) for g in groups]


def measured_section(faces, tolerance=0.1):
    xs = _levels([p[0] for f in faces for ring in f["localLoops"] for p in ring], tolerance / 100)
    gaps = sorted([(b - a, (a + b) / 2) for a, b in zip(xs, xs[1:]) if b - a > tolerance], reverse=True)
    require(bool(gaps), "PROFILE_AXIS_UNRESOLVED", "No measurable axial interval")
    # Inspect every topological interval; retain sections that contain all
    # others, so a local cut cannot silently become the nominal section.
    sections = [(x, section_at(faces, x)) for _, x in gaps]
    valid = [(x, s) for x, s in sections if s.geom_type == "Polygon" and s.area > tolerance**2]
    require(bool(valid), "PROFILE_SECTION_UNRESOLVED", "No closed connected section found")
    station, shape = max(valid, key=lambda item: item[1].area)
    require(
        all(shape.buffer(tolerance).covers(s) for _, s in sections),
        "PROFILE_SECTION_VARIES",
        "No measured section bounds the other material sections",
    )
    shape = shape.simplify(tolerance / 100, preserve_topology=True)
    return station, shape, [xs[0], xs[-1]]


def partition_section(shape, tolerance=0.1):
    rings = [shape.exterior] + list(shape.interiors)
    for ring in rings:
        for a, b in zip(list(ring.coords)[:-1], list(ring.coords)[1:]):
            require(
                min(abs(a[0] - b[0]), abs(a[1] - b[1])) <= tolerance / 100,
                "PROFILE_CURVED_OR_SLOPED_SECTION",
                "Section needs analytic curved or sloped-wall support",
            )
    levels = [
        _levels([p[d] for r in rings for p in list(r.coords)[:-1]], tolerance / 100) for d in (0, 1)
    ]
    candidates = []
    for along in (0, 1):
        across = 1 - along
        strips = []
        for a, b in zip(levels[along], levels[along][1:]):
            runs = []
            for c, d in zip(levels[across], levels[across][1:]):
                center = [(a + b) / 2, (c + d) / 2] if along == 0 else [(c + d) / 2, (a + b) / 2]
                if shape.covers(Point(center)):
                    if runs and abs(runs[-1][1] - c) < tolerance / 100:
                        runs[-1][1] = d
                    else:
                        runs.append([c, d])
            for c, d in runs:
                prev = next(
                    (
                        r
                        for r in strips
                        if abs(r[1] - a) < tolerance / 100
                        and abs(r[2] - c) < tolerance / 100
                        and abs(r[3] - d) < tolerance / 100
                    ),
                    None,
                )
                if prev:
                    prev[1] = b
                else:
                    strips.append([a, b, c, d])
        rects = [box(a, c, b, d) if along == 0 else box(c, a, d, b) for a, b, c, d in strips]
        candidates.append(rects)
    rects = min(
        candidates,
        key=lambda rs: (
            len(rs),
            sum(min(r.bounds[2] - r.bounds[0], r.bounds[3] - r.bounds[1]) for r in rs),
        ),
    )
    covered = union_all(rects)
    require(
        shape.symmetric_difference(covered).area <= tolerance * shape.length,
        "PROFILE_PARTITION_CONFLICT",
        "Rectangular walls do not cover measured section",
    )
    require(
        abs(sum(r.area for r in rects) - covered.area) <= tolerance**2,
        "PROFILE_PARTITION_OVERLAP",
        "Wall interiors overlap",
    )
    bounds = [r.bounds for r in rects]
    graph = {
        i: [
            j
            for j, r in enumerate(rects)
            if j != i and rects[i].boundary.intersection(r.boundary).length > tolerance
        ]
        for i in range(len(rects))
    }
    spans = [[b[2] - b[0], b[3] - b[1]] for b in bounds]
    normals = [int(s[1] < s[0]) + 1 for s in spans]  # 1=Y thickness, 2=Z thickness
    family = "RECTANGULAR_WALL_SECTION"
    if len(rects) == 1:
        family = "RECTANGULAR_SOLID"
    elif len(rects) == 2 and set(normals) == {1, 2}:
        # A T's stem touches the middle of a flange; an L meets at its end.
        joint = rects[0].boundary.intersection(rects[1].boundary).centroid
        interior_contact = []
        for i, r in enumerate(rects):
            axis = 1 if normals[i] == 1 else 0
            q = (joint.x, joint.y)[axis]
            half_joint = rects[0].boundary.intersection(rects[1].boundary).length / 2
            interior_contact.append(
                r.bounds[axis] + half_joint + tolerance < q < r.bounds[axis + 2] - half_joint - tolerance
            )
        family = "T" if any(interior_contact) else "L"
    elif len(rects) == 3 and sorted(len(v) for v in graph.values()) == [1, 1, 2]:
        web = next(i for i, v in graph.items() if len(v) == 2)
        ends = graph[web]
        axis = normals[web] - 1
        mid = (bounds[web][axis] + bounds[web][axis + 2]) / 2
        centers = [(bounds[i][axis] + bounds[i][axis + 2]) / 2 for i in ends]
        straddles = [
            bounds[i][axis] + tolerance < bounds[web][axis]
            and bounds[i][axis + 2] - tolerance > bounds[web][axis + 2]
            for i in ends
        ]
        family = (
            "I_H" if all(straddles) else "C_U" if (centers[0] - mid) * (centers[1] - mid) > 0 else "Z"
        )
    elif len(rects) == 4 and len(shape.interiors) == 1 and all(len(v) == 2 for v in graph.values()):
        family = "BOX"
    walls = []
    for i, (r, n) in enumerate(zip(rects, normals)):
        role = "WALL"
        if family in ("I_H", "C_U", "Z"):
            role = "WEB" if len(graph[i]) == 2 else "FLANGE"
        elif family == "L":
            role = "LEG"
        elif family == "T":
            role = "FLANGE" if interior_contact[i] else "WEB"
        elif family == "BOX":
            role = "BOX_WALL"
        elif family == "RECTANGULAR_SOLID":
            role = "SOLID_SECTION"
        walls.append(
            dict(
                id=f"wall:{i+1}",
                role=role,
                sectionBounds=list(r.bounds),
                thicknessAxis=n,
                widthAxis=3 - n,
                thickness=spans[i][n - 1],
                width=spans[i][2 - n],
                neighbors=[f"wall:{j+1}" for j in graph[i]],
            )
        )
    return family, walls
