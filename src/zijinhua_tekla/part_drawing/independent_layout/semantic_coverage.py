"""Independently check model-plane expression responsibilities, not rendered objects.

No adapter, candidate generator or old layout imports. A complete result does
not prove that a native drawing actually contains any of these objects.
"""

from collections import defaultdict, deque

from ..dimension_scheme import build_scheme, hole_callout_text
from ..expression_coverage import display_round
from ..expression_requirements import axis_aligned_arc
from ..plate_features import build_feature_model, outer_circle
from ..readability import load_profile
from .contracts import ContractError, canonical_json, check_seal, digest, require, seal


def _same(actual, expected, code):
    require(canonical_json(actual) == canonical_json(expected), code, 'Semantic content differs from source')


def _dimension(item, plan, scheme, profile):
    group = next(g for g in scheme['groups'] if g['key'] == item['schemeKey'])
    axis = group['axis']
    require(type(axis) is int and axis in (0, 1), 'DIMENSION_AXIS', item['objectId'])
    expected = dict(axis='xy'[axis], points=group['points'], role=group['role'], side=group['side'],
                    allowedSides=group['allowedSides'], reference=group.get('reference'),
                    localAllowed=group.get('localAllowed', False), band=group.get('band', []),
                    columnGroup=group.get('column'), combined=group.get('combined'))
    _same({k: item[k] for k in expected}, expected, 'DIMENSION_SCHEME_MISMATCH')
    require(group['side'] in group['allowedSides'] and set(group['allowedSides']) <= {-1, 1},
            'DIMENSION_SIDE', item['objectId'])
    ids = item['points']
    require(len(ids) >= 2 and len(set(ids)) == len(ids), 'DIMENSION_POINTS', item['objectId'])
    coords = [plan['facts']['points'][key] for key in ids]
    _same(item['coordinates'], coords, 'DIMENSION_ANCHOR')
    values = [abs(b[axis] - a[axis]) for a, b in zip(coords, coords[1:])]
    require(all(v > 0 for v in values), 'DIMENSION_ZERO_INTERVAL', item['objectId'])
    _same(item['values'], values, 'DIMENSION_VALUE')
    _same(item['displayValues'], [display_round(v, profile['display']['decimals']) for v in values],
          'DIMENSION_DISPLAY')
    _combined(item, plan, profile)
    claims = []
    if item['role'] == 'overall':
        bounds = plan['facts']['bounds']
        require(len(ids) == 2 and not any(k.startswith('hole:') for k in ids) and
                abs(min(p[axis] for p in coords) - bounds[axis]) <= plan['modelToleranceMm'] and
                abs(max(p[axis] for p in coords) - bounds[axis+2]) <= plan['modelToleranceMm'],
                'OVERALL_EXTENT', item['objectId'])
        claims.append('plate/overall_' + item['axis'])
    return claims, [(axis, a, b) for a, b in zip(ids, ids[1:])]


def _combined(item, plan, profile):
    combined = item['combined']
    expected_runs = [[0, len(item['values'])]] if combined else []
    _same(item['combineRuns'], expected_runs, 'COMBINE_RUNS')
    if not combined:
        return
    by_point = {h['pointId']: h for h in plan['facts']['holes']}
    members = [by_point[k] for k in item['points']]
    require(not any(h.get('origin') == 'BOLT' for h in members), 'BOLT_PITCH_FORBIDDEN', item['objectId'])
    require(len(item['values']) >= profile['pitch']['minIntervals'], 'PITCH_COUNT', item['objectId'])
    axis = 'xy'.index(item['axis'])
    points = item['coordinates']
    signed = [b[axis] - a[axis] for a, b in zip(points, points[1:])]
    require(max(signed) - min(signed) <= 1e-6 and
            max(p[1-axis] for p in points) - min(p[1-axis] for p in points) <= 1e-6,
            'PITCH_NOT_COLLINEAR_EQUAL', item['objectId'])
    ids = {h['id'] for h in members}
    group = next(g for g in plan['groups'] if ids <= set(g['members']))
    low, high = sorted((points[0][axis], points[-1][axis]))
    holes = {h['id']: plan['facts']['points'][h['pointId']] for h in plan['facts']['holes']}
    require(all(k in ids or not (low <= holes[k][axis] <= high and
                abs(holes[k][1-axis] - points[0][1-axis]) <= 1e-6) for k in group['members']),
            'PITCH_SKIPS_MEMBER', item['objectId'])
    require(combined['intervalCount'] == len(signed) and abs(combined['pitch'] - abs(signed[0])) <= 1e-6,
            'PITCH_VALUE', item['objectId'])
    number = f'{abs(signed[0]):.1f}'.rstrip('0').rstrip('.')
    require(combined['text'] == f'{len(signed)}@{number}', 'PITCH_TEXT', item['objectId'])


