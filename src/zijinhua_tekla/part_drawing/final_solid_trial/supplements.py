"""Approved hole and curve evidence, without replaying the cutting history."""

import math
from collections import Counter
import numpy as np
from shapely.geometry import Polygon, Point

from ..analytic_geometry import (
    frame_axes,
    project,
    line,
    contour_segments,
    parameter,
    arc,
    _at,
    hole_segments,
    simple_ring,
)
from ..native_geometry import _hole_candidates, _hole_effect, _boolean_cut_loops, fit_cut_produced_contours
from ..planar_cut_region import cut_region, circle_shape
from ..target_holes import physical_penetration
from ..solid_prisms import _shape_match
from .shell import read, require, EvidenceError


def shape_polygon(segments, tolerance=0.1):
    points = []
    for s in segments:
        count = 1
        if s["kind"] == "ARC":
            step = 2 * math.acos(max(-1, 1 - min(tolerance / 20, s["radius"] / 2) / s["radius"]))
            count = max(2, math.ceil(abs(s["sweepRadians"]) / step))
        points.extend(_at(s, i / count) for i in range(count))
    return Polygon(points)


def _contour(part, frame, source):
    node = read(part.get("modelingContour"), source)
    if isinstance(node, dict):
        node = read(node, source)
    points = [project(item["point"], frame)[:2] for item in node]
    chamfers = []
    for item in node:
        c = item["chamfer"]
        chamfers.append((read(c["Type"])["name"], read(c["X"]), read(c["Y"])))
    return contour_segments(points, chamfers, [source + f"/{i}" for i in range(len(points))])


def _replace_arcs(segments, candidates, tolerance):
    """Only replace observed chord runs backed by an explicit analytic arc."""
    for candidate in candidates:
        if candidate["kind"] != "ARC":
            continue
        output = []
        for s in segments:
            ta = parameter(s["start"], candidate, tolerance)
            tb = parameter(s["end"], candidate, tolerance)
            midpoint = [(a + b) / 2 for a, b in zip(s["start"], s["end"])]
            chord_error = abs(math.dist(midpoint, candidate["center"]) - candidate["radius"])
            if (
                s["kind"] == "LINE"
                and ta is not None
                and tb is not None
                and abs(ta - tb) > 1e-10
                and chord_error <= tolerance
            ):
                item = arc(
                    s["start"],
                    s["end"],
                    candidate["center"],
                    candidate["sweepRadians"] * (tb - ta),
                    sorted(set(s["sourceRefs"] + candidate["sourceRefs"])),
                )
                if (
                    output
                    and output[-1]["kind"] == "ARC"
                    and math.dist(output[-1]["center"], item["center"]) < 1e-8
                    and output[-1]["sweepRadians"] * item["sweepRadians"] > 0
                ):
                    output[-1]["end"] = item["end"]
                    output[-1]["sweepRadians"] += item["sweepRadians"]
                else:
                    output.append(item)
            else:
                output.append(s)
        segments = output
    return segments


