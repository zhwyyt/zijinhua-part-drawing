"""Material faces and local plate regions measured on the checked final shell."""

import math
import re

import numpy as np
from shapely import union_all
from shapely.geometry import Polygon, Point

from ..analytic_geometry import frame_axes, project, line
from .shell import make_frame, polygon, require, read


def face_pairs(regions, tolerance):
    result = []
    for i, a in enumerate(regions):
        for j, b in enumerate(regions[i + 1 :], i + 1):
            if np.dot(a["normal"], b["normal"]) > -1 + 1e-7:
                continue
            distance = -np.dot(np.array(b["loops"][0][0]) - a["loops"][0][0], a["normal"])
            if distance <= tolerance:
                continue
            pa, pb = polygon(a["loops"], a["frame"]), polygon(b["loops"], a["frame"])
            overlap = pa.intersection(pb).area
            if overlap > min(a["area"], b["area"]) * 0.5:
                result.append(dict(regions=[i, j], thickness=float(distance), overlapArea=overlap))
    return result


def _preferred(part):
    for name in ("dstvCoordinateSystem", "coordinateSystem"):
        node = part.get(name, {})
        if node.get("status") == "READ":
            frame_axes(node["value"])
            return node["value"]
    return None


def _frame(region, preferred):
    n = np.array(region["normal"])
    if preferred:
        axis = next(a for a in reversed(frame_axes(preferred)) if abs(np.dot(n, a)) > 1e-6)
        if np.dot(n, axis) < 0:
            n = -n
    return make_frame(n, preferred["origin"] if preferred else region["loops"][0][0], preferred)


def _ring(coords, refs):
    points = [list(p) for p in list(coords)[:-1]]
    return [line(a, b, refs) for a, b in zip(points, points[1:] + points[:1])]


def _bevel(region, frame, levels, tolerance):
    axes = frame_axes(frame)
    normal = [float(np.dot(region["normal"], a)) for a in axes]
    along = [i for i in (0, 1) if abs(normal[i]) < 1e-5]
    require(len(along) == 1, "BEVEL_ORIENTATION_UNSUPPORTED", "Bevel is not constant along a drawing axis")
    along = along[0]
    cross = 1 - along
    points = [project(p, frame) for loop in region["loops"] for p in loop]
    profile = []
    for p in points:
        q = [p[cross], p[2]]
        if not any(math.dist(q, r) <= tolerance / 100 for r in profile):
            profile.append(q)
    require(len(profile) == 2, "BEVEL_PROFILE_UNSUPPORTED", "Bevel cross-section is not one straight segment")
    profile.sort(key=lambda p: p[1])
    a, b = profile
    depth = b[1] - a[1]
    low, high = levels
    require(
        depth > tolerance and abs(b[0] - a[0]) > tolerance,
        "DEGENERATE_BEVEL",
        "Bevel has zero depth or width",
    )
    if abs(a[1] - low) <= tolerance:
        side, land = "LOW", high - b[1]
    else:
        require(
            abs(b[1] - high) <= tolerance,
            "INTERNAL_BEVEL_UNSUPPORTED",
            "Bevel does not reach a plate surface",
        )
        side, land = "HIGH", a[1] - low
    require(land >= -tolerance, "BEVEL_THICKNESS_CONFLICT", "Bevel lies outside the thickness domain")
    land = max(0.0, land)
    return dict(
        id="bevel:" + region["id"],
        alongAxis=along,
        profile=profile,
        depth=depth,
        land=land,
        thickness=high - low,
        side=side,
        normal=normal,
        offset=float(np.dot(normal, points[0])),
        angle=math.degrees(math.atan2(abs(b[0] - a[0]), depth)),
        extent=[min(p[along] for p in points), max(p[along] for p in points)],
        sourceRef=region["sourceRefs"][0],
        sourceFaces=region["faces"],
    )


