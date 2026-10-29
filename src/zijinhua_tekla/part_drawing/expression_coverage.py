"""Verify typed annotation observations against independently generated requirements."""
from __future__ import annotations

import math
from collections import defaultdict, deque

from .expression_requirements import GEOMETRY_TOLERANCE, arc_contains, axis_aligned_arc


def _close(a, b, tolerance=GEOMETRY_TOLERANCE):
    return isinstance(a, (float, int)) and not isinstance(a, bool) and math.isfinite(a) and abs(a - b) <= tolerance


def _point_equal(a, b):
    return isinstance(a, list) and len(a) == 2 and all(_close(x, y) for x, y in zip(a, b))


def display_round(value, decimals):
    """Round half away from zero, as Tekla dimension text does (216.5 -> 217)."""
    scale = 10 ** decimals
    return math.copysign(math.floor(abs(value) * scale + 0.5) / scale, value)


def display_matches(shown, value, decimals):
    """Shown text equals the rounded value; at an exact .5 tie floating noise in
    Tekla's own arithmetic may round either way, so both neighbours are valid."""
    scale = 10 ** decimals
    scaled = abs(value) * scale
    if abs(scaled - math.floor(scaled) - 0.5) <= 1e-6:
        return any(_close(shown, math.copysign(v / scale, value), 1e-9) for v in (math.floor(scaled), math.ceil(scaled)))
    return _close(shown, display_round(value, decimals), 1e-9)


def _number(value):
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _segment_equal(actual, expected):
    if actual.get("kind") != expected["kind"]:
        return False
    if not all(_point_equal(actual.get(key), expected[key]) for key in ("start", "end")):
        return False
    return expected["kind"] == "LINE" or (
        _point_equal(actual.get("center"), expected["center"])
        and actual.get("clockwise") is expected["clockwise"]
        and _close(actual.get("radius"), expected["radius"]))