def _hole_note(item, plan):
    group = next(g for g in plan['groups'] if g['id'] == item['group']['id'])
    _same(item['group'], group, 'HOLE_GROUP')
    require(item['text'] == hole_callout_text(group), 'HOLE_TEXT', item['objectId'])
    facts = plan['facts']
    if group.get('origin') == 'BOLT':
        axis = int(facts['bounds'][3] - facts['bounds'][1] > facts['bounds'][2] - facts['bounds'][0])
        members = [h for h in facts['holes'] if h['id'] in group['members']]
        chosen = sorted(members, key=lambda h: (facts['points'][h['pointId']][axis],
                        facts['points'][h['pointId']][1-axis], h['id']))[-1]['id']
        require(item['anchorPolicy'] == 'BOLT_CENTER' and item['representativeFeatureId'] == chosen and
                item['representativeCandidates'] == [chosen] and item['anchorSelection'] == 'FIXED',
                'BOLT_ANCHOR_POLICY', item['objectId'])
    else:
        require(item['anchorPolicy'] == 'NEAR_BOUNDARY' and item['representativeFeatureId'] is None and
                item['representativeCandidates'] == sorted(group['members']) and
                item['anchorSelection'] == 'REQUIRED_IN_CANDIDATE', 'HOLE_ANCHOR_POLICY', item['objectId'])
    return [group['id'] + '/size_count']


def _feature_claims(item, plan):
    facts, kind = plan['facts'], item['kind']
    feature = item['featureId']
    if kind in ('SEGMENT', 'RADIUS'):
        segment = next(s for s in facts['segments'] if s['id'] == feature)
        _same(item['geometry'], segment, 'SEGMENT_GEOMETRY')
        if kind == 'SEGMENT':
            return [feature + '/shape']
        require(segment['kind'] == 'ARC' and item['value'] == segment['radius'] and
                item['text'] == 'R=' + f"{segment['radius']:.1f}".rstrip('0').rstrip('.') and
                item['anchorPolicy'] == 'REAL_ARC', 'RADIUS_VALUE', item['objectId'])
        return [feature + '/radius']
    hole = next(h for h in facts['holes'] if h['id'] == feature)
    _same(item['geometry'], hole, 'HOLE_GEOMETRY')
    _same(item['center'], facts['points'][hole['pointId']], 'HOLE_CENTER')
    require(hole['kind'] in ('ROUND', 'SLOT') and item['obligations'] == ['SHAPE', 'CENTER_AXES'] and
            item['renderState'] == 'PENDING_CAPABILITY_BINDING', 'HOLE_DISPLAY_CONTRACT', item['objectId'])
    aspects = ['shape', 'center_cross'] + (['orientation'] if hole['kind'] == 'SLOT' else [])
    return ['hole:' + feature + '/' + aspect for aspect in aspects]


