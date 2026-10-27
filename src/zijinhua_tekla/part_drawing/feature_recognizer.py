from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contracts import DrawingIssue, DrawingStatus, IssueCode, PartDrawingSnapshot
from .geometry_analyzer import NormalizedPlateGeometry, Point2D, project_point


class FeatureType(str, Enum):
    ROUND_HOLE = "ROUND_HOLE"
    HOLE_GROUP = "HOLE_GROUP"
    SLOT = "SLOT"
    POLYGON_CUTOUT = "POLYGON_CUTOUT"
    CHAMFER = "CHAMFER"
    NOTCH = "NOTCH"
    ARC_CUTOUT = "ARC_CUTOUT"
    BEVEL = "BEVEL"


@dataclass(frozen=True)
class RecognizedFeature:
    feature_id: str
    feature_type: FeatureType
    anchors: tuple[Point2D, ...]
    parameters: dict[str, Any]
    source_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class FeatureRecognitionResult:
    features: tuple[RecognizedFeature, ...]
    status: DrawingStatus
    issues: tuple[DrawingIssue, ...]


def recognize_plate_features(
    snapshot: PartDrawingSnapshot,
    geometry: NormalizedPlateGeometry,
) -> FeatureRecognitionResult:
    features = [_explicit_hole_feature(snapshot, geometry, hole) for hole in snapshot.holes]
    features.extend(_hole_groups(features))
    issues: list[DrawingIssue] = []
    for index, loop in enumerate(geometry.inner_loops, 1):
        if all(segment.kind == "LINE" for segment in loop):
            features.append(_loop_feature(index, FeatureType.POLYGON_CUTOUT, loop, {}))
        elif all(segment.kind == "ARC" for segment in loop) and _consistent_arc_loop(loop):
            radius = _distance(loop[0].start, loop[0].center)
            features.append(_loop_feature(index, FeatureType.ARC_CUTOUT, loop, {"radius": radius}))
        else:
            issues.append(DrawingIssue(
                IssueCode.FEATURE_AMBIGUOUS,
                f"inner loop {index} cannot be classified reliably",
                False,
                (snapshot.part_id,),
                ("INNER_LOOP_ARC_CONFLICT",),
            ))
    features.extend(_outer_features(geometry))
    features.extend(_explicit_bevels(snapshot))
    evidence_bevels, bevel_issues = _principal_face_bevels(snapshot, geometry)
    features.extend(evidence_bevels)
    issues.extend(bevel_issues)
    return FeatureRecognitionResult(
        tuple(features),
        DrawingStatus.REVIEW_REQUIRED if issues else DrawingStatus.OK,
        tuple(issues),
    )


def _explicit_hole_feature(snapshot, geometry, hole) -> RecognizedFeature:
    projected = project_point(hole.center, snapshot.local_frame)
    anchor = Point2D(
        projected.x - geometry.normalization_offset.x,
        projected.y - geometry.normalization_offset.y,
    )
    if hole.kind == "ROUND":
        feature_type = FeatureType.ROUND_HOLE
        parameters = {"diameter": hole.diameter}
        evidence = ("TEKLA_HOLE_OPERATION",)
    else:
        feature_type = FeatureType.SLOT
        parameters = {"length": hole.length, "width": hole.width, "angle_deg": hole.angle_deg}
        evidence = ("TEKLA_SLOT_OPERATION",)
    return RecognizedFeature(hole.hole_id, feature_type, (anchor,), parameters, (hole.hole_id,), evidence, 1.0)


def _hole_groups(features: list[RecognizedFeature]) -> list[RecognizedFeature]:
    rounds = [item for item in features if item.feature_type == FeatureType.ROUND_HOLE]
    groups = []
    by_diameter: dict[float, list[RecognizedFeature]] = {}
    for item in rounds:
        by_diameter.setdefault(round(float(item.parameters["diameter"]), 2), []).append(item)
    for diameter, items in by_diameter.items():
        if len(items) < 3:
            continue
        for axis in ("x", "y"):
            ordered = sorted(items, key=lambda item: getattr(item.anchors[0], axis))
            fixed_axis = "y" if axis == "x" else "x"
            if max(getattr(item.anchors[0], fixed_axis) for item in ordered) - min(getattr(item.anchors[0], fixed_axis) for item in ordered) > 0.1:
                continue
            values = [getattr(item.anchors[0], axis) for item in ordered]
            spacings = [round(right - left, 2) for left, right in zip(values, values[1:])]
            if spacings and max(spacings) - min(spacings) <= 0.1:
                groups.append(RecognizedFeature(
                    f"hole-group-{len(groups) + 1}", FeatureType.HOLE_GROUP,
                    tuple(item.anchors[0] for item in ordered),
                    {"diameter": diameter, "count": len(ordered), "spacing": spacings[0], "axis": axis},
                    tuple(item.feature_id for item in ordered),
                    ("EQUAL_DIAMETER", "COLLINEAR", "EQUAL_SPACING"), 1.0,
                ))
                break
    return groups


