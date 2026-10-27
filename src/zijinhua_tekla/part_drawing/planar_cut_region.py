"""Analytic planar difference for through cuts; never fits SDK chords to curves."""

from copy import deepcopy
import math

from .analytic_geometry import (add, sub, mul, norm, unit, dot, cross, line, arc,
                                _at, parameter, intersections, inside_ring, simple_ring,
                                hole_segments, check_hole_topology, match_loop)


def circle_shape(segments, tolerance):
    if not segments or any(s['kind'] != 'ARC' for s in segments):
        return None
    first = segments[0]
    radius, center = first['radius'], first['center']
    if (any(norm(sub(s['center'], center)) > tolerance or abs(s['radius']-radius) > tolerance
            or s['sweepRadians']*first['sweepRadians'] <= 0 for s in segments)
            or abs(abs(sum(s['sweepRadians'] for s in segments))-2*math.pi)*radius > tolerance):
        return None
    return {'kind': 'ROUND', 'center': center, 'diameter': 2*radius, 'extension': 0,
            'direction': [1, 0]}


def canonical_ring(segments, tolerance, owner):
    result = deepcopy(segments)
    simple_ring(result, tolerance)
    circle = circle_shape(result, tolerance)
    if circle:
        refs = sorted({r for s in result for r in s['sourceRefs']})
        result = hole_segments(circle['center'], circle['diameter'], 0, [1, 0], refs)
    for segment in result:
        segment['ownerIds'] = [owner]
    return result


def _length(s):
    return norm(sub(s['end'], s['start'])) if s['kind'] == 'LINE' else s['radius']*abs(s['sweepRadians'])


def _fragment(s, start, end):
    a, b = _at(s, start), _at(s, end)
    result = (line(a, b, s['sourceRefs']) if s['kind'] == 'LINE' else
              arc(a, b, s['center'], s['sweepRadians']*(end-start), s['sourceRefs']))
    result['ownerIds'] = s['ownerIds'][:]
    return result


def _reverse(s):
    result = deepcopy(s)
    result['start'], result['end'] = result['end'], result['start']
    if result['kind'] == 'ARC':
        result['sweepRadians'] *= -1
    return result


def _crossings(a, b, eps):
    # Collinear/co-circular overlaps are split at both domains' endpoints.
    common = False
    if a['kind'] == b['kind'] == 'LINE':
        u, v = unit(sub(a['end'], a['start'])), unit(sub(b['end'], b['start']))
        common = abs(cross(u, v)) <= 1e-10 and abs(cross(sub(b['start'], a['start']), u)) <= eps
    elif a['kind'] == b['kind'] == 'ARC':
        common = norm(sub(a['center'], b['center'])) <= eps and abs(a['radius']-b['radius']) <= eps
    if common:
        return [p for p in (a['start'], a['end'], b['start'], b['end'])
                if parameter(p, a, eps) is not None and parameter(p, b, eps) is not None]
    return intersections(a, b, eps)


def _merge(ring, eps):
    changed = True
    while changed and len(ring) > 2:
        changed = False
        for i, a in enumerate(ring):
            j = (i+1) % len(ring)
            b = ring[j]
            if a['kind'] != b['kind'] or a['ownerIds'] != b['ownerIds']:
                continue
            common = (abs(cross(unit(sub(a['end'], a['start'])), unit(sub(b['end'], b['start'])))) <= 1e-10
                      and dot(sub(a['end'], a['start']), sub(b['end'], b['start'])) > 0) if a['kind'] == 'LINE' else (
                      norm(sub(a['center'], b['center'])) <= eps and abs(a['radius']-b['radius']) <= eps
                      and a['sweepRadians']*b['sweepRadians'] > 0)
            if not common:
                continue
            refs = sorted(set(a['sourceRefs']+b['sourceRefs']))
            merged = (line(a['start'], b['end'], refs) if a['kind'] == 'LINE' else
                      arc(a['start'], b['end'], a['center'], a['sweepRadians']+b['sweepRadians'], refs))
            merged['ownerIds'] = a['ownerIds'][:]
            ring[i] = merged
            ring.pop(j)
            changed = True
            break
    return ring


def _area(ring):
    return sum(cross(s['start'], s['end']) if s['kind'] == 'LINE' else
               cross(s['center'], sub(s['end'], s['start']))+s['radius']**2*s['sweepRadians']
               for s in ring)/2


def _slot_shape(ring, tolerance):
    arcs = [s for s in ring if s['kind'] == 'ARC']
    if len(ring) != 4 or len(arcs) != 2:
        return None
    a, b = arcs
    delta = sub(b['center'], a['center'])
    extension = norm(delta)
    if (extension <= tolerance or abs(a['radius']-b['radius']) > tolerance or
            any(abs(abs(s['sweepRadians'])-math.pi)*s['radius'] > tolerance for s in arcs)):
        return None
    center, diameter, direction = mul(add(a['center'], b['center']), .5), 2*a['radius'], unit(delta)
    points = [p for s in ring for p in ([s['start'], _at(s, .5)] if s['kind'] == 'ARC' else [s['start']])]
    try:
        match_loop(points, hole_segments(center, diameter, extension, direction), tolerance)
    except ValueError:
        return None
    return {'kind': 'SLOT', 'center': center, 'diameter': diameter, 'extension': extension, 'direction': direction}