def _text_claims(item, plan):
    kind = item['kind']
    if kind == 'LABEL':
        require(item['text'] == plan['source']['partPosition'] and item['leader'] is False,
                'PART_LABEL', item['objectId'])
        return ['plate/identity']
    if kind == 'THICKNESS':
        expected = next(r['expected'] for r in plan['requirements'] if r['id'] == 'plate/thickness')
        require(item['value'] == expected and item['contentBinding'] == 'PENDING_TEMPLATE_BINDING',
                'THICKNESS', item['objectId'])
        return ['plate/thickness']
    specification = plan['specifications'][item['aspect']]
    _same(item['source'], specification, 'SPECIFICATION_SOURCE')
    require(item['text'] == specification['value'], 'SPECIFICATION_TEXT', item['objectId'])
    return ['specification/' + item['aspect']]


def _diameter(item, plan):
    circle = outer_circle(plan['facts'], plan['modelToleranceMm'])
    require(circle is not None, 'DIAMETER_NOT_CIRCLE', item['objectId'])
    _same(item['center'], circle['center'], 'DIAMETER_CENTER')
    require(item['radius'] == circle['radius'] and item['anchorPolicy'] == 'OUTER_CIRCLE' and
            item['text'] == 'Φ' + f"{2 * circle['radius']:.1f}".rstrip('0').rstrip('.'),
            'DIAMETER_VALUE', item['objectId'])
    return ['plate/overall_x', 'plate/overall_y'] + [s['id'] + '/radius' for s in circle['segments']]


def _column_errors(expressions):
    members = defaultdict(list)
    for group in expressions['scheme']['groups']:
        if group.get('column') is not None:
            members[group['column']].append(group)
    expected = []
    for column, groups in sorted(members.items()):
        sides = set.intersection(*(set(g['allowedSides']) for g in groups))
        if len({g['axis'] for g in groups}) != 1 or not sides:
            return [dict(code='COLUMN_CONFLICT', columnGroup=column)]
        expected.append(dict(columnGroup=column, axis='xy'[groups[0]['axis']], allowedSides=sorted(sides),
                             memberObjectIds=sorted('dimension/' + g['key'] for g in groups)))
    if canonical_json(expected) != canonical_json(expressions['columnGroups']):
        return [dict(code='COLUMN_MEMBERSHIP_MISMATCH')]
    return []


