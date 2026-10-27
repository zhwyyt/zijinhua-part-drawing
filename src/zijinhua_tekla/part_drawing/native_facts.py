"""R1 evidence catalog. No inferred geometry is fabrication-ready in this layer."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID
from datetime import datetime


def _pointer(path, key):
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _walk(value, path="#"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, _pointer(path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, _pointer(path, index))


def _value(node):
    # Contour access has an outer exception boundary and inner collection status.
    while isinstance(node, dict) and "status" in node:
        if node["status"] != "READ":
            return None
        node = node.get("value")
    return node


def _items(node):
    # Retain partial enumeration results; errors are independently scanned below.
    if isinstance(node, dict) and node.get("status") in ("READ", "ERROR"):
        return node.get("value") if isinstance(node.get("value"), list) else []
    return []


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    return isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value)


def _fact(value, source, basis="DIRECT"):
    return {"basis": basis, "sourceRef": source, "value": deepcopy(value)}


def _issue(issues, code, source, message, read_error=False):
    issues.append({"code": code, "sourceRef": source, "message": message, "readError": read_error})


def _require_fields(node, names, path, issues):
    for name in names:
        if name not in node:
            _issue(issues, "FIELD_MISSING", _pointer(path, name), "Required reader field absent", True)


def _solid_catalog(solid, path, issues):
    edges = []
    for index, edge in enumerate(_items(solid.get("edges"))):
        ref = f"{path}/edges/value/{index}"
        if not isinstance(edge, dict) or "status" in edge:
            _issue(issues, "EDGE_UNREADABLE", ref, "Edge unavailable", True)
            continue
        _require_fields(edge, ("id", "apiType", "apiTypeCode", "start", "end", "curveParameters"), ref, issues)
        if not _point(edge.get("start")) or not _point(edge.get("end")):
            _issue(issues, "EDGE_POINTS_INVALID", ref, "Finite 3D endpoints required", True)
        edges.append({"id": edge.get("id"), "evidence": _fact(edge, ref),
                      "geometryKind": "UNKNOWN"})
    faces = []
    for face_index, face in enumerate(_items(solid.get("faces"))):
        ref = f"{path}/faces/value/{face_index}"
        if not isinstance(face, dict) or "status" in face:
            _issue(issues, "FACE_UNREADABLE", ref, "Face unavailable", True)
            continue
        _require_fields(face, ("id", "normal", "surfaceType", "loops"), ref, issues)
        loops = []
        for loop_index, loop in enumerate(_items(face.get("loops"))):
            loop_ref = f"{ref}/loops/value/{loop_index}"
            if not isinstance(loop, dict) or "status" in loop:
                _issue(issues, "LOOP_UNREADABLE", loop_ref, "Loop unavailable", True)
                continue
            _require_fields(loop, ("id", "vertices"), loop_ref, issues)
            vertices = _items(loop.get("vertices"))
            candidates = []
            for index, start in enumerate(vertices):
                end = vertices[(index + 1) % len(vertices)]
                if not _point(start) or not _point(end):
                    _issue(issues, "LOOP_POINT_INVALID", loop_ref, "Finite 3D vertices required", True)
                    continue
                matches = [edge["evidence"]["sourceRef"] for edge in edges
                           if (edge["evidence"]["value"].get("start"), edge["evidence"]["value"].get("end"))
                           in ((start, end), (end, start))]
                if len(matches) != 1:
                    _issue(issues, "EDGE_MAPPING_UNRESOLVED", loop_ref + f"/vertices/value/{index}",
                           f"Exact endpoints yield {len(matches)} candidates; no topology proof")
                candidates.append({"vertexIndices": [index, (index + 1) % len(vertices)],
                                   "edgeRefs": matches, "basis": "DERIVED", "method": "EXACT_ENDPOINT_EQUALITY",
                                   "status": "CANDIDATE" if len(matches) == 1 else "UNKNOWN"})
            loops.append({"evidence": _fact(loop, loop_ref), "endpointCandidates": candidates})
        faces.append({"evidence": _fact(face, ref), "surfaceKind": "UNKNOWN", "loops": loops})
    return {"edges": edges, "faces": faces}


def _bolt_catalog(bolt, path, issues):
    _require_fields(bolt, ("id", "properties", "positions", "targets", "coordinateSystem", "solidWithHighAccuracy"), path, issues)
    properties = bolt.get("properties", {})
    _require_fields(properties, ("BoltStandard", "BoltSize", "Tolerance", "SlottedHoleX", "SlottedHoleY",
                                "HoleType", "RotateSlots", "Bolt", "Hole1", "Hole2", "Hole3", "Hole4", "Hole5"),
                    path + "/properties", issues)
    standard = _value(properties.get("BoltStandard"))
    classification = "ATTACHMENT_STUD" if standard == "STUD" else (
        "HOLE_CANDIDATE" if isinstance(standard, str) and standard else "UNKNOWN")
    result = {"id": bolt.get("id"), "evidence": _fact(bolt, path),
              "classification": _fact(classification, path + "/properties/BoltStandard", "DERIVED"),
              "actualHoleStatus": "UNPROVEN", "parameterCandidate": None}
    targets = bolt.get("targets", {})
    _require_fields(targets, ("partToBoltTo", "partToBeBolted", "otherParts"), path + "/targets", issues)
    for key in ("partToBoltTo", "partToBeBolted"):
        target = _value(targets.get(key))
        if not isinstance(target, dict) or type(target.get("id")) is not int:
            _issue(issues, "TARGET_UNRESOLVED", path + "/targets/" + key, "Target identity not established")
    positions = _items(bolt.get("positions"))
    if not positions or not all(_point(p) for p in positions):
        _issue(issues, "BOLT_POSITIONS_UNRESOLVED", path + "/positions", "No complete finite position list")
    names = ("BoltSize", "Tolerance", "SlottedHoleX", "SlottedHoleY")
    values = [_value(properties.get(name)) for name in names]
    if classification == "HOLE_CANDIDATE" and all(_number(v) and v >= 0 for v in values) and values[0] > 0:
        size, tolerance, slot_x, slot_y = values
        if not all(_number(v) for v in (size + tolerance, size + tolerance + slot_x, size + tolerance + slot_y)):
            raise ValueError("Nonfinite derived bolt parameter")
        result["parameterCandidate"] = {
            "basis": "DERIVED", "status": "UNPROVEN", "baseDiameter": size + tolerance,
            "sizeAlongGroupX": size + tolerance + slot_x,
            "sizeAlongGroupY": size + tolerance + slot_y,
            "sourceRefs": [path + "/properties/" + name for name in names],
            "method": "BoltSize + Tolerance; add respective slot increment. Not an actual hole shape or orientation."}
    if classification != "ATTACHMENT_STUD":
        _issue(issues, "HOLE_EFFECT_UNPROVEN", path, "Prove actual shape, target intersection, slot flags and rotation in R2")
    return result


def _scan_envelopes(evidence, issues):
    for path, value in _walk(evidence):
        if isinstance(value, dict) and "status" in value:
            status = value["status"]
            if status not in ("READ", "UNAVAILABLE", "ERROR"):
                raise ValueError(f"Invalid raw status at {path}")
            if "value" not in value or not isinstance(value.get("source"), str):
                raise ValueError(f"Invalid raw evidence envelope at {path}")
            if status != "READ":
                _issue(issues, "READ_ERROR" if status == "ERROR" else "API_UNAVAILABLE", path,
                       value.get("reason", str(value.get("error", "Unknown"))), status == "ERROR")
            collection_names = ("edges", "faces", "loops", "vertices", "positions", "boltGroups", "operations", "children", "otherParts")
            if status == "READ" and path.rsplit("/", 1)[-1] in collection_names and not isinstance(value["value"], list):
                _issue(issues, "COLLECTION_INVALID", path, "Successful collection must contain a list", True)


def _valid_plane(plane):
    return isinstance(plane, dict) and all(
        _point(plane.get(key)) for key in ("origin", "axisX", "axisY", "axisZ"))


def _validate_context(evidence, issues):
    model = _value(evidence.get("model"))
    if not isinstance(model, dict) or not model.get("name") or not model.get("path"):
        _issue(issues, "MODEL_IDENTITY_MISSING", "#/model", "Model name/path required", True)
    before, after = _value(evidence.get("workPlaneBefore")), _value(evidence.get("workPlaneRestore"))
    if not _valid_plane(before) or not _valid_plane(after) or before != after:
        _issue(issues, "WORK_PLANE_UNCONFIRMED", "#/workPlaneRestore", "Original plane restoration not confirmed", True)
    raw_parts = evidence.get("parts")
    if not isinstance(raw_parts, list):
        raise ValueError("parts must be a list")
    selection = evidence.get("selection", [])
    if not isinstance(selection, list):
        raise ValueError("selection must be a list")
    requested = [s.get("requestedId") for s in selection if isinstance(s, dict)]
    ids = [part.get("id") for part in raw_parts if isinstance(part, dict)]
    if not ids or ids != requested or len(set(ids)) != len(ids) or any(type(i) is not int or i <= 0 for i in ids):
        _issue(issues, "PART_IDENTITY_MISMATCH", "#/selection", "Nonempty unique resolved/requested part IDs must agree", True)
    for item in selection:
        if not isinstance(item, dict) or not isinstance(item.get("resolved"), dict) or item["resolved"].get("id") != item.get("requestedId"):
            _issue(issues, "SELECTION_UNRESOLVED", "#/selection", "Requested object did not resolve", True)
    return raw_parts


def _source_identity(part, path, issues, required):
    names = ("guid", "modificationTime", "identityAfterRead", "dstvCoordinateSystem")
    if not required and not any(name in part for name in names):
        return {"status": "UNAVAILABLE", "reason": "Legacy R1 capture has no instance/version evidence"}
    _require_fields(part, names, path, issues)
    guid = _value(part.get("guid"))
    stamp = _value(part.get("modificationTime"))
    after = _value(part.get("identityAfterRead"))
    frame = _value(part.get("dstvCoordinateSystem"))
    valid = True
    try:
        valid = isinstance(guid, str) and UUID(guid).int != 0
        valid = valid and isinstance(stamp, dict) and stamp.get("kind") in ("Utc", "Local", "Unspecified")
        valid = valid and "T" in stamp["value"] and bool(datetime.fromisoformat(stamp["value"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        valid = False
    if not valid:
        _issue(issues, "SOURCE_IDENTITY_MISSING", path, "Nonempty GUID and explicit modification timestamp required", True)
    if not isinstance(after, dict) or after.get("guid") != guid or after.get("modificationTime") != stamp:
        valid = False
        _issue(issues, "SOURCE_CHANGED_DURING_READ", path, "Identity/timestamp changed or recheck unavailable", True)
    if not isinstance(frame, dict) or not all(_point(frame.get(k)) for k in ("origin", "axisX", "axisY")):
        valid = False
        _issue(issues, "DSTV_FRAME_INVALID", path, "Finite DSTV frame required", True)
    else:
        x, y = frame["axisX"], frame["axisY"]
        cross = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
        if not _point(cross) or not any(cross):
            valid = False
            _issue(issues, "DSTV_FRAME_INVALID", path, "DSTV axes must be nonzero and independent", True)
    return {"status": "READ_CONSISTENT" if valid else "BLOCKED", "guid": _fact(guid, path + "/guid"),
            "modificationTime": _fact(stamp, path + "/modificationTime"),
            "dstvCoordinateSystem": _fact(frame, path + "/dstvCoordinateSystem")}


def _part_catalog(part, index, issues, require_identity=False):
    if not isinstance(part, dict):
        raise ValueError("part must be an object")
    path = f"#/parts/{index}"
    _require_fields(part, ("id", "runtimeType", "reports", "coordinateSystem", "modelingContour", "finalSolid",
                           "boltGroups", "operations", "children", "readStatus"), path, issues)
    reports = part.get("reports", {})
    position = _value(reports.get("PART_POS"))
    alternate = _value(reports.get("PART_POSITION"))
    if not isinstance(position, str) or not position or (alternate and alternate != position):
        _issue(issues, "PART_POSITION_UNRESOLVED", path + "/reports", "Exact PART_POS missing or conflicts with PART_POSITION")
    solid = _value(part.get("finalSolid"))
    if not isinstance(solid, dict):
        solid = {}
    _require_fields(solid, ("bounds", "edges", "faces"), path + "/finalSolid/value", issues)
    bolts = []
    for bolt_index, bolt in enumerate(_items(part.get("boltGroups"))):
        bolt_path = f"{path}/boltGroups/value/{bolt_index}"
        if not isinstance(bolt, dict) or "status" in bolt:
            _issue(issues, "BOLT_UNREADABLE", bolt_path, "Bolt group unavailable", True)
            continue
        bolts.append(_bolt_catalog(bolt, bolt_path, issues))
    _issue(issues, "FINAL_GEOMETRY_UNPROVEN", path, "R2 must prove boundaries, arc domains, thickness, holes and operation effects")
    return {"id": part.get("id"), "partPosition": _fact(position, path + "/reports/PART_POS", "DIRECT" if position else "UNKNOWN"),
                  "sourceIdentity": _source_identity(part, path, issues, require_identity),
                  "rawEvidence": _fact(part, path), "manufacturingStatus": "BLOCKED",
                  "solidCatalog": _solid_catalog(solid, path + "/finalSolid/value", issues), "boltGroups": bolts,
                  "operations": _fact(part.get("operations"), path + "/operations")}


def build_native_facts(evidence):
    """Catalog source data without source cleanup, geometry defaults or readiness claims."""
    if not isinstance(evidence, dict) or evidence.get("schemaVersion") != "teklaNativeEvidence.v1":
        raise ValueError("Expected teklaNativeEvidence.v1")
    if evidence.get("coordinateSpace") != "GLOBAL":
        raise ValueError("Expected explicit GLOBAL coordinate space")
    # Reject nonfinite numbers even in currently uninterpreted raw fields.
    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    issues = []
    _require_fields(evidence, ("model", "connectionStatus", "selection", "parts", "errors", "readStatus",
                              "readerVersion", "sdkVersion", "workPlaneBefore", "workPlaneRestore"), "#", issues)
    _scan_envelopes(evidence, issues)
    raw_parts = _validate_context(evidence, issues)
    parts = [_part_catalog(part, index, issues, evidence.get("readerVersion") in ("native-reader.r2a", "native-reader.r2b"))
             for index, part in enumerate(raw_parts)]
    complete = (evidence.get("connectionStatus") == "CONNECTED" and evidence.get("readStatus") == "COMPLETE"
                and not evidence.get("errors") and all(p.get("readStatus") == "COMPLETE" for p in raw_parts)
                and not any(i["readError"] for i in issues))
    return {"schemaVersion": "nativePartFacts.v1", "rawDigest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "digestMethod": "SHA256 canonical JSON UTF-8 sorted keys; no numeric rounding",
            "coordinateSpace": "GLOBAL", "model": _fact(evidence.get("model"), "#/model"),
            "readStatus": "COMPLETE" if complete else "PARTIAL", "manufacturingStatus": "BLOCKED",
            "parts": parts, "issues": issues}


def load_native_facts(path):
    raw = Path(path).read_bytes()
    result = build_native_facts(json.loads(raw.decode("utf-8-sig")))
    result["rawFileSha256"] = hashlib.sha256(raw).hexdigest()
    return result