def _loop_feature(index, feature_type, loop, parameters):
    return RecognizedFeature(
        f"inner-{index}", feature_type, tuple(segment.start for segment in loop), parameters,
        (f"inner-loop-{index}",), ("CLOSED_INNER_LOOP",), 1.0,
    )


def _consistent_arc_loop(loop) -> bool:
    centers = [segment.center for segment in loop if segment.center is not None]
    if len(centers) != len(loop):
        return False
    first = centers[0]
    radii = [_distance(segment.start, segment.center) for segment in loop]
    return max(_distance(first, center) for center in centers) <= 0.1 and max(radii) - min(radii) <= 0.1


def _outer_features(geometry) -> list[RecognizedFeature]:
    loop = geometry.outer_loop
    diagonal = [item for item in loop if abs(item.end.x - item.start.x) > 0.1 and abs(item.end.y - item.start.y) > 0.1]
    if diagonal:
        item = diagonal[0]
        return [RecognizedFeature("outer-chamfer-1", FeatureType.CHAMFER, (item.start, item.end), {}, ("outer-loop",), ("DIAGONAL_BOUNDARY_EDGE",), 0.95)]
    width, height = geometry.bounds[2], geometry.bounds[3]
    if len(loop) > 4 and geometry.area < width * height - 0.1:
        return [RecognizedFeature("outer-notch-1", FeatureType.NOTCH, tuple(item.start for item in loop), {}, ("outer-loop",), ("BOUNDING_RECTANGLE_DEFICIT",), 0.95)]
    return []


def _explicit_bevels(snapshot) -> list[RecognizedFeature]:
    return [
        RecognizedFeature(
            f"bevel-{index}", FeatureType.BEVEL, (), dict(cut),
            (str(cut.get("operationId") or index),), ("TEKLA_EXPLICIT_BEVEL",), 1.0,
        )
        for index, cut in enumerate(snapshot.cuts, 1)
        if str(cut.get("operationType") or "").upper() == "BEVEL"
    ]


def _principal_face_bevels(snapshot, geometry):
    evidence, message = _principal_face_evidence(snapshot, geometry)
    if message:
        return [], [_bevel_issue(snapshot, message)]
    if evidence is None:
        return [], []
    first_segments, second_segments = evidence[4:]
    alignment = _best_edge_alignment(first_segments, second_segments)
    if alignment is None:
        return [], [_bevel_issue(snapshot, "principal face edges cannot be paired")]
    return _bevel_from_alignment(snapshot, evidence, alignment)


def _principal_face_evidence(snapshot, geometry):
    solid = snapshot.final_geometry
    if snapshot.schema_version != "partDrawingSnapshot.v2" or solid is None:
        return None, None
    if len(solid.principal_face_ids) != 2:
        return None, "principal face pair is incomplete"

    faces = {face.face_id: face for face in solid.faces}
    edges = {edge.edge_id: edge for edge in solid.edges}
    try:
        first_face, second_face = (faces[item] for item in solid.principal_face_ids)
    except KeyError:
        return None, "principal face reference is missing"
    first_loop = _largest_face_loop(first_face, snapshot)
    second_loop = _largest_face_loop(second_face, snapshot)
    if first_loop is None or second_loop is None:
        return None, "principal face outer loop is missing"
    if len(first_loop.ordered_edge_ids) != len(second_loop.ordered_edge_ids):
        return None, "principal face topology does not match"

    first_segments = _loop_segments(first_loop, snapshot, geometry, edges)
    second_segments = _loop_segments(second_loop, snapshot, geometry, edges)
    if first_segments is None or second_segments is None:
        return None, "principal face edge evidence is unresolved"
    return (
        first_face,
        second_face,
        first_loop,
        second_loop,
        first_segments,
        second_segments,
    ), None


