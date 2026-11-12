"""Single-sided thickness steps measured from final faces, without cut replay."""

import math

import numpy as np
from shapely import union_all
from shapely.geometry import Polygon, LineString, box

from ..analytic_geometry import project, frame_axes, line
from .shell import polygon, require


def region_shape(region):
    return Polygon(region["polygon"], region["interiors"])


def _rings(shape):
    return dict(
        polygon=[list(p) for p in list(shape.exterior.coords)[:-1]],
        interiors=[[list(p) for p in list(r.coords)[:-1]] for r in shape.interiors],
    )


def stepped_features(shell, regions, frame, tolerance):
    axis = np.array(frame_axes(frame)[2])
    records = []
    for region in regions:
        pts = np.array(
            [
                project(p, frame)
                for i in region["faces"]
                for loop in shell["faces"][i]["loops"]
                for p in loop
            ]
        )
        records.append((region, pts))
    horizontal = [(r, ps) for r, ps in records if np.ptp(ps[:, 2]) <= tolerance]
    levels = [min(ps[:, 2].min() for _, ps in records), max(ps[:, 2].max() for _, ps in records)]
    shapes = {r["id"]: polygon(r["loops"], frame) for r, _ in horizontal}
    whole = union_all(list(shapes.values()))
    bases = [
        (r, ps)
        for r, ps in horizontal
        if min(abs(float(np.mean(ps[:, 2])) - z) for z in levels) <= tolerance
        and shapes[r["id"]].buffer(tolerance).covers(whole)
    ]
    require(len(bases) == 1, "STEP_BASE_UNRESOLVED", "Single-sided steps require one complete back face")
    base, base_pts = bases[0]
    base_z = float(np.mean(base_pts[:, 2]))
    base_sign = float(np.dot(base["normal"], axis))
    outer = shapes[base["id"]]
    require(
        not outer.interiors,
        "STEP_OPENINGS_UNSUPPORTED",
        "Observed openings in stepped caps need local wall matching",
    )
    side = "LOW" if base_z > sum(levels) / 2 else "HIGH"
    front = [(r, ps) for r, ps in horizontal if r is not base]
    require(len(front) >= 2, "STEP_REGIONS_MISSING", "Thickness step requires multiple front regions")
    require(
        all(np.dot(r["normal"], axis) * base_sign < -0.99 for r, _ in front),
        "STEP_ORIENTATION_CONFLICT",
        "Front regions must face away from the complete back face",
    )
    coverage = union_all([shapes[r["id"]] for r, _ in front])
    require(
        coverage.buffer(tolerance).covers(outer)
        and outer.buffer(tolerance).covers(coverage)
        and outer.symmetric_difference(coverage).area <= tolerance * outer.length,
        "STEP_COVERAGE_CONFLICT",
        "Local thickness regions do not cover the complete back face",
    )
    for i, (a, _) in enumerate(front):
        for b, _ in front[i + 1 :]:
            # Compare eroded interiors: numerical boundary overlap is allowed,
            # but real overlapping material columns are not.
            require(
                not shapes[a["id"]].buffer(-tolerance).intersects(shapes[b["id"]].buffer(-tolerance)),
                "STEP_OVERLAP_CONFLICT",
                "Local thickness regions overlap",
            )
    zones, roles = [], []
    for r, ps in front:
        height = float(np.mean(ps[:, 2]))
        local = sorted([base_z, height])
        thickness = local[1] - local[0]
        require(
            thickness > tolerance, "STEP_THICKNESS_INVALID", "Step leaves no positive material thickness"
        )
        zones.append(
            dict(
                id=r["id"],
                **_rings(shapes[r["id"]]),
                levels=local,
                thickness=thickness,
                depth=(levels[1] - levels[0]) - thickness,
                side=side,
                sourceFaces=r["faces"],
                sourceRefs=r["sourceRefs"],
                backFaces=base["faces"],
                measuredHeightRange=[float(ps[:, 2].min()), float(ps[:, 2].max())],
            )
        )
        roles.extend(dict(faceIndex=i, region=r["id"], role="LOCAL_THICKNESS_FACE") for i in r["faces"])
    roles.extend(dict(faceIndex=i, region=base["id"], role="COMPLETE_BACK_FACE") for i in base["faces"])
    by_face = {i: r["id"] for r, _ in horizontal for i in r["faces"]}
    by_zone = {z["id"]: z for z in zones}
    walls = []
    for r, ps in records:
        if r["id"] in shapes:
            continue
        xy = ps[:, :2]
        _, _, vectors = np.linalg.svd(xy - xy.mean(axis=0), full_matrices=False)
        along = vectors[0]
        require(
            max(abs((xy - xy.mean(axis=0)) @ vectors[-1])) <= tolerance,
            "STEP_SLOPED_WALL_UNSUPPORTED",
            "Step walls must be straight and parallel to thickness",
        )
        stations = xy @ along
        a, b = xy[int(np.argmin(stations))], xy[int(np.argmax(stations))]
        trace = LineString([a, b])
        require(trace.length > tolerance, "STEP_WALL_DEGENERATE", "Step wall has no measured width")
        neighbors = {
            by_face[i]
            for e in shell["edges"]
            if set(e["faces"]) & set(r["faces"])
            for i in e["faces"]
            if i in by_face
        }
        internal = base["id"] not in neighbors
        require(
            len(neighbors) == 2 if internal else len(neighbors) >= 2,
            "STEP_WALL_CONNECTION_UNRESOLVED",
            "Wall needs connected measured cap neighbors",
        )
        boundaries_match = (
            all(shapes[n].boundary.buffer(tolerance).covers(trace) for n in neighbors)
            if internal
            else shapes[base["id"]].boundary.buffer(tolerance).covers(trace)
            and union_all([shapes[n].boundary for n in neighbors if n != base["id"]])
            .buffer(tolerance)
            .covers(trace)
        )
        require(
            boundaries_match,
            "STEP_WALL_BOUNDARY_CONFLICT",
            "Wall trace does not match its cap boundaries",
        )
        heights = [
            (
                base_z
                if n == base["id"]
                else (by_zone[n]["levels"][0] if side == "LOW" else by_zone[n]["levels"][1])
            )
            for n in neighbors
        ]
        require(
            max(abs(float(ps[:, 2].min()) - min(heights)), abs(float(ps[:, 2].max()) - max(heights)))
            <= tolerance,
            "STEP_WALL_DEPTH_CONFLICT",
            "Wall endpoints contradict local thickness levels",
        )
        roles.extend(
            dict(faceIndex=i, region=r["id"], role="STEP_WALL" if internal else "OUTER_WALL")
            for i in r["faces"]
        )
        if internal:
            walls.append(
                dict(
                    id="step:" + r["id"],
                    boundary=[a.tolist(), b.tolist()],
                    regions=sorted(neighbors),
                    height=max(heights) - min(heights),
                    side=side,
                    sourceFaces=r["faces"],
                    sourceRefs=r["sourceRefs"],
                )
            )
    require(bool(walls), "STEP_WALLS_MISSING", "No internal wall explains the thickness change")
    # Curved internal steps cannot be inferred from tessellated straight walls.
    for edge in shell["edges"]:
        require(
            edge["apiType"] in (None, "EDGE_TYPE_NORMAL"),
            "STEP_CURVE_UNSUPPORTED",
            "Stepped plate curved edges need dedicated analytic evidence",
        )
    refs = base["sourceRefs"]
    points = list(outer.exterior.coords)[:-1]
    material = dict(
        status="MATCHED",
        basis="FINAL_SHELL_AND_APPROVED_SUPPLEMENTS",
        outline=dict(
            kind="OUTLINE",
            segments=[line(list(a), list(b), refs) for a, b in zip(points, points[1:] + points[:1])],
            sourceRefs=refs,
        ),
        holes=[],
        operationMappings=[],
        holeEvidence=[],
        modelToleranceMm=tolerance,
    )
    return dict(
        partType="STEPPED_PLATE",
        frame=frame,
        thickness=levels[1] - levels[0],
        levels=levels,
        material=material,
        thicknessRegions=zones,
        steps=walls,
        faceRoles=roles,
        stepCoverage=dict(
            differenceAreaMm2=outer.symmetric_difference(coverage).area,
            modelToleranceMm=tolerance,
            basis="FINAL_FACE_PROJECTIONS",
        ),
    )


