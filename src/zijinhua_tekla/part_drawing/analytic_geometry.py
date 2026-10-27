"""Finite analytic candidates from modeling semantics; never fits a tessellated curve."""

import math


def add(a, b):
    return [x + y for x, y in zip(a, b)]


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def mul(a, s):
    return [x * s for x in a]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def norm(a):
    return math.hypot(*a)


def unit(a):
    length = norm(a)
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("Degenerate direction")
    return mul(a, 1 / length)


def frame_axes(frame):
    x, y = unit(frame["axisX"]), unit(frame["axisY"])
    if abs(dot(x, y)) > 1e-8:
        raise ValueError("Nonorthogonal coordinate frame")
    z = unit([x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]])
    return x, y, z


def project(point, frame):
    return [dot(sub(point, frame["origin"]), axis) for axis in frame_axes(frame)]


def line(a, b, refs=()):
    if norm(sub(a, b)) <= 1e-10:
        raise ValueError("Zero length boundary")
    return {"kind": "LINE", "start": a, "end": b, "sourceRefs": list(refs), "basis": "DERIVED"}


def arc(a, b, center, sweep, refs=()):
    radius = norm(sub(a, center))
    if radius <= 1e-10 or abs(sweep) <= 1e-10 or abs(sweep) > 2*math.pi + 1e-10:
        raise ValueError("Degenerate arc")
    return {"kind": "ARC", "start": a, "end": b, "center": center, "radius": radius,
            "sweepRadians": sweep, "sourceRefs": list(refs), "basis": "DERIVED"}


def short_arc(a, b, center, refs):
    u, v = sub(a, center), sub(b, center)
    return arc(a, b, center, math.atan2(cross(u, v), dot(u, v)), refs)


def three_point_arc(a, middle, b, refs=()):
    u, v = sub(middle, a), sub(b, a)
    det = 2 * cross(u, v)
    if abs(det) <= 1e-10 * norm(u) * norm(v):
        raise ValueError("Collinear arc points")
    center = add(a, [(dot(u, u)*v[1]-dot(v, v)*u[1])/det,
                     (u[0]*dot(v, v)-v[0]*dot(u, u))/det])
    angle = lambda p: math.atan2(p[1]-center[1], p[0]-center[0])
    sweep = (angle(b)-angle(a)) % (2*math.pi)
    if (angle(middle)-angle(a)) % (2*math.pi) > sweep:
        sweep -= 2*math.pi
    return arc(a, b, center, sweep, refs)


def contour_segments(points, chamfers, refs):
    """CHAMFER_ARC is corner-centered; ROUNDING is tangent to adjacent lines."""
    n = len(points)
    if n < 3 or len(chamfers) != n:
        raise ValueError("At least three contour points required")
    incoming, outgoing, corners = list(points), list(points), {}
    arc_points = {i for i, c in enumerate(chamfers) if c[0] == "CHAMFER_ARC_POINT"}
    for i, (kind, x, y) in enumerate(chamfers):
        if kind in ("CHAMFER_NONE", "CHAMFER_ARC_POINT"):
            continue
        if kind == "CHAMFER_ROUNDING" and x == 0 and y == 0:
            continue  # Inactive parameter; the final solid must still match the straight corner.
        if (i-1) % n in arc_points or (i+1) % n in arc_points:
            raise ValueError("Adjacent chamfer/arc-point combination unsupported")
        p, previous, following = points[i], points[i-1], points[(i+1) % n]
        u, v = unit(sub(previous, p)), unit(sub(following, p))
        if x <= 0 or abs(cross(u, v)) <= 1e-10:
            raise ValueError("Invalid chamfer dimensions/angle")
        if kind == "CHAMFER_LINE":
            if y <= 0:
                raise ValueError("Both straight chamfer dimensions required")
            d1, d2, center = x, y, None
        elif kind == "CHAMFER_ARC":
            d1 = d2 = x
            center = p
        elif kind == "CHAMFER_ROUNDING":
            half = math.acos(max(-1, min(1, dot(u, v)))) / 2
            d1 = d2 = x / math.tan(half)
            center = add(p, mul(unit(add(u, v)), x / math.sin(half)))
        else:
            raise ValueError("Unsupported chamfer: " + kind)
        if d1 >= norm(sub(previous, p)) or d2 >= norm(sub(following, p)):
            raise ValueError("Chamfer consumes adjacent edge")
        incoming[i], outgoing[i] = add(p, mul(u, d1)), add(p, mul(v, d2))
        corners[i] = (line(incoming[i], outgoing[i], [refs[i]]) if center is None else
                      short_arc(incoming[i], outgoing[i], center, [refs[i]]))
    segments = []
    for i in range(n):
        if i in arc_points:
            if (i+1) % n in arc_points or (i-1) % n in arc_points:
                raise ValueError("Adjacent arc points unsupported")
            segments.append(three_point_arc(points[i-1], points[i], points[(i+1) % n],
                                            [refs[i-1], refs[i], refs[(i+1) % n]]))
            continue
        if i in corners:
            segments.append(corners[i])
        j = (i+1) % n
        if j not in arc_points:
            if dot(sub(incoming[j], outgoing[i]), sub(points[j], points[i])) <= 0:
                raise ValueError("Overlapping chamfers")
            segments.append(line(outgoing[i], incoming[j], [refs[i], refs[j]]))
    return segments


