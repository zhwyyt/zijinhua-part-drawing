from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .contracts import (
    ContourSegmentSnapshot,
    HoleSnapshot,
    LocalFrame,
    OperationSnapshot,
    PartDrawingSnapshot,
    Point3D,
    QualityIssueSnapshot,
    SolidEdgeSnapshot,
    SolidFaceSnapshot,
    SolidGeometrySnapshot,
    SolidLoopSnapshot,
    SolidVertexSnapshot,
    Vector3D,
)


SCHEMA_V1 = "partDrawingSnapshot.v1"
SCHEMA_V2 = "partDrawingSnapshot.v2"
SUPPORTED_SCHEMAS = {SCHEMA_V1, SCHEMA_V2}


def load_part_snapshot(path: Path) -> PartDrawingSnapshot:
    return parse_part_snapshot(json.loads(path.read_text(encoding="utf-8")))


def parse_part_snapshot(data: dict[str, Any]) -> PartDrawingSnapshot:
    schema_version = str(data.get("schemaVersion") or "")
    if schema_version not in SUPPORTED_SCHEMAS:
        raise ValueError(f"unsupported schemaVersion: {schema_version!r}")

    part = data.get("part") or {}
    part_position = str(part.get("partPosition") or "").strip()
    if not part_position:
        raise ValueError("partPosition is required")

    thickness = float(part.get("thickness") or 0.0)
    if thickness <= 0.0:
        raise ValueError("thickness must be positive")

    frame = part.get("localFrame") or {}
    outer_loop = tuple(_segment(item) for item in part.get("outerLoop") or [])
    if len(outer_loop) < 3:
        raise ValueError("outerLoop requires at least three segments")

    final_geometry = (
        _solid_geometry(part.get("finalGeometry") or {})
        if schema_version == SCHEMA_V2
        else None
    )
    part_form = str(part.get("partForm") or "FLAT_PLATE").upper()
    if schema_version == SCHEMA_V2 and part_form == "FLAT_PLATE":
        if final_geometry is None or len(final_geometry.principal_face_ids) < 2:
            raise ValueError("v2 FLAT_PLATE requires two principal faces")

    return PartDrawingSnapshot(
        schema_version=schema_version,
        exporter_version=str(data.get("exporterVersion") or ""),
        model_identifier=str(data.get("modelIdentifier") or ""),
        exported_at=str(data.get("exportedAt") or ""),
        part_id=str(part.get("partId") or ""),
        part_position=part_position,
        assembly_id=str(part.get("assemblyId") or ""),
        name=str(part.get("name") or ""),
        profile=str(part.get("profile") or ""),
        material=str(part.get("material") or ""),
        quantity=max(1, int(part.get("quantity") or 1)),
        thickness=thickness,
        local_frame=LocalFrame(
            origin=_point(frame.get("origin")),
            x_axis=_vector(frame.get("xAxis")),
            y_axis=_vector(frame.get("yAxis")),
            normal=_vector(frame.get("normal")),
        ),
        outer_loop=outer_loop,
        inner_loops=tuple(
            tuple(_segment(segment) for segment in loop)
            for loop in part.get("innerLoops") or []
        ),
        holes=tuple(_hole(item) for item in part.get("holes") or []),
        cuts=tuple(part.get("cuts") or []),
        source_evidence=tuple(str(item) for item in part.get("sourceEvidence") or []),
        part_form=part_form,
        final_geometry=final_geometry,
        operations=tuple(_operation(item) for item in part.get("operations") or []),
        quality_issues=tuple(
            QualityIssueSnapshot(
                code=str(item.get("code") or ""),
                message=str(item.get("message") or ""),
                blocking=bool(item.get("blocking", False)),
                evidence=tuple(str(value) for value in item.get("evidence") or []),
            )
            for item in part.get("qualityIssues") or []
        ),
    )


