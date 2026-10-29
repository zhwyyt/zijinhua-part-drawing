import copy
import math
import unittest

import test_native_layout
from test_native_material import plate_with_hole
from zijinhua_tekla.part_drawing.expression_coverage import audit_expression_coverage
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import bolt_display, build_layout, combine_runs


def plate():
    raw = plate_with_hole(); batch = build_native_expression_requirements(raw)
    return batch['parts'][0], batch['geometryAudit']['parts'][0], raw['parts'][0]


class CombineRunTests(unittest.TestCase):
    def test_tekla_combines_three_or_more_equal_consecutive_intervals_only(self):
        self.assertEqual([[1, 4]], combine_runs([60, 90, 90, 90, 60], 3, .1))
        self.assertEqual([], combine_runs([73, 40, 40, 73], 3, .1))
        self.assertEqual([[0, 3], [4, 7]], combine_runs([5, 5, 5, 9, 2, 2, 2], 3, .1))

    def test_witnesses_inside_a_combined_run_are_not_drawn(self):
        req, geo, raw = test_native_layout.NativeLayoutTests().shoulder_data()
        for key, x in (('c', 113), ('d', 133)):
            req['facts']['points']['hole:'+key] = [x, 161]
            req['facts']['holes'].append(dict(id=key, pointId='hole:'+key, kind='ROUND', diameter=26))
        req['facts']['points']['hole:b'] = [93, 161]
        req['requirements'], req['groups'] = test_native_layout._requirements(req['source']['partPosition'], 10, req['facts'], {})
        plan = build_layout(req, geo, raw)
        holes = next(d for d in plan['dimensions'] if d['key'] == 'holes-0')
        self.assertEqual([18, 55, 20, 20, 20, 75, 18], [round(v, 5) for v in holes['values']])
        self.assertEqual([[2, 5]], holes['combineRuns'])
        starts = [c['start'][0] for c in plan['curves'] if c['role'] == 'witness' and c['feature'] == 'holes-0']
        self.assertEqual([0, 18, 73, 133, 208, 226], starts)


class DisplayTests(unittest.TestCase):
    def test_display_rounds_half_away_from_zero_like_tekla(self):
        from zijinhua_tekla.part_drawing.expression_coverage import display_round
        self.assertEqual([217, 77, 76, 24, -3], [display_round(v, 0) for v in (216.5, 76.5, 76.4999, 23.8, -2.5)])
        self.assertEqual(143.7, display_round(143.73, 1))

    def test_exact_half_accepts_either_tekla_rounding_but_nothing_else(self):
        from zijinhua_tekla.part_drawing.expression_coverage import display_matches
        self.assertTrue(display_matches(52, 52.5, 0))
        self.assertTrue(display_matches(53, 52.5, 0))
        self.assertFalse(display_matches(52, 52.6, 0))
        self.assertTrue(display_matches(53, 52.6, 0))
        self.assertFalse(display_matches(51, 52.5, 0))

    def test_dimension_values_display_as_integers_and_carry_display_contract(self):
        req, geo, raw = plate()
        plan = build_layout(req, geo, raw)
        self.assertEqual(dict(decimals=0, combineFormat='NumberOfItemsTimesLength', combineMinimum=3), plan['display'])
        for d in plan['dimensions']:
            self.assertEqual([math.floor(v+.5) for v in d['values']], d['displayValues'])

    def test_radius_uses_native_dimension_through_real_arc_points(self):
        req, geo, raw = test_native_layout.NativeLayoutTests().shoulder_data()
        plan = build_layout(req, geo, raw, scale=2)
        self.assertEqual(2, len(plan['radii']))
        self.assertFalse([n for n in plan['notes'] if n['kind'] == 'radius'])
        for r in plan['radii']:
            arc = next(s for s in req['facts']['segments'] if s['id'] == r['feature'])
            for point in r['points']:
                self.assertAlmostEqual(arc['radius'], sum((point[i]-arc['center'][i])**2 for i in (0, 1))**.5)
        texts = [o['text'] for o in plan['observations']['objects'] if o['kind'] == 'radius']
        self.assertEqual(['R10', 'R10'], texts)


class BoltSymbolTests(unittest.TestCase):
    def test_round_bolt_hole_uses_the_slot_form_with_zero_extension(self):
        req, geo, raw = plate()
        plan = build_layout(req, geo, raw)
        note = next(n for n in plan['notes'] if n['kind']=='hole_callout')
        self.assertEqual('1*D10(10x10)', note['text'])
        self.assertEqual(['SYMBOL'], [b['display'] for b in plan['bolts']])

    def test_group_with_a_position_lost_to_cutting_stays_hidden(self):
        req, geo, raw = plate()
        facts = copy.deepcopy(req['facts'])
        self.assertEqual(['SYMBOL'], [b['display'] for b in bolt_display(facts, raw)])
        hole = facts['holes'][0]['pointId']
        facts['sourceFeatureRefs'][hole] = facts['sourceFeatureRefs'][hole] + ['#/parts/0/operations/value/0']
        self.assertEqual([('HIDDEN', [])], [(b['display'], b['holes']) for b in bolt_display(facts, raw)])

    def test_coverage_accepts_symbol_for_shape_and_cross_but_not_a_wrong_diameter(self):
        req, geo, raw = plate()
        plan = build_layout(req, geo, raw)
        self.assertEqual('PLAN_COMPLETE', plan['coverage']['status'])
        observations = copy.deepcopy(plan['observations'])
        symbol = next(o for o in observations['objects'] if o['kind'] == 'bolt_symbol')
        symbol['diameter'] += 2
        self.assertEqual('FAIL', audit_expression_coverage(req, observations, display_decimals=0)['status'])


if __name__ == '__main__':
    unittest.main()