def hole_segments(center, diameter, extension, direction, refs=()):
    r = diameter / 2
    if not r > 0 or extension < 0:
        raise ValueError("Invalid hole dimensions")
    u = unit(direction)
    v = [-u[1], u[0]]
    a, b = sub(center, mul(u, extension/2)), add(center, mul(u, extension/2))
    if extension == 0:
        p, q = add(center, mul(u, r)), sub(center, mul(u, r))
        return [arc(p, q, center, math.pi, refs), arc(q, p, center, math.pi, refs)]
    p, q, s, t = add(a, mul(v, r)), sub(a, mul(v, r)), sub(b, mul(v, r)), add(b, mul(v, r))
    return [arc(p, q, a, math.pi, refs), line(q, s, refs), arc(s, t, b, math.pi, refs), line(t, p, refs)]


def parameter(point, segment, tolerance):
    """Return analytic domain position; radial proximity alone is insufficient."""
    if norm(sub(point, segment["start"])) <= tolerance:
        return 0.0
    if norm(sub(point, segment["end"])) <= tolerance:
        return 1.0
    if segment["kind"] == "LINE":
        delta = sub(segment["end"], segment["start"])
        length = norm(delta)
        t = dot(sub(point, segment["start"]), delta) / length**2
        residual = norm(sub(point, add(segment["start"], mul(delta, t))))
        return t if residual <= tolerance and 0 <= t <= 1 else None
    a, b = sub(segment["start"], segment["center"]), sub(point, segment["center"])
    if abs(norm(b)-segment["radius"]) > tolerance:
        return None
    angle = math.atan2(cross(a, b), dot(a, b))
    sweep = segment["sweepRadians"]
    angle = angle % (2*math.pi) if sweep > 0 else -((-angle) % (2*math.pi))
    t = angle / sweep
    return t if 0 <= t <= 1 else None


def match_loop(vertices, segments, tolerance):
    """Account for every observed chord and every analytic segment domain, once."""
    intervals = [[] for _ in segments]
    matches = []
    if len(vertices) < 3:
        raise ValueError("Incomplete loop")
    for i, a in enumerate(vertices):
        b = vertices[(i+1) % len(vertices)]
        if norm(sub(a, b)) <= tolerance:
            raise ValueError("Repeated/degenerate loop vertex")
        candidates = []
        for j, segment in enumerate(segments):
            t1, t2 = parameter(a, segment, tolerance), parameter(b, segment, tolerance)
            if t1 is not None and t2 is not None and abs(t1-t2) > 1e-12:
                candidates.append((j, min(t1, t2), max(t1, t2)))
        if len(candidates) != 1:
            raise ValueError("Loop chord has missing/ambiguous analytic attribution")
        j, lo, hi = candidates[0]
        intervals[j].append((lo, hi))
        matches.append({"chordIndex": i, "segmentIndex": j, "domain": [lo, hi]})
    for segment, domains in zip(segments, intervals):
        length = (norm(sub(segment["start"], segment["end"])) if segment["kind"] == "LINE" else
                  segment["radius"] * abs(segment["sweepRadians"]))
        eps, end = tolerance/length, 0.0
        for lo, hi in sorted(domains):
            if abs(lo-end) > eps:
                raise ValueError("Boundary domain gap/overlap")
            end = hi
        if abs(end-1) > eps:
            raise ValueError("Boundary domain not fully covered")
    return matches


def _at(segment, t):
    if segment["kind"] == "LINE":
        return add(segment["start"], mul(sub(segment["end"], segment["start"]), t))
    a = sub(segment["start"], segment["center"])
    angle = segment["sweepRadians"]*t
    return add(segment["center"], [a[0]*math.cos(angle)-a[1]*math.sin(angle),
                                   a[0]*math.sin(angle)+a[1]*math.cos(angle)])


