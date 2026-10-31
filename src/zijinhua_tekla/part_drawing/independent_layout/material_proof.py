"""Analytic attribution of an observed shell, checked against cutter subtraction."""

import math

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import set_precision

from ..analytic_geometry import project, parameter, line, arc, simple_ring, _at, check_hole_topology
from ..solid_prisms import value
from ..native_geometry import _match_cut_solid, _known_cut_children, _hole_effect
from ..planar_cut_region import circle_shape
from .contracts import require
from .cut_geometry import prove_additional_cut


VERSION = 'independent-material.r1'


def polygon(ring, tolerance):
    """Chord budget is 1/20 of the recorded model budget, not a new tolerance."""
    points = []
    for s in ring:
        count = 1
        if s['kind'] == 'ARC':
            step = 2*math.acos(max(-1, 1-min(tolerance/20, s['radius']/2)/s['radius']))
            count = max(2, math.ceil(abs(s['sweepRadians'])/step))
        points.extend(_at(s, i/count) for i in range(count))
    result = Polygon(points)
    require(result.is_valid and result.area > 0, 'MATERIAL_TOPOLOGY', 'Invalid analytic polygon')
    # Only the numeric comparison domain is quantized. Analytic facts and SDK
    # coordinates remain unchanged; displacement is bounded inside the budget.
    result = set_precision(result, tolerance/1000)
    require(result.geom_type == 'Polygon' and not result.is_empty, 'MATERIAL_PRECISION', 'Degenerate comparison domain')
    return result


def _attributed_ring(points, sources, tolerance):
    runs = []
    for a, b in zip(points, points[1:]+points[:1]):
        matches = []
        for i, s in enumerate(sources):
            ta, tb = parameter(a, s, tolerance), parameter(b, s, tolerance)
            if ta is not None and tb is not None and abs(ta-tb) > 1e-10:
                matches.append((i, ta, tb))
        require(len(matches) == 1, 'BOUNDARY_SOURCE', 'Missing or ambiguous analytic edge attribution')
        i, ta, tb = matches[0]
        if runs and runs[-1][0] == i and (runs[-1][4]-runs[-1][3])*(tb-ta) > 0:
            runs[-1][2], runs[-1][4] = b, tb
        else:
            runs.append([i, a, b, ta, tb])
    if len(runs)>1 and runs[0][0]==runs[-1][0] and (runs[0][4]-runs[0][3])*(runs[-1][4]-runs[-1][3])>0:
        runs[0][1], runs[0][3] = runs[-1][1], runs[-1][3]
        runs.pop()
    ring = []
    for i, a, b, ta, tb in runs:
        s = sources[i]
        item = (line(a, b, s['sourceRefs']) if s['kind']=='LINE' else
                arc(a, b, s['center'], s['sweepRadians']*(tb-ta), s['sourceRefs']))
        if s['kind']=='ARC':
            # Source radius is independent of SDK vertex roundoff.
            item['radius'] = s['radius']
        ring.append(dict(item, ownerIds=s['ownerIds']))
    simple_ring(ring, tolerance)
    return ring


def _observed_region(part, geometry, cuts, path, tolerance):
    frame = geometry['frame']
    sources = [dict(s, ownerIds=[part['id']]) for s in geometry['outline']['segments']]
    sources += [dict(s, ownerIds=[c['id']]) for c in cuts for s in c['segments']]
    faces = value(value(part['finalSolid'])['faces'])
    caps = []
    for f in faces:
        loops = [[project(p, frame) for p in value(l['vertices'])] for l in value(f['loops'])]
        zs = [p[2] for loop in loops for p in loop]
        if zs and max(zs)-min(zs)<=tolerance:
            caps.append(loops)
    require(len(caps)==2, 'MATERIAL_CAPS', 'Exactly two full thickness caps required')
    shapes = []
    for i, loop in enumerate(caps[0]):
        ring = _attributed_ring([p[:2] for p in loop], sources, tolerance)
        shape = circle_shape(ring, tolerance) or dict(kind='OUTLINE' if i==0 else 'CUTOUT')
        shape.update(segments=ring, sourceRefs=sorted({r for s in ring for r in s['sourceRefs']}),
                     ownerIds=sorted({o for s in ring for o in s['ownerIds']}))
        shapes.append(shape)
    region = dict(outline=shapes[0], holes=shapes[1:])
    shell = _match_cut_solid(part, region, cuts, frame, path, tolerance)
    expected = polygon(geometry['outline']['segments'], tolerance).difference(
        unary_union([polygon(c['segments'], tolerance) for c in cuts]))
    actual = polygon(shapes[0]['segments'], tolerance).difference(
        unary_union([polygon(h['segments'], tolerance) for h in shapes[1:]]))
    require(expected.geom_type == actual.geom_type == 'Polygon' and expected.is_valid and
            len(expected.interiors)==len(actual.interiors), 'MATERIAL_TOPOLOGY', 'Cut subtraction topology differs')
    distance = expected.boundary.hausdorff_distance(actual.boundary)
    require(distance <= tolerance*.8 and actual.difference(expected.buffer(tolerance*.8)).is_empty and
            expected.difference(actual.buffer(tolerance*.8)).is_empty,
            'MATERIAL_DOMAIN', f'Observed material contradicts full cutter subtraction: boundary distance {distance} mm')
    return region, shell, dict(method='ANALYTIC_POLYGON_SUBTRACTION', chordBudgetMm=tolerance/20, precisionGridMm=tolerance/1000,
                               maxBoundaryDistanceMm=distance, acceptanceBudgetMm=tolerance*.8)


