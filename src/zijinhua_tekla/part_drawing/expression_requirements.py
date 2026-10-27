"""Deterministic manufacturing-expression requirements, independent of annotation layout."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import Any

from .contracts import PartDrawingSnapshot
from .feature_recognizer import recognize_plate_features
from .geometry_analyzer import analyze_plate_geometry, project_point


RULES_VERSION = "flat-plate-expression.r1"
GEOMETRY_TOLERANCE = 1e-6
SPECIFICATIONS = ("cutting_method", "hole_method", "tolerance", "edge_finish")
EVIDENCE_CHECKS = ("final_contour", "all_holes", "uniform_thickness", "all_operations")


def content_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_digest(snapshot: PartDrawingSnapshot) -> str:
    return content_digest(asdict(snapshot))


def axis_aligned_arc(segment, tolerance=GEOMETRY_TOLERANCE):
    """Endpoint radius vectors lie on coordinate axes within the source budget."""
    return all(any(abs(p[a]-segment['center'][a]) <= tolerance for a in (0, 1))
               for p in (segment['start'], segment['end']))


def arc_contains(segment: dict, point: list[float], tolerance=GEOMETRY_TOLERANCE) -> bool:
    center, radius = segment["center"], segment["radius"]
    if abs(math.dist(center, point) - radius) > tolerance:
        return False
    angle = lambda p: math.atan2(p[1] - center[1], p[0] - center[0])
    direction = -1 if segment["clockwise"] else 1
    sweep = (direction * (angle(segment["end"]) - angle(segment["start"]))) % math.tau
    offset = (direction * (angle(point) - angle(segment["start"]))) % math.tau
    return offset <= sweep + tolerance / radius or math.dist(point, segment["start"]) <= tolerance


def _issue(code: str, message: str, feature="plate") -> dict:
    return {"code": code, "feature": feature, "message": message}


def _evidence(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)


def _context_issues(snapshot, digest, context):
    issues = []
    if not isinstance(context, dict) or context.get("schemaVersion") != "manufacturingContext.v1":
        return [_issue("EVIDENCE_MISSING", "manufacturingContext.v1 is required for READY")]
    expected = {"modelIdentifier": snapshot.model_identifier, "partId": snapshot.part_id,
                "partPosition": snapshot.part_position, "snapshotDigest": digest}
    if context.get("source") != expected:
        issues.append(_issue("SOURCE_MISMATCH", "context must match exact snapshot and part identity"))
    checks = context.get("checks", {})
    if not isinstance(checks, dict):
        return issues + [_issue("EVIDENCE_MISSING", "checks must be an object")]
    for name in EVIDENCE_CHECKS:
        check = checks.get(name, {})
        if not isinstance(check, dict) or check.get("status") != "VERIFIED" or not _evidence(check.get("evidence")):
            issues.append(_issue("EVIDENCE_MISSING", f"verified evidence required: {name}"))
    return issues


def _input_issues(snapshot):
    issues = []
    if not snapshot.model_identifier or not snapshot.part_id or not snapshot.part_position:
        issues.append(_issue("IDENTITY_MISSING", "model, part ID and exact part position are required"))
    if snapshot.part_form != "FLAT_PLATE":
        issues.append(_issue("UNSUPPORTED_FORM", snapshot.part_form))
    if snapshot.thickness <= 0:
        issues.append(_issue("INVALID_THICKNESS", "thickness must be positive"))
    if snapshot.inner_loops:
        issues.append(_issue("UNRESOLVED_INNER_LOOPS", "map inner loops to unique proven holes before this engine"))
    if snapshot.cuts or snapshot.operations:
        issues.append(_issue("UNRESOLVED_OPERATIONS", "operation effects must be resolved before using this flat-plate contract"))
    for item in snapshot.quality_issues:
        issues.append(_issue("SOURCE_QUALITY_ISSUE", f"{item.code}: {item.message}"))
    issues.extend(_hole_issues(snapshot))
    issues.extend(_boundary_issues(snapshot))
    issues.extend(_solid_issues(snapshot))
    return issues


def _hole_issues(snapshot):
    issues = []
    hole_ids = [hole.hole_id for hole in snapshot.holes]
    if any(not value for value in hole_ids) or len(hole_ids) != len(set(hole_ids)):
        issues.append(_issue("INVALID_HOLE_IDS", "hole IDs must be nonempty and unique"))
    for hole in snapshot.holes:
        valid = (hole.kind == "ROUND" and hole.diameter > 0) or (
            hole.kind == "SLOT" and hole.width > 0 and hole.length > hole.width)
        if not valid:
            issues.append(_issue("UNSUPPORTED_HOLE", "expected ROUND diameter > 0 or SLOT total length > width > 0", hole.hole_id))
        delta = [getattr(hole.center, a) - getattr(snapshot.local_frame.origin, a) for a in "xyz"]
        depth = sum(delta[i] * getattr(snapshot.local_frame.normal, a) for i, a in enumerate("xyz"))
        if abs(depth) > GEOMETRY_TOLERANCE:
            issues.append(_issue("HOLE_OFF_PLANE", "hole center must be projected onto the supplied principal plane", hole.hole_id))
    for i, hole in enumerate(snapshot.holes):
        if any(math.dist(asdict(hole.center).values(), asdict(other.center).values()) <= GEOMETRY_TOLERANCE
               for other in snapshot.holes[:i]):
            issues.append(_issue("AMBIGUOUS_COINCIDENT_HOLES", "resolve duplicate/conflicting hole sources before counting", hole.hole_id))
    return issues


def _boundary_issues(snapshot):
    issues = []
    for index, segment in enumerate(snapshot.outer_loop):
        following = snapshot.outer_loop[(index + 1) % len(snapshot.outer_loop)]
        if math.dist(asdict(segment.end).values(), asdict(following.start).values()) > GEOMETRY_TOLERANCE:
            issues.append(_issue("OPEN_BOUNDARY", "boundary endpoints do not coincide", f"edge:{index}"))
        if segment.kind not in {"LINE", "ARC"} or math.dist(asdict(segment.start).values(), asdict(segment.end).values()) <= GEOMETRY_TOLERANCE:
            issues.append(_issue("INVALID_SEGMENT", "unsupported or zero-length boundary", f"edge:{index}"))
        if segment.kind == "ARC":
            if segment.center is None:
                issues.append(_issue("INVALID_ARC", "arc needs a proven center", f"edge:{index}"))
            else:
                radius = math.dist(asdict(segment.start).values(), asdict(segment.center).values())
                end_radius = math.dist(asdict(segment.end).values(), asdict(segment.center).values())
                if radius <= GEOMETRY_TOLERANCE or abs(radius - end_radius) > GEOMETRY_TOLERANCE:
                    issues.append(_issue("INVALID_ARC", "arc endpoints do not share the proven radius", f"edge:{index}"))
    return issues


def _solid_issues(snapshot):
    issues = []
    if snapshot.final_geometry:
        solid = snapshot.final_geometry
        if any(edge.evidence_status != "PROVEN" or edge.curve_type not in {"LINE", "ARC"} for edge in solid.edges):
            issues.append(_issue("UNRESOLVED_SOLID", "final solid contains unproven/unsupported edges"))
        principal = [face for face in solid.faces if face.face_id in solid.principal_face_ids]
        depths = []
        for face in principal:
            depths.append([sum((getattr(p, a) - getattr(snapshot.local_frame.origin, a)) *
                               getattr(snapshot.local_frame.normal, a) for a in "xyz")
                           for loop in face.loops for p in loop.ordered_vertices])
        if (len(depths) != 2 or any(not values or max(values) - min(values) > GEOMETRY_TOLERANCE for values in depths)
                or abs(abs(depths[0][0] - depths[1][0]) - snapshot.thickness) > GEOMETRY_TOLERANCE):
            issues.append(_issue("THICKNESS_EVIDENCE_CONFLICT", "principal faces do not prove supplied uniform thickness"))
    return issues


def _facts(snapshot):
    def xy(point):
        projected = project_point(point, snapshot.local_frame)
        return [projected.x, projected.y]

    points = {f"vertex:{i}": xy(segment.start) for i, segment in enumerate(snapshot.outer_loop)}
    segments = []
    for i, segment in enumerate(snapshot.outer_loop):
        item = {"id": f"edge:{i}", "kind": segment.kind, "start": xy(segment.start),
                "end": xy(segment.end), "startPoint": f"vertex:{i}",
                "endPoint": f"vertex:{(i + 1) % len(snapshot.outer_loop)}"}
        if segment.kind == "ARC":
            item.update(center=xy(segment.center), clockwise=segment.clockwise)
            item["radius"] = math.dist(item["start"], item["center"])
            for cardinal in range(4):
                angle = cardinal * math.pi / 2
                p = [item["center"][0] + item["radius"] * math.cos(angle),
                     item["center"][1] + item["radius"] * math.sin(angle)]
                if arc_contains(item, p):
                    points[f"edge:{i}:extreme:{cardinal}"] = p
        segments.append(item)
    boundary_points = list(points.values())
    holes = []
    for hole in sorted(snapshot.holes, key=lambda h: h.hole_id):
        point_id = f"hole:{hole.hole_id}"
        points[point_id] = xy(hole.center)
        parameters = {"diameter": hole.diameter} if hole.kind == "ROUND" else {
            "length": hole.length, "width": hole.width, "angle": hole.angle_deg % 180}
        holes.append({"id": hole.hole_id, "pointId": point_id, "kind": hole.kind, **parameters})
    bounds = [min(p[0] for p in boundary_points), min(p[1] for p in boundary_points),
              max(p[0] for p in boundary_points), max(p[1] for p in boundary_points)]
    datum = min((f"vertex:{i}" for i in range(len(segments))), key=lambda k: (points[k], k))
    return {"points": points, "segments": segments, "holes": holes, "datum": datum, "bounds": bounds}


def _hole_groups(facts):
    def signature(hole):
        return tuple((k, v) for k, v in hole.items() if k not in {"id", "pointId"})

    def equivalent(a, b):
        return len(a) == len(b) and all(ka == kb and (va == vb if isinstance(va, str) else
            abs(va-vb) <= GEOMETRY_TOLERANCE) for (ka, va), (kb, vb) in zip(a, b))

    grouped = []
    for hole in sorted(facts['holes'], key=signature):
        group = next((g for g in grouped if all(equivalent(signature(hole), signature(h)) for h in g)), None)
        if group is None:
            grouped.append([hole])
        else:
            group.append(hole)
    groups = []
    for index, holes in enumerate(grouped):
        groups.append({"id": f"group:{index}", **dict(signature(holes[0])), "members": [h["id"] for h in holes],
                       "count": len(holes)})
    return groups


def _pitch_candidates(facts, groups):
    candidates = []
    for group in groups:
        members = {h["id"]: facts["points"][h["pointId"]] for h in facts["holes"] if h["id"] in group["members"]}
        for axis in (0, 1):
            rows = []
            for member, p in members.items():
                row = next((row for row in rows if all(abs(p[1-axis]-members[m][1-axis]) <= GEOMETRY_TOLERANCE for m in row)), None)
                if row is None:
                    rows.append([member])
                else:
                    row.append(member)
            for row in rows:
                row.sort(key=lambda member: (members[member][axis], member))
                if len(row) < 3:
                    continue
                steps = [members[b][axis] - members[a][axis] for a, b in zip(row, row[1:])]
                if min(steps) > GEOMETRY_TOLERANCE and max(steps) - min(steps) <= GEOMETRY_TOLERANCE:
                    candidates.append({"members": row, "axis": "xy"[axis], "holeCount": len(row),
                                       "intervalCount": len(row) - 1, "pitch": steps[0]})
    return candidates


def _specifications(context):
    context = context if isinstance(context, dict) else {}
    specs, issues = {}, []
    supplied = context.get("specifications", {})
    required = context.get("requiredSpecifications", [])
    if not isinstance(supplied, dict) or not isinstance(required, list):
        return {}, [_issue("INVALID_SPECIFICATIONS", "specifications must be an object; requiredSpecifications a list")]
    for name in set(supplied) | set(str(v) for v in required):
        if name not in SPECIFICATIONS:
            issues.append(_issue("UNSUPPORTED_SPECIFICATION", name))
            continue
        spec = supplied.get(name, {})
        if not isinstance(spec, dict) or not isinstance(spec.get("value"), str) or not spec["value"].strip() or not _evidence(spec.get("evidence")):
            issues.append(_issue("SPECIFICATION_MISSING", f"explicit value and evidence required: {name}"))
        else:
            specs[name] = spec
    return dict(sorted(specs.items())), sorted(issues, key=lambda i: (i["code"], i["message"]))


def build_expression_requirements(snapshot: PartDrawingSnapshot, context: dict | None = None) -> dict:
    """Build a requirements manifest, never a drawing or a production-release decision."""
    digest = snapshot_digest(snapshot)  # also rejects NaN/Infinity before geometry algorithms
    issues = _input_issues(snapshot)
    issues.extend(_context_issues(snapshot, digest, context))
    specs, spec_issues = _specifications(context)
    issues.extend(spec_issues)
    geometry_errors = [i for i in issues if i["code"] not in {
        "EVIDENCE_MISSING", "SOURCE_MISMATCH", "SPECIFICATION_MISSING", "UNSUPPORTED_SPECIFICATION", "INVALID_SPECIFICATIONS"}]
    if not geometry_errors:
        analysis = analyze_plate_geometry(snapshot)
        geometry_errors = [_issue("GEOMETRY_INVALID", issue.message) for issue in analysis.issues]
        if analysis.geometry is not None and snapshot.final_geometry is not None:
            recognition = recognize_plate_features(snapshot, analysis.geometry)
            geometry_errors.extend(_issue("UNRESOLVED_SOLID", issue.message) for issue in recognition.issues)
            if any(feature.feature_type.value == "BEVEL" for feature in recognition.features):
                geometry_errors.append(_issue("UNSUPPORTED_BEVEL", "principal faces prove a bevel outside this rule set"))
        issues.extend(geometry_errors)
    facts = _facts(snapshot) if not geometry_errors else {}
    requirements, groups = _requirements(snapshot.part_position, snapshot.thickness, facts, specs)
    source = {"modelIdentifier": snapshot.model_identifier, "partId": snapshot.part_id,
              "partPosition": snapshot.part_position, "snapshotDigest": digest}
    return {"schemaVersion": "expressionRequirements.v1", "rulesVersion": RULES_VERSION,
            "source": source, "sourceDigest": content_digest({"snapshot": digest, "context": context, "rules": RULES_VERSION}),
            "status": "BLOCKED" if issues else "READY", "issues": issues,
            "tolerance": GEOMETRY_TOLERANCE, "displayDecimals": 1,
            "facts": facts, "groups": groups, "pitchCandidates": _pitch_candidates(facts, groups) if facts else [],
            "requirements": requirements, "specifications": specs,
            "unspecified": [{"aspect": name, "status": "NOT_SPECIFIED"} for name in SPECIFICATIONS if name not in specs],
            "scope": "GEOMETRIC_EXPRESSION_ONLY; layout, persistence and production release are separate gates"}


def _requirements(part_position, thickness, facts, specs):
    requirements = []

    def require(feature, aspect, expected, rule, alternatives):
        requirements.append({"id": f"{feature}/{aspect}", "feature": feature, "aspect": aspect,
                             "expected": expected, "rule": rule, "alternatives": alternatives})

    require("plate", "identity", part_position, "PLATE-01", ["label"])
    require("plate", "thickness", thickness, "PLATE-02", ["table", "dimension"])
    groups = []
    if facts:
        bounds = facts["bounds"]
        for axis in (0, 1):
            require("plate", f"overall_{'xy'[axis]}", bounds[axis + 2] - bounds[axis], "PLATE-03", ["dimension"])
        for segment in facts["segments"]:
            require(segment["id"], "shape", segment, "OUTLINE-01", ["visible_segment"])
            if segment["kind"] == "ARC":
                require(segment["id"], "radius", segment["radius"], "ARC-01", ["radius_callout"])
        # Point positions are constraints, not instructions to create one dimension per point.
        for point_id in sorted(facts["points"]):
            if ":extreme:" in point_id:
                continue
            for axis in (0, 1):
                offset = facts["points"][point_id][axis] - facts["points"][facts["datum"]][axis]
                require(point_id, f"location_{'xy'[axis]}", offset, "LOCATION-01", ["dimension_chain", "shared_coordinate"])
        groups = _hole_groups(facts)
        for group in groups:
            require(group["id"], "size_count", group, "HOLE-01", ["hole_callout"])
        for hole in facts["holes"]:
            require(f"hole:{hole['id']}", "shape", {**hole, "center": facts["points"][hole["pointId"]]}, "HOLE-00", ["visible_hole"])
            require(f"hole:{hole['id']}", "center_cross", facts["points"][hole["pointId"]], "HOLE-02", ["center_lines"])
            if hole["kind"] == "SLOT":
                require(f"hole:{hole['id']}", "orientation", hole["angle"], "SLOT-01", ["oriented_center_lines"])
    for name, spec in specs.items():
        require("specification", name, spec["value"], "PROCESS-01", ["note", "table"])
    return requirements, groups