def _bevel_from_alignment(snapshot, evidence, alignment):
    first_face, second_face, first_loop, second_loop, first_segments, second_segments = evidence
    paired, offsets = alignment
    changed = [index for index, value in enumerate(offsets) if value > 0.1]
    if not changed:
        return [], []
    if len(changed) != 1:
        return [], [_bevel_issue(snapshot, "multiple principal face edge offsets are unexplained")]

    first_area = _point_loop_area(tuple(item[0] for item in first_segments))
    second_area = _point_loop_area(tuple(item[0] for item in second_segments))
    if abs(first_area - second_area) <= 0.1:
        return [], [_bevel_issue(snapshot, "setback face side cannot be proven")]
    changed_index = changed[0]
    first_edge, second_edge = paired[changed_index]
    if second_area < first_area:
        shifted_face, shifted_edge = second_face, second_edge
        counterpart_edge = first_edge
    else:
        shifted_face, shifted_edge = first_face, first_edge
        counterpart_edge = second_edge

    setback = round(offsets[changed_index], 6)
    parameters = {
        "setback": setback,
        "depth": snapshot.thickness,
        "face_side": shifted_face.relation_to_part_frame,
        "angle_status": "REVIEW_REQUIRED",
    }
    face_separation = _principal_face_separation(first_loop, second_loop, snapshot)
    evidence_codes = ["PRINCIPAL_FACE_SINGLE_EDGE_SETBACK"]
    if abs(face_separation - snapshot.thickness) <= 0.1:
        parameters["angleDeg"] = round(
            math.degrees(math.atan2(snapshot.thickness, setback)), 6
        )
        parameters["angle_status"] = "PROVEN"
        evidence_codes.append("PRINCIPAL_FACE_SEPARATION_MATCHES_THICKNESS")

    return [RecognizedFeature(
        "bevel-principal-face-1",
        FeatureType.BEVEL,
        tuple(sorted((shifted_edge[0], shifted_edge[1]))),
        parameters,
        (
            first_face.face_id,
            second_face.face_id,
            shifted_edge[2],
            counterpart_edge[2],
        ),
        tuple(evidence_codes),
        1.0,
    )], []


def _largest_face_loop(face, snapshot):
    valid = [loop for loop in face.loops if len(loop.ordered_vertices) >= 3]
    return max(
        valid,
        key=lambda loop: _point_loop_area(tuple(
            project_point(point, snapshot.local_frame)
            for point in loop.ordered_vertices
        )),
        default=None,
    )


def _loop_segments(loop, snapshot, geometry, edges):
    if len(loop.ordered_vertices) != len(loop.ordered_edge_ids):
        return None
    points = tuple(
        Point2D(
            project_point(point, snapshot.local_frame).x - geometry.normalization_offset.x,
            project_point(point, snapshot.local_frame).y - geometry.normalization_offset.y,
        )
        for point in loop.ordered_vertices
    )
    result = []
    for index, edge_id in enumerate(loop.ordered_edge_ids):
        edge = edges.get(edge_id)
        if edge is None or edge.curve_type != "LINE" or edge.evidence_status != "PROVEN":
            return None
        result.append((points[index], points[(index + 1) % len(points)], edge_id))
    return tuple(result)


def _best_edge_alignment(first, second):
    variants = [second, tuple((end, start, edge_id) for start, end, edge_id in reversed(second))]
    candidates = []
    for variant in variants:
        for offset in range(len(variant)):
            rotated = variant[offset:] + variant[:offset]
            distances = []
            for left, right in zip(first, rotated):
                distance = _parallel_line_offset(left, right)
                if distance is None:
                    break
                distances.append(distance)
            if len(distances) == len(first):
                candidates.append((
                    sum(value > 0.1 for value in distances),
                    round(sum(distances), 6),
                    tuple(zip(first, rotated)),
                    tuple(distances),
                ))
    if not candidates:
        return None
    best = min(candidates, key=lambda item: (item[0], item[1]))
    return best[2], best[3]


def _parallel_line_offset(first, second):
    first_line = _support_line(first[0], first[1])
    second_line = _support_line(second[0], second[1])
    if first_line is None or second_line is None:
        return None
    if abs(first_line[0] * second_line[1] - first_line[1] * second_line[0]) > 0.001:
        return None
    return abs(first_line[2] - second_line[2])


def _support_line(start, end):
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    nx, ny = -dy / length, dx / length
    if nx < -1e-9 or (abs(nx) <= 1e-9 and ny < 0):
        nx, ny = -nx, -ny
    return nx, ny, nx * start.x + ny * start.y


def _point_loop_area(points):
    return abs(sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )) / 2.0


def _principal_face_separation(first_loop, second_loop, snapshot):
    normal = snapshot.local_frame.normal
    origin = snapshot.local_frame.origin

    def coordinate(point):
        return (
            (point.x - origin.x) * normal.x
            + (point.y - origin.y) * normal.y
            + (point.z - origin.z) * normal.z
        )

    first = sum(coordinate(point) for point in first_loop.ordered_vertices) / len(first_loop.ordered_vertices)
    second = sum(coordinate(point) for point in second_loop.ordered_vertices) / len(second_loop.ordered_vertices)
    return abs(first - second)


def _bevel_issue(snapshot, message):
    return DrawingIssue(
        IssueCode.FEATURE_AMBIGUOUS,
        message,
        False,
        (snapshot.part_id,),
        ("PRINCIPAL_FACE_BEVEL_EVIDENCE",),
    )


def _distance(first, second) -> float:
    return math.hypot(first.x - second.x, first.y - second.y)