def _holes(part, geometry, region, shell, path, tolerance):
    holes = list(region['holes'])
    mappings, evidence = [], []
    for bolt, group in zip(value(part['boltGroups']), geometry['holeGroups']):
        if group['classification']=='ATTACHMENT_STUD':
            continue
        require(isinstance(group['candidates'], list), 'HOLE_PARAMETERS', 'Missing hole facts')
        targets = bolt['targets']
        ids = [value(targets[k]) for k in ('partToBoltTo','partToBeBolted')] + value(targets['otherParts'])
        require(part['id'] in [p['id'] for p in ids if p], 'HOLE_TARGET', 'Wrong target')
        fastener = value(bolt['properties']['Bolt'])
        require(type(fastener) is bool, 'HOLE_TYPE', 'Unknown bolt type')
        proof = (dict(status='RULE_CONFIRMED_THROUGH_HOLE', rule='BOLT-THROUGH-01') if fastener else
                 _hole_effect(bolt, part['id'], group['candidates'], geometry['frame'], tolerance, group['sourceRef'], shell))
        evidence.append(dict(proof, groupId=group['id']))
        for h in group['candidates']:
            shape = dict(h, ownerIds=[bolt['id']], fastener=fastener)
            hp = polygon(h['segments'], tolerance)
            matches = [i for i, existing in enumerate(holes)
                       if existing['kind'] in ('ROUND','SLOT') and
                       hp.boundary.hausdorff_distance(polygon(existing['segments'], tolerance).boundary)<=tolerance*.8]
            require(len(matches)<=1, 'HOLE_AMBIGUOUS', 'Multiple matching openings')
            if matches:
                i = matches[0]
                holes[i] = dict(holes[i], fastener=fastener,
                                sourceRefs=sorted(set(holes[i]['sourceRefs']+h['sourceRefs'])))
                mappings.append(dict(sourceGroupId=bolt['id'], sourceHoleId=h['id'], boundaryIndex=i,
                                     status='MATCHED_EXISTING_OPENING'))
            else:
                require(fastener, 'HOLE_OBSERVATION', 'Hole-only group lacks final opening')
                material = polygon(region['outline']['segments'], tolerance)
                require(material.contains(hp) and all(hp.distance(polygon(x['segments'], tolerance))>tolerance for x in holes),
                        'HOLE_INTERACTION', 'Intersecting/contained hole needs union proof')
                holes.append(shape)
                mappings.append(dict(sourceGroupId=bolt['id'], sourceHoleId=h['id'], boundaryIndex=len(holes)-1,
                                     status='RULE_CONFIRMED_NEW_HOLE'))
    check_hole_topology(region['outline']['segments'], holes, tolerance)
    return holes, mappings, evidence


def prove_material(part, geometry, path, tolerance):
    require(geometry['nativeSource']['status']=='READ_CONSISTENT' and
            geometry['partType']['route']=='FLAT_PLATE', 'MATERIAL_SOURCE', 'Consistent flat plate source required')
    require(geometry.get('ncSource',{}).get('status')=='NOT_REQUESTED', 'MATERIAL_NC', 'NC requires separate reconciliation')
    require(not any('CONFLICT' in i['code'] for i in geometry['issues']), 'MATERIAL_CONFLICT', 'Preserve upstream conflict')
    operations = value(part['operations'])
    require(_known_cut_children(operations,value(part['children'])) and
            len({o['id'] for o in operations})==len(operations), 'MATERIAL_OPERATIONS', 'Unknown/duplicate operation')
    require(len(value(part['boltGroups']))==len(geometry['holeGroups']), 'HOLE_INVENTORY', 'Missing group')
    cuts = []
    for i, (op, old) in enumerate(zip(operations, geometry['planarCuts'])):
        cuts.append(old if old['status']=='CANDIDATE' else
                    prove_additional_cut(op, part, geometry['frame'],path+f'/operations/value/{i}', tolerance))
    require(len(cuts)==len(operations), 'MATERIAL_OPERATIONS', 'Missing cutter')
    region, shell, domain = _observed_region(part, geometry, cuts, path, tolerance)
    profile = value(part['profile'])
    require(profile.startswith('PL') and abs(float(profile[2:].split('*')[0])-shell['thickness'])<=tolerance and
            abs(value(part['reports']['PROFILE.WIDTH'])-shell['thickness'])<=tolerance,
            'MATERIAL_THICKNESS', 'Profile/report and complete shell thickness differ')
    holes, hole_mappings, evidence = _holes(part, geometry, region, shell, path, tolerance)
    mappings = [dict(operationId=c['id'], status='SUBTRACTION_AND_SHELL_PROVEN', sourceRef=c['sourceRef']) for c in cuts]
    return dict(status='MATCHED', proofVersion=VERSION, basis='INDEPENDENT_CUTTERS_AND_COMPLETE_NATIVE_SHELL',
                outline=region['outline'], holes=holes, solidCheck=shell, domainCheck=domain, cuts=cuts,
                operationMappings=mappings+hole_mappings, holeEvidence=evidence,
                modelToleranceMm=tolerance, remainingProofs=[])