def flat_features(part, shell, regions, pair, tolerance):
    preferred = _preferred(part)
    frame = _frame(regions[pair["regions"][0]], preferred)
    axes = frame_axes(frame)
    projected = [[project(p, frame) for loop in r["loops"] for p in loop] for r in regions]
    levels = [
        min(p[2] for points in projected for p in points),
        max(p[2] for points in projected for p in points),
    ]
    cap_holes = [
        Polygon([project(p, frame)[:2] for p in loop]).boundary
        for r, points in zip(regions, projected)
        if max(p[2] for p in points) - min(p[2] for p in points) <= tolerance
        for loop in r["loops"][1:]
    ]
    roles, caps, bevels = [], [], []
    for r, points in zip(regions, projected):
        zs = [p[2] for p in points]
        if max(zs) - min(zs) <= tolerance:
            require(
                min(abs(zs[0] - z) for z in levels) <= tolerance,
                "PARTIAL_THICKNESS_REGION",
                "Interior parallel face needs pocket/step expression",
                r["sourceRefs"][0],
            )
            role = "THICKNESS_FACE"
            caps.append(r)
        elif cap_holes and all(any(h.distance(Point(p[:2])) <= tolerance for h in cap_holes) for p in points):
            role = "OPENING_WALL"
        elif abs(np.dot(r["normal"], axes[2])) <= 1e-5:
            role = "SIDE_OR_OPENING_WALL"
        else:
            role = "BEVEL"
            bevels.append(_bevel(r, frame, levels, tolerance))
        roles.extend(dict(faceIndex=i, region=r["id"], role=role) for i in r["faces"])
    require(len(caps) == 2, "THICKNESS_FACES_UNRESOLVED", "Two complete thickness regions required")
    shapes = [polygon(r["loops"], frame) for r in caps]
    low_shape, high_shape = sorted(
        zip(caps, shapes), key=lambda item: project(item[0]["loops"][0][0], frame)[2]
    )
    if not bevels:
        require(
            shapes[0].boundary.hausdorff_distance(shapes[1].boundary) <= tolerance,
            "OPPOSITE_BOUNDARY_CONFLICT",
            "Thickness face boundaries differ without an explained bevel",
        )
    # The projection is observed material, never a reconstruction of the stock/cutters.
    projected_faces = [
        polygon(r["loops"], frame) for r in regions if abs(np.dot(r["normal"], axes[2])) > 1e-8
    ]
    shape = union_all(projected_faces, grid_size=shell["stitchToleranceMm"] / 10)
    require(
        shape.geom_type == "Polygon" and shape.is_valid,
        "PROJECTION_UNRESOLVED",
        "Final projection is not one material region",
    )
    shape = shape.simplify(shell["stitchToleranceMm"], preserve_topology=True)
    refs = [ref for cap in caps for ref in cap["sourceRefs"]]
    openings = []
    require(
        len(low_shape[1].interiors) == len(high_shape[1].interiors),
        "OPENING_THROUGHNESS_UNRESOLVED",
        "Opposite faces have different opening inventories",
    )
    remaining = list(high_shape[1].interiors)
    for lower in low_shape[1].interiors:
        hits = [
            r
            for r in remaining
            if Polygon(lower).boundary.hausdorff_distance(Polygon(r).boundary) <= tolerance
        ]
        require(
            len(hits) == 1, "OPENING_THROUGHNESS_UNRESOLVED", "Opening does not match both thickness faces"
        )
        remaining.remove(hits[0])
        openings.append(
            dict(
                kind="CUTOUT",
                segments=_ring(lower.coords, refs),
                sourceRefs=refs,
                observedPoints=[list(p) for p in list(lower.coords)[:-1]],
            )
        )
    material = dict(
        status="MATCHED",
        basis="FINAL_SHELL_AND_APPROVED_SUPPLEMENTS",
        outline=dict(kind="OUTLINE", segments=_ring(shape.exterior.coords, refs), sourceRefs=refs),
        holes=openings,
        operationMappings=[],
        holeEvidence=[],
        modelToleranceMm=tolerance,
    )
    section = dict(
        status="MATCHED",
        bevels=bevels,
        levels=levels,
        thickness=levels[1] - levels[0],
        faces=[dict(points=[project(p, frame) for p in f["loops"][0]]) for f in shell["faces"]],
        sourceRef=shell["sourceRef"],
        basis="MEASURED_FINAL_FACES",
    )
    return dict(
        partType="FLAT_PLATE",
        frame=frame,
        thickness=levels[1] - levels[0],
        levels=levels,
        material=material,
        sectionGeometry=section,
        faceRoles=roles,
    )