def cut_region(outline, cuts, tolerance, part_id):
    """Return one material region with complete analytic source attribution."""
    cuts = sorted(cuts, key=lambda c: c['id'])
    eps = min(tolerance/1000, 1e-6)
    rings = [canonical_ring(outline['segments'], tolerance, part_id)]
    rings += [canonical_ring(c['segments'], tolerance, c['id']) for c in cuts]
    flattened = [(ri, s) for ri, ring in enumerate(rings) for s in ring]
    params = [[0., 1.] for _ in flattened]
    for i, (ri, a) in enumerate(flattened):
        for j in range(i+1, len(flattened)):
            rj, b = flattened[j]
            if ri == rj:
                continue
            for point in _crossings(a, b, eps):
                for index, segment in ((i, a), (j, b)):
                    t = parameter(point, segment, eps)
                    if t is not None:
                        params[index].append(t)

    def material(point):
        return inside_ring(point, rings[0], eps/8) and not any(inside_ring(point, r, eps/8) for r in rings[1:])

    retained = []
    for (_, s), values in zip(flattened, params):
        ordered = sorted(values)
        unique = [ordered[0]]
        for t in ordered[1:]:
            if (t-unique[-1])*_length(s) > eps:
                unique.append(t)
        for low, high in zip(unique, unique[1:]):
            piece = _fragment(s, low, high)
            if _length(piece) <= 64*eps:
                raise ValueError('Cut intersection creates an ambiguous tiny boundary fragment')
            midpoint = _at(piece, .5)
            tangent = (unit(sub(piece['end'], piece['start'])) if piece['kind'] == 'LINE' else
                       unit(mul([-midpoint[1]+piece['center'][1], midpoint[0]-piece['center'][0]],
                                1 if piece['sweepRadians'] > 0 else -1)))
            offset = mul([-tangent[1], tangent[0]], 8*eps)
            left, right = material(add(midpoint, offset)), material(sub(midpoint, offset))
            if left == right:
                continue
            if not left:
                piece = _reverse(piece)
            duplicates = [p for p in retained if p['kind'] == piece['kind'] and
                          all(norm(sub(_at(p, t), _at(piece, t))) <= eps for t in (0, .5, 1))]
            if duplicates:
                duplicates[0]['ownerIds'] = sorted(set(duplicates[0]['ownerIds']+piece['ownerIds']))
                duplicates[0]['sourceRefs'] = sorted(set(duplicates[0]['sourceRefs']+piece['sourceRefs']))
            else:
                retained.append(piece)
    if not retained:
        raise ValueError('Cuts remove the whole plate or leave no supported boundary')
    loops = []
    while retained:
        chain = [retained.pop(0)]
        while norm(sub(chain[-1]['end'], chain[0]['start'])) > eps:
            matches = [i for i, s in enumerate(retained) if norm(sub(chain[-1]['end'], s['start'])) <= eps]
            if len(matches) != 1:
                raise ValueError('Disconnected or branching cut boundary')
            chain.append(retained.pop(matches[0]))
        loops.append(_merge(chain, eps))
    positive = [r for r in loops if _area(r) > 0]
    negative = [r for r in loops if _area(r) < 0]
    if len(positive) != 1 or len(positive)+len(negative) != len(loops):
        raise ValueError('Cut result is not a single connected plate')
    # Normalize hole orientation for the existing analytic-domain matcher.
    holes = [[_reverse(s) for s in reversed(r)] for r in negative]
    ordered = positive+holes
    check_hole_topology(positive[0], [{'segments': r} for r in holes], tolerance)
    shapes = []
    for index, ring in enumerate(ordered):
        shape = circle_shape(ring, tolerance) or _slot_shape(ring, tolerance) or {'kind': 'OUTLINE' if index == 0 else 'CUTOUT'}
        shape.update(id='boundary:'+str(index), segments=ring,
                     sourceRefs=sorted({r for s in ring for r in s['sourceRefs']}),
                     ownerIds=sorted({o for s in ring for o in s['ownerIds']}))
        shapes.append(shape)
    return {'outline': shapes[0], 'holes': shapes[1:], 'operationMappings': [
        {'operationId': c['id'], 'boundaryIds': [s['id'] for s in shapes if c['id'] in s['ownerIds']],
         'status': 'BOUNDARY_CONTRIBUTOR' if any(c['id'] in s['ownerIds'] for s in shapes)
                   else 'NO_EXPOSED_BOUNDARY'} for c in cuts]}