def safe_part_position(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return safe or "unnamed-part"


def _point(value: Any) -> Point3D:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("3D point requires three coordinates")
    return Point3D(*(float(item) for item in value))


def _vector(value: Any) -> Vector3D:
    point = _point(value)
    return Vector3D(point.x, point.y, point.z)


def _segment(data: dict[str, Any]) -> ContourSegmentSnapshot:
    kind = str(data.get("kind") or "").upper()
    if kind not in {"LINE", "ARC"}:
        raise ValueError(f"unsupported contour segment kind: {kind!r}")
    return ContourSegmentSnapshot(
        kind=kind,
        start=_point(data.get("start")),
        end=_point(data.get("end")),
        center=_point(data["center"]) if data.get("center") is not None else None,
        clockwise=bool(data.get("clockwise", False)),
    )


def _hole(data: dict[str, Any]) -> HoleSnapshot:
    return HoleSnapshot(
        hole_id=str(data.get("holeId") or ""),
        kind=str(data.get("kind") or "ROUND").upper(),
        center=_point(data.get("center")),
        diameter=float(data.get("diameter") or 0.0),
        length=float(data.get("length") or 0.0),
        width=float(data.get("width") or 0.0),
        angle_deg=float(data.get("angleDeg") or 0.0),
    )


def _solid_geometry(data: dict[str, Any]) -> SolidGeometrySnapshot:
    vertices = tuple(
        SolidVertexSnapshot(str(item.get("vertexId") or ""), _point(item.get("point")))
        for item in data.get("vertices") or []
    )
    edges = tuple(
        SolidEdgeSnapshot(
            edge_id=str(item.get("edgeId") or ""),
            curve_type=str(item.get("curveType") or "UNKNOWN").upper(),
            start=_point(item.get("start")),
            end=_point(item.get("end")),
            center=_point(item["center"]) if item.get("center") is not None else None,
            radius=float(item.get("radius") or 0.0),
            adjacent_face_ids=tuple(str(value) for value in item.get("adjacentFaceIds") or []),
            evidence_status=str(item.get("curveEvidenceStatus") or "UNRESOLVED").upper(),
            source_evidence=tuple(str(value) for value in item.get("sourceEvidence") or []),
        )
        for item in data.get("edges") or []
    )
    faces = tuple(
        SolidFaceSnapshot(
            face_id=str(item.get("faceId") or ""),
            surface_type=str(item.get("surfaceType") or "UNKNOWN").upper(),
            normal=_vector(item.get("normal")),
            relation_to_part_frame=str(item.get("relationToPartFrame") or "UNKNOWN").upper(),
            loops=tuple(
                SolidLoopSnapshot(
                    loop_id=str(loop.get("loopId") or ""),
                    ordered_edge_ids=tuple(str(value) for value in loop.get("orderedEdgeIds") or []),
                    ordered_vertices=tuple(_point(value) for value in loop.get("orderedVertices") or []),
                )
                for loop in item.get("loops") or []
            ),
            source_evidence=tuple(str(value) for value in item.get("sourceEvidence") or []),
        )
        for item in data.get("faces") or []
    )
    _require_unique("vertexId", (item.vertex_id for item in vertices))
    _require_unique("edgeId", (item.edge_id for item in edges))
    _require_unique("faceId", (item.face_id for item in faces))
    edge_ids = {item.edge_id for item in edges}
    face_ids = {item.face_id for item in faces}
    for face in faces:
        for loop in face.loops:
            missing = set(loop.ordered_edge_ids) - edge_ids
            if missing:
                raise ValueError(f"loop references unknown edges: {sorted(missing)}")
    for edge in edges:
        missing = set(edge.adjacent_face_ids) - face_ids
        if missing:
            raise ValueError(f"edge references unknown faces: {sorted(missing)}")
    principal_face_ids = tuple(str(value) for value in data.get("principalFaceIds") or [])
    missing = set(principal_face_ids) - face_ids
    if missing:
        raise ValueError(f"principalFaceIds reference unknown faces: {sorted(missing)}")
    return SolidGeometrySnapshot(vertices, edges, faces, principal_face_ids)


def _operation(data: dict[str, Any]) -> OperationSnapshot:
    return OperationSnapshot(
        operation_id=str(data.get("operationId") or ""),
        operation_type=str(data.get("operationType") or "UNKNOWN").upper(),
        operative_geometry=_solid_geometry(data.get("operativeGeometry") or {}),
        source_evidence=tuple(str(value) for value in data.get("sourceEvidence") or []),
    )


def _require_unique(label: str, values) -> None:
    items = list(values)
    if any(not item for item in items) or len(items) != len(set(items)):
        raise ValueError(f"{label} values must be non-empty and unique")