def intersections(a, b, tolerance):
    """Analytic intersections, including overlap rejection, on bounded domains."""
    if a["kind"] == "ARC" and b["kind"] == "LINE":
        return intersections(b, a, tolerance)
    points = []
    if a["kind"] == b["kind"] == "LINE":
        u, v, w = sub(a["end"], a["start"]), sub(b["end"], b["start"]), sub(b["start"], a["start"])
        det = cross(u, v)
        if abs(det) > 1e-12*norm(u)*norm(v):
            points = [add(a["start"], mul(u, cross(w, v)/det))]
        elif abs(cross(w, u))/norm(u) <= tolerance:
            t1, t2 = sorted(dot(sub(p, a["start"]), u)/dot(u,u) for p in (b["start"],b["end"]))
            if min(1,t2)-max(0,t1) > tolerance/norm(u):
                raise ValueError("Overlapping line boundaries")
            points = [a["start"],a["end"],b["start"],b["end"]]
    elif a["kind"] == "LINE":
        u, w = unit(sub(a["end"], a["start"])), sub(b["center"], a["start"])
        foot = add(a["start"], mul(u, dot(u,w)))
        square = b["radius"]**2-norm(sub(foot,b["center"]))**2
        if square >= 0:
            points = [add(foot,mul(u,math.sqrt(square))),sub(foot,mul(u,math.sqrt(square)))]
    else:
        delta, ra, rb = sub(b["center"],a["center"]), a["radius"],b["radius"]
        distance=norm(delta)
        if distance <= tolerance and abs(ra-rb) <= tolerance:
            for first,second in ((a,b),(b,a)):
                for t in (0,.5,1):
                    other=parameter(_at(first,t),second,tolerance)
                    if other is not None and 1e-9 < other < 1-1e-9:
                        raise ValueError("Overlapping arc boundaries")
            points=[a["start"],a["end"],b["start"],b["end"]]
        elif distance > tolerance and abs(ra-rb) <= distance <= ra+rb:
            along=(ra*ra-rb*rb+distance*distance)/(2*distance)
            height=math.sqrt(max(0,ra*ra-along*along))
            u=unit(delta);v=[-u[1],u[0]];foot=add(a["center"],mul(u,along))
            points=[add(foot,mul(v,height)),sub(foot,mul(v,height))]
    result=[]
    for p in points:
        if parameter(p,a,tolerance) is not None and parameter(p,b,tolerance) is not None:
            if not any(norm(sub(p,q)) <= tolerance for q in result): result.append(p)
    return result


def simple_ring(segments, tolerance):
    if len(segments) < 2:
        raise ValueError("Insufficient boundary segments")
    for i, a in enumerate(segments):
        if norm(sub(a["end"],segments[(i+1)%len(segments)]["start"])) > tolerance:
            raise ValueError("Disconnected analytic boundary")
        for j in range(i+1,len(segments)):
            b=segments[j]
            shared=[]
            if j==i+1: shared.append(a["end"])
            if i==0 and j==len(segments)-1: shared.append(a["start"])
            if any(not any(norm(sub(p,q)) <= tolerance for q in shared) for p in intersections(a,b,tolerance)):
                raise ValueError("Self-intersecting analytic boundary")


def inside_ring(point, segments, tolerance):
    if any(parameter(point,s,tolerance) is not None for s in segments):
        return False
    # Choose a ray missing every segment endpoint and circular tangency.
    reach=1+max(norm(sub(p,point)) + (2*s.get("radius",0))
                for s in segments for p in (s["start"],s["end"]))
    for k in range(1,33):
        u=unit([1,k*math.sqrt(2)])
        if any(abs(cross(sub(p,point),u)) <= tolerance for s in segments for p in (s["start"],s["end"])):
            continue
        if any(s["kind"]=="ARC" and abs(abs(cross(sub(s["center"],point),u))-s["radius"]) <= tolerance for s in segments):
            continue
        ray=line(point,add(point,mul(u,reach)))
        return sum(len(intersections(ray,s,tolerance)) for s in segments)%2==1
    raise ValueError("Containment ray unresolved near boundary")


def check_hole_topology(outline, holes, tolerance):
    simple_ring(outline,tolerance)
    for i,hole in enumerate(holes):
        ring=hole["segments"]
        simple_ring(ring,tolerance)
        if not inside_ring(ring[0]["start"],outline,tolerance):
            raise ValueError("Hole lies outside/touches outline")
        if any(intersections(a,b,tolerance) for a in ring for b in outline):
            raise ValueError("Hole crosses/touches outline")
        for other in holes[:i]:
            second=other["segments"]
            if (any(intersections(a,b,tolerance) for a in ring for b in second)
                    or inside_ring(ring[0]["start"],second,tolerance)
                    or inside_ring(second[0]["start"],ring,tolerance)):
                raise ValueError("Holes overlap/touch/nest")
    return {"status":"MATCHED", "holeCount":len(holes)}