class _Coverage:
    def __init__(self, plan, geometry_tolerance=GEOMETRY_TOLERANCE, display_decimals=None):
        self.plan = plan
        self.decimals = plan.get("displayDecimals", 1) if display_decimals is None else display_decimals
        self.facts = plan["facts"]
        self.covered = defaultdict(set)
        self.graphs = [defaultdict(list), defaultdict(list)]
        self.errors = []
        self.hole_notes = {}
        self.visible_segments = set()
        self.radius_segments = {}
        self.geometry_tolerance = geometry_tolerance

    def mark(self, requirement, object_id):
        self.covered[requirement].add(object_id)

    def edge(self, axis, left, right, object_id):
        self.graphs[axis][left].append((right, object_id))
        self.graphs[axis][right].append((left, object_id))

    def observe(self, observation):
        object_id = observation["objectId"]
        kind = observation["kind"]
        handler = {"dimension": self.dimension, "segment": self.segment, "radius": self.radius,
                   "hole_callout": self.hole_callout, "hole_shape": self.hole_shape, "center_lines": self.center_lines,
                   "label": self.label, "thickness": self.thickness, "specification": self.specification,
                   "sampled_boundary": self.sampled_boundary, "diameter": self.diameter,
                   "bolt_symbol": self.bolt_symbol}.get(kind)
        if handler is None:
            raise ValueError(f"unsupported observation kind: {kind}")
        handler(observation, object_id)

    def label(self, item, object_id):
        if item["text"] != self.plan["source"]["partPosition"] or item.get("leader") is not False:
            raise ValueError("part label must be exact and have no leader")
        self.mark("plate/identity", object_id)

    def thickness(self, item, object_id):
        expected = next(r["expected"] for r in self.plan["requirements"] if r["id"] == "plate/thickness")
        measured_expected=round(expected,1) if item.get('valueBasis')=='DISPLAY_TEXT' else expected
        if item["placement"] not in {"table", "dimension"} or not _close(item["value"], measured_expected):
            raise ValueError("thickness value/placement mismatch")
        if not _close(item["displayValue"], round(expected, 1), 1e-9):
            raise ValueError("thickness display value mismatch")
        self.mark("plate/thickness", object_id)

    def specification(self, item, object_id):
        expected = self.plan["specifications"].get(item["aspect"])
        if not expected or item["text"] != expected["value"]:
            raise ValueError("specification lacks matching source or text")
        self.mark(f"specification/{item['aspect']}", object_id)

    def segment(self, item, object_id):
        segment = next(s for s in self.facts["segments"] if s["id"] == item["feature"])
        if item.get("visible") is not True or not _segment_equal(item["geometry"], segment):
            raise ValueError("visible segment geometry differs from actual boundary")
        self.visible_segments.add(segment["id"])
        self.mark(f"{segment['id']}/shape", object_id)

    def sampled_boundary(self, item, object_id):
        from .native_render import boundary_matches
        segment = next(s for s in self.facts['segments'] if s['id'] == item['feature'])
        if item.get('visible') is not True or not item.get('exportEntities') or not boundary_matches(segment, item['paths'], self.geometry_tolerance):
            raise ValueError('Exported boundary does not cover the required real segment')
        self.visible_segments.add(segment['id'])
        self.mark(f"{segment['id']}/shape", object_id)

    def dimension(self, item, object_id):
        axis = {"x": 0, "y": 1}[item["axis"]]
        point_ids, coordinates, values, displayed = item["points"], item["coordinates"], item["values"], item["displayValues"]
        if len(point_ids) < 2 or len(coordinates) != len(point_ids) or len(values) != len(point_ids) - 1 or len(displayed) != len(values):
            raise ValueError("dimension chain lengths mismatch")
        for point_id, coordinate in zip(point_ids, coordinates):
            if not _point_equal(coordinate, self.facts["points"][point_id]):
                raise ValueError("dimension anchor is not the actual source point")
        for i, (left, right) in enumerate(zip(coordinates, coordinates[1:])):
            expected = abs(right[axis] - left[axis])
            if not _close(values[i], expected) or not display_matches(displayed[i], expected, self.decimals):
                raise ValueError("measured/displayed dimension value mismatch")
        self._combined_dimension(item, point_ids, coordinates, values, axis)
        bounds = self.facts["bounds"]
        if item.get("role") == "overall":
            if not _close(min(p[axis] for p in coordinates), bounds[axis]) or not _close(max(p[axis] for p in coordinates), bounds[axis + 2]) or len(point_ids) != 2:
                raise ValueError("overall dimension must span true boundary extrema")
            if any(p.startswith("hole:") for p in point_ids):
                raise ValueError("overall endpoints must be on the part boundary")
            self.mark(f"plate/overall_{item['axis']}", object_id)
        for left_id, right_id in zip(point_ids, point_ids[1:]):
            self.edge(axis, left_id, right_id, object_id)

    def _combined_dimension(self, item, point_ids, coordinates, values, axis):
        combined = item.get("combined")
        if combined is not None:
            diffs = [b[axis] - a[axis] for a, b in zip(coordinates, coordinates[1:])]
            if len(values) < 2 or not all(p.startswith("hole:") for p in point_ids) or len(set(point_ids)) != len(point_ids):
                raise ValueError("pitch combination must contain only distinct hole centers")
            if not all(_close(p[1 - axis], coordinates[0][1 - axis]) for p in coordinates):
                raise ValueError("pitch combination cannot cross rows")
            if not all(_close(v, diffs[0]) and abs(v) > GEOMETRY_TOLERANCE for v in diffs):
                raise ValueError("pitch combination is not consecutive equal spacing")
            self._check_pitch_members(point_ids, coordinates, axis)
            if combined["intervalCount"] != len(values) or not _close(combined["pitch"], abs(diffs[0])):
                raise ValueError("pitch count must equal intervals, not holes")
            if combined["text"] != f"{len(values)}@{_number(abs(diffs[0]))}":
                raise ValueError("combined pitch text mismatch")

    def _check_pitch_members(self, point_ids, coordinates, axis):
        members = {point_id.removeprefix("hole:") for point_id in point_ids}
        group = next(g for g in self.plan["groups"] if members <= set(g["members"]))
        low, high = sorted((coordinates[0][axis], coordinates[-1][axis]))
        for member in group["members"]:
            point_id = f"hole:{member}"
            p = self.facts["points"][point_id]
            if _close(p[1-axis], coordinates[0][1-axis]) and low <= p[axis] <= high and member not in members:
                raise ValueError("pitch combination skips an intermediate hole")

    def radius(self, item, object_id):
        segment = next(s for s in self.facts["segments"] if s["id"] == item["feature"])
        expected_value = display_round(segment['radius'], self.decimals) if item.get('valueBasis') == 'DISPLAY_TEXT' else segment['radius']
        if segment["kind"] != "ARC" or not _close(item["value"], expected_value):
            raise ValueError("radius differs from proven arc")
        # Text leader "R=10" or native radius dimension "R10" at display precision.
        texts = {f"R={_number(segment['radius'])}", f"R{display_round(segment['radius'], self.decimals):.{self.decimals}f}"}
        if item["text"] not in texts or item.get("auxiliaryCircle") is not False:
            raise ValueError("radius text or unwanted full circle")
        if not arc_contains(segment, item["anchor"]):
            raise ValueError("radius leader must touch the actual arc")
        self.mark(f"{segment['id']}/radius", object_id)
        self.radius_segments[segment["id"]] = object_id

    def diameter(self, item, object_id):
        from .native_layout import outer_circle
        circle=outer_circle(self.facts,self.plan.get('modelToleranceMm',GEOMETRY_TOLERANCE))
        if circle is None or item['text']!='Φ'+_number(2*circle['radius']) or not _close(item['value'],round(2*circle['radius'],1)):
            raise ValueError('Diameter note must describe the complete circular outer boundary')
        if abs(math.dist(item['anchor'],circle['center'])-circle['radius'])>self.geometry_tolerance:
            raise ValueError('Outer diameter leader misses the true circle')
        for segment in circle['segments']:
            self.mark(segment['id']+'/radius',object_id)
            self.radius_segments[segment['id']]=object_id
        for axis in (0,1):
            self.mark('plate/overall_'+'xy'[axis],object_id)
            for key,p in self.facts['points'].items():
                if abs(p[axis]-circle['center'][axis])<=self.plan.get('modelToleranceMm',GEOMETRY_TOLERANCE):
                    self.edge(axis,self.facts['datum'],key,object_id)

    def hole_callout(self, item, object_id):
        members = item["members"]
        if not members or len(members) != len(set(members)):
            raise ValueError("hole callout needs unique members")
        group = next(g for g in self.plan["groups"] if set(members) <= set(g["members"]))
        if item["count"] != len(members) or item["holeKind"] != group["kind"]:
            raise ValueError("hole count/kind mismatch")
        keys = ("diameter",) if group["kind"] == "ROUND" else ("width", "length", "angle")
        if not all(_close(item[k], round(group[k],1) if item.get('valueBasis')=='DISPLAY_TEXT' and k!='angle' else group[k]) for k in keys):
            raise ValueError("hole size/orientation mismatch")
        from .dimension_scheme import hole_callout_text
        text = hole_callout_text({**group, "count": len(members)})
        if item["text"] != text:
            raise ValueError("hole callout text mismatch")
        if any(member in self.hole_notes for member in members):
            raise ValueError("hole counted in more than one callout")
        for member in members:
            self.hole_notes[member] = object_id

    def hole_shape(self, item, object_id):
        hole = next(h for h in self.facts["holes"] if h["id"] == item["holeId"])
        keys = ("diameter",) if hole["kind"] == "ROUND" else ("width", "length", "angle")
        if (item.get("visible") is not True or item["holeKind"] != hole["kind"]
                or not all(_close(a,b,self.geometry_tolerance) for a,b in zip(item["center"], self.facts["points"][hole["pointId"]]))
                or len(item['center']) != 2
                or not all(_close(item[k], hole[k], self.geometry_tolerance) for k in keys)):
            raise ValueError("visible hole geometry differs from the source feature")
        self.mark(f"hole:{hole['id']}/shape", object_id)

    def bolt_symbol(self, item, object_id):
        """A Tekla bolt symbol with hole and axis drawn shows the bolt hole and its cross."""
        hole = next(h for h in self.facts["holes"] if h["id"] == item["holeId"])
        same_kind = item.get("holeKind") == hole["kind"]
        shown = (item.get("containsHole") is True and item.get("containsAxis") is True
                 and str(item.get("representation", "")).startswith("Symbol"))
        center_ok = (len(item.get("center") or []) == 2 and all(
            _close(a, b, self.geometry_tolerance) for a, b in zip(item["center"], self.facts["points"][hole["pointId"]])))
        if hole["kind"] == "ROUND":
            size_ok = _close(item.get("diameter"), hole["diameter"], self.geometry_tolerance)
        elif hole["kind"] == "SLOT":
            size_ok = all(_close(item.get(k), hole[k], self.geometry_tolerance) for k in ("width", "length", "angle"))
        else:
            size_ok = False
        if not (same_kind and shown and center_ok and size_ok):
            raise ValueError("bolt symbol does not show the source bolt hole")
        self.mark(f"hole:{hole['id']}/shape", object_id)
        self.mark(f"hole:{hole['id']}/center_cross", object_id)
        if hole["kind"] == "SLOT":
            self.mark(f"hole:{hole['id']}/orientation", object_id)

    def center_lines(self, item, object_id):
        hole = next(h for h in self.facts["holes"] if h["id"] == item["holeId"])
        center = self.facts["points"][hole["pointId"]]
        lines = item["lines"]
        if len(lines) != 2 or item.get("style") != "DASH_DOT":
            raise ValueError("two dash-dot lines required")
        angle = math.radians(hole.get("angle", 0))
        expected_axes = [(math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))]
        directions = []
        for line in lines:
            start, end = line
            vector = [end[i] - start[i] for i in (0, 1)]
            length = math.hypot(*vector)
            if not math.isfinite(length) or length <= GEOMETRY_TOLERANCE:
                raise ValueError("center line has no valid length")
            direction = [v / length for v in vector]
            relative = [center[i] - start[i] for i in (0, 1)]
            along = sum(relative[i] * direction[i] for i in (0, 1))
            cross = relative[0] * direction[1] - relative[1] * direction[0]
            if abs(cross) > GEOMETRY_TOLERANCE or not GEOMETRY_TOLERANCE < along < length - GEOMETRY_TOLERANCE:
                raise ValueError("center line must pass through true hole center")
            directions.append(direction)
        def parallel(a, b):
            return abs(a[0] * b[1] - a[1] * b[0]) <= GEOMETRY_TOLERANCE
        if not any(parallel(directions[0], expected_axes[i]) and parallel(directions[1], expected_axes[1-i]) for i in (0, 1)):
            raise ValueError("center cross axes do not match hole orientation")
        self.mark(f"hole:{hole['id']}/center_cross", object_id)
        if hole["kind"] == "SLOT":
            self.mark(f"hole:{hole['id']}/orientation", object_id)

    def finish(self):
        # A proven quarter/half circle and its radius determine endpoint offsets;
        # arbitrary arcs still need endpoint positioning dimensions.
        for segment in self.facts["segments"]:
            if segment["id"] not in self.visible_segments or segment["id"] not in self.radius_segments:
                continue
            aligned = axis_aligned_arc(segment, self.plan.get('modelToleranceMm', GEOMETRY_TOLERANCE))
            if aligned:
                for axis in (0, 1):
                    self.edge(axis, segment["startPoint"], segment["endPoint"], self.radius_segments[segment["id"]])
        for axis in (0, 1):
            points = self.facts["points"]
            ordered = sorted(points, key=lambda p: (points[p][axis], p))
            # Native source coordinates may share a datum within the approved
            # model budget. Compare every member with the cluster's first value,
            # so repeated small differences never accumulate into a false link.
            tolerance = self.plan.get('modelToleranceMm', 0)
            representative = ordered[0]
            for right in ordered[1:]:
                if abs(points[representative][axis]-points[right][axis]) <= tolerance:
                    self.edge(axis, representative, right, "SOURCE_SHARED_COORDINATE")
                else:
                    representative = right
            datum = self.facts["datum"]
            paths = {datum: {"SOURCE_DATUM"}}
            queue = deque([datum])
            while queue:
                current = queue.popleft()
                for target, object_id in self.graphs[axis][current]:
                    if target not in paths:
                        paths[target] = paths[current] | {object_id}
                        queue.append(target)
            for point_id, evidence in paths.items():
                self.covered[f"{point_id}/location_{'xy'[axis]}"].update(evidence)
        for group in self.plan["groups"]:
            if all(member in self.hole_notes for member in group["members"]):
                self.covered[f"{group['id']}/size_count"].update(self.hole_notes[member] for member in group["members"])