def supplement_curves(part, feature, shell, tolerance):
    frame = feature["frame"]
    material = feature["material"]
    path = f"parts/{part['id']}"
    # Optional history is consulted solely for curve semantics, never for a
    # stock/material consistency test or an operation inventory gate.
    stock = []
    try:
        stock = _contour(part, frame, path + "/modelingContour")
    except (KeyError, TypeError, ValueError):
        pass
    operations = part.get("operations", {}).get("value") or []
    cutter_loops = _boolean_cut_loops(operations, frame, path)
    if not stock and cutter_loops:
        raw = part.get("rawSolid", {}).get("value") or {}
        for f in raw.get("faces", {}).get("value") or []:
            if abs(abs(np.dot(read(f["normal"]), frame_axes(frame)[2])) - 1) > 1e-6:
                continue
            loop = read(read(f["loops"])[0]["vertices"])
            points = [project(p, frame)[:2] for p in loop]
            stock = [line(a, b, [path + "/rawSolid"]) for a, b in zip(points, points[1:] + points[:1])]
            break
    for shape in [material["outline"]] + material["holes"]:
        segments = _replace_arcs(shape["segments"], stock, tolerance)
        segments = fit_cut_produced_contours(segments, stock, cutter_loops, tolerance)
        simple_ring(segments, tolerance)
        shape["segments"] = segments
    # Curved boundary chords need actual analytic evidence; the API flag on a
    # coplanar seam alone does not turn a straight seam into a curve.
    boundary = material["outline"]["segments"]
    curved_incidence = Counter()
    for edge in shell["edges"]:
        if edge["apiType"] in (None, "EDGE_TYPE_NORMAL"):
            continue
        a, b = (shell["faces"][i] for i in edge["faces"])
        if np.dot(a["normal"], b["normal"]) < 1 - 1e-8:
            curved_incidence.update(edge["faces"])
    # A straight wall adjoining an arc has one curved seam; an interior
    # tessellation facet has two. Do not extend the arc into its straight flank.
    curved_faces = {
        i
        for i, count in curved_incidence.items()
        if count >= 2 and abs(np.dot(shell["faces"][i]["normal"], frame_axes(frame)[2])) < 1 - 1e-6
    }
    for edge in shell["edges"]:
        if edge["apiType"] in (None, "EDGE_TYPE_NORMAL") and not curved_faces.intersection(edge["faces"]):
            continue
        a, b = [project(edge[k], frame) for k in ("start", "end")]
        if abs(a[2] - b[2]) > tolerance:
            continue
        if not any(
            parameter(a[:2], s, tolerance) is not None and parameter(b[:2], s, tolerance) is not None
            for s in boundary
        ):
            continue
        require(
            any(
                s["kind"] == "ARC"
                and parameter(a[:2], s, tolerance) is not None
                and parameter(b[:2], s, tolerance) is not None
                for s in boundary
            ),
            "CURVE_PARAMETERS_MISSING",
            "Curved outer boundary has no confirmed arc parameters",
            path + "/finalSolid/edges",
        )


def _target(group, part_id, path):
    targets = group.get("targets", {})
    primary = [read(targets.get(k), path + "/targets") for k in ("partToBoltTo", "partToBeBolted")]
    other = read(targets.get("otherParts"), path + "/targets")
    require(
        part_id in [p["id"] for p in primary + other if p is not None],
        "HOLE_WRONG_TARGET",
        "Hole group does not target this part",
        path,
    )


def _group_candidates(group, frame, path):
    try:
        return _hole_candidates(group, frame, path)
    except (KeyError, TypeError, ValueError) as error:
        raise EvidenceError("HOLE_PARAMETERS_UNRESOLVED", str(error), path) from error


