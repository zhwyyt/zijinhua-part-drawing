"""P3 readability gate: checks a dimension scheme against a convention profile.

Completeness stays with expression_coverage; this gate asks whether the
scheme reads the way the shop convention does. Each finding names a rule
code, the groups involved and the measured coordinates.
"""
import json
from pathlib import Path


VERSION = 'readability.m3a'
CONVENTIONS = Path(__file__).with_name('conventions')


def load_profile(name='flat-plate.v1'):
    return json.loads((CONVENTIONS / (name + '.json')).read_text(encoding='utf-8'))


def _position(facts, key, axis):
    return facts['points'][key][axis]


def _issue(code, message, **evidence):
    return dict(code=code, message=message, **evidence)


def _datum_issues(facts, groups, tol, profile):
    """Each group reaches an envelope extreme, itself or through shared hole centres.

    A chain may continue through a hole centre; an interior contour point used
    as a start is a local datum, which convention v1 does not allow.
    """
    bounds = facts['bounds']
    allowed = set(profile['datum']['localDatums'])
    issues = []
    measured = [g for g in groups if not g.get('reference')]

    def extremes(g):
        values = [_position(facts, k, g['axis']) for k in g['points']]
        return (any(abs(v-bounds[g['axis']]) <= tol for v in values),
                any(abs(v-bounds[g['axis']+2]) <= tol for v in values))

    anchored = {g['key']: extremes(g) for g in measured}
    changed = True
    while changed:
        changed = False
        for g in measured:
            holes = {k for k in g['points'] if k.startswith('hole:')}
            for o in measured:
                if o['axis'] != g['axis'] or not holes & set(o['points']):
                    continue
                merged = tuple(a or b for a, b in zip(anchored[g['key']], anchored[o['key']]))
                if merged != anchored[g['key']]:
                    anchored[g['key']] = merged; changed = True
    for g in measured:
        axis = g['axis']
        values = [_position(facts, k, axis) for k in g['points']]
        low, high = anchored[g['key']]
        if not low and not (high and 'OPPOSITE_EXTREME' in allowed):
            issues.append(_issue('RD_DATUM_SPREAD', 'group is measured from an interior datum',
                                 group=g['key'], axis='xy'[axis], positions=[v-bounds[axis] for v in values]))
        elif not low and not any((o.get('reference') or {}).get('category') == 'OVERALL' and o['axis'] == axis
                                 for o in groups):
            issues.append(_issue('RD_LOCAL_DATUM_UNLOCATED', 'opposite extreme used without an overall dimension',
                                 group=g['key'], axis='xy'[axis]))
        if (profile['holes'].get('chainBothEnds') and any(k.startswith('hole:') for k in g['points'])
                and not (low and high)):
            issues.append(_issue('RD_HOLE_CHAIN_OPEN', 'hole chain does not close to both envelope extremes',
                                 group=g['key'], axis='xy'[axis], reaches=dict(low=low, high=high)))
    return issues


def _redundancy_issues(facts, groups, tol, profile):
    """Without reference groups, each axis must form a forest of measured intervals.

    Convention v1 closes chains edge to edge, so n closed chains on one axis
    may form n-1 loops among themselves; any further loop is undeclared.
    """
    allowed = set(profile['referenceCategories'])
    issues = []
    for g in groups:
        category = (g.get('reference') or {}).get('category')
        if category and category not in allowed:
            issues.append(_issue('RD_REFERENCE_CATEGORY', 'reference category not allowed by convention',
                                 group=g['key'], category=category))
    for axis in (0, 1):
        nodes, parent = [], []

        def node(value):
            for i, v in enumerate(nodes):
                if abs(v-value) <= tol:
                    return i
            nodes.append(value); parent.append(len(parent))
            return len(nodes)-1

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i

        bounds = facts['bounds']
        measured = [g for g in groups if g['axis'] == axis and (g.get('reference') or {}).get('category') not in allowed]
        closed = sum(1 for g in measured if profile.get('closedChains') and
                     abs(_position(facts, g['points'][0], axis)-bounds[axis]) <= tol and
                     abs(_position(facts, g['points'][-1], axis)-bounds[axis+2]) <= tol)
        allowance = max(0, closed-1)
        for g in measured:
            values = [_position(facts, k, axis) for k in g['points']]
            for a, b in zip(values, values[1:]):
                i, j = root(node(a)), root(node(b))
                if i == j and allowance:
                    allowance -= 1
                elif i == j:
                    issues.append(_issue('RD_UNDECLARED_REDUNDANCY', 'interval closes a loop without a reference group',
                                         group=g['key'], axis='xy'[axis],
                                         interval=[a-facts['bounds'][axis], b-facts['bounds'][axis]]))
                else:
                    parent[i] = j
    return issues


def _pitch_issues(groups, profile):
    minimum = profile['pitch']['minIntervals']
    return [_issue('RD_COMBINE_BELOW_MIN', f'n@p combines fewer than {minimum} intervals',
                   group=g['key'], intervals=g['combined']['intervalCount'])
            for g in groups if g.get('combined') and g['combined']['intervalCount'] < minimum]


def _plan_issues(plan, tol):
    """Tier order and binding columns, read from planned line coordinates."""
    issues = []
    lines = {}
    for d in plan['dimensions']:
        axis = 0 if d['axis'] == 'x' else 1
        side = d['up'][1-axis]
        line = d['coordinates'][0][1-axis] + side*d['distance']
        lines[d['key']] = line
        d_side = (axis, side)
        if d['role'] == 'overall':
            inner = [o for o in plan['dimensions'] if o is not d and o['axis'] == d['axis'] and o['up'] == d['up']]
            for o in inner:
                oline = o['coordinates'][0][1-axis] + side*o['distance']
                if side*(oline-line) > tol:
                    issues.append(_issue('RD_TIER_ORDER', 'overall dimension is not outermost on its side',
                                         group=d['key'], inner=o['key'], side=list(d_side)))
    columns = {}
    for d in plan['dimensions']:
        if d.get('columnGroup'):
            columns.setdefault(d['columnGroup'], []).append(lines[d['key']])
    for column, values in columns.items():
        if max(values)-min(values) > tol:
            issues.append(_issue('RD_SPLIT_COLUMN', 'column members are not on one line', column=column))
    return issues


def audit_readability(requirements, scheme, profile=None, plan=None):
    profile = profile or load_profile()
    facts = requirements['facts']
    tol = requirements['modelToleranceMm']
    groups = scheme['groups']
    issues = (_datum_issues(facts, groups, tol, profile) + _redundancy_issues(facts, groups, tol, profile)
              + _pitch_issues(groups, profile) + (_plan_issues(plan, tol) if plan else []))
    return dict(schemaVersion='readabilityReport.v1', version=VERSION,
                profile=profile['id']+'.'+profile['version'], mode=profile['gate']['mode'],
                status='READABLE' if not issues else 'NOT_READABLE', issues=issues)
