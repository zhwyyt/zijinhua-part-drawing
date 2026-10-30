"""R2b finite analytic candidates and exhaustive checks of the available evidence.

EVIDENCE_MATCHED is not exact BREP proof and never authorizes manufacturing.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from .analytic_geometry import (add, sub, mul, dot, norm, unit, cross, frame_axes, project,
                                contour_segments, hole_segments, check_hole_topology, line, inside_ring,
                                simple_ring, parameter, three_point_arc)
from .nc_witness import _bytes, audit_nc_capture
from .nc_geometry import match_nc_geometry, nc_hole_inventory, NcGeometryConflict
from .geometry_evidence import native_sources, nc_source, feature_evidence, evidence_sufficiency
from .hole_evidence import validate_observations, composite_hole_effect
from .finished_holes import finished_hole_candidates
from .planar_cut_region import canonical_ring, circle_shape, cut_region
from .target_holes import nc_target_shapes, physical_penetration
from .solid_prisms import value, _circle_loop, _shape_match, _point_set_equal, match_prisms
from .geometric_types import collect_shape_facts, classify_shape


# User-approved model comparison tolerance; raw coordinates remain unchanged.
DEFAULT_MODEL_TOLERANCE_MM = 0.1


def finite_positive(number):
    if isinstance(number, bool):
        raise ValueError("Numeric budget required")
    result = float(number)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Positive finite numeric budget required")
    return result


def _outline(part, frame, path, tolerance):
    if part["runtimeType"] != "Tekla.Structures.Model.ContourPlate":
        return _stock_outline(part, frame, path, tolerance)
    contour = value(value(part["modelingContour"]))
    points = [project(c["point"], frame) for c in contour]
    if not points or max(p[2] for p in points)-min(p[2] for p in points) > tolerance:
        raise ValueError("Modeling contour is not planar")
    chamfers, refs = [], []
    for i, point in enumerate(contour):
        c = point["chamfer"]
        if value(c["DZ1"]) != 0 or value(c["DZ2"]) != 0:
            raise ValueError("Thickness chamfer unsupported")
        chamfers.append((value(c["Type"])["name"], value(c["X"]), value(c["Y"])))
        refs.append(path + f"/modelingContour/value/value/{i}")
    return {"kind": "OUTLINE", "segments": contour_segments([p[:2] for p in points], chamfers, refs),
            "sourceRefs": refs, "basis": "DERIVED"}


def _profile_thickness(part):
    profile = re.fullmatch(r'PL(\d+(?:\.\d+)?)(?:\*\d+(?:\.\d+)?)?', value(part['profile']))
    return float(profile[1]) if profile else None


def _stock_outline(part, frame, path, tolerance):
    """Derive straight stock edges only when native edge and complete prism evidence agree."""
    if 'rawSolid' not in part:
        raise ValueError('RAW stock solid required for a part without plate-contour semantics')
    solid = value(part['rawSolid'])
    if not value(solid['edges']) or any(e['apiType'] != 'EDGE_TYPE_NORMAL' for e in value(solid['edges'])):
        raise ValueError('Curved/hidden stock edges need analytic curve evidence; no faceted approximation')
    normal = frame_axes(frame)[2]
    cap = next((f for f in value(solid['faces'])
                if abs(abs(dot(unit(value(f['normal'])), normal))-1) <= 1e-8), None)
    if cap is None or len(value(cap['loops'])) != 1:
        raise ValueError('Stock has no single planar outer cap in this direction')
    points = [project(p, frame) for p in value(value(cap['loops'])[0]['vertices'])]
    refs = [path+'/rawSolid']
    outline = {'kind': 'OUTLINE', 'segments': [line(a[:2], b[:2], refs)
               for a, b in zip(points, points[1:]+points[:1])],
               'sourceRefs': refs, 'basis': 'NATIVE_PLANAR_STOCK', 'planeZ': points[0][2]}
    check_hole_topology(outline['segments'], [], tolerance)
    shell = match_prisms(solid, [outline], frame, tolerance, path+'/rawSolid/value', origin_id=part['id'])
    spans = [max(p[i] for p in points)-min(p[i] for p in points) for i in (0, 1)]
    if shell['thickness'] >= min(spans)-tolerance:
        raise ValueError('Extrusion direction is not a unique thin plate direction')
    declared = _profile_thickness(part)
    if declared is not None and abs(declared-shell['thickness']) > tolerance:
        raise ValueError('Stock parallel-face spacing conflicts with declared plate thickness')
    outline['stockLevels'] = shell['levels']
    return outline


def _stock_frame(part, preferred, path, tolerance):
    """The building command does not determine the thin direction of a solid."""
    if 'rawSolid' not in part:
        raise ValueError('RAW stock solid required for geometric plate classification')
    axes = frame_axes(preferred)
    normals, candidates = [], []
    for face in value(value(part['rawSolid'])['faces']):
        normal = unit(value(face['normal']))
        if any(abs(dot(normal, n)) > 1-1e-8 for n in normals):
            continue
        normals.append(normal)
        # Keep the preferred orientation whenever it lies in the actual plate plane.
        sign_axis = next(axis for axis in (axes[2], axes[1], axes[0]) if abs(dot(normal, axis)) > 1e-8)
        if dot(normal, sign_axis) < 0:
            normal = [-v for v in normal]
        direction = next(axis for axis in axes if abs(dot(axis, normal)) < 1-1e-8)
        x = unit([a-dot(direction, normal)*n for a, n in zip(direction, normal)])
        y = [normal[1]*x[2]-normal[2]*x[1], normal[2]*x[0]-normal[0]*x[2], normal[0]*x[1]-normal[1]*x[0]]
        candidate = dict(origin=preferred['origin'], axisX=x, axisY=y)
        try:
            _stock_outline(part, candidate, path, tolerance)
            candidates.append(candidate)
        except ValueError:
            continue
    if len(candidates) != 1:
        raise ValueError('Stock has no unique complete planar constant-thickness prism; bent/nonplanar or incomplete evidence')
    return candidates[0]


def _hole_candidates(bolt, frame, path):
    props = bolt["properties"]
    standard = value(props["BoltStandard"])
    if not isinstance(standard, str) or not standard.strip():
        raise ValueError("Unknown bolt standard")
    if standard == "STUD":
        raise ValueError("STUD attachment requires independent non-hole effect proof")
    group_frame = value(bolt["coordinateSystem"])
    gx, gy, _axis = frame_axes(group_frame)
    px, py, _plate = frame_axes(frame)
    x, y = value(props["SlottedHoleX"]), value(props["SlottedHoleY"])
    diameter = value(props["BoltSize"]) + value(props["Tolerance"])
    if min(x, y) < 0 or (x and y) or value(props["Tolerance"]) < 0 or value(props["BoltSize"]) <= 0:
        raise ValueError("Invalid/two-axis slot parameters")
    hole_type = value(props["HoleType"])["name"]
    if hole_type not in ("HOLE_TYPE_SLOTTED", "HOLE_TYPE_OVERSIZED"):
        raise ValueError("Unimplemented hole mode")
    if hole_type == "HOLE_TYPE_OVERSIZED":
        if y:
            raise ValueError("Unimplemented two-axis/alternate oversized parameters")
        diameter += x
        x = 0
    elif x or y:
        if hole_type != "HOLE_TYPE_SLOTTED" or value(props["RotateSlots"])["name"] != "ROTATE_SLOTS_PARALLEL":
            raise ValueError("Ambiguous slot rotation")
    # A sloped cylinder is still a round hole. Only a slot whose long axis
    # lies along the plate normal has no in-plane direction to draw.
    axis = gy if y else gx
    projected = [dot(axis, px), dot(axis, py)]
    if norm(projected) <= 1e-8:
        if x or y:
            raise ValueError("Slot direction is parallel to the plate normal")
        direction = [1.0, 0.0]
    else:
        direction = unit(projected)
    shapes = []
    for i, point in enumerate(value(bolt["positions"])):
        center = project(point, frame)[:2]
        refs = [path + f"/positions/value/{i}", path + "/properties", path + "/targets"]
        shapes.append({"id": f"{bolt['id']}:{i}", "kind": "SLOT" if x or y else "ROUND", "center": center,
                       "diameter": diameter, "extension": x+y, "direction": direction,
                       "segments": hole_segments(center, diameter, x+y, direction, refs),
                       "basis": "DERIVED", "sourceRefs": refs})
    if not shapes:
        raise ValueError("No hole positions")
    return shapes


def _hole_effect(bolt, part_id, candidates, frame, tolerance, path, shell):
    if value(bolt["properties"]["Bolt"]) is not False:
        raise ValueError("Fastener solid cannot be used as hole cutter evidence")
    targets = bolt["targets"]
    if (value(targets["partToBoltTo"])["id"] != part_id or
            value(targets["partToBeBolted"])["id"] != part_id or value(targets["otherParts"])):
        raise ValueError("Multiple/different target hole effects unsupported")
    if shell["status"] != "MATCHED":
        raise ValueError("Plate thickness domain not established")
    lo, hi = shell["levels"]
    if any(not lo-tolerance <= project(p, frame)[2] <= hi+tolerance for p in value(bolt["positions"])):
        raise ValueError("Hole position lies outside the target thickness domain")
    solid = value(bolt["solidWithHighAccuracy"])
    own_frame = value(bolt["coordinateSystem"])
    own_shapes = _hole_candidates(bolt, own_frame, path)
    result = match_prisms(solid, own_shapes, own_frame, tolerance, path+"/solidWithHighAccuracy/value")
    # A tiny tilt is a length residual at the target boundary, not a malformed
    # cutter side face. Validate the cutter first in its actual extrusion frame.
    same_direction = dot(frame_axes(own_frame)[2], frame_axes(frame)[2]) > 0
    target_caps = []
    for cap in result['capEvidence']:
        face = value(solid['faces'])[cap['faceIndex']]
        pts = [project(p, frame) for p in value(value(face['loops'])[0]['vertices'])]
        level = cap['level'] if same_direction else 1-cap['level']
        residual = max(abs(p[2]-shell['levels'][level]) for p in pts)
        if residual > tolerance:
            raise ValueError('Hole cutter caps do not match both target thickness faces')
        mapping = _shape_match([p[:2] for p in pts], candidates[cap['shapeIndex']], tolerance)
        target_caps.append({'faceIndex': cap['faceIndex'], 'shapeIndex': cap['shapeIndex'],
                            'targetLevel': level, 'maxThicknessResidualMm': residual,
                            'mapping': mapping, 'sourceRef': cap['sourceRef']})
    result.update(coordinateSystem=own_frame, targetLevels=shell['levels'],
                  targetCapEvidence=target_caps, basis='HOLE_ONLY_CUTTER_NOT_FINAL_HOLE_SURFACE')
    return result


def _checked(name, work, issues):
    try:
        return work()
    except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, OverflowError) as exc:
        issues.append({"code": name, "message": str(exc)})
        return {"status": "UNRESOLVED", "reason": str(exc)}


def _circular_cut(operation, part, frame, path, tolerance):
    """Recognize the cutter in its own frame; keep final-boundary checks separate."""
    if operation['runtimeType'] != 'Tekla.Structures.Model.BooleanPart':
        return {'status': 'NOT_APPLICABLE', 'reason': 'This operation is not a circular Boolean cutter'}
    if value(operation['type'])['name'] != 'BOOLEAN_CUT':
        raise ValueError('Only subtractive circular cutters supported')
    operative = value(operation['operativeGeometry'])
    own_frame = value(operative['coordinateSystem'])
    outline = _outline(operative, own_frame, path+'/operativeGeometry/value', tolerance)
    segments = outline['segments']
    if not segments or any(s['kind'] != 'ARC' for s in segments):
        raise ValueError('Operative contour is not entirely analytic circular arcs')
    first = segments[0]
    center, radius = first['center'], first['radius']
    residuals = {'centerSpreadMm': max(norm(sub(s['center'], center)) for s in segments),
                 'radiusSpreadMm': max(abs(s['radius']-radius) for s in segments),
                 'closureGapMm': max(norm(sub(s['end'], segments[(i+1) % len(segments)]['start']))
                                     for i, s in enumerate(segments)),
                 'circumferenceResidualMm': abs(abs(sum(s['sweepRadians'] for s in segments))-2*math.pi)*radius}
    if (any(r > tolerance for r in residuals.values())
            or any(s['sweepRadians']*first['sweepRadians'] <= 0 for s in segments)):
        return {'id': operation['id'], 'status': 'UNRESOLVED', 'sourceRef': path,
                'reason': 'Operative arcs do not form one closed circle within the declared tolerance',
                'operativeContour': outline, 'circularityResiduals': residuals,
                'issues': [{'code': 'NONCOCIRCULAR_CUT_ARCS'}]}
    shape = {'kind': 'ROUND', 'center': center, 'diameter': 2*radius}
    cutter = match_prisms(value(operation['operativeSolid']), [shape], own_frame, tolerance,
                          path+'/operativeSolid/value', origin_id=value(operation['operativePart'])['id'])
    contour = value(value(operative['modelingContour']))
    z = project(contour[0]['point'], own_frame)[2]
    axes = frame_axes(own_frame)
    global_center = [own_frame['origin'][i]+axes[0][i]*center[0]+axes[1][i]*center[1]+axes[2][i]*z
                     for i in range(3)]
    projected = project(global_center, frame)
    candidate = {'id': 'cut:'+str(operation['id']), 'kind': 'ROUND', 'center': projected[:2],
                 'diameter': 2*radius, 'extension': 0, 'direction': [1, 0], 'basis': 'DERIVED',
                 'sourceRefs': [path+'/operativeGeometry', path+'/operativeSolid', path+'/type']}
    candidate['segments'] = hole_segments(candidate['center'], 2*radius, 0, [1, 0], candidate['sourceRefs'])
    issues = []

    def check_boundaries():
        target_normal = frame_axes(frame)[2]
        # Bound out-of-plane variation in model units, not display precision.
        tilt = math.hypot(dot(axes[0], target_normal), dot(axes[1], target_normal))
        if tilt*radius > tolerance:
            raise ValueError('Oblique cutter projection requires ellipse/section proof')
        solid = value(part['finalSolid'])
        faces = value(solid['faces'])
        all_points = [project(p, frame) for f in faces for loop in value(f['loops'])
                      for p in value(loop['vertices'])]
        lo, hi = min(p[2] for p in all_points), max(p[2] for p in all_points)
        if hi-lo <= tolerance:
            raise ValueError('Target thickness domain is degenerate')
        depths = [project([own_frame['origin'][i]+axes[0][i]*center[0]+axes[1][i]*center[1]+axes[2][i]*level
                           for i in range(3)], frame)[2] for level in cutter['levels']]
        if min(depths)+tilt*radius > lo+tolerance or max(depths)-tilt*radius < hi-tolerance:
            raise ValueError('Cutter does not span both target thickness faces')
        matches = []
        for fi, face in enumerate(faces):
            if abs(abs(dot(unit(value(face['normal'])), target_normal))-1) > 1e-8:
                continue
            loops = value(face['loops'])
            if len(loops) < 2:
                continue
            for li, loop in enumerate(loops):
                pts = [project(p, frame) for p in value(loop['vertices'])]
                levels = [level for level in (lo, hi) if all(abs(p[2]-level) <= tolerance for p in pts)]
                if len(levels) != 1:
                    continue
                try:
                    mapping = _circle_loop([p[:2] for p in pts], candidate, tolerance)
                except ValueError:
                    continue
                if value(face['originPartId']) != part['id']:
                    raise ValueError('Target cap source differs from target instance')
                matches.append({'level': levels[0], 'faceIndex': fi, 'loopIndex': li, 'mapping': mapping})
        if len(matches) != 2 or sorted(m['level'] for m in matches) != [lo, hi]:
            raise ValueError('Missing/ambiguous circular inner boundary on opposite target caps')
        return {'status': 'MATCHED_BOUNDARIES', 'levels': [lo, hi], 'matches': matches,
                'remainingProofs': ['CUT_SIDE_SURFACES', 'COMPLETE_OPERATION_INTERACTIONS']}

    boundary = _checked('CIRCULAR_CUT_BOUNDARY_UNRESOLVED', check_boundaries, issues)
    return {'id': operation['id'], 'status': 'CANDIDATE', 'sourceRef': path,
            'operativeCircle': shape, 'operativeContour': outline, 'circularityResiduals': residuals,
            'operativeSolidCheck': cutter,
            'targetCandidate': candidate, 'innerBoundaryCheck': boundary, 'issues': issues}


def _fitting_frame(operation, part, frame, tolerance):
    """Plane kept by the finished solid: through the thickness, touching one side."""
    plane = value(operation['plane'])
    axes = frame_axes(frame)
    normal3 = frame_axes(plane)[2]
    origin = project(plane['origin'], frame)
    normal = [dot(normal3, axis) for axis in axes]
    points = [project(point, frame) for face in value(value(part['finalSolid'])['faces'])
              for loop in value(face['loops']) for point in value(loop['vertices'])]
    if not points:
        raise ValueError('Fitting has no final solid to check')
    levels = [min(point[2] for point in points), max(point[2] for point in points)]
    if abs(normal[2])*(levels[1]-levels[0]) > tolerance:
        raise ValueError('Fitting plane is oblique through the plate thickness')
    if norm(normal[:2]) <= 1e-12:
        raise ValueError('Fitting plane does not intersect the plate plane')
    signed = [dot(sub(point, origin), normal) for point in points]
    if min(signed) >= -tolerance and max(signed) > tolerance:
        retained = 1
    elif max(signed) <= tolerance and min(signed) < -tolerance:
        retained = -1
    else:
        raise ValueError('Final solid does not lie on one side of the fitting plane')
    return origin, normal, retained


def _same_edges(left, right, tolerance):
    if len(left) != len(right) or not _point_set_equal(
            [segment['start'] for segment in left], [segment['start'] for segment in right], tolerance):
        return False
    unused = list(right)
    for segment in left:
        hits = [index for index, other in enumerate(unused)
                if _point_set_equal([segment['start'], segment['end']],
                                    [other['start'], other['end']], tolerance)]
        if len(hits) != 1:
            return False
        unused.pop(hits[0])
    return True


def _fit_one(segments, operation, part, frame, path, tolerance, plane_z):
    """Move the nearest straight end onto the plane by trimming or extending its shoulders."""
    if len(segments) < 3 or any(segment.get('kind') != 'LINE' for segment in segments):
        raise ValueError('Fitting requires a straight polygonal end')
    origin, normal, retained = _fitting_frame(operation, part, frame, tolerance)
    offset = ((plane_z if plane_z is not None else origin[2]) - origin[2]) * normal[2]

    def signed(point):
        return dot(sub(point, origin[:2]), normal[:2]) + offset

    distances = [abs(signed([(segment['start'][0]+segment['end'][0])/2,
                             (segment['start'][1]+segment['end'][1])/2])) for segment in segments]
    nearest = min(distances)
    chosen = [index for index, distance in enumerate(distances) if abs(distance-nearest) <= tolerance]
    if len(chosen) != 1:
        raise ValueError('Fitting plane does not select one stock end')
    end = chosen[0]
    count = len(segments)

    def hit(segment):
        direction = sub(segment['end'], segment['start'])
        denom = dot(direction, normal[:2])
        if abs(denom) <= 1e-9:
            raise ValueError('Fitting shoulder is parallel to the fitting plane')
        parameter = -signed(segment['start']) / denom
        return [segment['start'][index] + parameter*direction[index] for index in (0, 1)]

    left_hit, right_hit = hit(segments[(end-1) % count]), hit(segments[(end+1) % count])
    if norm(sub(left_hit, right_hit)) <= tolerance:
        raise ValueError('Fitting collapses an edge')
    rebuilt = []
    for index, segment in enumerate(segments):
        if index == end:
            piece = line(left_hit, right_hit, [path+'/plane'])
        else:
            piece = dict(segment, start=list(segment['start']), end=list(segment['end']))
            if index == (end-1) % count:
                piece['end'] = left_hit
            if index == (end+1) % count:
                piece['start'] = right_hit
            if norm(sub(piece['start'], piece['end'])) <= tolerance:
                raise ValueError('Fitting collapses an edge')
        rebuilt.append(piece)
    simple_ring(rebuilt, tolerance)
    return rebuilt, {'id': operation['id'], 'status': 'CANDIDATE', 'sourceKind': 'FITTING',
                     'sourceRef': path, 'retainedHalfspaceSign': retained, 'endEdgeIndex': end,
                     'sideOriginIds': [part['id'], operation['id']]}


def _apply_fittings(outline, operations, part, frame, path, tolerance):
    indexed = [(index, operation) for index, operation in enumerate(operations)
               if operation.get('runtimeType') == 'Tekla.Structures.Model.Fitting']
    if not indexed:
        return {'outline': outline, 'records': {}}
    plane_z = outline.get('planeZ')

    def run(order):
        segments, records = deepcopy(outline['segments']), {}
        for index, operation in order:
            segments, record = _fit_one(segments, operation, part, frame,
                                        path+f'/operations/value/{index}', tolerance, plane_z)
            records[operation['id']] = record
        return segments, records

    forward, records = run(indexed)
    backward, _ignored = run(list(reversed(indexed)))
    if not _same_edges(forward, backward, tolerance):
        raise ValueError('Fitting planes do not commute on the stock outline')
    return {'outline': dict(outline, segments=forward, basis='STOCK_ENDS_FITTED_TO_PLANES'),
            'records': records}


def _append_fitting_mappings(region, cuts):
    for fitting in cuts:
        if fitting.get('sourceKind') != 'FITTING':
            continue
        region['operationMappings'].append({
            'operationId': fitting['id'], 'boundaryIds': [], 'status': 'END_REPLACED',
            'sourceKind': 'FITTING', 'sourceOperationId': fitting['id'],
            'endEdgeIndex': fitting.get('endEdgeIndex'),
            'retainedHalfspaceSign': fitting.get('retainedHalfspaceSign')})


def _plane_cut(operation, part, outline, frame, path, tolerance):
    if not outline or 'segments' not in outline:
        raise ValueError('CutPlane requires a proven planar source outline')
    plane = value(operation['plane'])
    normal = frame_axes(plane)[2]
    axes = frame_axes(frame)
    points = [project(p, frame) for f in value(value(part['finalSolid'])['faces'])
              for loop in value(f['loops']) for p in value(loop['vertices'])]
    levels = [min(p[2] for p in points), max(p[2] for p in points)]
    if abs(dot(normal, axes[2]))*(levels[1]-levels[0]) > tolerance:
        raise ValueError('CutPlane is oblique through the plate thickness')
    origin = project(plane['origin'], frame)
    n = [dot(normal, axis) for axis in axes]
    signed = [dot(sub(p, origin), n) for p in points]
    if min(signed) >= -tolerance and max(signed) > tolerance:
        retained = 1
    elif max(signed) <= tolerance and min(signed) < -tolerance:
        retained = -1
    else:
        raise ValueError('Final solid does not establish a unique retained CutPlane halfspace')
    # Bound the analytic halfplane outside the complete source contour, including arcs.
    bounds = [s[k] for s in outline['segments'] for k in ('start', 'end')]+[origin[:2]]
    for s in outline['segments']:
        if s['kind'] == 'ARC':
            bounds += [[s['center'][0]+dx*s['radius'], s['center'][1]+dy*s['radius']]
                       for dx, dy in ((-1,-1), (1,1))]
    lo = [min(p[i] for p in bounds) for i in (0,1)]
    hi = [max(p[i] for p in bounds) for i in (0,1)]
    margin = max(hi[i]-lo[i] for i in (0,1))+tolerance
    rectangle = [[lo[0]-margin,lo[1]-margin], [hi[0]+margin,lo[1]-margin],
                 [hi[0]+margin,hi[1]+margin], [lo[0]-margin,hi[1]+margin]]
    def removed(p):
        return -retained*dot(sub(p+[levels[0]], origin), n)
    clipped = []
    for a, b in zip(rectangle, rectangle[1:]+rectangle[:1]):
        da, db = removed(a), removed(b)
        if da >= 0:
            clipped.append(a)
        if (da >= 0) != (db >= 0):
            clipped.append([a[i]+(b[i]-a[i])*da/(da-db) for i in (0,1)])
    segments = [line(a, b, [path+'/plane']) for a, b in zip(clipped, clipped[1:]+clipped[:1])]
    return {'id': operation['id'], 'status': 'CANDIDATE', 'sourceRef': path,
            'sourceKind': 'CUT_PLANE', 'segments': canonical_ring(segments, tolerance, operation['id']),
            'retainedHalfspaceSign': retained, 'targetLevels': levels,
            'sideOriginIds': [part['id'], operation['id']]}


def _planar_cut(operation, part, frame, path, tolerance, outline=None):
    if operation['runtimeType'] == 'Tekla.Structures.Model.CutPlane':
        return _plane_cut(operation, part, outline, frame, path, tolerance)
    if operation['runtimeType'] != 'Tekla.Structures.Model.BooleanPart':
        raise ValueError('Cutting operation has no supported analytic semantics')
    if value(operation['type'])['name'] != 'BOOLEAN_CUT':
        raise ValueError('Only subtractive through cuts are supported')
    operative = value(operation['operativeGeometry'])
    own_frame = value(operative['coordinateSystem'])
    if operative['runtimeType'] != 'Tekla.Structures.Model.ContourPlate':
        own_frame = _stock_frame(operative, own_frame, path+'/operativeGeometry/value', tolerance)
    raw_outline = _outline(operative, own_frame, path+'/operativeGeometry/value', tolerance)
    ring = canonical_ring(raw_outline['segments'], tolerance, operation['id'])
    shape = circle_shape(ring, tolerance) or {'kind': 'OUTLINE'}
    shape['segments'] = ring
    cutter = match_prisms(value(operation['operativeSolid']), [shape], own_frame, tolerance,
                          path+'/operativeSolid/value', origin_id=value(operation['operativePart'])['id'])
    axes, target_axes = frame_axes(own_frame), frame_axes(frame)
    cosine = dot(axes[2], target_axes[2])
    if abs(cosine) <= .5:
        raise ValueError('Oblique cut requires section-specific geometry')
    extent = max(norm(s['start'])+2*s.get('radius', 0) for s in ring)
    tilt = math.hypot(dot(axes[0], target_axes[2]), dot(axes[1], target_axes[2]))
    if tilt*extent > tolerance:
        raise ValueError('Cutter tilt exceeds the model length tolerance')
    origin_z = project(own_frame['origin'], frame)[2]
    depths = sorted(origin_z+z*cosine for z in cutter['levels'])
    final_points = [project(p, frame) for f in value(value(part['finalSolid'])['faces'])
                    for loop in value(f['loops']) for p in value(loop['vertices'])]
    lo, hi = min(p[2] for p in final_points), max(p[2] for p in final_points)
    if hi-lo <= tolerance or depths[0]+tilt*extent > lo+tolerance or depths[1]-tilt*extent < hi-tolerance:
        raise ValueError('Cutter does not span the complete target thickness')
    contour_z = (raw_outline['planeZ'] if 'planeZ' in raw_outline else
                 project(value(value(operative['modelingContour']))[0]['point'], own_frame)[2])

    def projected(xy):
        point = [own_frame['origin'][i]+axes[0][i]*xy[0]+axes[1][i]*xy[1]+axes[2][i]*contour_z
                 for i in range(3)]
        return project(point, frame)[:2]

    segments = []
    for s in ring:
        transformed = dict(s, start=projected(s['start']), end=projected(s['end']))
        if s['kind'] == 'ARC':
            transformed.update(center=projected(s['center']), sweepRadians=s['sweepRadians']*(1 if cosine > 0 else -1))
        segments.append(transformed)
    return {'id': operation['id'], 'status': 'CANDIDATE', 'sourceRef': path,
            'operativeContour': raw_outline, 'segments': segments, 'operativeSolidCheck': cutter,
            'targetLevels': [lo, hi], 'maxTiltBoundMm': tilt*extent,
            'sideOriginIds': sorted({operation['id'], value(operation['operativePart'])['id']})}


def _match_cut_solid(part, region, cuts, frame, path, tolerance):
    """Cover all observed cap loops, side quads and edges, with source attribution."""
    solid = value(part['finalSolid'])
    faces = value(solid['faces'])
    shapes = [region['outline']]+region['holes']
    projected = [(i, f, [[project(p, frame) for p in value(loop['vertices'])] for loop in value(f['loops'])])
                 for i, f in enumerate(faces)]
    points = [p for _, _, loops in projected for loop in loops for p in loop]
    lo, hi = min(p[2] for p in points), max(p[2] for p in points)
    if hi-lo <= tolerance:
        raise ValueError('Degenerate final plate thickness')
    caps, sides, seen = [], [], Counter()
    axes = frame_axes(frame)
    for fi, face, loops in projected:
        if not loops or any(len(loop) < 3 for loop in loops):
            raise ValueError('Missing or degenerate final face loops')
        pts = [p for loop in loops for p in loop]
        levels = [level for level in (0, 1) if all(abs(p[2]-(lo, hi)[level]) <= tolerance for p in pts)]
        normal = unit(value(face['normal']))
        local_normal = [dot(normal, axis) for axis in axes]
        if any(abs(dot(sub(p, pts[0]), local_normal)) > tolerance for p in pts):
            raise ValueError('Final face vertices contradict the face normal plane')
        if levels:
            if len(loops) != len(shapes):
                raise ValueError('Final cap must contain the outer boundary and all inner loops together')
            if len(levels) != 1 or abs(abs(local_normal[2])-1) > 1e-8 or value(face['originPartId']) != part['id']:
                raise ValueError('Final cap source/normal is inconsistent')
            level = levels[0]
            if local_normal[2]*(1 if level else -1) <= 0:
                raise ValueError('Final cap normal points into the plate')
            for li, loop in enumerate(loops):
                matches = []
                for si, shape in enumerate(shapes):
                    try:
                        matches.append((si, _shape_match([p[:2] for p in loop], shape, tolerance)))
                    except ValueError:
                        pass
                if len(matches) != 1:
                    raise ValueError('Final cap loop has missing/ambiguous analytic boundary attribution')
                si, mapping = matches[0]
                if (li == 0) != (si == 0):
                    raise ValueError('Final face outer/inner loop roles disagree with the material region')
                seen[si, level] += 1
                caps.append({'faceIndex': fi, 'loopIndex': li, 'shapeIndex': si, 'level': level,
                             'mapping': mapping, 'points': loop,
                             'sourceRef': path+f'/finalSolid/value/faces/value/{fi}/loops/value/{li}'})
        else:
            if len(loops) != 1 or len(pts) != 4 or any(min(abs(p[2]-lo), abs(p[2]-hi)) > tolerance for p in pts):
                raise ValueError('Final side is not a supported through-thickness quad')
            sides.append((fi, face, pts))
    if any(seen[i, level] != 1 for i in range(len(shapes)) for level in (0, 1)):
        raise ValueError('Missing or duplicate final opposite cap boundary')
    expected = []
    for cap in (c for c in caps if c['level'] == 0):
        upper = next(c for c in caps if c['shapeIndex'] == cap['shapeIndex'] and c['level'] == 1)
        lower_points, upper_points = cap['points'], upper['points']
        if not _point_set_equal([p[:2] for p in lower_points], [p[:2] for p in upper_points], tolerance):
            raise ValueError('Final opposite boundary vertex sets differ')
        corresponding = [next(p for p in upper_points if norm(sub(p[:2], q[:2])) <= tolerance) for q in lower_points]
        area = sum(cross(a[:2], b[:2]) for a, b in zip(lower_points, lower_points[1:]+lower_points[:1]))
        for i, a in enumerate(lower_points):
            j = (i+1) % len(lower_points)
            shape = shapes[cap['shapeIndex']]
            mapping = cap['mapping'][i]
            owners = (shape['segments'][mapping['segmentIndex']]['ownerIds']
                      if 'segmentIndex' in mapping else shape['ownerIds'])
            origins = {part['id']} if region.get('outlineOwnerId', part['id']) in owners else set()
            origins.update(origin for cut in cuts if cut['id'] in owners for origin in cut['sideOriginIds'])
            delta = sub(lower_points[j][:2], a[:2])
            outward = [delta[1], -delta[0]]
            if (area > 0) != (cap['shapeIndex'] == 0):
                outward = [-v for v in outward]
            expected.append((cap['shapeIndex'], [a, lower_points[j], corresponding[i], corresponding[j]], origins, outward))
    side_mapping = []
    for fi, face, pts in sides:
        matches = [i for i, (_, quad, _, _) in enumerate(expected) if _point_set_equal(pts, quad, tolerance)]
        if len(matches) != 1:
            raise ValueError('Final side face has missing/duplicate boundary attribution')
        si, _, origins, outward = expected.pop(matches[0])
        if value(face['originPartId']) not in origins:
            raise ValueError('Final side face source is not an operation contributing this boundary')
        normal = unit(value(face['normal']))
        if dot([dot(normal, axes[0]), dot(normal, axes[1])], outward) <= 0:
            raise ValueError('Final side face normal points into material')
        side_mapping.append({'faceIndex': fi, 'shapeIndex': si, 'originPartId': value(face['originPartId'])})
    if expected:
        raise ValueError('Final cut or outer side faces are missing')
    chords = [(a, b) for _, _, loops in projected for loop in loops for a, b in zip(loop, loop[1:]+loop[:1])]
    edges = [[project(e[k], frame) for k in ('start', 'end')] for e in value(solid['edges'])]
    if (any(not any(_point_set_equal(e, c, tolerance) for c in chords) for e in edges)
            or any(not any(_point_set_equal(c, e, tolerance) for e in edges) for c in chords)):
        raise ValueError('Final edge/face boundary coverage is incomplete')
    return {'status': 'MATCHED', 'levels': [lo, hi], 'thickness': hi-lo,
            'capEvidence': [{k: v for k, v in c.items() if k != 'points'} for c in caps],
            'sideEvidence': side_mapping, 'faceCount': len(faces), 'edgeCount': len(edges),
            'sourceRef': path+'/finalSolid', 'basis': 'ANALYTIC_BOOLEAN_AND_NATIVE_SHELL_WITHIN_TOLERANCE'}


def _known_cut_children(operations, children):
    types = {op['id']: op.get('runtimeType') for op in operations}
    supported = {'Tekla.Structures.Model.BooleanPart', 'Tekla.Structures.Model.CutPlane',
                 'Tekla.Structures.Model.Fitting'}
    return all(c.get('runtimeType') in supported and types.get(c.get('id')) == c['runtimeType'] for c in children)


def _cut_final_state(part, outline, cuts, frame, path, tolerance):
    operations, children = value(part['operations']), value(part['children'])
    ids = [op['id'] for op in operations]
    if len(ids) != len(set(ids)) or len(cuts) != len(ids) or any(c.get('status') != 'CANDIDATE' for c in cuts):
        raise ValueError('All unique cutting operations must be parsed')
    if not _known_cut_children(operations, children):
        raise ValueError('Unknown child operation prevents final cut coverage')
    removals = [cut for cut in cuts if cut.get('sourceKind') != 'FITTING']
    region = cut_region(outline, removals, tolerance, part['id'])
    solid = _match_cut_solid(part, region, removals, frame, path, tolerance)
    scope = 'PLANAR_CUTS' if any(cut.get('sourceKind') == 'CUT_PLANE' for cut in removals) else 'BOOLEAN_CUTS'
    if any(cut.get('sourceKind') == 'FITTING' for cut in cuts):
        scope = 'FITTINGS' if not removals else scope+'_AND_FITTINGS'
    _append_fitting_mappings(region, cuts)
    return dict(region, status='MATCHED', solidCheck=solid, scope=scope, modelToleranceMm=tolerance,
                remainingProofs=['HOLE_CUT_INTERACTIONS_UNPROVEN'] if value(part['boltGroups']) else [])


def _material_final_state(part, outline, cuts, groups, frame, path, tolerance):
    """Resolve all removals using target geometry and the approved bolt through rule."""
    operations, children = value(part['operations']), value(part['children'])
    ids = [op['id'] for op in operations]
    if (len(ids) != len(set(ids)) or len(cuts) != len(ids)
            or any(c.get('status') != 'CANDIDATE' for c in cuts)):
        raise ValueError('All unique cutting operations must be resolved')
    if not _known_cut_children(operations, children):
        raise ValueError('Unknown child effect prevents complete material verification')
    fitting_cuts = [cut for cut in cuts if cut.get('sourceKind') == 'FITTING']
    removable = [cut for cut in cuts if cut.get('sourceKind') != 'FITTING']
    removals = [dict(c, id='boolean:'+str(c['id']), sourceOperationId=c['id'],
                     sourceKind=c.get('sourceKind', 'BOOLEAN_CUT')) for c in removable]
    bolts = value(part['boltGroups'])
    if len({b['id'] for b in bolts}) != len(bolts):
        raise ValueError('Duplicate hole group identity')
    for bolt, group in zip(bolts, groups):
        if group['classification'] == 'ATTACHMENT_STUD':
            continue  # User-confirmed surface-welded attachment; no material removal.
        if not isinstance(group['candidates'], list):
            raise ValueError('Every hole group or attachment needs explained parameters/effect')
        targets = bolt['targets']
        primary = [value(targets[k]) for k in ('partToBoltTo', 'partToBeBolted')]
        target_ids = [p['id'] for p in primary if p is not None]
        target_ids += [p['id'] for p in value(targets['otherParts'])]
        if part['id'] not in target_ids:
            raise ValueError('Hole group is not associated with the target instance')
        if type(value(bolt['properties']['Bolt'])) is not bool:
            raise ValueError('Bolt/hole-only classification is unknown')
        for hole in group['candidates']:
            if not inside_ring(hole['center'], outline['segments'], tolerance):
                raise ValueError('Hole center lies outside the source plate')
            removals.append(dict(hole, id='hole:'+hole['id'], sourceHoleId=hole['id'],
                                 sourceGroupId=bolt['id'], sourceKind='HOLE',
                                 sideOriginIds=[part['id'], bolt['id']]))
    owner = 'part:'+str(part['id'])
    region = cut_region(outline, removals, tolerance, owner)
    region['outlineOwnerId'] = owner
    hole_evidence = []
    basis = 'PARAMETERS_PLUS_INDEPENDENT_COMPLETE_TARGET_SHELL'
    try:
        solid = _match_cut_solid(part, region, removals, frame, path, tolerance)
        for bolt, group in zip(bolts, groups):
            if group['classification'] == 'ATTACHMENT_STUD' or value(bolt['properties']['Bolt']) is not False:
                continue
            targets = bolt['targets']
            if (value(targets['partToBoltTo']) == value(targets['partToBeBolted'])
                    and not value(targets['otherParts'])):
                proof = _hole_effect(bolt, part['id'], group['candidates'], frame, tolerance, group['sourceRef'], solid)
                hole_evidence.append(dict(proof, groupId=group['id'], status='HOLE_ONLY_CUTTER_CONFIRMED'))
        observed = 'MATCHED'
    except ValueError as error:
        if not groups:
            raise
        # Tekla 2017 may omit every bolt-generated hole from Part.GetSolid.
        # Only a fully matched Boolean-only shell admits the explicit user rule;
        # a malformed/extra/partially observed hole cannot fall back to this path.
        base_cuts = [c for c in removals if c['sourceKind'] in ('BOOLEAN_CUT', 'CUT_PLANE')]
        base = cut_region(outline, base_cuts, tolerance, owner)
        base['outlineOwnerId'] = owner
        solid = _match_cut_solid(part, base, base_cuts, frame, path, tolerance)
        hole_evidence = []
        for bolt, group in zip(bolts, groups):
            if group['classification'] == 'ATTACHMENT_STUD':
                continue
            if value(bolt['properties']['Bolt']) is True:
                props = bolt['properties']
                special = value(props['SlottedHoleX']) or value(props['SlottedHoleY'])
                proof = {'status': 'RULE_CONFIRMED_THROUGH_HOLE', 'rule': 'BOLT-THROUGH-01',
                         'basis': 'USER_CONFIRMED_BOLT_HOLES_ARE_THROUGH', 'sourceRef': group['sourceRef']}
                if special:
                    # 2026-10-27: the bolt group's own parameters are this plate's
                    # hole shape. Hole1–Hole5 do not reassign a slot to a round hole.
                    # A supplied NC witness that fails to match still blocks above.
                    shape = group.get('targetShapeEvidence', {})
                    if shape.get('status') == 'MATCHED':
                        proof['targetShapeEvidence'] = shape
                        proof['penetration'] = physical_penetration(
                            bolt, part['id'], group['candidates'], frame, solid['levels'], tolerance)
                        proof['shapeBasis'] = 'NUMBERED_NC'
                    elif shape:
                        raise ValueError('Special hole needs target-specific shape evidence: '+shape.get('reason', 'unavailable'))
                    else:
                        proof['shapeBasis'] = 'BOLT_GROUP_PARAMETERS'
            else:
                proof = _hole_effect(bolt, part['id'], group['candidates'], frame, tolerance, group['sourceRef'], solid)
                proof = dict(proof, status='HOLE_ONLY_CUTTER_CONFIRMED')
            hole_evidence.append(dict(proof, groupId=group['id']))
        observed = 'NOT_EXPOSED_BY_PART_SOLID'
        basis = 'NATIVE_BOOLEAN_SHELL_PLUS_CONFIRMED_THROUGH_HOLE_RULES'
        region['targetShellDiagnostic'] = str(error)
    for mapping, removal in zip(region['operationMappings'], sorted(removals, key=lambda c: c['id'])):
        mapping.update({k: removal[k] for k in ('sourceKind', 'sourceOperationId', 'sourceGroupId', 'sourceHoleId') if k in removal})
    _append_fitting_mappings(region, fitting_cuts)
    scope = 'ALL_HOLES_AND_PLANAR_CUTS' if any(c.get('sourceKind') == 'CUT_PLANE' for c in removable) else 'ALL_HOLES_AND_BOOLEAN_CUTS'
    if fitting_cuts:
        scope = 'ALL_HOLES_AND_FITTINGS' if not removable else scope+'_AND_FITTINGS'
    return dict(region, status='MATCHED', scope=scope,
                solidCheck=solid, modelToleranceMm=tolerance, remainingProofs=[],
                basis=basis, holeEvidence=hole_evidence, finalHoleSurfaceObservation=observed)


def _spatial_domain(part, frame, path, tolerance, holes):
    """Observed extrusion only: does not turn faceted edges into analytic curves."""
    solid = value(part['finalSolid'])
    normal = frame_axes(frame)[2]
    cap = next((f for f in value(solid['faces'])
                if abs(abs(dot(unit(value(f['normal'])), normal))-1) <= 1e-8), None)
    if cap is None or len(value(cap['loops'])) != 1:
        raise ValueError('Single outer cap domain unavailable')
    points = [project(p, frame)[:2] for p in value(value(cap['loops'])[0]['vertices'])]
    segments = [line(a, b, [path+'/finalSolid']) for a, b in zip(points, points[1:]+points[:1])]
    result = match_prisms(solid, [{'kind': 'OUTLINE', 'segments': segments}], frame, tolerance,
                          path+'/finalSolid/value', origin_id=part['id'])
    profile = re.fullmatch(r'PL(\d+(?:\.\d+)?)(?:\*\d+(?:\.\d+)?)?', value(part['profile']))
    if (not profile or abs(float(profile[1])-result['thickness']) > tolerance
            or abs(value(part['reports']['PROFILE.WIDTH'])-result['thickness']) > tolerance):
        raise ValueError('Observed domain and profile thickness disagree')
    result['topology'] = check_hole_topology(segments, holes, tolerance)
    result['basis'] = 'OBSERVED_PRISM_NOT_EXACT_SURFACE'
    return result


def _loop_diameter(points):
    """Minor and major diameters of a closed loop about its vertex centroid."""
    center = [sum(p[i] for p in points) / len(points) for i in range(len(points[0]))]
    radii = [norm(sub(p, center)) for p in points]
    return center, 2 * min(radii), 2 * max(radii)


def _face_shows_diameter(solid, diameter, tolerance):
    for face in value(solid['faces']):
        for loop in value(face['loops']):
            points = value(loop['vertices'])
            if len(points) < 3:
                continue
            _center, minor, _major = _loop_diameter(points)
            if abs(minor - diameter) <= tolerance:
                return True
    return False


def _distance_to_segment(point, start, end):
    delta = sub(end, start)
    length2 = dot(delta, delta)
    if length2 <= 1e-20:
        return norm(sub(point, start))
    scale = max(0.0, min(1.0, dot(sub(point, start), delta) / length2))
    return norm(sub(point, add(start, mul(delta, scale))))


def _loop_distance(point, points):
    best = None
    for start, end in zip(points, points[1:] + points[:1]):
        if norm(sub(end, start)) <= 1e-10:
            continue
        distance = _distance_to_segment(point, start, end)
        best = distance if best is None else min(best, distance)
    return best


def _midpoint(segment):
    return [(segment['start'][0] + segment['end'][0]) / 2,
            (segment['start'][1] + segment['end'][1]) / 2]


def _on_stock(segment, stock_segments, tolerance):
    middle = _midpoint(segment)
    return any(parameter(segment['start'], stock, tolerance) is not None
               and parameter(segment['end'], stock, tolerance) is not None
               and parameter(middle, stock, tolerance) is not None
               for stock in stock_segments)


def _on_loop(segment, points, tolerance):
    distances = [_loop_distance(point, points) for point in (segment['start'], _midpoint(segment), segment['end'])]
    return all(distance is not None and distance <= tolerance for distance in distances)


def _boolean_cut_loops(operations, frame, path):
    """In-plane cap loops of BooleanPart cutters. Unreadable cutters are skipped."""
    normal = frame_axes(frame)[2]
    loops = []
    for index, operation in enumerate(operations):
        if operation.get('runtimeType') != 'Tekla.Structures.Model.BooleanPart':
            continue
        try:
            if value(operation['type'])['name'] != 'BOOLEAN_CUT':
                continue
            solid = value(operation['operativeSolid'])
        except (ValueError, KeyError, TypeError):
            continue
        source = path + f'/operations/value/{index}/operativeSolid'
        for face in value(solid['faces']):
            face_normal = unit(value(face['normal']))
            if abs(abs(dot(face_normal, normal)) - 1) > 1e-6:
                continue
            for loop in value(face['loops']):
                points = [project(point, frame)[:2] for point in value(loop['vertices'])]
                if len(points) >= 3:
                    loops.append({'operationId': operation['id'], 'sourceRef': source, 'points': points})
    return loops


def _line_deviation(points):
    delta = sub(points[-1], points[0])
    length = norm(delta)
    if length <= 1e-12:
        return None
    return max(abs(cross(sub(point, points[0]), delta)) / length for point in points[1:-1])


def _radial_error(points, arc):
    return max(abs(norm(sub(point, arc['center'])) - arc['radius']) for point in points)


def _chord_error(points, arc):
    worst = 0.0
    for start, end in zip(points, points[1:]):
        middle = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]
        worst = max(worst, abs(norm(sub(middle, arc['center'])) - arc['radius']))
    return worst


def _accept_arc(points, tolerance):
    """One circle within tolerance, and not a straight line within that tolerance."""
    deviation = _line_deviation(points)
    if deviation is None or deviation <= tolerance or len(points) < 4:
        return None
    try:
        primary = three_point_arc(points[0], points[len(points) // 2], points[-1])
        other = three_point_arc(points[0], points[1], points[-1])
    except ValueError:
        return None
    if (norm(sub(primary['center'], other['center'])) > tolerance
            or abs(primary['radius'] - other['radius']) > tolerance):
        return None
    radial, chord = _radial_error(points, primary), _chord_error(points, primary)
    if radial > tolerance or chord > tolerance:
        return None
    return primary, radial, chord


def _cut_runs(flags):
    count = len(flags)
    if not any(flags) or all(flags):
        return []
    origin = next(index for index, flag in enumerate(flags) if not flag)
    runs, index = [], 0
    while index < count:
        if not flags[(origin + index) % count]:
            index += 1
            continue
        run = []
        while index < count and flags[(origin + index) % count]:
            run.append((origin + index) % count)
            index += 1
        runs.append(run)
    return runs


def _arc_spans(points, tolerance):
    spans, start, last = [], 0, len(points) - 1
    while start < last:
        found = None
        for end in range(last, start + 2, -1):
            if end - start < 3:
                break
            accepted = _accept_arc(points[start:end + 1], tolerance)
            if accepted is not None:
                found = (end, accepted)
                break
        if found is None:
            start += 1
            continue
        end, accepted = found
        spans.append((start, end, accepted))
        start = end
    return spans


def fit_cut_produced_contours(segments, stock_segments, cutter_loops, tolerance):
    """Fit only Boolean-cut outline chains that already lie on the cutter.

    Stock edges, hole symbols, and facets that do not lie on a BooleanPart
    cutter stay lines. A chain becomes an arc only when its chords are within
    the model tolerance of one circle and are not themselves a straight line.
    A failed fit stays a line.
    """
    if not segments or not stock_segments or not cutter_loops:
        return segments
    flags, owners = [], []
    for segment in segments:
        if segment.get('kind') != 'LINE' or _on_stock(segment, stock_segments, tolerance):
            flags.append(False)
            owners.append(())
            continue
        ids = tuple(sorted({loop['operationId'] for loop in cutter_loops
                            if _on_loop(segment, loop['points'], tolerance)}))
        flags.append(bool(ids))
        owners.append(ids)
    runs = _cut_runs(flags)
    if not runs:
        return segments
    output = list(segments)
    for run in runs:
        points = [segments[index]['start'] for index in run] + [segments[run[-1]]['end']]
        spans = _arc_spans(points, tolerance)
        if not spans:
            continue
        pieces, cursor = [], 0
        for start, end, (arc, radial, chord) in spans:
            pieces.extend(segments[run[index]] for index in range(cursor, start))
            ids = sorted({op for index in range(start, end) for op in owners[run[index]]})
            refs = list(segments[run[start]]['sourceRefs'])
            for loop in cutter_loops:
                if loop['operationId'] in ids and loop['sourceRef'] not in refs:
                    refs.append(loop['sourceRef'])
            pieces.append(dict(arc, sourceRefs=refs, basis='CUT_CONTOUR_FITTED_WITHIN_TOLERANCE',
                               ownerIds=list(segments[run[start]].get('ownerIds', [])),
                               fit={'method': 'CUT_CONTOUR_CIRCLE', 'center': list(arc['center']),
                                    'radius': arc['radius'], 'maxRadialResidualMm': radial,
                                    'maxChordDeviationMm': chord, 'chordCount': end - start,
                                    'operationIds': ids}))
            cursor = end
        pieces.extend(segments[run[index]] for index in range(cursor, len(run)))
        output[run[0]] = pieces
        for index in run[1:]:
            output[index] = None
    result = []
    for item in output:
        if item is None:
            continue
        result.extend(item if isinstance(item, list) else [item])
    return result


def _fit_cut_produced_contours(segments, stock_segments, operations, frame, path, tolerance):
    return fit_cut_produced_contours(
        segments, stock_segments, _boolean_cut_loops(operations, frame, path), tolerance)


def _observed_flat_material(part, stock_outline, groups, frame, path, tolerance):
    """Admit a flat plate from the final solid when the operation-by-operation proof stops.

    The drawn edge, diameter and center are the ones on the solid. A sloped
    cylinder stays an ordinary round hole. A partial-thickness face is a bevel
    and stays on the section proof. Faceted contours are fitted to arcs only
    when they are the Boolean-cut boundary and already lie on one circle within
    the model tolerance. Stock edges and other facets stay lines.
    """
    operations, children = value(part['operations']), value(part['children'])
    if len({op['id'] for op in operations}) != len(operations):
        raise ValueError('Duplicate cutting operation')
    if not _known_cut_children(operations, children):
        raise ValueError('Unknown child effect prevents reading the final solid')
    if operations and 'segments' not in stock_outline:
        raise ValueError('Stock outline is not readable')
    solid = value(part['finalSolid'])
    faces = value(solid['faces'])
    axes = frame_axes(frame)
    projected = []
    for index, face in enumerate(faces):
        loops = []
        for loop in value(face['loops']):
            points = [project(p, frame) for p in value(loop['vertices'])]
            if len(points) < 3:
                raise ValueError('Final face loop is degenerate')
            loops.append(points)
        if not loops:
            raise ValueError('Final face has no loops')
        normal = unit(value(face['normal']))
        local_normal = [dot(normal, axis) for axis in axes]
        if any(abs(dot(sub(point, loops[0][0]), local_normal)) > tolerance
               for loop in loops for point in loop):
            raise ValueError('Final face vertices contradict the face normal')
        levels = [point[2] for loop in loops for point in loop]
        projected.append((index, face, loops, min(levels), max(levels), local_normal))
    all_levels = [point[2] for _, _, loops, _, _, _ in projected for loop in loops for point in loop]
    if not all_levels:
        raise ValueError('Final solid has no vertices')
    low, high = min(all_levels), max(all_levels)
    thickness = high - low
    if thickness <= tolerance:
        raise ValueError('Degenerate final plate thickness')
    caps = []
    for index, face, loops, z0, z1, local_normal in projected:
        span = z1 - z0
        if span <= tolerance:
            level = (z0 + z1) / 2
            if min(abs(level - low), abs(level - high)) > tolerance:
                raise ValueError('Partial-thickness face has no flat-plate expression')
            caps.append((index, face, loops, local_normal))
        elif span < thickness - tolerance:
            raise ValueError('Partial-thickness face has no flat-plate expression')
    if len(caps) != 2:
        raise ValueError('Final solid does not have two thickness caps')
    positive = [cap for cap in caps if cap[3][2] > 0]
    if len(positive) != 1 or positive[0][3][2] < 1 - 1e-6:
        raise ValueError('Cap normal does not agree with the plate thickness axis')
    index, face, loops, local_normal = positive[0]
    other = next(cap for cap in caps if cap[0] != index)
    if other[3][2] > -1 + 1e-6:
        raise ValueError('Opposite cap normal points into the plate')
    if value(face['originPartId']) != part['id'] or value(other[1]['originPartId']) != part['id']:
        raise ValueError('Final cap source differs from the target instance')
    if len(other[2]) != len(loops):
        raise ValueError('Opposite cap does not show the same openings')

    def area(points):
        return abs(sum(cross(a[:2], b[:2]) for a, b in zip(points, points[1:] + points[:1])))

    ranked = sorted(loops, key=area, reverse=True)
    if len(ranked) > 1 and area(ranked[0]) <= area(ranked[1]) + tolerance:
        raise ValueError('Final cap outer loop is ambiguous')
    other_ranked = sorted(other[2], key=area, reverse=True)
    if len(other_ranked) > 1 and area(other_ranked[0]) <= area(other_ranked[1]) + tolerance:
        raise ValueError('Final cap outer loop is ambiguous')
    refs = [path + f'/finalSolid/value/faces/value/{index}']
    outer = [point[:2] for point in ranked[0]]
    other_outer = [point[:2] for point in other_ranked[0]]
    segments = [line(a, b, refs) for a, b in zip(outer, outer[1:] + outer[:1])]
    for segment in segments:
        segment['ownerIds'] = [part['id']]
    simple_ring(segments, tolerance)
    stock_points = ([segment['start'] for segment in stock_outline['segments']]
                    if stock_outline.get('segments') else None)
    stock_same = stock_points is not None and _point_set_equal(stock_points, outer, tolerance)
    inners = [[point[:2] for point in loop] for loop in ranked[1:]]
    # An uncut plate has one outline. Either cap drifting is the solid disagreeing
    # with the stock, not a cut the final solid is allowed to measure.
    if stock_points is not None and not operations and not (
            stock_same and _point_set_equal(stock_points, other_outer, tolerance)):
        raise ValueError('Final outline disagrees with the uncut stock')
    if operations and stock_same and not inners:
        raise ValueError('Cutting operation is not present in the final solid')
    covered = [item for item in projected if item[0] not in (index, other[0])]
    for loop in loops:
        for start, end in zip(loop, loop[1:] + loop[:1]):
            if not any(_point_set_equal([start, end], [a, b], tolerance)
                       for _, _, face_loops, _, _, _ in covered
                       for face_loop in face_loops
                       for a, b in zip(face_loop, face_loop[1:] + face_loop[:1])):
                raise ValueError('Final cap edge has no adjacent face')
    plane_checks = []
    for operation in operations:
        if operation.get('runtimeType') not in ('Tekla.Structures.Model.CutPlane',
                                                'Tekla.Structures.Model.Fitting'):
            continue
        plane = value(operation['plane'])
        normal = frame_axes(plane)[2]
        signed = [dot(sub(point, plane['origin']), normal)
                  for item in faces for loop in value(item['loops']) for point in value(loop['vertices'])]
        if min(signed) < -tolerance and max(signed) > tolerance:
            raise ValueError('Cutting plane crosses the final solid')
        plane_checks.append((normal, plane['origin'], signed))
    for index, (normal, origin, signed) in enumerate(plane_checks):
        if min(abs(distance) for distance in signed) <= tolerance:
            continue
        # An earlier parallel cut can sit entirely outside the final solid when a
        # later cut on the same side took more material. A lone missed plane cannot.
        side = 1 if max(signed) > 0 else -1
        excused = False
        for other_index, (other_normal, other_origin, other_signed) in enumerate(plane_checks):
            if other_index == index or dot(normal, other_normal) < 1 - 1e-6:
                continue
            if min(abs(distance) for distance in other_signed) > tolerance:
                continue
            gap = dot(sub(other_origin, origin), normal)
            if gap * side > tolerance:
                excused = True
                break
        if not excused:
            raise ValueError('Cutting plane does not meet the final solid')
    openings = []
    for points in inners:
        center, minor, major = _loop_diameter(points)
        ring = [line(a, b, refs) for a, b in zip(points, points[1:] + points[:1])]
        simple_ring(ring, tolerance)
        openings.append({'center': center[:2], 'dmin': minor, 'dmax': major, 'segments': ring})
    bolts = value(part['boltGroups'])
    if len(bolts) != len(groups):
        raise ValueError('Hole group inventory does not match the part')
    candidates = []
    for bolt, group in zip(bolts, groups):
        if group['classification'] == 'ATTACHMENT_STUD':
            continue
        if not isinstance(group['candidates'], list):
            raise ValueError('Hole parameters are not readable')
        fastener = value(bolt['properties']['Bolt'])
        if type(fastener) is not bool:
            raise ValueError('Bolt/hole-only classification is unknown')
        targets = bolt['targets']
        primary = [value(targets[key]) for key in ('partToBoltTo', 'partToBeBolted')]
        target_ids = [item['id'] for item in primary if item is not None]
        target_ids += [item['id'] for item in value(targets['otherParts'])]
        if part['id'] not in target_ids:
            raise ValueError('Hole group is not associated with the target instance')
        if fastener is False and 'solidWithHighAccuracy' in bolt:
            # The cutter solid is optional. When it is stored, a loop of another
            # size contradicts the opening; tilt alone does not.
            cutter = value(bolt['solidWithHighAccuracy'])
            if any(not _face_shows_diameter(cutter, hole['diameter'], tolerance)
                   for hole in group['candidates']):
                raise ValueError('Hole-only cutter does not show the opening diameter')
        for hole in group['candidates']:
            if not inside_ring(hole['center'], segments, tolerance):
                raise ValueError('Hole center lies outside the source plate')
            candidates.append((bolt, group, hole, fastener))
    claimed = {}
    for opening_index, opening in enumerate(openings):
        hits = [candidate_index for candidate_index, (_bolt, _group, hole, _fastener) in enumerate(candidates)
                if abs(opening['dmin'] - hole['diameter']) <= tolerance
                and inside_ring(hole['center'], opening['segments'], tolerance)]
        if len(hits) != 1:
            raise ValueError('Opening has no unique manufacturing diameter and center')
        if hits[0] in claimed:
            raise ValueError('One hole matches more than one opening')
        claimed[hits[0]] = opening_index
    for candidate_index, (_bolt, _group, _hole, fastener) in enumerate(candidates):
        if fastener is False and candidate_index not in claimed:
            raise ValueError('Hole-only group has no opening in the final solid')
    holes, evidence = [], []
    for candidate_index, (bolt, group, hole, fastener) in enumerate(candidates):
        opening = openings[claimed[candidate_index]] if candidate_index in claimed else None
        center = opening['center'] if opening else hole['center']
        source_refs = list(hole['sourceRefs']) + [path + '/finalSolid']
        item = {'id': hole['id'], 'kind': hole['kind'], 'center': list(center),
                'diameter': hole['diameter'], 'extension': hole['extension'],
                'direction': list(hole['direction']),
                'segments': hole_segments(center, hole['diameter'], hole['extension'],
                                          hole['direction'], source_refs),
                'sourceRefs': source_refs, 'ownerIds': [part['id'], bolt['id']],
                'fastener': fastener, 'basis': 'OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE'}
        holes.append(item)
        if fastener:
            proof = {'status': 'RULE_CONFIRMED_THROUGH_HOLE', 'rule': 'BOLT-THROUGH-01',
                     'basis': 'USER_CONFIRMED_BOLT_HOLES_ARE_THROUGH',
                     'shapeBasis': 'BOLT_GROUP_PARAMETERS', 'sourceRef': group['sourceRef'],
                     'groupId': group['id']}
        else:
            proof = {'status': 'OPENING_MATCHED', 'basis': 'CYLINDER_DIAMETER_AND_PROJECTED_CENTER',
                     'groupId': group['id'], 'sourceRef': group['sourceRef'],
                     'diameter': hole['diameter'], 'minorDiameter': opening['dmin'],
                     'majorDiameter': opening['dmax'], 'center': list(opening['center'])}
        evidence.append(proof)
    segments = _fit_cut_produced_contours(
        segments, stock_outline.get('segments') or [], operations, frame, path, tolerance)
    simple_ring(segments, tolerance)
    outline = {'kind': 'OUTLINE', 'segments': segments, 'sourceRefs': refs,
               'basis': 'OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE', 'ownerIds': [part['id']]}
    fits = [segment['fit'] for segment in segments if segment.get('fit')]
    if fits:
        outline['cutContourFits'] = fits
    return {'status': 'MATCHED', 'outline': outline, 'holes': holes,
            'operationMappings': [{'operationId': operation['id'], 'status': 'OBSERVED_IN_FINAL_SOLID',
                                   'sourceKind': operation['runtimeType'], 'sourceRef': path}
                                  for operation in operations],
            'solidCheck': {'status': 'MATCHED', 'levels': [low, high], 'thickness': thickness,
                           'sourceRef': path + '/finalSolid',
                           'basis': 'OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE'},
            'scope': 'OBSERVED_FINAL_SOLID', 'modelToleranceMm': tolerance, 'remainingProofs': [],
            'basis': 'OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE', 'holeEvidence': evidence,
            'finalHoleSurfaceObservation': 'CAP_OPENING'}


def _part_geometry(part, index, source, witness, nc_origin, tolerance, nc_tolerance, observations=None):
    issues, path = [], f"#/parts/{index}"
    frame = value(part["dstvCoordinateSystem"])
    common = _checked('COMMON_SHAPE_FACTS_UNRESOLVED',
                      lambda: collect_shape_facts(part, frame, path, tolerance), issues)
    part_type = (classify_shape(common,tolerance) if common.get('schemaVersion') else
                 dict(type='UNKNOWN',status='UNRESOLVED',route=None,sourceRefs=[path],
                      basis='COMMON_SHAPE_FACTS_UNAVAILABLE',missingProofs=['COMMON_SHAPE_FACTS']))
    if part_type['route'] != 'FLAT_PLATE':
        code = 'PART_TYPE_UNRESOLVED' if part_type['status'] != 'CLASSIFIED' else 'PART_TYPE_ROUTE_UNSUPPORTED'
        message = part_type['type']+': '+part_type['basis']
        issues.append(dict(code=code,message=message))
        nc_holes = dict(status='SKIPPED',reason=nc_origin['status'])
        if nc_origin['status']=='CANDIDATE' and source['status']=='READ_CONSISTENT':
            nc_holes = _checked('NC_HOLE_INVENTORY_UNRESOLVED',lambda: nc_hole_inventory(witness['ncFile']['parsed']),[])
        if source['status'] != 'READ_CONSISTENT':
            issues.append(dict(code='NATIVE_SOURCE_BLOCKED',message='Target native provenance failed'))
        gap = dict(id='part/type',kind='PART_TYPE',sourceRefs=part_type['sourceRefs'],
                   evidenceStatus='UNRESOLVED',proofStatus='INSUFFICIENT',missingProofs=[code],ncEvidence=[])
        return dict(id=part['id'],partPosition=source['partPosition'],geometryStatus='UNRESOLVED',
                    manufacturingStatus='BLOCKED',sourceStatus=source['status'],nativeSource=source,ncSource=nc_origin,
                    commonShapeFacts=common,partType=part_type,
                    blockedStage='TYPE_ROUTING' if source['status']=='READ_CONSISTENT' else 'SOURCE',
                    specializedGeometryStatus='NOT_RUN',evidenceSufficiency='INSUFFICIENT',
                    ncCheck=dict(status='SKIPPED',reason=code),ncHoleInventory=nc_holes,
                    featureEvidence=[gap],issues=issues,remainingProofs=part_type['missingProofs'],
                    materialFinalState=dict(status='NOT_RUN',reason=message))
    if part['runtimeType'] != 'Tekla.Structures.Model.ContourPlate':
        frame = part_type['frame']
    outline = _checked("OUTLINE_UNRESOLVED", lambda: _outline(part, frame, path, tolerance), issues)
    operations = value(part['operations'])
    shell = {"status": "UNRESOLVED"}
    if "segments" in outline and not operations:
        shell = _checked("PRISM_UNRESOLVED", lambda: match_prisms(value(part["finalSolid"]), [outline],
                         frame, tolerance, path + "/finalSolid/value", origin_id=part["id"]), issues)
    groups, holes = [], []
    for i, bolt in enumerate(value(part["boltGroups"])):
        bpath = path + f"/boltGroups/value/{i}"
        stud = value(bolt['properties']['BoltStandard']) == 'STUD'
        if stud:
            candidates = {'status': 'NOT_APPLICABLE', 'reason': 'STUD is a surface-welded attachment without a hole',
                          'rule': 'STUD-NO-HOLE-01'}
        else:
            candidates = _checked("HOLE_PARAMETERS_UNRESOLVED",
                                  lambda: _hole_candidates(bolt, frame, bpath), issues)
        group = {"id": bolt["id"], "sourceRef": bpath, "candidates": candidates,
                 "observedPositionCount": len(value(bolt["positions"])),
                 "classification": "ATTACHMENT_STUD" if stud
                                   else "HOLE_PARAMETER_CANDIDATE"}
        if isinstance(candidates, list):
            holes.extend(candidates)
        groups.append(group)
    if witness is not None and source['status'] == 'READ_CONSISTENT':
        target_shapes = _checked('TARGET_HOLE_SHAPE_UNRESOLVED', lambda: nc_target_shapes(
            part, outline, groups, frame, witness, nc_tolerance), [])
        for group in groups:
            group['targetShapeEvidence'] = target_shapes
    children = value(part['children'])
    working, fitting_records = outline, {}
    if outline.get('segments') and any(op.get('runtimeType') == 'Tekla.Structures.Model.Fitting' for op in operations):
        fitted = _checked('FITTING_UNRESOLVED', lambda: _apply_fittings(
            outline, operations, part, frame, path, tolerance), issues)
        if fitted.get('outline'):
            working, fitting_records = fitted['outline'], fitted['records']
    cuts, planar_cuts = [], []
    for i, operation in enumerate(operations):
        diagnostics = []
        cut = _checked('CIRCULAR_CUT_UNRESOLVED', lambda: _circular_cut(
            operation, part, frame, path+f'/operations/value/{i}', tolerance), diagnostics)
        cut.setdefault('id', operation['id'])
        cut.setdefault('issues', diagnostics)
        cuts.append(cut)
        if operation.get('runtimeType') == 'Tekla.Structures.Model.Fitting':
            planar = dict(fitting_records.get(operation['id'], {
                'status': 'UNRESOLVED', 'reason': 'Fitting end replacement was not proven'}))
            planar.setdefault('id', operation['id'])
            planar['issues'] = []
        else:
            planar_diagnostics = []
            planar = _checked('PLANAR_CUT_UNRESOLVED', lambda: _planar_cut(
                operation, part, frame, path+f'/operations/value/{i}', tolerance, working), planar_diagnostics)
            planar.setdefault('id', operation['id'])
            planar['issues'] = planar_diagnostics
        planar_cuts.append(planar)
    final_cut = (_checked('CUT_FINAL_STATE_UNRESOLVED', lambda: _cut_final_state(
        part, working, planar_cuts, frame, path, tolerance), issues) if operations else {'status': 'NOT_APPLICABLE'})
    material_diagnostics = []
    material = _checked('MATERIAL_FINAL_STATE_UNRESOLVED', lambda: _material_final_state(
        part, working, planar_cuts, groups, frame, path, tolerance), material_diagnostics)
    material['issues'] = material_diagnostics
    section = {'status': 'NOT_APPLICABLE'}
    if operations and material['status'] != 'MATCHED' and any(c.get('status') != 'CANDIDATE' for c in planar_cuts):
        from .section_geometry import prove_sections, section_material
        section_issues = []
        section = _checked('SECTION_GEOMETRY_UNRESOLVED', lambda: prove_sections(
            part, outline, frame, path, tolerance), section_issues)
        section['issues'] = section_issues
        if section['status'] == 'MATCHED':
            # Preserve independently proved machining facts even if hole attribution
            # blocks the complete material. Do not misreport that as an unknown cut.
            shell = dict(status='MATCHED',levels=section['levels'],thickness=section['thickness'],
                         sourceRef=section['sourceRef'],faceEvidence=section['faceEvidence'])
            issues = [i for i in issues if i['code'] != 'CUT_FINAL_STATE_UNRESOLVED']
            material_diagnostics = []
            material = _checked('SECTION_MATERIAL_UNRESOLVED', lambda: section_material(
                part, outline, groups, section, frame, path, tolerance), material_diagnostics)
            material['issues'] = material_diagnostics
            if material['status'] != 'MATCHED':
                issues.extend(material_diagnostics)
    if material['status'] != 'MATCHED':
        observed = _checked('OBSERVED_SOLID_UNRESOLVED', lambda: _observed_flat_material(
            part, outline if outline.get('segments') else {}, groups, frame, path, tolerance), [])
        if observed.get('status') == 'MATCHED':
            material = observed
            material['issues'] = []
            issues = [item for item in issues if item['code'] not in (
                'CUT_FINAL_STATE_UNRESOLVED', 'PRISM_UNRESOLVED', 'FITTING_UNRESOLVED')]
            # No partial-thickness face survived the observed reading, so a failed
            # narrower section attempt is not a missing section.
            if section.get('status') != 'MATCHED':
                section = {'status': 'NOT_APPLICABLE'}
    complete = material['status'] == 'MATCHED'
    final_outline = material['outline'] if complete else (final_cut['outline'] if final_cut['status'] == 'MATCHED' else outline)
    if final_cut['status'] == 'MATCHED':
        shell = final_cut['solidCheck']
    if complete:
        shell = material['solidCheck']
        issues = [i for i in issues if i['code'] not in ('PRISM_UNRESOLVED', 'CUT_FINAL_STATE_UNRESOLVED')]
    if shell['status'] == 'MATCHED':
        def check_thickness():
            if 'stockLevels' in outline and any(abs(a-b) > tolerance for a, b in zip(outline['stockLevels'], shell['levels'])):
                raise ValueError('Final thickness faces differ from the proven stock thickness domain')
            declared = _profile_thickness(part)
            if declared is not None and abs(declared-shell['thickness']) > tolerance:
                raise ValueError('Profile and double-face thickness disagree')
            if declared is not None and abs(value(part['reports']['PROFILE.WIDTH'])-shell['thickness']) > tolerance:
                raise ValueError('Profile report and thickness disagree')
            return {'status': 'MATCHED', 'value': shell['thickness'],
                    'sourceRefs': [path+'/profile', path+'/reports/PROFILE.WIDTH', path+'/finalSolid']}
        thickness = _checked('THICKNESS_UNRESOLVED', check_thickness, issues)
    else:
        thickness = {'status': 'UNRESOLVED'}
    cut_holes = [c['targetCandidate'] for c in cuts
                 if c.get('innerBoundaryCheck', {}).get('status') == 'MATCHED_BOUNDARIES']
    if final_cut['status'] == 'MATCHED':
        cut_holes = [dict(h, id='cut-result:'+str(i), sourceOperationIds=h['ownerIds'])
                     for i, h in enumerate(final_cut['holes'])]
    known_children = _known_cut_children(operations, children)
    inputs_complete = (all(isinstance(g['candidates'], list) or g['classification'] == 'ATTACHMENT_STUD' for g in groups)
                       and (section['status'] == 'MATCHED' or final_cut['status'] == 'MATCHED' or len(cut_holes) == len(operations)) and known_children)
    finished = finished_hole_candidates(holes+cut_holes, tolerance, inputs_complete)
    if complete:
        final_holes = [dict(h, id='material-result:'+str(i), sourceFeatureIds=h['ownerIds'])
                       for i, h in enumerate(material['holes'])]
        finished = {'status': 'MATCHED', 'basis': material['basis'], 'inputComplete': True,
                    'sourceCount': len(holes)+len(planar_cuts), 'candidateCount': len(final_holes),
                    'candidates': final_holes, 'sourceMappings': material['operationMappings'], 'issues': []}
    if finished['status'] == 'UNRESOLVED':
        issues.extend(finished['issues'])
    spatial_issues = []
    spatial = (dict(shell) if complete or section['status'] == 'MATCHED' or final_cut['status'] == 'MATCHED' else
               _checked('TARGET_SPATIAL_DOMAIN_UNPROVEN',
                        lambda: _spatial_domain(part, frame, path, tolerance, finished['candidates']), spatial_issues))
    spatial['issues'] = spatial_issues
    if (complete or final_cut['status'] == 'MATCHED') and spatial['status'] == 'MATCHED' and thickness['status'] != 'MATCHED':
        spatial.update(status='UNRESOLVED', reason='Target thickness sources disagree')
    for bolt, group in zip(value(part['boltGroups']), groups):
        if not isinstance(group['candidates'], list):
            continue
        diagnostics = []
        group['solidCheck'] = _checked('HOLE_SOLID_UNRESOLVED', lambda: _hole_effect(
            bolt, part['id'], group['candidates'], frame, tolerance, group['sourceRef'], spatial), diagnostics)
        group['solidDiagnostics'] = diagnostics
        effect = composite_hole_effect(bolt, part['id'], group['candidates'], frame, spatial,
                                       observations, tolerance, source)
        targets = bolt['targets']
        cutter_applicable = (value(bolt['properties']['Bolt']) is False
                             and value(targets['partToBoltTo'])['id'] == part['id']
                             and value(targets['partToBeBolted'])['id'] == part['id']
                             and not value(targets['otherParts']) and spatial['status'] == 'MATCHED')
        if (effect['status'] == 'COMPOSITE_CONFIRMED' and cutter_applicable
                and group['solidCheck']['status'] != 'MATCHED'):
            # An available cutter path must be reconciled, not erased by a new source.
            effect.update(status='INSUFFICIENT', missingProofs=['CANDIDATE_SOLID_UNRESOLVED'])
        group['effectEvidence'] = effect
        if complete:
            group['finalStateEvidence'] = {'status': 'FINAL_MATERIAL_CONFIRMED', 'basis': material['basis'],
                'sourceRef': path+'/finalSolid', 'mappings': [m for m in material['operationMappings']
                                                          if m.get('sourceGroupId') == group['id']],
                'holeEvidence': [h for h in material['holeEvidence'] if h['groupId'] == group['id']]}
        if effect['status'] != 'COMPOSITE_CONFIRMED' and not complete:
            issues.extend(diagnostics)
        if effect['status'] == 'CONFLICT':
            issues.append({'code': 'HOLE_EVIDENCE_CONFLICT', 'message': ', '.join(effect['missingProofs'])})
    if (operations or children) and final_cut['status'] != 'MATCHED' and section['status'] != 'MATCHED' and not complete:
        issues.append({"code": "OPERATION_EFFECT_UNPROVEN", "message": "Nonempty operation/child set needs explicit proof"})
    for gap in ([] if complete else final_cut.get('remainingProofs', [])):
        issues.append({'code': gap, 'message': 'Boolean cut verification does not prove bolt-hole interactions'})
    topology = {"status": "UNRESOLVED"}
    if "segments" in final_outline:
        topology = _checked("TOPOLOGY_UNRESOLVED",
                            lambda: check_hole_topology(final_outline["segments"], finished['candidates'], tolerance), issues)
    operation = {"status": 'MATCHED' if complete or section['status'] == 'MATCHED' or final_cut['status'] == 'MATCHED' else
                           ("EMPTY_SET" if not operations and not children else "UNRESOLVED"),
                 "sourceRefs": [path+"/operations", path+"/children", path+'/finalSolid'],
                 'remainingProofs': [] if complete else final_cut.get('remainingProofs', [])}
    nc = {"status": "SKIPPED", "reason": nc_origin["status"]}
    nc_holes = {'status': 'SKIPPED', 'reason': nc_origin['status']}
    if nc_origin["status"] == "CANDIDATE" and source["status"] == "READ_CONSISTENT":
        nc_holes = _checked('NC_HOLE_INVENTORY_UNRESOLVED',
                            lambda: nc_hole_inventory(witness['ncFile']['parsed']), [])
        if nc_origin['geometryRepresentation'] == 'UNFOLDED':
            nc['reason'] = 'UNFOLDED_COORDINATE_MAPPING_REQUIRED'
        elif any(norm(sub(a, b)) > 1e-8 for a, b in zip(frame_axes(frame), frame_axes(value(part['dstvCoordinateSystem'])))):
            nc['reason'] = 'PLATE_FRAME_TO_NC_MAPPING_REQUIRED'
        elif complete and any(h['kind'] not in ('ROUND', 'SLOT') for h in finished['candidates']):
            nc['reason'] = 'FINAL_CUTOUT_NC_MAPPING_REQUIRED'
        elif not complete and operations and (final_cut['status'] != 'MATCHED' or groups or
                             any(h['kind'] not in ('ROUND', 'SLOT') for h in cut_holes)):
            nc['reason'] = 'BOOLEAN_FINAL_NC_MAPPING_REQUIRED'
        elif "segments" not in outline:
            nc["reason"] = "NATIVE_OUTLINE_UNRESOLVED"
        elif any(not isinstance(group["candidates"], list) and group['classification'] != 'ATTACHMENT_STUD' for group in groups):
            nc["reason"] = "NATIVE_HOLE_INVENTORY_UNRESOLVED"
        else:
            try:
                nc = match_nc_geometry(final_outline, finished['candidates'] if complete else (cut_holes if operations else holes),
                                       thickness.get("value"), witness["ncFile"]["parsed"], nc_tolerance)
            except NcGeometryConflict as exc:
                nc = {"status": "CONFLICT", "reason": str(exc), "featureScope": exc.feature_scope}
                issues.append({"code": "NC_GEOMETRY_CONFLICT", "message": str(exc)})
            except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, OverflowError) as exc:
                nc = {"status": "UNRESOLVED", "reason": str(exc)}
    if source["status"] != "READ_CONSISTENT":
        nc["reason"] = "NATIVE_SOURCE_BLOCKED"
        nc_holes = {'status': 'SKIPPED', 'reason': 'NATIVE_SOURCE_BLOCKED'}
        issues.append({"code": "NATIVE_SOURCE_BLOCKED", "message": "Target native provenance failed"})
    if nc['status'] == 'MATCHED' and nc_holes['status'] == 'READ':
        nc_holes['modelPositionMapping'] = 'MATCHED'
    features = feature_evidence(final_outline, shell, thickness, groups, topology, operation, source, nc, path, final_cut, material)
    return {"id": part["id"], "partPosition": source["partPosition"],
            "geometryStatus": "UNRESOLVED" if issues else "EVIDENCE_MATCHED", "manufacturingStatus": "BLOCKED",
            "sourceStatus": source["status"], "sourceIssues": source["issues"], "nativeSource": source,
            "ncSource": nc_origin, "frame": frame,
            'commonShapeFacts': common, 'partType': part_type,
            'specializedGeometryStatus': 'MATCHED' if complete and not issues else 'UNRESOLVED',
            'blockedStage': ('SOURCE' if source['status'] != 'READ_CONSISTENT' else
                             'SPECIALIZED_GEOMETRY' if issues or not complete else None),
            "outline": outline, "shellCheck": shell, "thickness": thickness, "holeGroups": groups,
            "finishedHoleCandidates": finished, "circularCuts": cuts,
            'planarCuts': planar_cuts, 'cutFinalState': final_cut, 'finalOutline': final_outline,
            'materialFinalState': material, 'sectionGeometry': section,
            "spatialDomainCheck": spatial,
            "ncCheck": nc, "ncHoleInventory": nc_holes, "topologyCheck": topology,
            "operationCheck": operation, "issues": issues, "featureEvidence": features,
            "evidenceSufficiency": evidence_sufficiency(features),
            "remainingProofs": sorted({gap for f in features for gap in f["missingProofs"]} | {"RECHECK_BEFORE_DRAWING"})}


def audit_native_geometry(capture, model_tolerance_mm=DEFAULT_MODEL_TOLERANCE_MM, nc_tolerance_mm=None, max_resolution_mm=None,
                          hole_observations=None):
    tolerance = finite_positive(model_tolerance_mm)
    digest = hashlib.sha256(json.dumps(capture, sort_keys=True, allow_nan=False).encode()).hexdigest()
    source, nc_error, nc_tolerance, resolution = None, None, None, None
    if capture.get("schemaVersion") == "teklaNcCapture.v1":
        nc_tolerance = finite_positive(nc_tolerance_mm)
        finite_positive(max_resolution_mm)
        resolution = str(max_resolution_mm)
        raw = json.loads(_bytes(capture["before"]).decode("utf-8-sig"))
        after = json.loads(_bytes(capture["after"]).decode("utf-8-sig"))
        origins = native_sources(raw, after, capture)
        native_sha256 = capture["before"]["sha256"]
        try:
            source = audit_nc_capture(capture, max_resolution_mm)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
            nc_error = exc
    elif capture.get("schemaVersion") == "teklaNativeEvidence.v1":
        raw = capture
        origins = native_sources(raw)
        native_sha256 = None  # File digest is added by the loader; inputDigest covers in-memory inputs.
    else:
        raise ValueError("Expected teklaNativeEvidence.v1 or teklaNcCapture.v1")
    observations = validate_observations(hole_observations, raw)
    parts = []
    for index, part in enumerate(raw["parts"]):
        witness = source["parts"][index] if source is not None else None
        nc_origin = nc_source(witness, nc_error)
        result = _checked("PART_GEOMETRY_UNRESOLVED", lambda: _part_geometry(
            part, index, origins[index], witness, nc_origin, tolerance, nc_tolerance, observations), [])
        if "id" not in result:
            gap = {"id": "part", "kind": "PART", "sourceRefs": [f"#/parts/{index}"],
                   "evidenceStatus": "UNRESOLVED", "proofStatus": "INSUFFICIENT",
                   "missingProofs": ["PART_GEOMETRY_UNRESOLVED"], "ncEvidence": [], "ncRole": "NOT_NEEDED"}
            result.update(id=part["id"], partPosition=origins[index]["partPosition"],
                          geometryStatus="UNRESOLVED", manufacturingStatus="BLOCKED",
                          nativeSource=origins[index], ncSource=nc_origin, evidenceSufficiency="INSUFFICIENT",
                          featureEvidence=[gap], ncCheck={"status": "SKIPPED", "reason": "NATIVE_GEOMETRY_UNRESOLVED"},
                          issues=[{"code": "PART_GEOMETRY_UNRESOLVED", "message": result["reason"]}],
                          remainingProofs=["PART_GEOMETRY_UNRESOLVED"])
        # Finite raw doubles can still overflow derived arithmetic; never serialize a false match.
        try:
            json.dumps(result, allow_nan=False)
        except ValueError:
            result = {"id": part["id"], "geometryStatus": "UNRESOLVED", "manufacturingStatus": "BLOCKED",
                      "evidenceSufficiency": "INSUFFICIENT",
                      "issues": [{"code": "NONFINITE_DERIVATION", "message": "Derived geometry is not finite"}]}
        result.setdefault('partPosition',origins[index]['partPosition'])
        result.setdefault('commonShapeFacts',dict(status='UNRESOLVED',sourceRefs=[f'#/parts/{index}']))
        result.setdefault('partType',dict(type='UNKNOWN',status='UNRESOLVED',route=None,
                                         basis='GEOMETRIC_EVIDENCE_UNAVAILABLE',sourceRefs=[f'#/parts/{index}']))
        result.setdefault('blockedStage','SOURCE' if origins[index]['status']!='READ_CONSISTENT' else 'COMMON_GEOMETRY')
        result.setdefault('specializedGeometryStatus','UNRESOLVED')
        parts.append(result)
    return {"schemaVersion": "nativeGeometryAudit.v1", "algorithmVersion": "r2b.13",
            "holeObservations": hole_observations,
            "inputDigest": digest, "captureDigest": digest if capture.get("schemaVersion") == "teklaNcCapture.v1" else None,
            "nativeEvidenceSha256": native_sha256,
            "modelToleranceMm": tolerance, "ncToleranceMm": nc_tolerance,
            "maxWitnessResolutionMm": resolution,
            "meaning": "Analytic candidates checked against available evidence; not exact BREP or manufacturing proof",
            "manufacturingStatus": "BLOCKED", "parts": parts,
            "matchedCount": sum(p["geometryStatus"] == "EVIDENCE_MATCHED" for p in parts),
            "unresolvedCount": sum(p["geometryStatus"] != "EVIDENCE_MATCHED" for p in parts)}


def load_geometry_audit(path, model_tolerance_mm=DEFAULT_MODEL_TOLERANCE_MM, nc_tolerance_mm=None, max_resolution_mm=None,
                        hole_observations_path=None):
    data = Path(path).read_bytes()
    observations = (json.loads(Path(hole_observations_path).read_text(encoding='utf-8-sig'))
                    if hole_observations_path else None)
    result = audit_native_geometry(json.loads(data.decode("utf-8-sig")),
                                   model_tolerance_mm, nc_tolerance_mm, max_resolution_mm, observations)
    if result["nativeEvidenceSha256"] is None:
        result["nativeEvidenceSha256"] = hashlib.sha256(data).hexdigest()
    return result
