"""Conservative correspondence with plate NC fields; source gate is separate."""

import math

from .analytic_geometry import add, sub, mul, norm


class NcGeometryConflict(ValueError):
    """An applicable, interpreted witness contradicts a specific native feature family."""

    def __init__(self, message, feature_scope):
        super().__init__(message)
        self.feature_scope = feature_scope


def _fields(row):
    return [float(field["text"]) for field in row["fields"]]


def _outline_match(outline, block, tolerance):
    rows = block["rows"]
    if any(row["face"] != "v" for row in rows):
        raise ValueError("Only NC front face is implemented")
    if any(row["fields"][0]["modifier"] != ("u" if i == 0 else "") for i, row in enumerate(rows)):
        raise ValueError("NC outline reference modifier unsupported")
    if _fields(rows[-1])[2] != 0:
        raise ValueError("Closing NC row contains unconsumed curvature")
    segments = outline["segments"]
    if len(rows)-1 != len(segments):
        raise ValueError("NC/model boundary segmentation differs; correspondence unproven")
    if any(s["kind"] == "ARC" and abs(s["sweepRadians"]) > math.pi+1e-10 for s in segments):
        raise ValueError("NC major-arc correspondence is not implemented")
    matches, used = [], set()
    order, directions = [], []
    for i in range(len(rows)-1):
        fields, following = _fields(rows[i]), _fields(rows[i+1])
        a, b, radius = fields[:2], following[:2], fields[2]
        candidates = []
        for j, segment in enumerate(segments):
            for direction in (1, -1):
                start, end = ((segment["start"], segment["end"]) if direction == 1 else
                              (segment["end"], segment["start"]))
                if max(norm(sub(a, start)), norm(sub(b, end))) > tolerance:
                    continue
                if segment["kind"] == "LINE":
                    correct = radius == 0
                else:
                    signed = math.copysign(segment["radius"], segment["sweepRadians"]*direction)
                    correct = abs(segment["sweepRadians"]) <= math.pi+1e-10 and abs(radius-signed) <= tolerance
                if correct:
                    candidates.append((j, direction))
        if not candidates:
            raise NcGeometryConflict("NC boundary endpoint/radius contradicts native segments", "OUTLINE")
        if len(candidates) != 1 or candidates[0][0] in used:
            raise ValueError("NC boundary has ambiguous/reused segment attribution")
        j, direction = candidates[0]
        used.add(j)
        order.append(j)
        directions.append(direction)
        matches.append({"lineNumber": rows[i]["lineNumber"], "segmentIndex": j, "direction": direction,
                        "endpointResidualMm": max(norm(sub(a, segments[j]["start" if direction == 1 else "end"])),
                                                  norm(sub(b, segments[j]["end" if direction == 1 else "start"])))})
    if len(set(directions)) != 1 or any(order[(i+1) % len(order)] != (j+directions[0]) % len(order)
                                       for i, j in enumerate(order)):
        raise NcGeometryConflict("NC boundary order differs", "OUTLINE")
    return matches


def _hole_witnesses(blocks):
    witnesses = []
    for block in blocks:
        if block["kind"] == "IK":
            rows = block["rows"]
            if len(rows) != 3 or any(r["face"] != "v" for r in rows):
                raise ValueError("Noncircular IK not implemented")
            if any(row["fields"][0]["modifier"] != ("s" if i == 0 else "") for i, row in enumerate(rows)):
                raise ValueError("NC inner contour reference modifier unsupported")
            a, b, last = [_fields(row) for row in rows]
            if (a[:2] != last[:2] or a[2] <= 0 or a[2] != b[2] or last[2] != 0
                    or abs(norm(sub(a[:2], b[:2]))-2*a[2]) > 1e-8):
                raise ValueError("IK circle semantics unresolved")
            witnesses.append({"kind": "ROUND", "center": mul(add(a[:2], b[:2]), .5), "diameter": 2*a[2],
                              "lineNumbers": [row["lineNumber"] for row in rows]})
        elif block["kind"] == "BO":
            for row in block["rows"]:
                x, y, diameter, angle, dx, dy, extra = _fields(row)
                modifier = row["fields"][3]["modifier"]
                if row["fields"][0]["modifier"] != "s":
                    raise ValueError("NC hole reference modifier unsupported")
                if row["face"] != "v" or angle != 0 or extra != 0 or min(dx, dy) < 0 or (dx and dy):
                    raise ValueError("NC hole face/rotation/extension unsupported")
                if dx or dy:
                    if modifier != "l":
                        raise ValueError("NC slot modifier required")
                    witnesses.append({"kind": "SLOT", "ends": [[x, y], [x+dx, y+dy]], "diameter": diameter,
                                      "lineNumbers": [row["lineNumber"]]})
                else:
                    if modifier:
                        raise ValueError("Unresolved round BO modifier")
                    witnesses.append({"kind": "ROUND", "center": [x, y], "diameter": diameter,
                                      "lineNumbers": [row["lineNumber"]]})
    return witnesses


