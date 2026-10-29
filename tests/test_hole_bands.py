"""2026-10-28 hole bands: unrelated hole features no longer share one closed chain.

Failure case T2-10B-101 (449291360): 11820x520 plate, a bolt group at each end
(Y 135/215/295) and three single large holes (Y 215, 260, 260). One global Y
chain 0|135|80|45|35|225 mixed them; 35 and 45 had no engineering meaning.
Expected: each band is its own closed chain 0|..|520 beside its own holes, and
repeated stations are declared HOLE_BAND references, not undeclared loops.
Coordinates here are a synthetic copy; no project data.
"""
import unittest

from shapely.geometry import box

from test_native_material import plate_with_hole
from zijinhua_tekla.part_drawing.dimension_scheme import build_scheme, hole_bands
from zijinhua_tekla.part_drawing.expression_requirements import _requirements
from zijinhua_tekla.part_drawing.layout_solver import dimension_candidates, scene_conflicts
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.plate_features import build_feature_model
from zijinhua_tekla.part_drawing.readability import audit_readability


def long_plate():
    raw = plate_with_hole(); batch = build_native_expression_requirements(raw)
    req = batch['parts'][0]; req['source']['partPosition'] = 'LONG-PLATE'
    xy = [(0, 0), (11820, 0), (11820, 520), (0, 520)]
    points = {f'vertex:{i}': list(p) for i, p in enumerate(xy)}
    segments = [dict(id=f'edge:{i}', kind='LINE', start=points[f'vertex:{i}'], end=points[f'vertex:{(i+1)%4}'],
                     startPoint=f'vertex:{i}', endPoint=f'vertex:{(i+1)%4}') for i in range(4)]
    holes, refs = [], {}
    def add(name, x, y, diameter, ref):
        points['hole:'+name] = [x, y]
        holes.append(dict(id=name, pointId='hole:'+name, kind='ROUND', diameter=diameter))
        refs['hole:'+name] = [ref]
    for group, xs in ((0, (55, 135)), (1, (11685, 11765))):
        for i, (x, y) in enumerate((x, y) for x in xs for y in (135, 215, 295)):
            add(f'b{group}{i}', x, y, 26, f'#/parts/0/boltGroups/value/{group}/positions/value/{i}')
    for i, (x, y) in enumerate(((976, 215), (5484, 260), (8887, 260))):
        add(f'c{i}', x, y, 150, f'#/parts/0/operations/value/{i}')
    req['facts'] = dict(points=points, segments=segments, holes=holes, datum='vertex:0', bounds=[0, 0, 11820, 520],
                        boundaryLoops=[dict(segments=[s['id'] for s in segments])], sourceFeatureRefs=refs)
    req['requirements'], req['groups'] = _requirements('LONG-PLATE', 10, req['facts'], {})
    return req


def y_chains(req, scheme):
    points = req['facts']['points']
    return {g['key']: [round(points[k][1]) for k in g['points']] for g in scheme['groups']
            if g['axis'] == 1 and g['key'].startswith('holes')}


class HoleBandTests(unittest.TestCase):
    def scheme(self):
        req = long_plate()
        return req, build_scheme(req, build_feature_model(req['facts'], req['modelToleranceMm']))

    def test_unrelated_features_get_their_own_closed_y_chain(self):
        req, scheme = self.scheme()
        chains = y_chains(req, scheme)
        self.assertEqual(sorted([[0, 135, 215, 295, 520]]*2+[[0, 215, 520], [0, 260, 520], [0, 260, 520]]),
                         sorted(chains.values()))
        intervals = {b-a for c in chains.values() for a, b in zip(c, c[1:])}
        self.assertFalse({35, 45} & intervals)
        self.assertEqual('READABLE', audit_readability(req, scheme)['status'])

    def test_each_band_sits_on_its_own_side_and_repeats_are_declared(self):
        req, scheme = self.scheme()
        points = req['facts']['points']
        for g in scheme['groups']:
            if g['axis'] != 1 or not g['key'].startswith('holes'):
                continue
            holes = [points[k][0] for k in g['points'] if k.startswith('hole:')]
            expected = 1 if sum(holes)/len(holes) > 5910 else -1
            self.assertEqual(expected, g['side'], g['key'])
            self.assertTrue(g['localAllowed'])
        refs = [g for g in scheme['groups'] if (g.get('reference') or {}).get('category') == 'HOLE_BAND']
        self.assertEqual(4, len(refs))
        self.assertFalse([d for d in scheme['decisions'] if d['axis'] == 1])

    def test_x_axis_keeps_one_chain_across_the_plate(self):
        # Bands join along X: every feature lies within the plate width of the others.
        req, _ = self.scheme()
        facts = req['facts']
        self.assertEqual(1, len(hole_bands(facts, [h['pointId'] for h in facts['holes']], 0, req['modelToleranceMm'])))

    def test_plain_holes_without_sources_stay_one_band(self):
        req = long_plate(); req['facts']['sourceFeatureRefs'] = {}
        scheme = build_scheme(req, build_feature_model(req['facts'], req['modelToleranceMm']))
        self.assertEqual([[0, 135, 215, 260, 295, 520]], list(y_chains(req, scheme).values()))


def chain(coordinates, points, side=-1):
    values = [abs(b[1]-a[1]) for a, b in zip(coordinates, coordinates[1:])]
    return dict(key='holes-1-3', axis='y', role='location', coordinates=coordinates, points=points,
                up=[side, 0], distance=10, values=values, displayValues=values, combine=False, combineRuns=[])


class LocalCandidateTests(unittest.TestCase):
    def candidates(self, x, local=True):
        d = chain([[x, 0], [x, 260], [x, 520]], ['edge:a', 'hole:c', 'edge:b'])
        g = dict(key=d['key'], side=-1, allowedSides=[-1, 1], lane=12, localAllowed=local)
        return list(dimension_candidates([d], {d['key']: g}, [0, 0, 11820, 520], 1, 0, [], box(0, 0, 11820, 520)))

    def test_mid_plate_band_is_dimensioned_beside_its_hole_first(self):
        first = self.candidates(5484)[0]
        self.assertTrue(first['choice']['local'])
        line = first['dimensions'][0]['coordinates'][0][0]+first['dimensions'][0]['up'][0]*first['dimensions'][0]['distance']
        self.assertLess(abs(line-5484), 100)
        costs = [c['choice']['cost'] for c in self.candidates(5484)]
        self.assertEqual(sorted(costs), costs)

    def test_band_near_an_edge_stays_outside(self):
        self.assertFalse(self.candidates(40)[0]['choice'].get('local'))

    def test_chain_inside_the_plate_is_not_an_outside_layer(self):
        # 101 preview: a mid-plate chain was rejected as DIMENSION_LAYER_ORDER
        # against the end bolt group's outside chain on the same side.
        local = next(c for c in self.candidates(5484) if c['choice'].get('local'))
        outside = next(c for c in self.candidates(40) if not c['choice'].get('local'))
        outside['spans'][0]['key'] = 'holes-1-0'
        self.assertEqual([], [e for e in scene_conflicts(local, [outside]) if e['code'] == 'DIMENSION_LAYER_ORDER'])

    def test_without_band_declaration_order_is_unchanged(self):
        self.assertFalse(any(c['choice'].get('local') for c in self.candidates(5484, local=False)))
        self.assertTrue(all('cost' not in c['choice'] for c in self.candidates(5484, local=False)))


if __name__ == '__main__':
    unittest.main()
