"""Adapt stable machining schemes into model-plane semantic objects, never layout."""

from ..dimension_scheme import build_scheme
from ..expression_coverage import display_round
from ..plate_features import build_feature_model
from ..readability import load_profile
from .contracts import check_seal, digest, require, seal


VERSION = 'independent-expressions.r1'


def source_requirements(source):
    check_seal(source)
    require(source.get('schemaVersion') == 'NativeLayoutSource.v1', 'SOURCE_SCHEMA', 'Native source required')
    plan = source['requirements']
    require(plan.get('schemaVersion') in ('expressionRequirements.v1', 'expressionRequirements.v2'),
            'REQUIREMENTS_SCHEMA', 'Unsupported upstream version')
    require(digest(plan) == source['source']['requirementsDigest'], 'REQUIREMENTS_DIGEST', 'Changed requirements')
    require(plan['sourceDigest'] == source['source']['sourceDigest'] and
            all(source['source'][k] == v for k, v in plan['source'].items()), 'SOURCE_MISMATCH', 'Mixed source')
    return plan


def _dimension(group, facts, decimals):
    axis = group['axis']
    require(type(axis) is int and axis in (0, 1), 'DIMENSION_AXIS', group['key'])
    ids = group['points']
    require(len(ids) >= 2 and len(set(ids)) == len(ids), 'DIMENSION_POINTS', group['key'])
    require(all(k in facts['points'] for k in ids), 'POINT_MISSING', group['key'])
    coordinates = [facts['points'][k] for k in ids]
    values = [abs(b[axis] - a[axis]) for a, b in zip(coordinates, coordinates[1:])]
    runs = [[0, len(values)]] if group.get('combined') else []
    return dict(objectId='dimension/' + group['key'], kind='DIMENSION', mobility='DYNAMIC',
                schemeKey=group['key'], axis='xy'[axis], points=ids, coordinates=coordinates,
                values=values, displayValues=[display_round(v, decimals) for v in values],
                role=group['role'], side=group['side'], allowedSides=group['allowedSides'],
                reference=group.get('reference'), localAllowed=group.get('localAllowed', False),
                band=group.get('band', []), columnGroup=group.get('column'),
                combined=group.get('combined'), combineRuns=runs)


def _columns(dimensions):
    columns = {}
    for item in dimensions:
        column = item['columnGroup']
        if column is not None:
            require(isinstance(column, str) and bool(column), 'COLUMN_ID', item['objectId'])
            columns.setdefault(column, []).append(item)
    result = []
    for key, members in sorted(columns.items()):
        sides = set.intersection(*(set(m['allowedSides']) for m in members))
        require(len({m['axis'] for m in members}) == 1 and bool(sides), 'COLUMN_CONFLICT', key)
        result.append(dict(columnGroup=key, memberObjectIds=sorted(m['objectId'] for m in members),
                           axis=members[0]['axis'], allowedSides=sorted(sides)))
    return result


def _hole_note(callout, facts):
    group = callout['group']
    members = [h for h in facts['holes'] if h['id'] in group['members']]
    require(len(members) == len(group['members']), 'HOLE_MEMBER_MISSING', group['id'])
    representative, policy = None, 'NEAR_BOUNDARY'
    if group.get('origin') == 'BOLT':
        axis = 0 if facts['bounds'][2] - facts['bounds'][0] >= facts['bounds'][3] - facts['bounds'][1] else 1
        representative = max(members, key=lambda h: (
            facts['points'][h['pointId']][axis], facts['points'][h['pointId']][1-axis], h['id']))['id']
        policy = 'BOLT_CENTER'
    return dict(objectId='hole-note/' + group['id'], kind='HOLE_NOTE', mobility='DYNAMIC',
                group=group, text=callout['text'], anchorPolicy=policy,
                representativeFeatureId=representative,
                representativeCandidates=[representative] if representative else sorted(group['members']),
                anchorSelection='FIXED' if representative else 'REQUIRED_IN_CANDIDATE')


