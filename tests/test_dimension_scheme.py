import copy
import json
import unittest

import test_native_layout
from zijinhua_tekla.part_drawing.dimension_scheme import build_scheme, assign_closed_coordinates
from zijinhua_tekla.part_drawing.readability import load_profile, audit_readability
from zijinhua_tekla.part_drawing.native_layout import build_layout
from zijinhua_tekla.part_drawing.plate_features import build_feature_model


class DimensionSchemeTests(unittest.TestCase):
    def setUp(self):
        self.req, self.geo, self.raw = test_native_layout.NativeLayoutTests().shoulder_data()
        self.tol = self.req['modelToleranceMm']

    def test_feature_model_is_serialisable_geometry_without_paper_terms(self):
        features = build_feature_model(self.req['facts'], self.tol)
        encoded = json.dumps(features)
        self.assertIsNone(features['circular'])
        self.assertEqual(['hole:a', 'hole:b'], features['holeIds'])
        for axis in features['axes']:
            self.assertTrue(axis['contourChains'])
            self.assertIsNotNone(axis['holeDatums'])
        for paper in ('distance', 'scale', 'lane'):
            self.assertNotIn(paper, encoded)

    def test_layout_keeps_scheme_groups_order_and_anchors(self):
        features = build_feature_model(self.req['facts'], self.tol)
        scheme = build_scheme(self.req, features)
        plan = build_layout(self.req, self.geo, self.raw, scale=2)
        self.assertEqual([g['key'] for g in scheme['groups']], [d['key'] for d in plan['dimensions']])
        self.assertEqual([g['points'] for g in scheme['groups']], [d['points'] for d in plan['dimensions']])
        for group in scheme['groups']:
            self.assertFalse({'distance', 'up', 'coordinates'} & set(group))

    def test_scale_changes_layout_but_never_the_scheme(self):
        # Scales 1 and 3 straddle the old 12-paper-mm short_run threshold for the 18/8 flats.
        plans = [build_layout(copy.deepcopy(self.req), self.geo, self.raw, scale=s) for s in (1, 2, 3)]
        keys = [[(d['key'], d['points']) for d in p['dimensions']] for p in plans]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[0], keys[2])
        self.assertNotEqual(plans[0]['dimensions'][0]['distance'], plans[2]['dimensions'][0]['distance'])

    def test_scheme_is_deterministic_and_does_not_mutate_requirements(self):
        before = copy.deepcopy(self.req)
        features = build_feature_model(self.req['facts'], self.tol)
        first = build_scheme(self.req, features)
        second = build_scheme(self.req, build_feature_model(self.req["facts"], self.tol))
        self.assertEqual(first, second)
        self.assertEqual(before, self.req)
        self.assertEqual('UNRELATED-SHOULDER', first['label'])
        self.assertEqual(['2Φ26'], [c['text'] for c in first['holeCallouts']])
        self.assertEqual(['R=10', 'R=10'], [r['text'] for r in first['radii']])

    def test_shared_closed_chain_stations_are_assigned_once_without_relaxing_gate(self):
        for axis, factor in ((0, 1), (1, 1.7)):
            with self.subTest(axis=axis, factor=factor):
                coords = {'vertex:lo': 0, 'vertex:a': 20, 'vertex:b': 40,
                          'vertex:c': 65, 'hole:a': 20, 'vertex:hi': 100}
                facts = dict(points={k: ([v*factor, 0] if axis == 0 else [0, v*factor])
                                     for k, v in coords.items()}, bounds=[0, 0, 100*factor, 100*factor])
                def chain(key, points, **extra):
                    return dict(key=key, axis=axis, points=points, **extra)
                groups = [chain('first', ['vertex:lo', 'vertex:a', 'vertex:b', 'vertex:hi']),
                          chain('second', ['vertex:lo', 'vertex:a', 'vertex:c', 'vertex:hi']),
                          chain('holes', ['vertex:lo', 'hole:a', 'vertex:hi']),
                          chain('repeat', ['vertex:lo', 'vertex:a', 'vertex:hi'],
                                reference=dict(category='SYMMETRIC'))]
                req = dict(facts=facts, modelToleranceMm=.1)
                self.assertEqual('NOT_READABLE', audit_readability(req, dict(groups=groups))['status'])
                decisions = assign_closed_coordinates(groups, facts, .1, load_profile())
                self.assertEqual('READABLE', audit_readability(req, dict(groups=groups))['status'])
                self.assertEqual(['vertex:lo', 'hole:a', 'vertex:hi'], groups[2]['points'])
                self.assertEqual(['vertex:lo', 'vertex:a', 'vertex:hi'], groups[3]['points'])
                self.assertEqual({'holes'}, {d['definingGroup'] for d in decisions})
                self.assertEqual({'vertex:lo', 'vertex:b', 'vertex:c', 'vertex:hi', 'hole:a'},
                                 {k for g in groups if not g.get('reference') for k in g['points']})


if __name__ == '__main__':
    unittest.main()
