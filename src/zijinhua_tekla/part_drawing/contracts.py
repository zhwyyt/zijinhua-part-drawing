from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DrawingStatus(str, Enum):
    OK = "OK"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class IssueCode(str, Enum):
    SNAPSHOT_INVALID = "SNAPSHOT_INVALID"
    PART_POSITION_CONFLICT = "PART_POSITION_CONFLICT"
    GEOMETRY_INVALID = "GEOMETRY_INVALID"
    FEATURE_AMBIGUOUS = "FEATURE_AMBIGUOUS"
    DIMENSION_INCOMPLETE = "DIMENSION_INCOMPLETE"
    DIMENSION_PLAN_INCOMPLETE = "DIMENSION_PLAN_INCOMPLETE"
    LAYOUT_OVERFLOW = "LAYOUT_OVERFLOW"
    NEEDS_DETAIL_VIEW = "NEEDS_DETAIL_VIEW"
    RENDER_FAILED = "RENDER_FAILED"


@dataclass(frozen=True)
class Point3D:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Vector3D:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class LocalFrame:
    origin: Point3D
    x_axis: Vector3D
    y_axis: Vector3D
    normal: Vector3D


@dataclass(frozen=True)
class ContourSegmentSnapshot:
    kind: str
    start: Point3D
    end: Point3D
    center: Point3D | None = None
    clockwise: bool = False


@dataclass(frozen=True)
class HoleSnapshot:
    hole_id: str
    kind: str
    center: Point3D
    diameter: float = 0.0
    length: float = 0.0
    width: float = 0.0
    angle_deg: float = 0.0


@dataclass(frozen=True)
class SolidVertexSnapshot:
    vertex_id: str
    point: Point3D


@dataclass(frozen=True)
class SolidEdgeSnapshot:
    edge_id: str
    curve_type: str
    start: Point3D
    end: Point3D
    center: Point3D | None = None
    radius: float = 0.0
    adjacent_face_ids: tuple[str, ...] = ()
    evidence_status: str = "PROVEN"
    source_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SolidLoopSnapshot:
    loop_id: str
    ordered_edge_ids: tuple[str, ...]
    ordered_vertices: tuple[Point3D, ...]


@dataclass(frozen=True)
class SolidFaceSnapshot:
    face_id: str
    surface_type: str
    normal: Vector3D
    relation_to_part_frame: str
    loops: tuple[SolidLoopSnapshot, ...]
    source_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SolidGeometrySnapshot:
    vertices: tuple[SolidVertexSnapshot, ...] = ()
    edges: tuple[SolidEdgeSnapshot, ...] = ()
    faces: tuple[SolidFaceSnapshot, ...] = ()
    principal_face_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationSnapshot:
    operation_id: str
    operation_type: str
    operative_geometry: SolidGeometrySnapshot
    source_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityIssueSnapshot:
    code: str
    message: str
    blocking: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PartDrawingSnapshot:
    schema_version: str
    exporter_version: str
    model_identifier: str
    exported_at: str
    part_id: str
    part_position: str
    assembly_id: str
    name: str
    profile: str
    material: str
    quantity: int
    thickness: float
    local_frame: LocalFrame
    outer_loop: tuple[ContourSegmentSnapshot, ...]
    inner_loops: tuple[tuple[ContourSegmentSnapshot, ...], ...] = ()
    holes: tuple[HoleSnapshot, ...] = ()
    cuts: tuple[dict[str, Any], ...] = ()
    source_evidence: tuple[str, ...] = ()
    part_form: str = "FLAT_PLATE"
    final_geometry: SolidGeometrySnapshot | None = None
    operations: tuple[OperationSnapshot, ...] = ()
    quality_issues: tuple[QualityIssueSnapshot, ...] = ()


@dataclass(frozen=True)
class DrawingIssue:
    code: IssueCode
    message: str
    blocking: bool
    part_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
