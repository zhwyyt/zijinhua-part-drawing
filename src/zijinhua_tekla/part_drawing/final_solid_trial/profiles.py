"""Profile wall facts from clipped final faces and proven local hole evidence."""

import math

import numpy as np
from shapely import union_all
from shapely.geometry import Polygon, Point, LineString

from ..analytic_geometry import frame_axes, project, line
from .shell import require, read
from .profile_sections import local_faces, measured_section, partition_section


def _pieces(shape):
    return (
        [shape]
        if shape.geom_type == "Polygon"
        else [g for g in getattr(shape, "geoms", []) if g.geom_type == "Polygon"]
    )


def _halfplane(points, normal, offset):
    output = []
    for a, b in zip(points, points[1:] + points[:1]):
        da, db = float(np.dot(a, normal) - offset), float(np.dot(b, normal) - offset)
        if da <= 0:
            output.append(a)
        if (da < 0 < db) or (db < 0 < da):
            output.append(np.array(a) + (np.array(b) - a) * da / (da - db))
    return output


def clip_face(face, bounds, tolerance=0.1):
    """Intersect an actual planar face with a wall's infinite axial prism."""
    rings = [np.array(r) for r in face["localLoops"]]
    origin = rings[0][0]
    normal = np.array(face["localNormal"])
    edges = [b - a for r in rings for a, b in zip(r, np.roll(r, -1, axis=0))]
    u = max(edges, key=np.linalg.norm)
    u = u / np.linalg.norm(u)
    v = np.cross(normal, u)
    v = v / np.linalg.norm(v)
    rings2 = [[(float(np.dot(p - origin, u)), float(np.dot(p - origin, v))) for p in r] for r in rings]
    rings2.sort(key=lambda r: Polygon(r).area, reverse=True)
    original = Polygon(rings2[0], rings2[1:])
    require(original.is_valid, "PROFILE_FACE_INVALID", "Face cannot be projected for wall attribution")
    extent = max(abs(c) for r in rings2 for p in r for c in p) + 10000
    clip = [
        np.array(p) for p in [(-extent, -extent), (extent, -extent), (extent, extent), (-extent, extent)]
    ]
    for axis in (1, 2):
        for sign, bound in ((-1, bounds[axis - 1]), (1, bounds[axis + 1])):
            n = sign * np.array([u[axis], v[axis]])
            limit = sign * (bound - origin[axis])
            if np.linalg.norm(n) < 1e-8:
                if limit < -tolerance:
                    return [], original.area
                if abs(limit) <= tolerance and sign * normal[axis] < -0.99:
                    return [], original.area
                continue
            clip = _halfplane(clip, n, limit)
            if len(clip) < 3:
                return [], original.area
    shape = original.intersection(Polygon(clip))

    def lift(r):
        return [(origin + x * u + y * v).tolist() for x, y in list(r.coords)[:-1]]

    return [
        dict(
            localLoops=[lift(p.exterior)] + [lift(r) for r in p.interiors],
            area=p.area,
            faceIndex=face["index"],
            sourceRef=face["sourceRef"],
            normal=face["localNormal"],
        )
        for p in _pieces(shape)
        if p.area > 1e-6
    ], original.area


def wall_frame(frame, wall):
    axes = frame_axes(frame)
    width = wall["widthAxis"]
    result = dict(origin=frame["origin"], axisX=list(axes[0]), axisY=list(axes[width]))
    # local normal is +Z for XY, -Y for XZ.
    sign = 1 if width == 1 else -1
    axis = wall["thicknessAxis"]
    bounds = wall["sectionBounds"]
    levels = sorted([sign * bounds[axis - 1], sign * bounds[axis + 1]])
    return result, levels, sign


def _segments(ring, refs):
    ps = [list(p) for p in list(ring.coords)[:-1]]
    return [line(a, b, refs) for a, b in zip(ps, ps[1:] + ps[:1])]


