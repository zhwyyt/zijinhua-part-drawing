"""Manufacturing requirements from audited native material, without legacy VERIFIED flags."""

import json
import math

from .expression_requirements import (content_digest, arc_contains, _requirements,
                                      _pitch_candidates, _specifications, GEOMETRY_TOLERANCE)
from .native_geometry import audit_native_geometry, DEFAULT_MODEL_TOLERANCE_MM
from .nc_witness import _bytes


RULES_VERSION = 'native-flat-plate-expression.r1'


def _facts(material):
    points, segments, holes, refs, loops = {}, [], [], {}, []

    def boundary(shape, prefix):
        ring = shape['segments']
        identities = []
        for i, segment in enumerate(ring):
            identity = prefix+'edge:'+str(i)
            start_id, end_id = prefix+'vertex:'+str(i), prefix+'vertex:'+str((i+1) % len(ring))
            item = {'id': identity, 'kind': segment['kind'], 'start': segment['start'], 'end': segment['end'],
                    'startPoint': start_id, 'endPoint': end_id}
            points[start_id] = segment['start']
            if segment['kind'] == 'ARC':
                item.update(center=segment['center'], radius=segment['radius'], clockwise=segment['sweepRadians'] < 0)
                for cardinal in range(4):
                    angle = cardinal*math.pi/2
                    p = [item['center'][0]+item['radius']*math.cos(angle),
                         item['center'][1]+item['radius']*math.sin(angle)]
                    if arc_contains(item, p):
                        points[identity+':extreme:'+str(cardinal)] = p
            segments.append(item)
            refs[identity] = segment['sourceRefs']
            identities.append(identity)
        loops.append({'id': prefix or 'outer', 'segments': identities})

    boundary(material['outline'], '')
    outer_points = list(points.values())
    bounds = [min(p[0] for p in outer_points), min(p[1] for p in outer_points),
              max(p[0] for p in outer_points), max(p[1] for p in outer_points)]
    datum = min((k for k in points if k.startswith('vertex:')), key=lambda k: (points[k], k))
    for i, hole in enumerate(material['holes']):
        identity = 'material-result:'+str(i)
        if hole['kind'] not in ('ROUND', 'SLOT'):
            boundary(hole, 'cutout:'+str(i)+':')
            continue
        point_id = 'hole:'+identity
        points[point_id] = hole['center']
        item = {'id': identity, 'pointId': point_id, 'kind': hole['kind']}
        if hole['kind'] == 'ROUND':
            item['diameter'] = hole['diameter']
        else:
            item.update(width=hole['diameter'], length=hole['diameter']+hole['extension'],
                        angle=math.degrees(math.atan2(*reversed(hole['direction']))) % 180)
        # fastener False is a hole-only opening: ordinary nΦ / n×D, even when
        # the parameters were read from a bolt group. Absent means the older
        # material path, which treats every bolt-group source as a bolt hole.
        if hole.get('fastener') is True or (
                hole.get('fastener') is None and any('/boltGroups/' in ref for ref in hole.get('sourceRefs', []))):
            item['origin'] = 'BOLT'
        holes.append(item)
        refs[point_id] = hole['sourceRefs']
    return {'points': points, 'segments': segments, 'holes': holes, 'datum': datum, 'bounds': bounds,
            'boundaryLoops': loops, 'sourceFeatureRefs': refs}


