"""Small, independent final-solid fact and expression analyzer.

The analyzer is deliberately conservative.  It reports facts that can be
derived from the final solid itself and keeps supplemental bolt data separate.
It never calls Tekla and never imports the legacy geometry/expression code.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


TOL = 0.1


def _value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "status" in value:
        return value["value"]
    return value


def _vec(a: Any, b: Any) -> tuple[float, float, float]:
    return (float(b[0]) - float(a[0]), float(b[1]) - float(a[1]), float(b[2]) - float(a[2]))


def _dot(a: Any, b: Any) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _cross(a: Any, b: Any) -> tuple[float, float, float]:
    return (
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    )


def _norm(a: Any) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Any) -> tuple[float, float, float] | None:
    length = _norm(a)
    if length <= 1e-12:
        return None
    return tuple(float(x) / length for x in a)


def _area(points: list[list[float]], normal: Any) -> float:
    """Area of a planar polygon after projection onto its dominant plane."""
    if len(points) < 3:
        return 0.0
    n = _unit(normal)
    if n is None:
        return 0.0
    total = (0.0, 0.0, 0.0)
    for left, right in zip(points, points[1:] + points[:1]):
        total = tuple(x + y for x, y in zip(total, _cross(left, right)))
    return abs(_dot(total, n)) / 2.0


def _face_data(solid: dict[str, Any]) -> list[dict[str, Any]]:
    faces = _value(solid.get("faces", {})) or []
    result = []
    for index, raw in enumerate(faces):
        normal = _unit(_value(raw.get("normal")))
        loops = []
        for loop in _value(raw.get("loops", {})) or []:
            points = _value(loop.get("vertices", {})) or []
            if len(points) >= 3:
                loops.append(points)
        if normal is None or not loops:
            continue
        result.append(
            {
                "id": raw.get("id", f"F{index}"),
                "normal": list(normal),
                "loops": loops,
                "area": _area(loops[0], normal),
                "source": raw.get("normal", {}).get("source", f"finalSolid/faces/{index}"),
            }
        )
    return result


def _pair(faces: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for left_index, left in enumerate(faces):
        points = left["loops"][0]
        if not points:
            continue
        for right in faces[left_index + 1 :]:
            if _dot(left["normal"], right["normal"]) > -0.999:
                continue
            distance = abs(_dot(_vec(points[0], right["loops"][0][0]), left["normal"]))
            if distance <= TOL or distance > 5000:
                continue
            candidates.append((min(left["area"], right["area"]), distance, left, right))
    if not candidates:
        return None
    largest_id = max(faces, key=lambda face: face["area"])["id"] if faces else None
    anchored = [item for item in candidates if largest_id in (item[2]["id"], item[3]["id"])]
    area, distance, left, right = max(anchored or candidates, key=lambda item: item[0])
    return {"faces": [left["id"], right["id"]], "area": area, "thickness": distance, "normal": left["normal"]}


def _basis(normal: list[float], points: list[list[float]]) -> tuple[list[float], list[float]] | None:
    for left, right in zip(points, points[1:]):
        u = _unit(_vec(left, right))
        if u is not None and abs(_dot(u, normal)) < 0.999:
            v = _unit(_cross(normal, u))
            if v is not None:
                return list(u), list(v)
    return None


def _projected_extents(points: list[list[float]], origin: list[float], u: list[float], v: list[float]) -> dict[str, float]:
    values = [( _dot(_vec(origin, point), u), _dot(_vec(origin, point), v)) for point in points]
    xs, ys = zip(*values)
    return {"minX": min(xs), "maxX": max(xs), "minY": min(ys), "maxY": max(ys),
            "sizeX": max(xs) - min(xs), "sizeY": max(ys) - min(ys)}


def _circle(loop: list[list[float]], normal: list[float]) -> dict[str, Any] | None:
    if len(loop) < 6:
        return None
    center = [sum(point[i] for point in loop) / len(loop) for i in range(3)]
    radii = [_norm(_vec(center, point)) for point in loop]
    mean = sum(radii) / len(radii)
    if mean <= 0 or max(radii) - min(radii) > max(0.5, mean * 0.03):
        return None
    return {"kind": "ROUND", "center": center, "diameter": 2.0 * mean, "vertices": len(loop), "normal": normal}


def _final_holes(faces: list[dict[str, Any]], pair: dict[str, Any] | None) -> list[dict[str, Any]]:
    if pair is None:
        return []
    holes = []
    for face in faces:
        if face["id"] not in pair["faces"] or len(face["loops"]) < 2:
            continue
        for loop in face["loops"][1:]:
            circle = _circle(loop, face["normal"])
            if circle:
                holes.append({"source": f"finalSolid/face/{face['id']}/innerLoop", **circle})
    return holes


def _bolt_supplement(part: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, group in enumerate(_value(part.get("boltGroups", {})) or []):
        props = {key: _value(value) for key, value in (_value(group.get("properties", {})) or {}).items()}
        positions = _value(group.get("positions", {})) or []
        if not positions:
            continue
        hole_type = _value(props.get("HoleType")) or {}
        shape = "SLOT" if "SLOT" in str(hole_type.get("name", "")).upper() else "ROUND"
        result.append({"id": group.get("id", f"boltGroup:{index}"), "source": "boltGroups",
                       "shape": shape, "count": len(positions), "positions": positions,
                       "bolt": props.get("Bolt"), "boltSize": props.get("BoltSize"),
                       "tolerance": props.get("Tolerance"), "holeType": hole_type.get("name")})
    return result


def _part(part: dict[str, Any]) -> dict[str, Any]:
    reports = {key: _value(value) for key, value in (_value(part.get("reports", {})) or {}).items()}
    position = reports.get("PART_POS") or f"id:{part.get('id')}"
    solid = _value(part.get("finalSolid", {}))
    faces = _face_data(solid or {}) if isinstance(solid, dict) else []
    edges = _value((solid or {}).get("edges", {})) if isinstance(solid, dict) else []
    pair = _pair(faces)
    largest = max(faces, key=lambda face: face["area"], default=None)
    outline = None
    if largest:
        basis = _basis(largest["normal"], largest["loops"][0])
        if basis:
            u, v = basis
            outline = {"face": largest["id"], "normal": largest["normal"], "basis": {"u": u, "v": v},
                       "extents": _projected_extents(largest["loops"][0], largest["loops"][0][0], u, v),
                       "vertices": largest["loops"][0]}
    holes = _final_holes(faces, pair)
    supplemental = _bolt_supplement(part)
    issues = []
    if not faces or not edges:
        issues.append({"code": "FINAL_SOLID_INCOMPLETE", "message": "finalSolid faces or edges are missing"})
    if pair is None:
        issues.append({"code": "THICKNESS_UNRESOLVED", "message": "no parallel final-solid face pair proves thickness"})
    runtime = part.get("runtimeType", "")
    profile = _value(part.get("profile", {}))
    is_plate_profile = isinstance(profile, str) and profile.upper().startswith("PL")
    # The final solid supplies all numeric facts.  A segmented strip has more
    # than one substantial top/bottom face pair; the runtime is only a
    # conservative guard for the PolyBeam family and never supplies dimensions.
    largest_area = max((face["area"] for face in faces), default=0.0)
    parallel_faces = [face for face in faces if pair and abs(abs(_dot(face["normal"], pair["normal"])) - 1.0) < 0.01
                      and face["area"] > largest_area * 0.05]
    folded = bool(pair and "PolyBeam" in runtime and len(parallel_faces) > 2)
    if pair and is_plate_profile and not folded:
        part_type = "FLAT_PLATE"
    elif pair and is_plate_profile and folded:
        part_type = "FOLDED_PLATE"
    elif pair:
        part_type = "SECTION_OR_OTHER"
    else:
        part_type = "UNRESOLVED_SOLID"
    status = "READY" if not issues and outline else "BLOCKED"
    return {"id": part.get("id"), "partPosition": position, "runtimeType": runtime, "profile": profile,
            "material": _value(part.get("material", {})), "status": status, "partType": part_type,
            "finalSolid": {"faceCount": len(faces), "edgeCount": len(edges),
                            "bounds": _value((solid or {}).get("bounds", {})) if isinstance(solid, dict) else None,
                            "thickness": pair["thickness"] if pair else None,
                            "thicknessFaces": pair["faces"] if pair else [],
                            "outline": outline},
            "holes": holes, "supplementalBoltGroups": supplemental, "issues": issues}


def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    source_digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    facts = [_part(part) for part in snapshot.get("parts", [])]
    expressions = []
    for fact in facts:
        required = ["outline.shape", "outline.sizeX", "outline.sizeY", "material.thickness"]
        if fact["supplementalBoltGroups"]:
            required.append("holes.shape_and_location")
        issues = list(fact["issues"])
        if fact["partType"] == "FOLDED_PLATE":
            issues.append({"code": "FOLDED_EXPRESSION_ROUTE_UNIMPLEMENTED", "message": "final-solid facts are present; folded-plate expression mapping is not in this trial"})
        elif fact["partType"] == "SECTION_OR_OTHER":
            issues.append({"code": "TYPE_OUT_OF_SCOPE", "message": "section/beam expression mapping is outside this first trial domain"})
        if fact["supplementalBoltGroups"] and not fact["holes"]:
            issues.append({"code": "HOLE_FINAL_STATE_UNRESOLVED", "message": "bolt groups exist but no circular final-solid loop was independently proven"})
        status = "READY" if fact["status"] == "READY" and not issues else "BLOCKED"
        expressions.append({"partPosition": fact["partPosition"], "status": status, "required": required,
                            "covered": ["outline.shape", "outline.sizeX", "outline.sizeY", "material.thickness"] if fact["finalSolid"]["outline"] else [],
                            "holes": fact["holes"], "issues": issues})
    ready = sum(item["status"] == "READY" for item in expressions)
    return {"schemaVersion": "finalSolidTrial.v1", "status": "READY" if facts and ready == len(facts) else "PARTIAL",
            "source": {"schemaVersion": snapshot.get("schemaVersion"), "readerVersion": snapshot.get("readerVersion"),
                       "model": _value(snapshot.get("model", {})), "inputDigest": source_digest,
                       "selectionSource": snapshot.get("selectionSource")},
            "geometryFacts": facts, "expressionRequirements": expressions,
            "summary": {"parts": len(facts), "geometryReady": sum(item["status"] == "READY" for item in facts),
                        "expressionReady": ready, "blocked": len(facts) - ready}}


def write_trial(input_path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    input_path = Path(input_path)
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    snapshot = json.loads(input_path.read_text(encoding="utf-8-sig"))
    report = analyze_snapshot(snapshot)
    out_dir.mkdir(parents=True)
    (out_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "facts.json").write_text(json.dumps({"schemaVersion": report["schemaVersion"], "source": report["source"], "parts": report["geometryFacts"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "expressions.json").write_text(json.dumps({"schemaVersion": report["schemaVersion"], "source": report["source"], "parts": report["expressionRequirements"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
