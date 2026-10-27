from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.validation import explain_validity

from .contracts import (
    ContourSegmentSnapshot,
    DrawingIssue,
    IssueCode,
    LocalFrame,
    PartDrawingSnapshot,
    Point3D,
)


@dataclass(frozen=True, order=True)
class Point2D:
    x: float
    y: float


@dataclass(frozen=True)
class NormalizedSegment:
    kind: str
    start: Point2D
    end: Point2D
    center: Point2D | None = None
    clockwise: bool = False


@dataclass(frozen=True)
class NormalizedPlateGeometry:
    outer_loop: tuple[NormalizedSegment, ...]
    inner_loops: tuple[tuple[NormalizedSegment, ...], ...]
    thickness: float
    normalization_offset: Point2D
    bounds: tuple[float, float, float, float]
    area: float
    perimeter: float
    fingerprint: str


@dataclass(frozen=True)
class GeometryAnalysisResult:
    geometry: NormalizedPlateGeometry | None
    issues: tuple[DrawingIssue, ...]


def project_point(point: Point3D, frame: LocalFrame) -> Point2D:
    delta = (
        point.x - frame.origin.x,
        point.y - frame.origin.y,
        point.z - frame.origin.z,
    )
    return Point2D(
        round(_dot(delta, (frame.x_axis.x, frame.x_axis.y, frame.x_axis.z)), 6),
        round(_dot(delta, (frame.y_axis.x, frame.y_axis.y, frame.y_axis.z)), 6),
    )


def analyze_plate_geometry(snapshot: PartDrawingSnapshot) -> GeometryAnalysisResult:
    frame_error = _validate_orthonormal_frame(snapshot.local_frame)
    if frame_error:
        return _invalid(snapshot, frame_error)
    planarity_error = _validate_planarity(snapshot, tolerance_mm=0.1)
    if planarity_error:
        return _invalid(snapshot, planarity_error)

    outer = tuple(_project_segment(item, snapshot.local_frame) for item in snapshot.outer_loop)
    inner = tuple(
        tuple(_project_segment(item, snapshot.local_frame) for item in loop)
        for loop in snapshot.inner_loops
    )
    errors = _loop_continuity_errors(outer, 0.01)
    for loop in inner:
        errors.extend(_loop_continuity_errors(loop, 0.01))
    if errors:
        return _invalid(snapshot, "; ".join(errors))

    outer_points = _sample_loop(outer)
    inner_points = tuple(_sample_loop(loop) for loop in inner)
    polygon = Polygon(
        [(point.x, point.y) for point in outer_points],
        [[(point.x, point.y) for point in loop] for loop in inner_points],
    )
    if not polygon.is_valid or polygon.area <= 0.0:
        return _invalid(snapshot, explain_validity(polygon))

    min_x, min_y, max_x, max_y = polygon.bounds
    normalized_outer = _translate_loop(outer, -min_x, -min_y)
    normalized_inner = tuple(_translate_loop(loop, -min_x, -min_y) for loop in inner)
    return GeometryAnalysisResult(
        geometry=NormalizedPlateGeometry(
            outer_loop=normalized_outer,
            inner_loops=normalized_inner,
            thickness=snapshot.thickness,
            normalization_offset=Point2D(min_x, min_y),
            bounds=(0.0, 0.0, round(max_x - min_x, 6), round(max_y - min_y, 6)),
            area=float(polygon.area),
            perimeter=float(polygon.length),
            fingerprint=_canonical_fingerprint(normalized_outer, normalized_inner, snapshot.thickness),
        ),
        issues=(),
    )


def _invalid(snapshot: PartDrawingSnapshot, message: str) -> GeometryAnalysisResult:
    return GeometryAnalysisResult(
        None,
        (DrawingIssue(IssueCode.GEOMETRY_INVALID, message, True, (snapshot.part_id,)),),
    )


def _validate_orthonormal_frame(frame: LocalFrame, tolerance: float = 1e-6) -> str:
    vectors = (
        (frame.x_axis.x, frame.x_axis.y, frame.x_axis.z),
        (frame.y_axis.x, frame.y_axis.y, frame.y_axis.z),
        (frame.normal.x, frame.normal.y, frame.normal.z),
    )
    if any(abs(math.sqrt(_dot(item, item)) - 1.0) > tolerance for item in vectors):
        return "local frame must be orthonormal"
    if any(abs(_dot(vectors[left], vectors[right])) > tolerance for left, right in ((0, 1), (0, 2), (1, 2))):
        return "local frame must be orthonormal"
    return ""