def step_sections(fact, hole_shapes, tolerance=0.1):
    """Cross each step wall using a hole-free plane and measured material columns."""
    sections = []
    zones = fact["thicknessRegions"]
    span = max(math.dist(a, b) for z in zones for a in z["polygon"] for b in z["polygon"]) * 2
    for wall in fact["steps"]:
        a, b = np.array(wall["boundary"])
        tangent = (b - a) / np.linalg.norm(b - a)
        direction = np.array([-tangent[1], tangent[0]])
        free = [(tolerance, float(np.linalg.norm(b - a)) - tolerance)]
        for hole in hole_shapes:
            stations = (np.array(hole.exterior.coords) - a) @ tangent
            low, high = float(min(stations)) - tolerance, float(max(stations)) + tolerance
            free = [
                (x, y)
                for start, end in free
                for x, y in [(start, min(end, low)), (max(start, high), end)]
                if y - x > tolerance
            ]
        require(bool(free), "STEP_SECTION_UNRESOLVED", "No hole-free section crosses the step wall")
        start, end = max(free, key=lambda interval: interval[1] - interval[0])
        origin = a + (start + end) / 2 * tangent
        cut = LineString([origin - span * direction, origin + span * direction])
        intervals = []
        for zone in zones:
            hit = region_shape(zone).intersection(cut)
            pieces = [hit] if hit.geom_type == "LineString" else list(getattr(hit, "geoms", []))
            for piece in pieces:
                if piece.geom_type != "LineString" or piece.length <= tolerance:
                    continue
                ss = (np.array(piece.coords) - origin) @ direction
                intervals.append(
                    dict(
                        region=zone["id"], extent=[float(min(ss)), float(max(ss))], levels=zone["levels"]
                    )
                )
        # Align only section endpoints within the existing model budget;
        # retain raw extents and the displacement as evidence.
        clusters = []
        for value in sorted(v for item in intervals for v in item["extent"]):
            if not clusters or value - clusters[-1][0] > tolerance:
                clusters.append([])
            clusters[-1].append(value)

        def snap(value):
            return next(sum(c) / len(c) for c in clusters if c[0] <= value <= c[-1])

        rectangles = [
            box(snap(i["extent"][0]), i["levels"][0], snap(i["extent"][1]), i["levels"][1])
            for i in intervals
        ]
        shape = union_all(rectangles)
        require(
            shape.geom_type == "Polygon" and not shape.interiors,
            "STEP_SECTION_UNRESOLVED",
            "Section material is not one connected polygon",
        )
        require(
            set(wall["regions"]) <= {i["region"] for i in intervals},
            "STEP_SECTION_COVERAGE",
            "Section misses a wall's material regions",
        )
        sections.append(
            dict(
                id=f"step-section:{len(sections)+1}",
                stepIds=[wall["id"]],
                origin=origin.tolist(),
                direction=direction.tolist(),
                frame=fact["frame"],
                polygon=[list(p) for p in list(shape.exterior.coords)[:-1]],
                intervals=intervals,
                maxEndpointAdjustmentMm=max(abs(snap(v) - v) for i in intervals for v in i["extent"]),
                sourceRefs=wall["sourceRefs"],
                basis="FINAL_THICKNESS_REGION_INTERSECTION",
            )
        )
    return sections
