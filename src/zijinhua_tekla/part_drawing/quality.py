from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from .contracts import DrawingIssue, DrawingStatus, IssueCode, PartDrawingSnapshot
from .geometry_analyzer import NormalizedPlateGeometry, Point2D, analyze_plate_geometry, project_point


@dataclass(frozen=True)
class PartPositionGroup:
    part_position: str
    snapshots: tuple[PartDrawingSnapshot, ...]
    representative: PartDrawingSnapshot
    geometry: NormalizedPlateGeometry | None
    quantity: int
    status: DrawingStatus
    issues: tuple[DrawingIssue, ...]


def group_part_snapshots(snapshots: list[PartDrawingSnapshot]) -> list[PartPositionGroup]:
    by_position: dict[str, list[PartDrawingSnapshot]] = {}
    for snapshot in snapshots:
        by_position.setdefault(snapshot.part_position, []).append(snapshot)
    return [_build_group(position, items) for position, items in sorted(by_position.items())]


def _build_group(position: str, snapshots: list[PartDrawingSnapshot]) -> PartPositionGroup:
    analyses = [analyze_plate_geometry(snapshot) for snapshot in snapshots]
    issues = tuple(issue for analysis in analyses for issue in analysis.issues)
    geometries = [analysis.geometry for analysis in analyses if analysis.geometry is not None]
    quantity = sum(snapshot.quantity for snapshot in snapshots)
    if issues or len(geometries) != len(snapshots):
        return PartPositionGroup(
            position, tuple(snapshots), snapshots[0], None, quantity, DrawingStatus.REJECTED, issues
        )

    fingerprints = {
        _manufacturing_fingerprint(snapshot, geometry)
        for snapshot, geometry in zip(snapshots, geometries)
    }
    if len(fingerprints) != 1:
        conflict = DrawingIssue(
            IssueCode.PART_POSITION_CONFLICT,
            f"partPosition {position} contains {len(fingerprints)} manufacturing fingerprints",
            True,
            tuple(snapshot.part_id for snapshot in snapshots),
            tuple(sorted(fingerprints)),
        )
        return PartPositionGroup(
            position, tuple(snapshots), snapshots[0], None, quantity, DrawingStatus.REJECTED, (conflict,)
        )
    return PartPositionGroup(
        position, tuple(snapshots), snapshots[0], geometries[0], quantity, DrawingStatus.OK, ()
    )


def _manufacturing_fingerprint(
    snapshot: PartDrawingSnapshot,
    geometry: NormalizedPlateGeometry,
) -> str:
    transforms = (
        lambda x, y: (x, y),
        lambda x, y: (x, -y),
        lambda x, y: (-x, y),
        lambda x, y: (-x, -y),
        lambda x, y: (y, x),
        lambda x, y: (y, -x),
        lambda x, y: (-y, x),
        lambda x, y: (-y, -x),
    )
    candidates = []
    for transform in transforms:
        offset = _transformed_outer_offset(geometry, transform)
        outer = _loop_signature(geometry.outer_loop, transform, offset)
        inner = sorted(_loop_signature(loop, transform, offset) for loop in geometry.inner_loops)
        holes = sorted(_hole_signature(snapshot, geometry, hole, transform, offset) for hole in snapshot.holes)
        payload = [round(snapshot.thickness, 2), outer, inner, holes, _stable_cuts(snapshot.cuts)]
        candidates.append(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    return hashlib.sha256(min(candidates).encode("ascii")).hexdigest()


def _transformed_outer_offset(geometry, transform) -> tuple[float, float]:
    points = [
        transform(point.x, point.y)
        for segment in geometry.outer_loop
        for point in (segment.start, segment.end)
    ]
    return min(point[0] for point in points), min(point[1] for point in points)


def _loop_signature(loop, transform, offset) -> list:
    descriptors = []
    for segment in loop:
        start = transform(segment.start.x, segment.start.y)
        end = transform(segment.end.x, segment.end.y)
        center = transform(segment.center.x, segment.center.y) if segment.center else None
        descriptors.append((segment.kind, start, end, center))
    min_x, min_y = offset

    def normalized(descriptor):
        kind, start, end, center = descriptor
        move = lambda point: None if point is None else (round(point[0] - min_x, 2), round(point[1] - min_y, 2))
        return (kind, move(start), move(end), move(center))

    values = [normalized(item) for item in descriptors]
    variants = []
    for sequence in (values, list(reversed([(kind, end, start, center) for kind, start, end, center in values]))):
        variants.extend(sequence[index:] + sequence[:index] for index in range(len(sequence)))
    return min(variants)


def _hole_signature(snapshot, geometry, hole, transform, offset) -> tuple:
    projected = project_point(hole.center, snapshot.local_frame)
    local = Point2D(
        projected.x - geometry.normalization_offset.x,
        projected.y - geometry.normalization_offset.y,
    )
    x, y = transform(local.x, local.y)
    x -= offset[0]
    y -= offset[1]
    direction = transform(math.cos(math.radians(hole.angle_deg)), math.sin(math.radians(hole.angle_deg)))
    angle = 0.0 if hole.kind == "ROUND" else round(math.degrees(math.atan2(direction[1], direction[0])) % 180.0, 2)
    return (
        hole.kind,
        round(x, 2),
        round(y, 2),
        round(hole.diameter, 2),
        round(hole.length, 2),
        round(hole.width, 2),
        angle,
    )


def _stable_cuts(cuts: tuple[dict, ...]) -> list[str]:
    return sorted(json.dumps(item, sort_keys=True, ensure_ascii=True, separators=(",", ":")) for item in cuts)
