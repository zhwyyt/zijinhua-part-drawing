"""Shared geometric shell checks; no part-type or drawing decisions."""

from collections import Counter
import math

from .analytic_geometry import sub, dot, norm, unit, cross, frame_axes, project, match_loop


def value(node):
    if not isinstance(node, dict) or node.get("status") != "READ":
        raise ValueError("Required source unavailable")
    return node["value"]


def _circle_loop(points, shape, tolerance):
    center, radius = shape["center"], shape["diameter"]/2
    vectors = [sub(p, center) for p in points]
    if len(points) < 3 or any(abs(norm(v)-radius) > tolerance for v in vectors):
        raise ValueError("Circle radius/center differs from loop")
    angles = [math.atan2(cross(v, vectors[(i+1) % len(vectors)]), dot(v, vectors[(i+1) % len(vectors)]))
              for i, v in enumerate(vectors)]
    if (any(abs(a) <= 1e-10 or abs(a) >= math.pi-1e-10 for a in angles)
            or not (all(a > 0 for a in angles) or all(a < 0 for a in angles))
            or abs(abs(sum(angles))-2*math.pi) > 1e-8):
        raise ValueError("Circle winding/gap/overlap invalid")
    return [{"chordIndex": i, "sweepRadians": a} for i, a in enumerate(angles)]


def _shape_match(points, shape, tolerance):
    if shape["kind"] == "ROUND":
        return _circle_loop(points, shape, tolerance)
    return match_loop(points, shape["segments"], tolerance)


def _point_set_equal(a, b, tolerance):
    if len(a) != len(b):
        return False
    remaining = list(b)
    for p in a:
        matches = [i for i, q in enumerate(remaining) if norm(sub(p, q)) <= tolerance]
        if len(matches) != 1:
            return False
        remaining.pop(matches[0])
    return True


def match_prisms(solid, shapes, frame, tolerance, path, levels=None, origin_id=None):
    """Match all cap loops and side quads. Repeated observations stay counted."""
    faces = value(solid["faces"])
    if not faces:
        raise ValueError("No solid faces")
    projected = []
    for i, face in enumerate(faces):
        if origin_id is not None and value(face["originPartId"]) != origin_id:
            raise ValueError("Face source differs from target instance")
        loops = value(face["loops"])
        if len(loops) != 1:
            raise ValueError("Multiple/missing face loops require operation proof")
        points = [project(p, frame) for p in value(loops[0]["vertices"])]
        if len(points) < 3:
            raise ValueError("Degenerate face loop")
        projected.append((i, face, points))
    zs = [p[2] for _, _, pts in projected for p in pts]
    lo, hi = levels if levels is not None else (min(zs), max(zs))
    if hi-lo <= tolerance:
        raise ValueError("Degenerate thickness")
    caps, sides, cap_counts = [], [], Counter()
    axes = frame_axes(frame)
    for i, face, pts in projected:
        if any(min(abs(p[2]-lo), abs(p[2]-hi)) > tolerance for p in pts):
            raise ValueError("Solid contains intermediate/off-thickness vertices")
        z_values = {0 if abs(p[2]-lo) <= tolerance else 1 for p in pts}
        normal = unit(value(face["normal"]))
        if len(z_values) == 1:
            if abs(abs(dot(normal, axes[2]))-1) > 1e-8:
                raise ValueError("Cap normal is inconsistent")
            candidates = []
            for j, shape in enumerate(shapes):
                try:
                    mapping = _shape_match([p[:2] for p in pts], shape, tolerance)
                    candidates.append((j, mapping))
                except ValueError:
                    pass
            if len(candidates) != 1:
                raise ValueError("Cap loop has missing/ambiguous feature attribution")
            j, mapping = candidates[0]
            level = next(iter(z_values))
            cap_counts[j, level] += 1
            caps.append({"faceIndex": i, "shapeIndex": j, "level": level, "mapping": mapping,
                         "sourceRef": path + f"/faces/value/{i}/loops/value/0", "points": pts})
        else:
            if len(pts) != 4 or abs(dot(normal, axes[2])) > 1e-8:
                raise ValueError("Non-prismatic side face")
            local_normal = [dot(normal, axis) for axis in axes]
            if any(abs(dot(sub(p, pts[0]), local_normal)) > tolerance for p in pts):
                raise ValueError("Side loop contradicts its normal plane")
            sides.append((i, pts))
    expected = []
    for cap in caps:
        if cap["level"] == 0:
            pts = cap["points"]
            for a, b in zip(pts, pts[1:] + pts[:1]):
                expected.append((cap["shapeIndex"], [a[:2]+[lo], b[:2]+[lo], a[:2]+[hi], b[:2]+[hi]]))
    for j in range(len(shapes)):
        if not cap_counts[j, 0] or cap_counts[j, 0] != cap_counts[j, 1]:
            raise ValueError("Missing/unbalanced opposite caps")
    # Match repeated cap observations by count, not by deleting coincident evidence.
    for upper in [c for c in caps if c["level"] == 1]:
        lower = [c for c in caps if c["level"] == 0 and not c.get("paired") and
                 c["shapeIndex"] == upper["shapeIndex"] and
                 _point_set_equal([p[:2] for p in c["points"]], [p[:2] for p in upper["points"]], tolerance)]
        if not lower:
            raise ValueError("Opposite cap outlines differ")
        lower[0]["paired"] = True
    side_mapping = []
    for i, pts in sides:
        matches = [j for j, (_, quad) in enumerate(expected) if _point_set_equal(pts, quad, tolerance)]
        if not matches or len({expected[j][0] for j in matches}) != 1:
            raise ValueError("Side face has missing/ambiguous extrusion attribution")
        j = matches[0]
        shape_index, _ = expected.pop(j)
        side_mapping.append({"faceIndex": i, "shapeIndex": shape_index})
    if expected:
        raise ValueError("Extrusion side faces missing")
    # Every enumerated edge must be represented by a face chord; no unexplained edges.
    chords = [(a, b) for _, _, pts in projected for a, b in zip(pts, pts[1:]+pts[:1])]
    for edge in value(solid["edges"]):
        ends = [project(edge[k], frame) for k in ("start", "end")]
        if not any(_point_set_equal(ends, pair, tolerance) for pair in chords):
            raise ValueError("Solid edge absent from face loops")
    edges = [[project(edge[k], frame) for k in ("start", "end")] for edge in value(solid["edges"])]
    if any(not any(_point_set_equal(chord, edge, tolerance) for edge in edges) for chord in chords):
        raise ValueError("Face chord absent from solid edges")
    return {"status": "MATCHED", "levels": [lo, hi], "thickness": hi-lo,
            "capEvidence": [{k: v for k, v in c.items() if k not in ("points", "paired")} for c in caps],
            "sideEvidence": side_mapping, "faceCount": len(faces), "sourceRef": path}