def _local_penetration(group, wall, all_walls, tolerance):
    rows = read(group["axisIntersections"])
    local = []
    for row in rows:
        points = row["targetPoints"]
        mapped = []
        for other in all_walls:
            hits = [
                p
                for p in points
                if any(abs(project(p, other["frame"])[2] - z) <= tolerance for z in other["levels"])
            ]
            require(
                len(hits) == 2,
                "PROFILE_MULTI_WALL_HOLE_UNPROVEN",
                "Each penetrated wall needs both physical surface intersections",
            )
            mapped.extend(hits)
            if other["id"] == wall["id"]:
                local.append(dict(row, targetPoints=hits))
        require(
            len(mapped) == len(points) and len({tuple(p) for p in mapped}) == len(points),
            "PROFILE_MULTI_WALL_HOLE_UNPROVEN",
            "Physical intersections must map to distinct local material intervals",
        )
    return dict(group, axisIntersections=dict(group["axisIntersections"], value=local))


def interpret_profile(part, shell, tolerance=0.1):
    frame, faces = local_faces(part, shell)
    station, section, extent = measured_section(faces, tolerance)
    family, walls = partition_section(section, tolerance)
    require(
        family != "RECTANGULAR_WALL_SECTION",
        "PROFILE_FAMILY_UNSUPPORTED",
        "Measured wall graph needs a dedicated section definition",
    )
    coverage = {f["index"]: [] for f in faces}
    face_areas = {}
    for wall in walls:
        patches = []
        for face in faces:
            pieces, area = clip_face(face, wall["sectionBounds"], tolerance)
            face_areas[face["index"]] = area
            for piece in pieces:
                coverage[face["index"]].append(dict(wallId=wall["id"], area=piece["area"]))
                patches.append(piece)
        wf, levels, sign = wall_frame(frame, wall)
        width, normal = wall["widthAxis"], wall["thicknessAxis"]
        projections = []
        caps = [[], []]
        for p in patches:
            rings = [[[q[0], q[width]] for q in r] for r in p["localLoops"]]
            if abs(p["normal"][normal]) > 1e-7:
                rings.sort(key=lambda r: Polygon(r).area, reverse=True)
                shape = Polygon(rings[0], rings[1:])
                if shape.area > tolerance**2:
                    require(shape.is_valid, "PROFILE_PROJECTION_INVALID", "Wall projection is invalid")
                    projections.append(shape)
                    for i, level in enumerate(levels):
                        if all(
                            abs(sign * q[normal] - level) <= tolerance
                            for r in p["localLoops"]
                            for q in r
                        ):
                            caps[i].append(shape)
        projected_shape = union_all(projections, grid_size=1e-5)
        material_shape = projected_shape.simplify(tolerance / 4, preserve_topology=True)
        require(
            material_shape.geom_type == "Polygon" and material_shape.is_valid,
            "PROFILE_WALL_DISCONNECTED",
            "Wall material is not one connected region",
        )
        refs = sorted({p["sourceRef"] for p in patches})
        openings = []
        cap_shapes = [union_all(c, grid_size=1e-5) for c in caps]
        for ring in material_shape.interiors:
            matches = []
            for cap in cap_shapes:
                matches.append(
                    any(
                        Polygon(ring).boundary.hausdorff_distance(Polygon(r).boundary) <= tolerance
                        for piece in _pieces(cap)
                        for r in piece.interiors
                    )
                )
            require(
                all(matches),
                "PROFILE_OPENING_DEPTH_UNRESOLVED",
                "Opening must be observed on both local wall surfaces",
            )
            openings.append(
                dict(
                    kind="CUTOUT",
                    segments=_segments(ring, refs),
                    observedPoints=[list(p) for p in list(ring.coords)[:-1]],
                    sourceRefs=refs,
                )
            )
        wall.update(
            frame=wf,
            levels=levels,
            patches=patches,
            maxProjectionSimplificationMm=projected_shape.boundary.hausdorff_distance(
                material_shape.boundary
            ),
            material=dict(
                status="MATCHED",
                basis="FINAL_PROFILE_WALL_AND_APPROVED_SUPPLEMENTS",
                outline=dict(
                    kind="OUTLINE", segments=_segments(material_shape.exterior, refs), sourceRefs=refs
                ),
                holes=openings,
                operationMappings=[],
                holeEvidence=[],
                modelToleranceMm=tolerance,
            ),
            localBounds=list(material_shape.bounds),
            sourceFaces=sorted({p["faceIndex"] for p in patches}),
        )
    roles = []
    for face in faces:
        assignments = coverage[face["index"]]
        require(
            assignments
            and abs(sum(a["area"] for a in assignments) - face_areas[face["index"]])
            <= max(tolerance**2, face_areas[face["index"]] * 1e-6),
            "PROFILE_FACE_COVERAGE_CONFLICT",
            "Wall clipping must cover every source face without overlapping area",
            face["sourceRef"],
        )
        roles.append(dict(faceIndex=face["index"], role="PROFILE_BOUNDARY", wallAssignments=assignments))
    return dict(
        partType="STRAIGHT_PROFILE",
        profileFamily=family,
        frame=frame,
        axialExtent=extent,
        length=extent[1] - extent[0],
        section=dict(
            station=station,
            polygon=[list(p) for p in list(section.exterior.coords)[:-1]],
            interiors=[[list(p) for p in list(r.coords)[:-1]] for r in section.interiors],
            area=section.area,
            bounds=list(section.bounds),
            basis="MEASURED_FINAL_FACE_SECTION",
        ),
        walls=walls,
        faceRoles=roles,
    )