def interpret(part, shell, regions, tolerance=0.1):
    pairs = face_pairs(regions, tolerance)
    vertices = np.array([p for r in regions for loop in r["loops"] for p in loop])
    flat = []
    for pair in pairs:
        r = regions[pair["regions"][0]]
        distances = (vertices - vertices[0]) @ np.array(r["normal"])
        if abs(float(np.ptp(distances)) - pair["thickness"]) > tolerance:
            continue
        shape = polygon(r["loops"], r["frame"])
        bounds = shape.minimum_rotated_rectangle.exterior.coords
        spans = [math.dist(bounds[i], bounds[i + 1]) for i in (0, 1)]
        if pair["thickness"] < min(spans) - tolerance:
            flat.append(pair)
    # A narrow folded strip can also be a thick extrusion. Measure both and use
    # the repeated local thickness/complete face attribution before choosing.
    folded = folded_candidates(shell, regions, pairs, tolerance)
    declared = None
    profile = part.get("profile", {}).get("value")
    if isinstance(profile, str):
        match = re.fullmatch(r"PL(\d+(?:\.\d+)?)(?:\*\d+(?:\.\d+)?)?", profile)
        declared = float(match[1]) if match else None
    choices = [(p["thickness"], "flat", p) for p in flat] + [(f["thickness"], "folded", f) for f in folded]
    require(
        bool(choices),
        "FEATURE_INTERPRETATION_UNSUPPORTED",
        "Closed shell has no proven flat or sharp folded plate interpretation",
    )
    choices.sort(key=lambda item: item[0])
    if declared is not None:
        matching = [item for item in choices if abs(item[0] - declared) <= tolerance]
        require(
            len(matching) == 1,
            "DECLARED_THICKNESS_CONFLICT",
            "Declared thickness does not match one measured interpretation",
        )
        chosen = matching[0]
    else:
        require(
            len(choices) == 1,
            "GEOMETRIC_INTERPRETATION_AMBIGUOUS",
            "More than one material interpretation; thickness evidence required",
        )
        chosen = choices[0]
    result = (
        flat_features(part, shell, regions, chosen[2], tolerance)
        if chosen[1] == "flat"
        else folded_features(part, shell, regions, chosen[2], tolerance)
    )
    result["interpretations"] = [dict(thickness=t, kind=k) for t, k, _ in choices]
    result["declaredThickness"] = declared
    return result


def folded_candidates(shell, regions, pairs, tolerance):
    result, tried = [], []
    face_region = {i: ri for ri, r in enumerate(regions) for i in r["faces"]}
    adjacency = {tuple(sorted(face_region[i] for i in e["faces"])) for e in shell["edges"]}
    for seed in sorted(pairs, key=lambda p: p["thickness"]):
        t = seed["thickness"]
        if any(abs(t - prev) <= tolerance for prev in tried):
            continue
        tried.append(t)
        group = [p for p in pairs if abs(p["thickness"] - t) <= tolerance]
        used = [i for p in group for i in p["regions"]]
        if len(group) < 2 or len(set(used)) != len(used):
            continue
        graph = {i: set() for i in range(len(group))}
        for i, a in enumerate(group):
            for j, b in enumerate(group[i + 1 :], i + 1):
                connections = [
                    (x, y) for x in a["regions"] for y in b["regions"] if tuple(sorted((x, y))) in adjacency
                ]
                if (
                    len(connections) == 2
                    and len({x for x, _ in connections}) == 2
                    and len({y for _, y in connections}) == 2
                ):
                    graph[i].add(j)
                    graph[j].add(i)
        ends = [i for i in graph if len(graph[i]) == 1]
        if len(ends) != 2 or any(len(v) not in (1, 2) for v in graph.values()):
            continue
        order, current = [], ends[0]
        while current not in order:
            order.append(current)
            onward = graph[current] - set(order)
            if not onward:
                break
            current = next(iter(onward))
        if len(order) != len(group):
            continue
        # All remaining regions must be shared side faces or end closures.
        rest = [i for i in range(len(regions)) if i not in used]
        if len(rest) != 4:
            continue
        normals = [regions[p["regions"][0]]["normal"] for p in group]
        sides = [i for i in rest if all(abs(np.dot(regions[i]["normal"], n)) < 1e-5 for n in normals)]
        if len(sides) != 2 or np.dot(regions[sides[0]]["normal"], regions[sides[1]]["normal"]) > -1 + 1e-7:
            continue
        result.append(
            dict(
                thickness=t,
                pairs=[group[i] for i in order],
                sides=sides,
                ends=[i for i in rest if i not in sides],
                adjacency=sorted(adjacency),
            )
        )
    return result