def _hole_residual(hole, witness):
    if hole["kind"] != witness["kind"]:
        return math.inf
    residual = abs(hole["diameter"]-witness["diameter"])
    if hole["kind"] == "ROUND":
        return max(residual, norm(sub(hole["center"], witness["center"])))
    delta = mul(hole["direction"], hole["extension"]/2)
    ends = [sub(hole["center"], delta), add(hole["center"], delta)]
    return max(residual, min(max(norm(sub(a, b)) for a, b in zip(ends, order))
                             for order in (witness["ends"], witness["ends"][::-1])))


def nc_hole_inventory(parsed):
    """Read manufacturing hole data independently of original/unfolded mapping."""
    if parsed['issues']:
        raise ValueError('NC syntax has unresolved issues')
    holes = _hole_witnesses(parsed['blocks'])
    for hole in holes:
        coords = [hole['center']] if hole['kind'] == 'ROUND' else hole['ends']
        if (not math.isfinite(hole['diameter']) or hole['diameter'] <= 0
                or any(not math.isfinite(v) for point in coords for v in point)):
            raise ValueError('Invalid NC hole geometry')
    return {'status': 'READ', 'coordinateScope': parsed['geometryRepresentation'],
            'holeCount': len(holes), 'holes': holes, 'modelPositionMapping': 'NOT_ESTABLISHED'}


def match_nc_geometry(outline, holes, thickness, parsed, tolerance):
    if parsed["issues"]:
        raise ValueError("NC syntax/source semantics have unresolved issues")
    if any(b['kind'] == 'KA' for b in parsed['blocks']):
        raise ValueError('Unfolded NC requires an explicit mapping before original-geometry comparison')
    blocks = parsed["blocks"]
    outlines = [b for b in blocks if b["kind"] == "AK"]
    if len(outlines) != 1 or thickness is None:
        raise ValueError("One outline and checked thickness required")
    header = blocks[0]["lines"]
    if abs(float(header[10]["text"])-thickness) > tolerance:
        raise NcGeometryConflict("NC/model thickness differs", "THICKNESS")
    boundary = _outline_match(outline, outlines[0], tolerance)
    witnesses = _hole_witnesses(blocks)
    if len(witnesses) != len(holes):
        raise NcGeometryConflict("NC/model hole count differs", "HOLES")
    used, matches = set(), []
    for witness in witnesses:
        candidates = [(i, _hole_residual(h, witness)) for i, h in enumerate(holes)]
        candidates = [(i, r) for i, r in candidates if r <= tolerance]
        if not candidates:
            raise NcGeometryConflict("NC hole shape/position contradicts native candidates", "HOLES")
        if len(candidates) != 1 or candidates[0][0] in used:
            raise ValueError("NC hole has ambiguous/reused feature mapping")
        i, residual = candidates[0]
        used.add(i)
        matches.append({"holeId": holes[i]["id"], "lineNumbers": witness["lineNumbers"],
                        "residualMm": residual, "witness": witness})
    return {"status": "MATCHED", "outlineMapping": boundary, "holeMapping": matches,
            "holeCount": len(matches), "thicknessResidualMm": abs(float(header[10]["text"])-thickness)}