def supplement_holes(part, feature, tolerance=0.1):
    frame, material = feature["frame"], feature["material"]
    groups = read(part.get("boltGroups"), f"parts/{part['id']}/boltGroups")
    require(len({g["id"] for g in groups}) == len(groups), "DUPLICATE_HOLE_GROUP", "Repeated bolt group")
    existing = list(material["holes"])
    claimed = set()
    additions = []
    evidence = []
    outer = shape_polygon(material["outline"]["segments"], tolerance)
    for index, group in enumerate(groups):
        path = f"parts/{part['id']}/boltGroups/{index}"
        _target(group, part["id"], path)
        props = group["properties"]
        if read(props.get("BoltStandard"), path) == "STUD":
            evidence.append(dict(groupId=group["id"], status="ATTACHMENT_STUD", sourceRef=path))
            continue
        fastener = read(props.get("Bolt"), path)
        require(type(fastener) is bool, "HOLE_TYPE_UNRESOLVED", "Bolt flag must be boolean", path)
        holes = _group_candidates(group, frame, path)
        bolt_frame = read(group.get("coordinateSystem"), path)
        axis = np.array(frame_axes(bolt_frame)[2])
        normal = np.array(frame_axes(frame)[2])
        cosine = min(1.0, abs(float(np.dot(axis, normal))))
        drift = feature["thickness"] * math.sqrt(max(0.0, 1 - cosine * cosine)) / max(cosine, 1e-12)
        require(
            drift <= tolerance,
            "HOLE_AXIS_UNSUPPORTED",
            "Hole axis drift through local thickness exceeds supported projection precision",
            path,
        )
        intersections = group.get("axisIntersections", {})
        if fastener and intersections.get("status") == "READ" and intersections.get("value"):
            try:
                physical_penetration(group, part["id"], holes, frame, feature["levels"], tolerance)
            except (KeyError, ValueError, TypeError) as error:
                raise EvidenceError("HOLE_PENETRATION_CONFLICT", str(error), path) from error
        cutter_proof = None
        if not fastener:
            try:
                cutter_proof = _hole_effect(
                    group,
                    part["id"],
                    holes,
                    frame,
                    tolerance,
                    path,
                    dict(status="MATCHED", levels=feature["levels"]),
                )
            except (ValueError, KeyError, TypeError):
                pass  # A fully observed opening may independently prove the hole.
        for hole in holes:
            hp = shape_polygon(hole["segments"], tolerance)
            require(
                outer.contains(Point(hole["center"])),
                "HOLE_OUTSIDE_MATERIAL",
                "Hole axis misses local material region",
                path,
            )
            matches = []
            for i, h in enumerate(existing):
                try:
                    _shape_match(
                        h.get("observedPoints") or [s["start"] for s in h["segments"]], hole, tolerance
                    )
                    matches.append(i)
                except ValueError:
                    pass
            overlaps = [
                i
                for i, h in enumerate(existing)
                if hp.intersection(shape_polygon(h["segments"], tolerance)).area > tolerance * tolerance
            ]
            require(
                len(matches) <= 1 and set(overlaps) <= set(matches),
                "HOLE_FINAL_STATE_CONFLICT",
                "Observed opening contradicts hole parameters",
                path,
            )
            require(
                not matches or matches[0] not in claimed,
                "DUPLICATE_HOLE_EVIDENCE",
                "Opening is claimed twice",
                path,
            )
            require(
                fastener or bool(matches) or cutter_proof is not None,
                "HOLE_ONLY_NOT_OBSERVED",
                "Hole-only group needs a final opening or the approved complete target cutter proof",
                path,
            )
            hole = dict(
                hole,
                fastener=fastener,
                ownerIds=[part["id"], group["id"]],
                sourceGroupId=group["id"],
                proof=(
                    "FINAL_OPENING"
                    if matches
                    else "BOLT-THROUGH-01" if fastener else "HOLE_ONLY_CUTTER_CONFIRMED"
                ),
            )
            if matches:
                claimed.add(matches[0])
                if hole["kind"] == "ROUND":
                    points = existing[matches[0]]["observedPoints"]
                    center = np.mean(points, axis=0).tolist()
                    measured = dict(hole, center=center)
                    _shape_match(points, measured, tolerance)
                    hole.update(
                        center=center,
                        segments=hole_segments(
                            center, hole["diameter"], 0, hole["direction"], hole["sourceRefs"]
                        ),
                    )
            additions.append(hole)
            evidence.append(
                dict(
                    holeId=hole["id"],
                    groupId=group["id"],
                    sourceRef=path,
                    status=(
                        "OPENING_MATCHED"
                        if matches
                        else "RULE_CONFIRMED_THROUGH_HOLE" if fastener else "HOLE_ONLY_CUTTER_CONFIRMED"
                    ),
                    rule=(
                        "BOLT-THROUGH-01"
                        if fastener
                        else "FINAL_OPENING" if matches else "HOLE_ONLY_CUTTER_CONFIRMED"
                    ),
                    cutterProof=cutter_proof,
                    shapeBasis="BOLT_GROUP_PARAMETERS",
                    targetPartId=part["id"],
                )
            )
    unresolved = [h for i, h in enumerate(existing) if i not in claimed]
    # An unclaimed polygonal opening remains a measured cutout. Curves cannot
    # be silently relabelled as a many-sided polygon.
    for h in unresolved:
        circular = circle_shape(h["segments"], tolerance)
        if circular:
            h.update(circular)
        for edge in feature.get("finalSolid", {}).get("edges", []):
            if edge["apiType"] in (None, "EDGE_TYPE_NORMAL"):
                continue
            a, b = [project(edge[k], frame) for k in ("start", "end")]
            if abs(a[2] - b[2]) > tolerance:
                continue
            on_boundary = any(
                parameter(a[:2], s, tolerance) is not None and parameter(b[:2], s, tolerance) is not None
                for s in h["segments"]
            )
            if on_boundary:
                require(
                    any(
                        s["kind"] == "ARC"
                        and parameter(a[:2], s, tolerance) is not None
                        and parameter(b[:2], s, tolerance) is not None
                        for s in h["segments"]
                    ),
                    "UNATTRIBUTED_CURVED_OPENING",
                    "Curved opening needs analytic parameters",
                )
    material["holes"] = unresolved + additions
    # Approved supplemental holes may meet an edge or one another. Boolean
    # union here applies only to those holes, not to the modelling history.
    hole_polygons = [shape_polygon(h["segments"], tolerance) for h in material["holes"]]
    interacting = any(not outer.contains(hp) for hp in hole_polygons) or any(
        a.intersects(b) for i, a in enumerate(hole_polygons) for b in hole_polygons[i + 1 :]
    )
    if additions and interacting:
        removals = [
            dict(
                h,
                id=h.get("id", f"opening:{i}"),
                sourceKind="HOLE",
                sideOriginIds=h.get("ownerIds", [part["id"]]),
            )
            for i, h in enumerate(material["holes"])
        ]
        region = cut_region(material["outline"], removals, tolerance, "part:" + str(part["id"]))
        # Keep direct hole identity and fastener origin where union is unchanged.
        for h in region["holes"]:
            matches = [
                a
                for a in additions
                if shape_polygon(h["segments"], tolerance).boundary.hausdorff_distance(
                    shape_polygon(a["segments"], tolerance).boundary
                )
                <= tolerance
            ]
            if len(matches) == 1:
                h.update({k: v for k, v in matches[0].items() if k not in ("segments", "sourceRefs")})
        material.update(
            outline=region["outline"],
            holes=region["holes"],
            operationMappings=region.get("operationMappings", []),
        )
    material["holeEvidence"] = evidence