def supplement_profile(part, fact, shell, tolerance=0.1):
    from .supplements import (
        supplement_curves,
        supplement_holes,
        _target,
        _group_candidates,
        shape_polygon,
    )

    groups = read(part.get("boltGroups"), f"parts/{part['id']}/boltGroups")
    require(len({g["id"] for g in groups}) == len(groups), "DUPLICATE_HOLE_GROUP", "Repeated bolt group")
    axes = frame_axes(fact["frame"])
    attachments = []
    wall_groups = {w["id"]: [] for w in fact["walls"]}
    for index, group in enumerate(groups):
        path = f"parts/{part['id']}/boltGroups/{index}"
        _target(group, part["id"], path)
        axis = np.array(frame_axes(read(group["coordinateSystem"]))[2])
        stud = read(group["properties"].get("BoltStandard")) == "STUD"
        hits = []
        for wall in fact["walls"]:
            if abs(np.dot(axis, axes[wall["thicknessAxis"]])) < 1 - 1e-7:
                continue
            holes = (
                [dict(center=project(p, wall["frame"])[:2]) for p in read(group["positions"])]
                if stud
                else _group_candidates(group, wall["frame"], path)
            )
            outer = shape_polygon(wall["material"]["outline"]["segments"], tolerance)
            if not holes or not all(outer.buffer(tolerance).covers(Point(h["center"])) for h in holes):
                continue
            if stud or not read(group["properties"].get("Bolt")):
                zs = [project(p, wall["frame"])[2] for p in read(group["positions"])]
                if not all(
                    wall["levels"][0] - tolerance <= z <= wall["levels"][1] + tolerance for z in zs
                ):
                    continue
            hits.append(wall)
        require(
            bool(hits),
            "PROFILE_HOLE_WALL_UNRESOLVED",
            "Hole or attachment has no proven local wall",
            path,
        )
        if stud:
            require(len(hits) == 1, "PROFILE_ATTACHMENT_AMBIGUOUS", "Stud must attach to one wall", path)
            attachments.append(
                dict(
                    groupId=group["id"],
                    kind="STUD",
                    count=len(read(group["positions"])),
                    wallId=hits[0]["id"],
                    sourceRef=path,
                )
            )
            continue
        # For hardware crossing several walls require actual SDK penetration
        # evidence; a projected axis alone does not prove drilling both walls.
        if len(hits) > 1:
            require(
                bool(group.get("axisIntersections", {}).get("value")),
                "PROFILE_MULTI_WALL_HOLE_UNPROVEN",
                "Several intersected walls require physical penetration evidence",
                path,
            )
        for wall in hits:
            local_group = _local_penetration(group, wall, hits, tolerance) if len(hits) > 1 else group
            wall_groups[wall["id"]].append((index, local_group))
    holes = []
    cuts = []
    for wall in fact["walls"]:
        feature = dict(wall, finalSolid=shell)
        from .profile_curves import profile_curve_evidence

        curves, curve_shell = profile_curve_evidence(part, wall, shell, tolerance)
        wall["analyticReliefs"] = curves
        supplement_curves(part, feature, curve_shell, tolerance)
        if curves:
            wall["material"]["outline"][
                "representation"
            ] = "TESSELLATED_PROJECTION_WITH_ANALYTIC_RELIEF_SURFACES"
            projected_reliefs = union_all(
                [
                    Polygon([project(p, wall["frame"])[:2] for p in c["observedLoops"][0]]).buffer(
                        tolerance
                    )
                    for c in curves
                ]
            )
            for segment in wall["material"]["outline"]["segments"]:
                if segment["kind"] == "LINE" and projected_reliefs.covers(
                    LineString([segment["start"], segment["end"]])
                ):
                    segment["representation"] = "ANALYTIC_RELIEF_PROJECTION_SAMPLE"
        # Keep observed cutouts while proving all groups together. Source paths
        # are remapped to original group indices after the local pass.
        selected = wall_groups[wall["id"]]
        local_part = dict(part, boltGroups=dict(status="READ", value=[g for _, g in selected]))
        supplement_holes(local_part, feature, tolerance)

        def remap(value):
            if isinstance(value, str):
                for j, (original, _) in reversed(list(enumerate(selected))):
                    prefix = f"parts/{part['id']}/boltGroups/{j}"
                    if value == prefix or value.startswith(prefix + "/"):
                        return f"parts/{part['id']}/boltGroups/{original}" + value[len(prefix) :]
            if isinstance(value, dict):
                return {k: remap(v) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v) for v in value]
            return value

        wall["material"] = remap(feature["material"])
        for i, h in enumerate(wall["material"]["holes"]):
            h.update(wallId=wall["id"], wallRole=wall["role"], localThickness=wall["thickness"])
            h.setdefault("id", wall["id"] + f"/cutout:{i+1}")
            holes.append(h)
        normal_axis = wall["thicknessAxis"]
        for patch in wall["patches"]:
            points = [p for r in patch["localLoops"] for p in r]
            n = patch["normal"]
            depths = [p[normal_axis] for p in points]
            if max(depths) - min(depths) <= tolerance:
                continue
            xlo, xhi = min(p[0] for p in points), max(p[0] for p in points)
            projected = LineString([[p[0], p[wall["widthAxis"]]] for p in patch["localLoops"][0]])
            internal = any(
                shape_polygon(h["segments"]).boundary.buffer(tolerance).covers(projected)
                for h in wall["material"]["holes"]
            )
            kind = (
                "OPENING_WALL"
                if internal
                else "LONGITUDINAL_EDGE" if abs(n[0]) < 1e-7 else "END_OR_NOTCH_FACE"
            )
            if any(c["faceIndex"] == patch["faceIndex"] for c in wall["analyticReliefs"]):
                kind = "ANALYTIC_RELIEF_FACE"
            cut = dict(
                id=f"{wall['id']}/face:{patch['faceIndex']}",
                wallId=wall["id"],
                kind=kind,
                axialRange=[xlo, xhi],
                side="START" if (xlo + xhi) / 2 < sum(fact["axialExtent"]) / 2 else "END",
                normal=n,
                loops=patch["localLoops"],
                sourceRef=patch["sourceRef"],
                elevationAngle=math.degrees(math.atan2(n[1], abs(n[0]))) if abs(n[0]) > 1e-7 else None,
                planAngle=math.degrees(math.atan2(n[2], abs(n[0]))) if abs(n[0]) > 1e-7 else None,
                depth=max(depths) - min(depths),
                remainingLand=max(0.0, wall["thickness"] - (max(depths) - min(depths))),
            )
            cuts.append(cut)
    fact.update(holes=holes, attachments=attachments, endCuts=cuts)


