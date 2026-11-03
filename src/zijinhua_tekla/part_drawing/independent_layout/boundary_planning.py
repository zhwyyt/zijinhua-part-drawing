"""Attach surviving straight contour chains to whole boundary paths.

Local outward normals can point into an upper recess. They do not identify the
side of the drawing belonging to that recess. Shared stations remain necessary
when they bound a local interval in a surviving contour chain.
"""
import copy

from ..plate_features import build_feature_model
from .contracts import require


VERSION = 'boundary-paths.r1'


def _paths(facts, axis, tolerance):
    by_id = {s['id']: s for s in facts['segments']}
    edges = [by_id[k] for k in facts['boundaryLoops'][0]['segments']]
    points = [s['start'] for s in edges]
    low, high = min(p[axis] for p in points), max(p[axis] for p in points)
    def extreme(value):
        return min((i for i, p in enumerate(points) if abs(p[axis]-value) <= tolerance),
                   key=lambda i: (points[i][1-axis], edges[i]['startPoint']))
    start, end = extreme(low), extreme(high)
    require(start != end, 'BOUNDARY_PATHS', 'Degenerate projected extent')
    paths = []
    for first, last in ((start, end), (end, start)):
        indices = []; current = first
        while current != last:
            indices.append(current); current = (current+1) % len(edges)
        weights = [abs(edges[i]['end'][axis]-edges[i]['start'][axis]) for i in indices]
        mean = sum(w*(edges[i]['start'][1-axis]+edges[i]['end'][1-axis])/2
                   for i, w in zip(indices, weights))/sum(weights)
        paths.append(dict(segments=[edges[i]['id'] for i in indices], mean=mean))
    require(abs(paths[0]['mean']-paths[1]['mean']) > tolerance,
            'BOUNDARY_PATHS', 'Boundary sides cannot be distinguished')
    for path in paths:
        path['side'] = 1 if path['mean'] == max(p['mean'] for p in paths) else -1
    return paths


def plan_boundary_paths(plan, scheme):
    """Extend the independent straight-only route; never alter upstream code."""
    facts = plan['facts']; tolerance = plan['modelToleranceMm']
    require(len(facts['boundaryLoops']) == 1 and not facts['holes'] and
            all(s['kind'] == 'LINE' for s in facts['segments']),
            'BOUNDARY_SCOPE', 'Single straight hole-free contour required')
    result = copy.deepcopy(scheme)
    groups = {g['key']: g for g in result['groups']}
    features = build_feature_model(facts, tolerance)
    evidence = []
    for axis in (0, 1):
        paths = _paths(facts, axis, tolerance)
        side_by_edge = {key: p['side'] for p in paths for key in p['segments']}
        for index, run in enumerate(features['axes'][axis]['contourChains']):
            key = f'boundary-{axis}-{index}'
            if key not in groups:
                continue  # No new chain solely to repeat an already defined station.
            group = groups[key]
            members = {k for station in run['stations'] for k in station}
            edges = [s for s in facts['segments'] if s['id'] in side_by_edge and
                     s['startPoint'] in members and s['endPoint'] in members and
                     abs(s['end'][axis]-s['start'][axis]) > tolerance]
            sides = {side_by_edge[s['id']] for s in edges}
            require(len(sides) == 1, 'BOUNDARY_PATHS', key+' crosses boundary paths')
            side = sides.pop()
            # Preserve existing anchors at equivalent stations; add the missing
            # real endpoint instead of synthesizing a projected/virtual point.
            restored = list(group['points'])
            for point in run['points']:
                if not any(abs(facts['points'][point][axis]-facts['points'][p][axis]) <= tolerance
                           for p in restored):
                    restored.append(point)
            group['points'] = sorted(restored, key=lambda p: facts['points'][p][axis])
            group['side'] = side; group['allowedSides'] = [side]
            evidence.append(dict(key=key, axis=axis, side=side, paths=paths,
                                 segments=[s['id'] for s in edges], stations=run['stations'],
                                 directIntervals=[list(pair) for pair in zip(run['points'], run['points'][1:])]))
    result['boundaryPlanning'] = dict(version=VERSION, groups=evidence)
    return result


def check_boundary_placements(expressions, placements):
    """Reject a clearance solution that moved a chain off its source side."""
    by_key = {p['key']: p for p in placements}
    for group in expressions['scheme']['boundaryPlanning']['groups']:
        key = 'dimension/'+group['key']
        require(key in by_key and by_key[key]['side'] == group['side'],
                'BOUNDARY_SIDE', key)