def supplement_folded_holes(part, feature, tolerance=0.1):
    groups = read(part.get("boltGroups"), f"parts/{part['id']}/boltGroups")
    require(len({g["id"] for g in groups}) == len(groups), "DUPLICATE_HOLE_GROUP", "Repeated bolt group")
    for gi, group in enumerate(groups):
        path = f"parts/{part['id']}/boltGroups/{gi}"
        _target(group, part["id"], path)
        props = group["properties"]
        if read(props.get("BoltStandard"), path) == "STUD":
            continue
        require(
            read(props.get("Bolt"), path) is True,
            "FOLD_HOLE_ONLY_UNRESOLVED",
            "Folded hole-only opening needs its final surface evidence",
            path,
        )
        axis = np.array(frame_axes(read(group["coordinateSystem"]))[2])
        candidates = [_group_candidates(group, s["frame"], path) for s in feature["segments"]]
        for hi in range(len(read(group["positions"]))):
            hits = []
            for si, segment in enumerate(feature["segments"]):
                h = candidates[si][hi]
                if abs(np.dot(axis, segment["normal"])) < 1 - 1e-6:
                    continue
                hp = shape_polygon(h["segments"], tolerance)
                if Polygon(segment["polygon"]).contains(hp):
                    hits.append((segment, h))
            require(
                len(hits) == 1,
                "HOLE_SEGMENT_AMBIGUOUS",
                "Hole must cross exactly one complete local segment",
                path,
            )
            segment, hole = hits[0]
            rows = group.get("axisIntersections", {})
            if rows.get("status") == "READ" and rows.get("value"):
                selected = dict(group, axisIntersections=dict(rows, value=[dict(rows["value"][hi], index=0)]))
                try:
                    physical_penetration(
                        selected,
                        part["id"],
                        [hole],
                        segment["frame"],
                        [-feature["thickness"] / 2, feature["thickness"] / 2],
                        tolerance,
                    )
                except (ValueError, KeyError, TypeError) as error:
                    raise EvidenceError("HOLE_PENETRATION_CONFLICT", str(error), path) from error
            feature["holes"].append(
                dict(
                    hole,
                    segment=segment["id"],
                    fastener=True,
                    proof="BOLT-THROUGH-01",
                    sourceGroupId=group["id"],
                    unfoldedCenter=(np.array(hole["center"]) + segment["unfoldOffset"]).tolist(),
                )
            )
