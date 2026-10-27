"""Conservative union candidates, with every source hole retained and mapped."""

from copy import deepcopy
from .analytic_geometry import norm, sub


def finished_hole_candidates(holes, tolerance, input_complete=True):
    """Reduce only exact duplicates or strict circle containment, never intersections."""
    ids = [h['id'] for h in holes]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate source hole identity')
    parents, relations, issues = {}, [], []
    ordered = sorted(holes, key=lambda h: h['id'])
    for i, a in enumerate(ordered):
        for b in ordered[i+1:]:
            if a['kind'] != 'ROUND' or b['kind'] != 'ROUND':
                continue  # General slot/boundary intersections remain the topology gate's job.
            distance = norm(sub(a['center'], b['center']))
            ra, rb = a['diameter']/2, b['diameter']/2
            if a['center'] == b['center'] and ra == rb:
                child, parent, kind = b, a, 'COINCIDENT'
            elif distance + min(ra, rb) < max(ra, rb) - tolerance:
                child, parent = (a, b) if ra < rb else (b, a)
                kind = 'CONTAINED'
            elif distance > ra + rb + tolerance:
                continue
            else:
                issues.append({'code': 'CIRCLE_UNION_UNRESOLVED', 'sourceIds': [a['id'], b['id']],
                               'reason': 'Partial intersection, tangency or tolerance-boundary ambiguity'})
                continue
            parents.setdefault(child['id'], []).append(parent['id'])
            relations.append({'kind': kind, 'sourceId': child['id'], 'containerId': parent['id']})

    def root(identity):
        if identity not in parents:
            return identity
        roots = {root(p) for p in parents[identity]}
        if len(roots) != 1:
            raise ValueError('Ambiguous maximal containing circle')
        return roots.pop()

    if not input_complete:
        issues.append({'code': 'SOURCE_FEATURES_INCOMPLETE'})
    mappings = []
    if not issues:
        try:
            mappings = [{'sourceId': h['id'], 'candidateId': root(h['id'])} for h in ordered]
        except ValueError as exc:
            issues.append({'code': 'CIRCLE_UNION_UNRESOLVED', 'reason': str(exc)})
    if issues:
        # Do not silently drop even an apparently contained source from an incomplete inventory.
        candidates = deepcopy(holes)
        mappings = [{'sourceId': h['id'], 'candidateId': h['id']} for h in ordered]
    else:
        retained = {m['candidateId'] for m in mappings}
        candidates = deepcopy([h for h in ordered if h['id'] in retained])
    for candidate in candidates:
        candidate['sourceHoleIds'] = [m['sourceId'] for m in mappings if m['candidateId'] == candidate['id']]
    return {'status': 'UNRESOLVED' if issues else 'CANDIDATE', 'basis': 'PARAMETER_UNION_NOT_EFFECT_PROOF',
            'inputComplete': input_complete, 'sourceCount': len(holes), 'candidateCount': len(candidates),
            'candidates': candidates, 'sourceMappings': mappings, 'relations': relations, 'issues': issues}