def _objects(plan, scheme, decimals):
    facts = plan['facts']
    objects = [_dimension(g, facts, decimals) for g in scheme['groups']]
    for segment in facts['segments']:
        objects.append(dict(objectId='segment/' + segment['id'], kind='SEGMENT', mobility='FIXED',
                            featureId=segment['id'], geometry=segment))
    for hole in facts['holes']:
        objects.append(dict(objectId='hole/' + hole['id'], kind='HOLE', mobility='FIXED',
                            featureId=hole['id'], geometry=hole, center=facts['points'][hole['pointId']],
                            obligations=['SHAPE', 'CENTER_AXES'], renderState='PENDING_CAPABILITY_BINDING'))
    objects.extend(_hole_note(c, facts) for c in scheme['holeCallouts'])
    for radius in scheme['radii']:
        segment = radius['segment']
        objects.append(dict(objectId='radius/' + segment['id'], kind='RADIUS', mobility='DYNAMIC',
                            featureId=segment['id'], value=segment['radius'], text=radius['text'],
                            anchorPolicy='REAL_ARC', geometry=segment))
    if scheme['diameter']:
        objects.append(dict(objectId='outer-diameter', kind='DIAMETER', mobility='DYNAMIC',
                            **scheme['diameter'], anchorPolicy='OUTER_CIRCLE'))
    objects.extend([
        dict(objectId='part-label', kind='LABEL', mobility='DYNAMIC', text=scheme['label'], leader=False),
        dict(objectId='plate-thickness', kind='THICKNESS', mobility='TEMPLATE_FIELD', value=scheme['thickness'],
             contentBinding='PENDING_TEMPLATE_BINDING'),
    ])
    for aspect, specification in scheme['specifications']:
        objects.append(dict(objectId='specification/' + aspect, kind='SPECIFICATION', mobility='DYNAMIC',
                            aspect=aspect, text=specification['value'], source=specification))
    return sorted(objects, key=lambda obj: obj['objectId'])


def adapt_expressions(source):
    """Freeze semantic responsibilities; no template, geometry occupancy or native pass."""
    plan = source_requirements(source)
    require(source['status'] == 'SOURCE_READY' and plan['status'] == 'READY', 'SOURCE_NOT_READY', source['status'])
    require(plan.get('requiredViews', ['main']) == ['main'] and not plan.get('sectionRequirements'),
            'ADDITIONAL_VIEWS_REQUIRED', 'Cannot discard a required view')
    profile = load_profile()
    features = build_feature_model(plan['facts'], plan['modelToleranceMm'])
    scheme = build_scheme(plan, features, profile)
    # The stable upstream scheme exposes specification pairs as Python tuples.
    # The independent JSON contract stores the same pairs as JSON arrays.
    scheme['specifications'] = [[aspect, value] for aspect, value in scheme['specifications']]
    objects = _objects(plan, scheme, profile['display']['decimals'])
    envelope = dict(schemaVersion='LayoutExpressions.v1', adapterVersion=VERSION,
                    sourceEnvelopeDigest=source['digest'], source=source['source'],
                    units='VIEW_MM', coordinateSpace='AUDITED_MAIN_VIEW',
                    expressionRequirements=plan, scheme=scheme, schemeDigest=digest(scheme),
                    profile=profile, profileDigest=digest(profile), objects=objects,
                    columnGroups=_columns([o for o in objects if o['kind'] == 'DIMENSION']))
    from .semantic_coverage import audit_semantics
    coverage = audit_semantics(source, envelope)
    return seal({**envelope, 'status': coverage['status'], 'coverage': coverage,
                 'remainingGates': ['TEMPLATE_CONTENT_BINDING', 'NATIVE_HOLE_DISPLAY', 'LAYOUT_INPUT',
                                    'CANDIDATE_GEOMETRY', 'READBACK_COVERAGE', 'VISUAL_QA'],
                 'scope': 'SEMANTIC_RESPONSIBILITIES_ONLY'})


def adapt_expression_sources(sources):
    """Keep every source outcome visible; out-of-scope is never semantic success."""
    check_seal(sources)
    require(sources.get('schemaVersion') == 'NativeLayoutSources.v1', 'SOURCE_SCHEMA', 'Expected source batch')
    require(isinstance(sources.get('entries'), list) and bool(sources['entries']), 'SOURCES_EMPTY', 'Empty batch')
    entries, identities = [], set()
    for source in sources['entries']:
        source_requirements(source)
        identity = (source['source']['modelIdentifier'], source['source']['partId'])
        require(identity not in identities, 'PART_ID_INVALID', str(identity))
        identities.add(identity)
        if source['status'] == 'SOURCE_READY':
            entries.append(adapt_expressions(source))
        else:
            require(source['status'] in ('OUT_OF_SCOPE', 'INPUT_BLOCKED'), 'SOURCE_STATUS', source['status'])
            entries.append(seal(dict(schemaVersion='LayoutExpressionExclusion.v1', source=source['source'],
                                     sourceEnvelopeDigest=source['digest'], status=source['status'],
                                     reasons=source['reasons'], requiredViews=source['requiredViews'])))
    states = ('SEMANTICS_COMPLETE', 'UPSTREAM_CONTRACT_GAP', 'OUT_OF_SCOPE', 'INPUT_BLOCKED')
    return seal(dict(schemaVersion='LayoutExpressionBatch.v1', sourceBatchDigest=sources['digest'],
                     entries=entries, counts={key: sum(e['status'] == key for e in entries) for key in states},
                     scope='SEMANTIC_RESPONSIBILITIES_ONLY'))
