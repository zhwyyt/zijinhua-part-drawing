"""Additional cutter proofs. No drawing calls or mutation of the legacy engine."""

import math
import re

from ..analytic_geometry import (add, sub, mul, dot, norm, unit, frame_axes, project,
                                line, short_arc, hole_segments, simple_ring)
from ..solid_prisms import value, match_prisms
from ..planar_cut_region import canonical_ring
from .contracts import require


VERSION = 'independent-cut-geometry.r1'
JOIN_EPS = 1e-6


def tangent_contour(points, chamfers, refs):
    """Allow exactly consumed straight runs, never overlapping corner arcs."""
    require(len(points) >= 3 and len(points) == len(chamfers) == len(refs), 'CUT_CONTOUR', 'Invalid contour')
    incoming, outgoing, arcs = list(points), list(points), {}
    for i, (kind, radius, other) in enumerate(chamfers):
        if kind == 'CHAMFER_NONE' or (kind == 'CHAMFER_ROUNDING' and radius == other == 0):
            continue
        require(kind == 'CHAMFER_ROUNDING' and radius > 0, 'CUT_CHAMFER', 'Only tangent rounding supported')
        p = points[i]
        u, v = unit(sub(points[i-1], p)), unit(sub(points[(i+1) % len(points)], p))
        half = math.acos(max(-1, min(1, dot(u, v)))) / 2
        require(1e-8 < half < math.pi/2-1e-8, 'CUT_CORNER', 'Degenerate corner')
        distance = radius / math.tan(half)
        require(all(distance <= norm(sub(q, p))+JOIN_EPS for q in (points[i-1], points[(i+1) % len(points)])),
                'CUT_OVERLAP', 'Rounding exceeds adjacent edge')
        incoming[i], outgoing[i] = add(p, mul(u, distance)), add(p, mul(v, distance))
        center = add(p, mul(unit(add(u, v)), radius / math.sin(half)))
        arcs[i] = short_arc(incoming[i], outgoing[i], center, [refs[i]])
    segments = []
    for i in range(len(points)):
        if i in arcs:
            segments.append(arcs[i])
        j = (i+1) % len(points)
        delta = sub(incoming[j], outgoing[i])
        require(dot(delta, unit(sub(points[j], points[i]))) >= -JOIN_EPS,
                'CUT_OVERLAP', 'Adjacent roundings overlap')
        if norm(delta) > JOIN_EPS:
            segments.append(line(outgoing[i], incoming[j], [refs[i], refs[j]]))
    simple_ring(segments, JOIN_EPS)
    return segments


def _plate(operation, frame, path, tolerance):
    operative = value(operation['operativeGeometry'])
    own = value(operative['coordinateSystem'])
    contour = value(value(operative['modelingContour']))
    points = [project(c['point'], own) for c in contour]
    require(max(p[2] for p in points)-min(p[2] for p in points) <= tolerance,
            'CUT_NONPLANAR', 'Cutter contour is not planar')
    chamfers = []
    for c in contour:
        ch = c['chamfer']
        require(value(ch['DZ1']) == value(ch['DZ2']) == 0, 'CUT_DEPTH_CHAMFER', 'Partial thickness chamfer')
        chamfers.append((value(ch['Type'])['name'], value(ch['X']), value(ch['Y'])))
    refs = [path+f'/operativeGeometry/value/modelingContour/value/value/{i}' for i in range(len(points))]
    ring = tangent_contour([p[:2] for p in points], chamfers, refs)
    proof = match_prisms(value(operation['operativeSolid']), [dict(kind='OUTLINE', segments=ring)], own,
                         tolerance, path+'/operativeSolid/value', origin_id=value(operation['operativePart'])['id'])
    axes, target = frame_axes(own), frame_axes(frame)
    cosine = dot(axes[2], target[2])
    tilt = math.hypot(dot(axes[0], target[2]), dot(axes[1], target[2]))
    extent = max(norm(s['start'])+2*s.get('radius', 0) for s in ring)
    require(abs(cosine) > .5 and tilt*extent <= tolerance, 'CUT_TILT', 'Cutter needs section geometry')
    def xy(p):
        world = [own['origin'][i]+axes[0][i]*p[0]+axes[1][i]*p[1]+axes[2][i]*points[0][2] for i in range(3)]
        return project(world, frame)[:2]
    transformed = []
    for s in ring:
        item = dict(s, start=xy(s['start']), end=xy(s['end']))
        if s['kind'] == 'ARC':
            item.update(center=xy(s['center']), sweepRadians=s['sweepRadians']*(1 if cosine > 0 else -1))
        transformed.append(item)
    origin_z = project(own['origin'], frame)[2]
    levels = sorted(origin_z+z*cosine for z in proof['levels'])
    return transformed, proof, [levels[0]+tilt*extent, levels[1]-tilt*extent]


def _cylinder(operation, frame, path, tolerance):
    operative = value(operation['operativeGeometry'])
    match = re.fullmatch(r'D(\d+(?:\.\d+)?)', value(operative['profile']))
    require(match is not None, 'CUT_PROFILE', 'Only solid circular D profiles supported')
    own = value(operative['coordinateSystem'])
    axes, target = frame_axes(own), frame_axes(frame)
    require(abs(abs(dot(axes[0], target[2]))-1) <= 1e-10, 'CUT_TILT', 'Cylinder is not normal to plate')
    center, diameter = project(own['origin'], frame)[:2], float(match[1])
    require(diameter > 0, 'CUT_DIAMETER', 'Invalid cylinder diameter')
    ring = hole_segments(center, diameter, 0, [1, 0], [path+'/operativeGeometry/value/profile'])
    shape = dict(kind='ROUND', center=center, diameter=diameter, segments=ring)
    proof = match_prisms(value(operation['operativeSolid']), [shape], frame, tolerance,
                         path+'/operativeSolid/value', origin_id=value(operation['operativePart'])['id'])
    return ring, proof, proof['levels']


def prove_additional_cut(operation, part, frame, path, tolerance):
    require(operation['runtimeType'] == 'Tekla.Structures.Model.BooleanPart' and
            value(operation['type'])['name'] == 'BOOLEAN_CUT', 'CUT_TYPE', 'Subtractive Boolean required')
    kind = value(operation['operativeGeometry'])['runtimeType']
    require(kind in ('Tekla.Structures.Model.ContourPlate', 'Tekla.Structures.Model.Beam'),
            'CUT_TYPE', 'Unsupported cutter type')
    ring, proof, depths = (_plate if kind.endswith('ContourPlate') else _cylinder)(operation, frame, path, tolerance)
    levels = [project(p, frame)[2] for f in value(value(part['finalSolid'])['faces'])
              for loop in value(f['loops']) for p in value(loop['vertices'])]
    low, high = min(levels), max(levels)
    require(high-low > tolerance and depths[0] <= low+tolerance and depths[1] >= high-tolerance,
            'CUT_BLIND', 'Cutter does not span full thickness')
    return dict(id=operation['id'], status='CANDIDATE', sourceRef=path,
                segments=canonical_ring(ring, tolerance, operation['id']), operativeSolidCheck=proof,
                targetLevels=[low, high], proofVersion=VERSION,
                sideOriginIds=sorted({operation['id'], value(operation['operativePart'])['id']}))