def folded_features(part, shell, regions, candidate, tolerance):
    thickness = candidate["thickness"]
    width_axis = np.array(regions[candidate["sides"][0]]["normal"])
    segments, roles = [], []
    previous_a = None
    adjacency = set(tuple(x) for x in candidate["adjacency"])
    preferred = _preferred(part)
    if preferred:
        width_sign = next(a for a in reversed(frame_axes(preferred)) if abs(np.dot(width_axis, a)) > 1e-6)
        if np.dot(width_axis, width_sign) < 0:
            width_axis = -width_axis
    ordered_pairs = list(candidate["pairs"])
    if preferred:

        def position(pair):
            points = [p for ri in pair["regions"] for p in regions[ri]["loops"][0]]
            return tuple(round(v, 4) for v in project(np.mean(points, axis=0), preferred))

        if position(ordered_pairs[-1]) < position(ordered_pairs[0]):
            ordered_pairs.reverse()
    for index, pair in enumerate(ordered_pairs):
        ai, bi = pair["regions"]
        if previous_a is None and preferred:
            sign = next(
                a for a in reversed(frame_axes(preferred)) if abs(np.dot(regions[ai]["normal"], a)) > 1e-6
            )
            if np.dot(regions[ai]["normal"], sign) < 0:
                ai, bi = bi, ai
        if previous_a is not None and tuple(sorted((previous_a, ai))) not in adjacency:
            ai, bi = bi, ai
        previous_a = ai
        a, b = regions[ai], regions[bi]
        frame = make_frame(
            a["normal"],
            a["loops"][0][0],
            dict(
                origin=a["loops"][0][0],
                axisX=np.cross(width_axis, a["normal"]).tolist(),
                axisY=width_axis.tolist(),
            ),
        )
        pa = list(polygon(a["loops"], frame).simplify(tolerance / 100).exterior.coords)[:-1]
        pb = list(polygon(b["loops"], frame).simplify(tolerance / 100).exterior.coords)[:-1]
        require(
            len(a["loops"]) == len(b["loops"]) == 1 and len(pa) == len(pb) == 4,
            "FOLD_SEGMENT_BOUNDARY_UNSUPPORTED",
            "Sharp fold segment must have two quadrilateral faces",
        )
        options = []
        for points in (pb, list(reversed(pb))):
            for shift in range(4):
                rotated = points[shift:] + points[:shift]
                options.append((sum(math.dist(p, q) ** 2 for p, q in zip(pa, rotated)), rotated))
        matched = min(options, key=lambda x: x[0])[1]
        mid = [[(x + y) / 2 for x, y in zip(p, q)] for p, q in zip(pa, matched)]
        frame["origin"] = (np.array(frame["origin"]) - np.array(a["normal"]) * thickness / 2).tolist()
        polygon_mid = Polygon(mid)
        require(polygon_mid.is_valid, "FOLD_MIDPLANE_INVALID", "Invalid middle surface")
        bounds = polygon_mid.bounds
        segments.append(
            dict(
                id=f"segment:{index}",
                regions=[ai, bi],
                frame=frame,
                polygon=mid,
                length=bounds[2] - bounds[0],
                width=bounds[3] - bounds[1],
                thickness=pair["thickness"],
                normal=a["normal"],
                sourceRefs=a["sourceRefs"] + b["sourceRefs"],
            )
        )
        for r in (a, b):
            roles.extend(
                dict(faceIndex=i, region=r["id"], role="SEGMENT_THICKNESS_FACE", segment=index)
                for i in r["faces"]
            )
    bends = []
    for i, (a, b) in enumerate(zip(segments, segments[1:])):
        dot = float(np.clip(np.dot(a["normal"], b["normal"]), -1, 1))
        turn = math.degrees(math.atan2(float(np.dot(width_axis, np.cross(a["normal"], b["normal"]))), dot))
        require(abs(turn) > 1e-5, "FOLD_ZERO_ANGLE", "Coplanar split is not a bend")
        bends.append(
            dict(
                id=f"bend:{i}",
                segments=[a["id"], b["id"]],
                angle=abs(turn),
                signedAngle=turn,
                direction="A" if turn > 0 else "B",
                radius=0.0,
                basis="SHARP_FINAL_FACE_INTERSECTION",
            )
        )
    for name, role in (("sides", "SIDE_FACE"), ("ends", "END_FACE")):
        for ri in candidate[name]:
            roles.extend(dict(faceIndex=i, region=regions[ri]["id"], role=role) for i in regions[ri]["faces"])
    unfolded = unfold_segments(segments, tolerance)
    end_facts = []
    for ri in candidate["ends"]:
        r = regions[ri]
        near = [s for s in segments if all(tuple(sorted((ri, i))) in adjacency for i in s["regions"])]
        require(len(near) == 1, "END_SEGMENT_UNRESOLVED", "End face does not close one segment")
        s = near[0]
        local = [float(np.dot(r["normal"], axis)) for axis in frame_axes(s["frame"])]
        end_facts.append(
            dict(
                r,
                segment=s["id"],
                elevationAngle=math.degrees(math.atan2(abs(local[2]), abs(local[0]))),
                planAngle=math.degrees(math.atan2(abs(local[1]), abs(local[0]))),
            )
        )
    return dict(
        partType="FOLDED_PLATE",
        thickness=thickness,
        segments=segments,
        bends=bends,
        faceRoles=roles,
        holes=[],
        endFaces=[regions[i] for i in candidate["ends"]],
        endCuts=end_facts,
        unfolding=dict(
            unfolded,
            basis="GEOMETRIC_MIDDLE_SURFACES",
            manufacturingStatus="NOT_SPECIFIED",
            reason="Manufacturing bend allowance is not supplied",
        ),
    )


