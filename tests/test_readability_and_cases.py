import unittest
from pathlib import Path

from zijinhua_tekla.part_drawing.planning_cases import compare_case, load_cases
from zijinhua_tekla.part_drawing.readability import audit_readability, load_profile

CASES = Path(__file__).parent / 'fixtures' / 'planning_cases' / 'shop-20261021'


def requirements():
    points = {'vertex:0': [0, 0], 'vertex:1': [100, 0], 'vertex:2': [100, 50], 'vertex:3': [0, 50],
              'vertex:4': [30, 50], 'hole:a': [20, 25], 'hole:b': [50, 25], 'hole:c': [80, 25]}
    return dict(facts=dict(points=points, bounds=[0, 0, 100, 50]), modelToleranceMm=0.1)


def group(key, points, axis=0, **extra):
    return dict(key=key, axis=axis, points=points, **extra)


def codes(scheme_groups):
    return sorted(i['code'] for i in audit_readability(requirements(), dict(groups=scheme_groups))['issues'])


class ReadabilityGateTests(unittest.TestCase):
    def test_profile_v1_carries_confirmed_shop_conventions(self):
        profile = load_profile()
        self.assertEqual(['OPPOSITE_EXTREME'], profile['datum']['localDatums'])
        self.assertEqual(3, profile['pitch']['minIntervals'])
        self.assertEqual(0, profile['display']['decimals'])
        self.assertFalse(profile['directReadLengths']['tangentFlats'])
        self.assertEqual('BLOCKING', profile['gate']['mode'])

    def test_edge_to_edge_hole_chain_with_overall_reference_is_readable(self):
        self.assertEqual([], codes([
            group('overall', ['vertex:0', 'vertex:1'], reference=dict(category='OVERALL')),
            group('holes', ['vertex:0', 'hole:a', 'hole:b', 'hole:c', 'vertex:1'])]))

    def test_interior_contour_datum_is_reported(self):
        self.assertIn('RD_DATUM_SPREAD', codes([group('shoulder', ['vertex:4', 'hole:b'])]))

    def test_chain_through_shared_hole_reaches_extreme(self):
        found = codes([group('edge', ['vertex:0', 'hole:a']), group('pitch', ['hole:a', 'hole:b', 'hole:c']),
                       group('end', ['hole:c', 'vertex:1'])])
        self.assertNotIn('RD_DATUM_SPREAD', found)
        self.assertNotIn('RD_HOLE_CHAIN_OPEN', found)

    def test_open_hole_chain_is_reported(self):
        self.assertIn('RD_HOLE_CHAIN_OPEN', codes([group('holes', ['vertex:0', 'hole:a', 'hole:b'])]))

    def test_loop_without_reference_is_reported(self):
        self.assertIn('RD_UNDECLARED_REDUNDANCY', codes([
            group('top', ['vertex:0', 'hole:a', 'hole:b']), group('bottom', ['vertex:0', 'hole:b'])]))

    def test_two_interval_pitch_is_below_minimum(self):
        found = codes([group('holes', ['vertex:0', 'hole:a', 'hole:b', 'hole:c', 'vertex:1'],
                             combined=dict(intervalCount=2))])
        self.assertIn('RD_COMBINE_BELOW_MIN', found)


class GateBlockingTests(unittest.TestCase):
    def test_unreadable_scheme_blocks_the_plan(self):
        import test_native_layout
        from unittest import mock
        req, geo, raw = test_native_layout.NativeLayoutTests().shoulder_data()
        report = dict(status='NOT_READABLE', mode='BLOCKING', issues=[dict(code='RD_DATUM_SPREAD')])
        with mock.patch('zijinhua_tekla.part_drawing.native_layout.audit_readability', return_value=report):
            from zijinhua_tekla.part_drawing.native_layout import build_layout
            plan = build_layout(req, geo, raw, scale=2)
        self.assertEqual('PLAN_COMPLETE', plan['coverage']['status'])
        self.assertEqual('BLOCKED', plan['status'])


class ShopCaseTests(unittest.TestCase):
    def setUp(self):
        self.reports = {c['id']: compare_case(c) for c in load_cases(CASES)}

    def test_all_shop_cases_load_and_blocked_ones_keep_their_reason(self):
        self.assertEqual(10, len(self.reports))
        blocked = [r for r in self.reports.values() if r['status'] == 'BLOCKED']
        self.assertEqual(3, len(blocked))
        self.assertTrue(all(r['blockedBy'] for r in blocked))

    def test_confirmed_hole_chain_rule_reproduces_shop_hole_cases(self):
        for case in ('shop-KB5-1', 'shop-T2-12P-111', 'shop-T2-13B-572'):
            report = self.reports[case]
            self.assertEqual([], report['missing'], case)
            self.assertEqual([], report['extra'], case)
            self.assertEqual('READABLE', report['readability'], case)

    def test_shop_agreement_does_not_regress(self):
        matched = sum(len(r['matched']) for r in self.reports.values() if r['status'] == 'COMPARED')
        # 26 since the user-confirmed same-side merge (T2-11P-226 top and right).
        self.assertGreaterEqual(matched, 26)


if __name__ == '__main__':
    unittest.main()