def audit_expression_coverage(plan: dict, observations: dict | None = None, display_decimals: int | None = None) -> dict:
    """Audit declared PLAN/READBACK observations; does not certify their acquisition."""
    result = {"schemaVersion": "expressionCoverage.v1", "sourceDigest": plan["sourceDigest"],
              "stage": "NONE", "status": "PENDING", "errors": [], "coverage": [],
              "drawingStatus": "NOT_ASSESSED", "visualStatus": "PENDING", "persistenceStatus": "NOT_ASSESSED"}
    if plan["status"] != "READY":
        result.update(status="BLOCKED", errors=plan["issues"])
        return result
    if observations is None:
        result["coverage"] = [{"requirementId": r["id"], "status": "MISSING", "objectIds": []} for r in plan["requirements"]]
        return result
    if observations.get("schemaVersion") != "expressionObservations.v1" or observations.get("sourceDigest") != plan["sourceDigest"] or observations.get("stage") not in {"PLAN", "READBACK"}:
        result.update(status="FAIL", errors=[{"code": "OBSERVATION_SOURCE_INVALID", "message": "schema, source digest or stage mismatch"}])
        return result
    result["stage"] = observations["stage"]
    tolerance = observations.get('geometryToleranceMm', GEOMETRY_TOLERANCE)
    if not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or not 0 < tolerance <= plan.get('modelToleranceMm', GEOMETRY_TOLERANCE):
        result.update(status='FAIL', errors=[{'code':'OBSERVATION_TOLERANCE_INVALID','message':'Geometry observation budget exceeds source budget'}])
        return result
    coverage = _Coverage(plan, tolerance, display_decimals)
    seen = set()
    items = observations.get("objects")
    if not isinstance(items, list):
        result.update(status="FAIL", errors=[{"code": "OBSERVATIONS_INVALID", "message": "objects must be a list"}])
        return result
    for item in items:
        object_id = item.get("objectId") if isinstance(item, dict) else None
        try:
            if not isinstance(object_id, str) or not object_id or object_id in seen:
                raise ValueError("object IDs must be nonempty and unique")
            seen.add(object_id)
            coverage.observe(item)
        except (KeyError, ValueError, TypeError, IndexError, StopIteration) as exc:
            coverage.errors.append({"code": "INVALID_EXPRESSION", "objectId": object_id, "message": str(exc) or "unknown feature or malformed object"})
    coverage.finish()
    result["errors"] = coverage.errors
    result["coverage"] = [{"requirementId": r["id"], "status": "COVERED" if coverage.covered[r["id"]] else "MISSING",
                           "objectIds": sorted(coverage.covered[r["id"]])} for r in plan["requirements"]]
    complete = not coverage.errors and all(row["status"] == "COVERED" for row in result["coverage"])
    result["status"] = ("PASS" if result["stage"] == "READBACK" else "PLAN_COMPLETE") if complete else "FAIL"
    return result