def unfold_segments(segments, tolerance):
    """Join middle surfaces at their measured shared hinge, without bend allowance."""
    for index, s in enumerate(segments):
        axes = frame_axes(s["frame"])
        world = [
            np.array(s["frame"]["origin"]) + p[0] * np.array(axes[0]) + p[1] * np.array(axes[1])
            for p in s["polygon"]
        ]
        s["middleVertices"] = [p.tolist() for p in world]
        offset = np.zeros(2)
        if index:
            prev = segments[index - 1]
            matches = [
                (i, j)
                for i, p in enumerate(prev["middleVertices"])
                for j, q in enumerate(world)
                if np.linalg.norm(np.array(p) - q) <= tolerance
            ]
            require(
                len(matches) == 2, "FOLD_HINGE_UNRESOLVED", "Middle surfaces must share one complete hinge"
            )
            offsets = [np.array(prev["unfoldedPolygon"][i]) - s["polygon"][j] for i, j in matches]
            require(
                np.linalg.norm(offsets[0] - offsets[1]) <= tolerance,
                "FOLD_UNFOLD_CONFLICT",
                "Hinge endpoints disagree in unfolding",
            )
            offset = np.mean(offsets, axis=0)
        s["unfoldOffset"] = offset.tolist()
        s["unfoldedPolygon"] = [(np.array(p) + offset).tolist() for p in s["polygon"]]
    points = np.array([p for s in segments for p in s["unfoldedPolygon"]])
    origin = points.min(axis=0)
    for s in segments:
        s["unfoldOffset"] = (np.array(s["unfoldOffset"]) - origin).tolist()
        s["unfoldedPolygon"] = [(np.array(p) - origin).tolist() for p in s["unfoldedPolygon"]]
    return dict(
        sizeX=float(np.ptp(points[:, 0])),
        sizeY=float(np.ptp(points[:, 1])),
        area=sum(Polygon(s["polygon"]).area for s in segments),
        segmentPolygons=[dict(segment=s["id"], polygon=s["unfoldedPolygon"]) for s in segments],
    )