def _validate_planarity(snapshot: PartDrawingSnapshot, tolerance_mm: float) -> str:
    normal = (snapshot.local_frame.normal.x, snapshot.local_frame.normal.y, snapshot.local_frame.normal.z)
    origin = snapshot.local_frame.origin
    points = []
    for loop in (snapshot.outer_loop,) + snapshot.inner_loops:
        for segment in loop:
            points.extend((segment.start, segment.end))
            if segment.center is not None:
                points.append(segment.center)
    for point in points:
        delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
        if abs(_dot(delta, normal)) > tolerance_mm:
            return "contour points are not planar in the supplied local frame"
    return ""


def _project_segment(segment: ContourSegmentSnapshot, frame: LocalFrame) -> NormalizedSegment:
    return NormalizedSegment(
        segment.kind,
        project_point(segment.start, frame),
        project_point(segment.end, frame),
        project_point(segment.center, frame) if segment.center is not None else None,
        segment.clockwise,
    )


def _loop_continuity_errors(loop: tuple[NormalizedSegment, ...], tolerance: float) -> list[str]:
    if len(loop) < 3:
        return ["loop requires at least three segments"]
    errors = []
    for index, segment in enumerate(loop):
        following = loop[(index + 1) % len(loop)]
        if _distance(segment.end, following.start) > tolerance:
            errors.append(f"loop is open between segments {index} and {(index + 1) % len(loop)}")
    return errors


def _sample_loop(loop: tuple[NormalizedSegment, ...]) -> tuple[Point2D, ...]:
    points: list[Point2D] = []
    for segment in loop:
        points.append(segment.start)
        if segment.kind == "ARC" and segment.center is not None:
            points.extend(_arc_points(segment))
    return tuple(points)


def _arc_points(segment: NormalizedSegment) -> list[Point2D]:
    center = segment.center
    radius = _distance(segment.start, center)
    start = math.atan2(segment.start.y - center.y, segment.start.x - center.x)
    end = math.atan2(segment.end.y - center.y, segment.end.x - center.x)
    if segment.clockwise:
        while end >= start:
            end -= math.tau
    else:
        while end <= start:
            end += math.tau
    sweep = end - start
    count = max(2, int(math.ceil(abs(sweep) * max(radius, 1.0) / 2.0)))
    return [
        Point2D(center.x + radius * math.cos(start + sweep * index / count), center.y + radius * math.sin(start + sweep * index / count))
        for index in range(1, count)
    ]


def _translate_loop(loop: tuple[NormalizedSegment, ...], dx: float, dy: float) -> tuple[NormalizedSegment, ...]:
    def move(point: Point2D | None) -> Point2D | None:
        return None if point is None else Point2D(round(point.x + dx, 6), round(point.y + dy, 6))

    return tuple(NormalizedSegment(item.kind, move(item.start), move(item.end), move(item.center), item.clockwise) for item in loop)


def _canonical_fingerprint(outer, inner, thickness: float) -> str:
    loops = (_sample_loop(outer),) + tuple(_sample_loop(loop) for loop in inner)
    candidates = []
    transforms = (
        lambda x, y: (x, y), lambda x, y: (x, -y), lambda x, y: (-x, y), lambda x, y: (-x, -y),
        lambda x, y: (y, x), lambda x, y: (y, -x), lambda x, y: (-y, x), lambda x, y: (-y, -x),
    )
    for transform in transforms:
        canonical_loops = [_canonical_loop([transform(point.x, point.y) for point in loop]) for loop in loops]
        candidates.append(json.dumps([round(thickness, 2), canonical_loops[0], sorted(canonical_loops[1:])], separators=(",", ":")))
    return hashlib.sha256(min(candidates).encode("ascii")).hexdigest()


def _canonical_loop(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    normalized = [(round(x - min_x, 2), round(y - min_y, 2)) for x, y in points]
    variants = []
    for values in (normalized, list(reversed(normalized))):
        variants.extend(values[index:] + values[:index] for index in range(len(values)))
    return min(variants)


def _distance(first: Point2D, second: Point2D) -> float:
    return math.hypot(first.x - second.x, first.y - second.y)


def _dot(first, second) -> float:
    return sum(left * right for left, right in zip(first, second))