def build_native_expression_requirements(evidence, model_tolerance_mm=DEFAULT_MODEL_TOLERANCE_MM,
                                        nc_tolerance_mm=None, max_resolution_mm=None,
                                        hole_observations=None, specifications=None):
    """Recompute the gate from raw evidence; never accept an externally asserted audit status.

    READY permits annotation planning only. Snapshot freshness, native writing,
    persistence and independent drawing QA remain separate prerequisites.
    """
    audit = audit_native_geometry(evidence, model_tolerance_mm, nc_tolerance_mm,
                                 max_resolution_mm, hole_observations)
    raw = (json.loads(_bytes(evidence['before']).decode('utf-8-sig'))
           if evidence['schemaVersion'] == 'teklaNcCapture.v1' else evidence)
    specs, spec_issues = _specifications(specifications)
    model = raw['model']['value']
    model_identity = content_digest(model)
    plans = []
    for part in audit['parts']:
        material = part.get('materialFinalState', {})
        issues = list(spec_issues)
        if part.get('partType', {}).get('route') != 'FLAT_PLATE':
            issues.extend(i for i in part.get('issues',[]) if i['code'].startswith('PART_TYPE_'))
        if (material.get('status') != 'MATCHED' or part.get('evidenceSufficiency') != 'SUFFICIENT'
                or part.get('geometryStatus') != 'EVIDENCE_MATCHED'):
            issues.append({'code': 'NATIVE_MATERIAL_INSUFFICIENT', 'feature': 'plate',
                           'message': material.get('reason', 'Native feature evidence is incomplete or contradictory'),
                           'missingProofs': part.get('remainingProofs', [])})
        facts = _facts(material) if not issues else {}
        from .section_plan import section_requirements
        sections = []
        if part.get('sectionGeometry', {}).get('status') == 'MATCHED':
            try:
                sections = section_requirements(part['sectionGeometry'], part['frame'],
                    material.get('holes', []), audit['modelToleranceMm'])
            except ValueError as error:
                issues.append(dict(code='SECTION_REQUIREMENTS_UNRESOLVED', message=str(error)))
        requirements, groups = (_requirements(part['partPosition'], part['thickness']['value'], facts, specs)
                                if facts else ([], []))
        source = {'modelIdentifier': model_identity, 'partId': str(part['id']),
                  'partPosition': part.get('partPosition'), 'nativeInputDigest': audit['inputDigest']}
        digest = content_digest({'source': source, 'modelToleranceMm': audit['modelToleranceMm'],
                                 'ncToleranceMm': audit['ncToleranceMm'], 'resolution': audit['maxWitnessResolutionMm'],
                                 'holeObservations': hole_observations, 'specifications': specifications,
                                 'auditVersion': audit['algorithmVersion'], 'rules': RULES_VERSION})
        plans.append({'schemaVersion': 'expressionRequirements.v1', 'rulesVersion': RULES_VERSION,
                      'source': source, 'sourceDigest': digest, 'status': 'BLOCKED' if issues else 'READY',
                      'partType': part.get('partType'), 'blockedStage': part.get('blockedStage'),
                      'issues': issues, 'tolerance': GEOMETRY_TOLERANCE,
                      'modelToleranceMm': audit['modelToleranceMm'], 'displayDecimals': 1,
                      'facts': facts, 'groups': groups, 'requirements': requirements,
                      'pitchCandidates': _pitch_candidates(facts, groups) if facts else [],
                      'specifications': specs,
                      'unspecified': [{'aspect': n, 'status': 'NOT_SPECIFIED'} for n in
                                      ('cutting_method', 'hole_method', 'tolerance', 'edge_finish') if n not in specs],
                      'materialEvidence': {'status': material.get('status'), 'basis': material.get('basis'),
                                           'sourceMappings': material.get('operationMappings', [])},
                      'remainingGates': ['RECHECK_BEFORE_DRAWING', 'PLAN_COVERAGE', 'NATIVE_WRITE_READBACK', 'VISUAL_QA'],
                      'scope': 'GEOMETRIC_EXPRESSION_ONLY; no legacy VERIFIED context or manufacturing release'})
        if sections:
            plans[-1].update(schemaVersion='expressionRequirements.v2', sectionRequirements=sections,
                             requiredViews=['main']+[s['id'] for s in sections])
    return {'schemaVersion': 'nativeExpressionBatch.v1', 'geometryAudit': audit, 'parts': plans,
            'readyCount': sum(p['status'] == 'READY' for p in plans),
            'blockedCount': sum(p['status'] != 'READY' for p in plans)}