class SemanticCoverage:
    def __init__(self, plan, scheme, profile):
        self.plan, self.scheme, self.profile = plan, scheme, profile
        self.claims = defaultdict(set)
        self.graphs = [defaultdict(list), defaultdict(list)]
        self.dimensions = set()
        self.seen_features = set()

    def edge(self, axis, left, right, objects, source=None):
        proof = dict(objectIds=sorted(objects), sourceEvidence=source or [])
        self.graphs[axis][left].append((right, proof))
        self.graphs[axis][right].append((left, proof))

    def observe(self, item):
        kind = item['kind']
        mobility = {'SEGMENT': 'FIXED', 'HOLE': 'FIXED', 'THICKNESS': 'TEMPLATE_FIELD'}
        require(item['mobility'] == mobility.get(kind, 'DYNAMIC'), 'OBJECT_MOBILITY', item['objectId'])
        token = (kind, item.get('schemeKey', item.get('featureId',
                 item.get('aspect', item.get('group', {}).get('id', kind)))))
        require(token not in self.seen_features, 'DUPLICATE_SEMANTIC_OBJECT', str(token))
        edges = []
        if kind == 'DIMENSION':
            claims, edges = _dimension(item, self.plan, self.scheme, self.profile)
        elif kind in ('SEGMENT', 'RADIUS', 'HOLE'):
            claims = _feature_claims(item, self.plan)
        elif kind == 'HOLE_NOTE':
            claims = _hole_note(item, self.plan)
        elif kind in ('LABEL', 'THICKNESS', 'SPECIFICATION'):
            claims = _text_claims(item, self.plan)
        elif kind == 'DIAMETER':
            claims = _diameter(item, self.plan)
        else:
            raise ContractError('UNKNOWN_EXPRESSION_KIND', str(kind))
        self.seen_features.add(token)
        for requirement in claims:
            self.claims[requirement].add(item['objectId'])
        for axis, left, right in edges:
            self.edge(axis, left, right, [item['objectId']])
        if kind == 'DIMENSION':
            self.dimensions.add(item['schemeKey'])

    def _arc_edges(self):
        facts = self.plan['facts']
        for segment in facts['segments']:
            shape = self.claims[segment['id'] + '/shape']
            radius = self.claims[segment['id'] + '/radius']
            aligned = segment['kind'] == 'ARC' and axis_aligned_arc(segment, self.plan['modelToleranceMm'])
            if shape and radius and aligned:
                for axis in (0, 1):
                    self.edge(axis, segment['startPoint'], segment['endPoint'], shape | radius)

    def finish(self):
        facts = self.plan['facts']
        self._arc_edges()
        diameter_ids = self.claims['plate/overall_x'] & self.claims['plate/overall_y']
        circle = outer_circle(facts, self.plan['modelToleranceMm']) if diameter_ids else None
        paths = []
        for axis in (0, 1):
            if circle and all(self.claims[s['id'] + '/shape'] for s in circle['segments']):
                shapes = set().union(*(self.claims[s['id'] + '/shape'] for s in circle['segments']))
                for key, point in facts['points'].items():
                    if abs(point[axis] - circle['center'][axis]) <= self.plan['modelToleranceMm']:
                        self.edge(axis, facts['datum'], key, shapes | diameter_ids)
            self._shared(axis)
            paths.append(self._paths(axis))
        return paths

    def _shared(self, axis):
        points = self.plan['facts']['points']
        ordered = sorted(points, key=lambda key: (points[key][axis], key))
        representative = ordered[0]
        for key in ordered[1:]:
            delta = abs(points[representative][axis] - points[key][axis])
            if delta <= self.plan['modelToleranceMm']:
                self.edge(axis, representative, key, [], [dict(kind='SHARED_COORDINATE', axis='xy'[axis],
                          points=[representative, key], delta=delta, tolerance=self.plan['modelToleranceMm'])])
            else:
                representative = key

    def _paths(self, axis):
        datum = self.plan['facts']['datum']
        paths = {datum: dict(objectIds=[], sourceEvidence=[dict(kind='SOURCE_DATUM', point=datum)])}
        queue = deque([datum])
        while queue:
            current = queue.popleft()
            edges = sorted(self.graphs[axis][current], key=lambda edge: (edge[0], canonical_json(edge[1])))
            for target, proof in edges:
                if target not in paths:
                    paths[target] = dict(objectIds=sorted(set(paths[current]['objectIds'] + proof['objectIds'])),
                                         sourceEvidence=paths[current]['sourceEvidence'] + proof['sourceEvidence'])
                    queue.append(target)
        return paths


def _upstream_scheme(plan, profile):
    scheme = build_scheme(plan, build_feature_model(plan['facts'], plan['modelToleranceMm']), profile)
    scheme['specifications'] = [[aspect, value] for aspect, value in scheme['specifications']]
    return scheme


