"""Machining dimensions for proved axis-aligned closed rectangular openings."""

import copy
import math

from ..expression_requirements import axis_aligned_arc
from .contracts import require, canonical_json


VERSION = 'independent-cutout-dimensions.r1'


def rectangular_loops(facts, tolerance):
    by_id = {s['id']:s for s in facts['segments']}
    supported, unsupported = [], []
    for loop in facts['boundaryLoops'][1:]:
        ring = [by_id[k] for k in loop['segments']]
        lines = [s for s in ring if s['kind']=='LINE']
        arcs = [s for s in ring if s['kind']=='ARC']
        axes = [next((a for a in (0,1) if abs(s['end'][1-a]-s['start'][1-a])<=tolerance and
                      abs(s['end'][a]-s['start'][a])>tolerance), None) for s in lines]
        rounded = len(arcs)==4 and all(axis_aligned_arc(s,tolerance) and
                    abs(math.dist(s['start'],s['end'])-math.sqrt(2)*s['radius'])<=tolerance for s in arcs)
        if len(lines)==4 and axes.count(0)==axes.count(1)==2 and (not arcs or rounded):
            supported.append((loop, ring, list(zip(lines,axes))))
        else:
            unsupported.append(dict(loopId=loop['id'], reason='RECTANGULAR_AXIS_ALIGNED_CONTRACT_REQUIRED'))
    return supported, unsupported


def _group(key, ids, axis, facts, role, evidence):
    points = sorted(ids,key=lambda k:(facts['points'][k][axis],k))
    return dict(key=key,points=points,axis=axis,side=1,allowedSides=[-1,1],lane=12,
                role=role,combined=None,localAllowed=True,cutoutEvidence=evidence)


def complete_cutout_scheme(plan, baseline):
    scheme = copy.deepcopy(baseline)
    facts, tol = plan['facts'], plan['modelToleranceMm']
    loops, issues = rectangular_loops(facts,tol)
    outer_ids = set(facts['boundaryLoops'][0]['segments'])
    outer = [s for s in facts['segments'] if s['id'] in outer_ids and s['kind']=='LINE']
    for loop, ring, lines in loops:
        intervals = []
        for s, axis in sorted(lines,key=lambda item:item[0]['id']):
            extent = sorted([s['start'][axis],s['end'][axis]])
            if any(axis==a and all(abs(x-y)<=tol for x,y in zip(extent,e)) for a,e in intervals):
                continue
            intervals.append((axis,extent))
            scheme['groups'].append(_group('cutout-size/'+s['id'],[s['startPoint'],s['endPoint']],axis,facts,
                                    'cutout_size',dict(loopId=loop['id'],segmentId=s['id'])))
        for axis in (0,1):
            choices = []
            for edge in outer:
                if abs(edge['end'][axis]-edge['start'][axis])>tol:
                    continue
                lo,hi = sorted([edge['start'][1-axis],edge['end'][1-axis]])
                for s in ring:
                    for key in (s['startPoint'],s['endPoint']):
                        point = facts['points'][key]
                        if not lo-tol<=point[1-axis]<=hi+tol:
                            continue
                        distance = abs(point[axis]-edge['start'][axis])
                        if distance<=tol:
                            continue
                        anchor = min((edge['startPoint'],edge['endPoint']),key=lambda k:(abs(facts['points'][k][1-axis]-point[1-axis]),k))
                        # Distances differing only by source noise prefer the high-side edge.
                        choices.append((round(distance/tol),-edge['start'][axis],edge['id'],key,anchor))
            if not choices:
                issues.append(dict(loopId=loop['id'],axis=axis,reason='REAL_LOCAL_DATUM_MISSING'))
                continue
            _,_,edge_id,key,anchor = min(choices)
            scheme['groups'].append(_group(f'cutout-location/{loop["id"]}/{axis}',[anchor,key],axis,facts,
                                    'cutout_location',dict(loopId=loop['id'],datumSegmentId=edge_id,featurePoint=key)))
    scheme.update(schemaVersion='dimensionScheme.v2',extensionVersion=VERSION,cutoutDiagnostics=issues)
    return scheme


def validate_cutout_scheme(plan, scheme, baseline):
    """Check obligations/anchors independently of the dimension selection routine."""
    facts,tol = plan['facts'],plan['modelToleranceMm']
    require(scheme.get('extensionVersion')==VERSION,'CUTOUT_SCHEME_VERSION','Unknown extension')
    base = {k:v for k,v in scheme.items() if k not in ('extensionVersion','cutoutDiagnostics')}
    base['schemaVersion']='dimensionScheme.v1'
    base['groups']=[g for g in scheme['groups'] if not g['key'].startswith('cutout-')]
    require(canonical_json(base)==canonical_json(baseline),'UPSTREAM_SCHEME_MISMATCH','Baseline changed')
    by_id = {s['id']:s for s in facts['segments']}
    loops, unsupported = rectangular_loops(facts,tol)
    expected_loops = {loop['id']: (ring, lines) for loop,ring,lines in loops}
    extras = [g for g in scheme['groups'] if g['key'].startswith('cutout-')]
    sizes,locations = [],set()
    for g in extras:
        e = g['cutoutEvidence'];loop_id=e['loopId'];axis=g['axis']
        require(loop_id in expected_loops and type(axis) is int and axis in (0,1), 'CUTOUT_SCOPE','Unknown loop/axis')
        require(len(g['points'])==2 and g['combined'] is None, 'CUTOUT_DIMENSION','One real interval required')
        if g['role']=='cutout_size':
            s=by_id[e['segmentId']]
            require(s in expected_loops[loop_id][0] and s['kind']=='LINE' and
                    set(g['points'])=={s['startPoint'],s['endPoint']} and
                    abs(s['start'][1-axis]-s['end'][1-axis])<=tol,'CUTOUT_SIZE','Not a real axial straight')
            sizes.append((loop_id,axis,sorted(facts['points'][p][axis] for p in g['points'])))
        else:
            require(g['role']=='cutout_location','CUTOUT_ROLE','Unknown responsibility')
            edge=by_id[e['datumSegmentId']];point=facts['points'][e['featurePoint']]
            require(edge['id'] in facts['boundaryLoops'][0]['segments'] and edge['kind']=='LINE' and
                    e['featurePoint'] in {s[k] for s in expected_loops[loop_id][0] for k in ('startPoint','endPoint')} and
                    set(g['points']) in ({edge['startPoint'],e['featurePoint']},{edge['endPoint'],e['featurePoint']}) and
                    abs(edge['start'][axis]-edge['end'][axis])<=tol and
                    min(edge['start'][1-axis],edge['end'][1-axis])-tol<=point[1-axis]<=max(edge['start'][1-axis],edge['end'][1-axis])+tol,
                    'CUTOUT_DATUM','Datum is not a real covering outer edge')
            require((loop_id,axis) not in locations,'CUTOUT_LOCATION_DUPLICATE','Repeated location')
            locations.add((loop_id,axis))
    errors = list(unsupported)
    for loop_id,(_,lines) in expected_loops.items():
        for s,axis in lines:
            extent=sorted([s['start'][axis],s['end'][axis]])
            if not any(loop_id==lid and axis==a and all(abs(x-y)<=tol for x,y in zip(extent,values)) for lid,a,values in sizes):
                errors.append(dict(code='CUTOUT_STRAIGHT_SIZE_MISSING',segmentId=s['id']))
        for axis in (0,1):
            if (loop_id,axis) not in locations:
                errors.append(dict(code='CUTOUT_LOCATION_MISSING',loopId=loop_id,axis=axis))
    return errors