def profile_expressions(fact):
    from ..native_expressions import _facts
    from ..expression_requirements import _requirements

    requirements = []
    wall_facts = []

    def add(feature, aspect, value, refs=()):
        requirements.append(
            dict(
                id=feature + "/" + aspect,
                feature=feature,
                aspect=aspect,
                expected=value,
                rule="FINAL-PROFILE-01",
                alternatives=["dimension", "view", "section", "note"],
                sourceRefs=list(refs),
            )
        )

    add("profile", "specification", fact["profile"])
    add("profile", "section", fact["section"])
    add("profile", "length", fact["length"])
    add("profile", "family", fact["profileFamily"])
    for wall in fact["walls"]:
        measured = _facts(wall["material"])
        sampled = set()
        for i, segment in enumerate(wall["material"]["outline"]["segments"]):
            if segment.get("representation") == "ANALYTIC_RELIEF_PROJECTION_SAMPLE":
                measured["segments"][i]["kind"] = "SAMPLED_ANALYTIC_BOUNDARY"
                sampled.add(i)
        count = len(wall["material"]["outline"]["segments"])
        interior_points = {f"vertex:{i}" for i in sampled if (i - 1) % count in sampled}
        measured["datum"] = min(
            (k for k in measured["points"] if k.startswith("vertex:") and k not in interior_points),
            key=lambda k: (measured["points"][k], k),
        )
        local, groups = _requirements(fact["partPosition"], wall["thickness"], measured, {})
        for requirement in local:
            if requirement["feature"] in interior_points and requirement["aspect"].startswith(
                "location_"
            ):
                continue
            requirement = dict(
                requirement,
                id=wall["id"] + "/" + requirement["id"],
                wallId=wall["id"],
                wallRole=wall["role"],
            )
            if (
                requirement["aspect"] == "shape"
                and requirement["expected"].get("kind") == "SAMPLED_ANALYTIC_BOUNDARY"
            ):
                requirement.update(rule="FINAL-PROFILE-RELIEF-01", alternatives=["analytic_curve_view"])
            requirements.append(requirement)
        add(
            wall["id"],
            "section_location",
            dict(bounds=wall["sectionBounds"], frame=fact["frame"], neighbors=wall["neighbors"]),
        )
        for curve in wall["analyticReliefs"]:
            add(
                wall["id"] + f"/relief:{curve['faceIndex']}",
                "analytic_surface",
                curve,
                [curve["sourceRef"]],
            )
        wall_facts.append(
            dict(id=wall["id"], role=wall["role"], frame=wall["frame"], facts=measured, groups=groups)
        )
    for cut in fact["endCuts"]:
        add(cut["id"], "shape_and_orientation", cut, [cut["sourceRef"]])
    for hole in fact["holes"]:
        add(
            hole["wallId"] + "/" + hole["id"],
            "wall_ownership",
            dict(wallId=hole["wallId"], role=hole["wallRole"], localThickness=hole["localThickness"]),
            hole["sourceRefs"],
        )
    return dict(
        schemaVersion="profileExpressionRequirements.v1",
        requirements=requirements,
        wallFacts=wall_facts,
        sectionRequirements=[dict(id="profile-section", **fact["section"], frame=fact["frame"])],
        requiredViews=["profile-section"] + [w["id"] for w in fact["walls"]],
        holes=fact["holes"],
        attachments=fact["attachments"],
    )