def _validate_contract(source, expressions):
    check_seal(source)
    canonical_json(expressions)
    require(source.get('schemaVersion') in ('NativeLayoutSource.v1','NativeLayoutSource.v2') and source['status'] == 'SOURCE_READY',
            'SOURCE_NOT_READY', 'Admitted source required')
    if source['schemaVersion']=='NativeLayoutSource.v2':
        from .source_extension import verify_extended_source
        verify_extended_source(source)
    require(expressions.get('schemaVersion') == 'LayoutExpressions.v1' and
            expressions.get('units') == 'VIEW_MM' and expressions.get('coordinateSpace') == 'AUDITED_MAIN_VIEW',
            'EXPRESSION_CONTRACT', 'Wrong schema or coordinate space')
    require(expressions['sourceEnvelopeDigest'] == source['digest'], 'SOURCE_MISMATCH', 'Different snapshot')
    _same(expressions['source'], source['source'], 'SOURCE_MISMATCH')
    plan, scheme, profile = source['requirements'], expressions['scheme'], expressions['profile']
    require(plan.get('schemaVersion') in ('expressionRequirements.v1', 'expressionRequirements.v2') and
            scheme.get('schemaVersion') in ('dimensionScheme.v1','dimensionScheme.v2') and
            profile.get('schemaVersion') == 'planningConvention.v1' and
            expressions.get('adapterVersion') in ('independent-expressions.r1','independent-expressions.r2'),
            'SEMANTIC_VERSION', 'Unsupported version')
    _same(expressions['expressionRequirements'], plan, 'REQUIREMENTS_MISMATCH')
    require(digest(plan) == source['source']['requirementsDigest'] and
            digest(scheme) == expressions['schemeDigest'] and digest(profile) == expressions['profileDigest'],
            'SEMANTIC_DIGEST', 'Changed contract')
    _same(profile, load_profile(), 'PROFILE_MISMATCH')
    require(plan['status'] == 'READY' and plan.get('requiredViews', ['main']) == ['main'] and
            not plan.get('sectionRequirements'), 'SOURCE_NOT_READY', 'Full single-view requirements required')
    group_keys = [g['key'] for g in scheme['groups']]
    require(len(set(group_keys)) == len(group_keys), 'SCHEME_GROUP_DUPLICATE', 'Duplicate group keys')
    if expressions['adapterVersion']=='independent-expressions.r1':
        require(source['schemaVersion']=='NativeLayoutSource.v1','SEMANTIC_VERSION','Extended source needs extended dimensions')
        _same(scheme, _upstream_scheme(plan, profile), 'UPSTREAM_SCHEME_MISMATCH')
    else:
        require(scheme['schemaVersion']=='dimensionScheme.v2','SEMANTIC_VERSION','Extended scheme required')
    return plan, scheme, profile


def audit_semantics(source, expressions):
    """Recheck semantic objects and compute bindings; never trust reported coverage."""
    plan, scheme, profile = _validate_contract(source, expressions)
    state, errors, seen = SemanticCoverage(plan, scheme, profile), _column_errors(expressions), set()
    if expressions['adapterVersion']=='independent-expressions.r2':
        from .cutout_dimensions import validate_cutout_scheme
        errors.extend(validate_cutout_scheme(plan,scheme,_upstream_scheme(plan,profile)))
    require(isinstance(expressions['objects'], list), 'OBJECTS_INVALID', 'Expected object list')
    for item in expressions['objects']:
        identity = item.get('objectId') if isinstance(item, dict) else None
        try:
            require(isinstance(identity, str) and bool(identity) and identity not in seen,
                    'OBJECT_ID_INVALID', str(identity))
            seen.add(identity)
            state.observe(item)
        except (ContractError, KeyError, TypeError, ValueError, StopIteration) as error:
            errors.append(dict(code=getattr(error, 'code', 'INVALID_SEMANTIC_OBJECT'),
                               objectId=identity, detail=str(error)))
    missing_groups = sorted({g['key'] for g in scheme['groups']} - state.dimensions)
    if missing_groups:
        errors.append(dict(code='SCHEME_GROUP_MISSING', groups=missing_groups))
    paths, bindings = state.finish(), []
    for requirement in plan['requirements']:
        proof = dict(objectIds=sorted(state.claims[requirement['id']]), sourceEvidence=[])
        if requirement['aspect'] in ('location_x', 'location_y'):
            proof = paths['xy'.index(requirement['aspect'][-1])].get(requirement['feature'], proof)
        bindings.append(dict(requirementId=requirement['id'],
                             status='BOUND' if proof['objectIds'] or proof['sourceEvidence'] else 'MISSING', **proof))
    complete = not errors and all(row['status'] == 'BOUND' for row in bindings)
    return seal(dict(schemaVersion='LayoutSemanticCoverage.v1', status='SEMANTICS_COMPLETE' if complete else
                     'UPSTREAM_CONTRACT_GAP', sourceEnvelopeDigest=source['digest'],
                     objectsDigest=digest(expressions['objects']), bindings=bindings, errors=errors,
                     scope='DECLARED_SEMANTIC_RESPONSIBILITIES_NOT_RENDERED_COVERAGE'))
