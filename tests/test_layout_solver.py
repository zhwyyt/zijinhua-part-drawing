import copy
import unittest

from shapely.geometry import box

from zijinhua_tekla.part_drawing.layout_solver import (LayoutOverflow, search_candidates, scene_conflicts,
    solve_native_layout, scene_envelope, text_width, dimension_boxes, note_bounds)


def candidate(key, bounds):
    return dict(key=key, boxes=[dict(key=key, text=key, bounds=bounds)], lines=[], spans=[],
                dimensions=[], notes=[], radii=[])


class BoundedLayoutTests(unittest.TestCase):
    def test_later_required_text_forces_backtrack_of_earlier_choice(self):
        units = [('movable', lambda _: [candidate('movable', [0, 0, 10, 3]),
                                        candidate('movable', [20, 0, 30, 3])]),
                 ('fixed', lambda _: [candidate('fixed', [5, 0, 15, 3])])]
        result, report = search_candidates(units, scene_conflicts, lambda _: True, 20)
        self.assertEqual([20, 0, 30, 3], result[0]['boxes'][0]['bounds'])
        self.assertGreater(report['backtracks'], 0)
        self.assertEqual('SOLVED', report['status'])
        self.assertEqual((result, report), search_candidates(units, scene_conflicts, lambda _: True, 20))

    def test_budget_exhaustion_is_distinct_from_exhausted_candidates(self):
        units = [('fixed', lambda _: [candidate('fixed', [0, 0, 10, 3])]),
                 ('blocked', lambda _: [candidate('blocked', [0, 0, 10, 3])])]
        for budget, reason in ((1, 'SEARCH_BUDGET_EXHAUSTED'), (20, 'NO_FEASIBLE_CANDIDATE')):
            with self.subTest(budget=budget), self.assertRaises(LayoutOverflow) as caught:
                search_candidates(units, scene_conflicts, lambda _: True, budget)
            report = caught.exception.report
            self.assertEqual(reason, report['reason'])
            self.assertLessEqual(report['visited'], budget)
            self.assertEqual('blocked', report['blockedObject'])
            if budget > 1:
                self.assertEqual({'blocked', 'fixed'}, set(report['conflicts'][-1]['objects']))

    def test_template_fit_failure_backtracks_without_changing_expression(self):
        units = [('note', lambda _: [candidate('note', [0, 0, 100, 10]), candidate('note', [0, 0, 20, 10])])]
        result, report = search_candidates(units, scene_conflicts,
                                          lambda chosen: all(box(*c['boxes'][0]['bounds']).area < 500 for c in chosen), 10)
        self.assertEqual(20, result[0]['boxes'][0]['bounds'][2])
        self.assertEqual(2, report['visited'])


def dimension(key='holes-0', coordinates=None, side=-1, distance=12, **extra):
    coordinates = coordinates or [[0, 0], [100, 0]]
    values = [b[0]-a[0] for a,b in zip(coordinates,coordinates[1:])]
    return dict(key=key,axis='x',role='location',coordinates=coordinates,
                points=[f'vertex:{i}' for i in range(len(coordinates))],up=[0,side],distance=distance,
                values=values,displayValues=values,combine=False,combineRuns=[],**extra)


class NativeCandidateTests(unittest.TestCase):
    def test_native_leader_occupancy_contains_measured_sdk_and_dxf_bounds(self):
        # Saved/reopened live-02: native Text + independently exported glyphs,
        # relative to the actual insertion point. Ordinary font bounds failed.
        cases = [('6×D26(56×26)', [-117.238184,-13.1,117.238184,13.1],
                  [-115.522052,-19.849932,73.742103,12.926331]),
                 ('45°  d=12  c=2', [-125.569483,-13.1,125.569483,13.1],
                  [-124.592607,-12.926330,66.903813,12.926331])]
        for text,sdk,dxf in cases:
            with self.subTest(text=text):
                planned=box(*note_bounds(text,[0,0],25,leader=True))
                self.assertTrue(planned.covers(box(*sdk)))
                self.assertTrue(planned.covers(box(*dxf)))

    def solve(self, dimensions, groups, *, fits=lambda _: True, budget=4096):
        return solve_native_layout(dimensions,[],[],dict(groups=groups),box(0,0,100,50),
            dict(bounds=[0,0,100,50],unit=1,viewScale=1,decimals=0,padding=5,fits=fits,budget=budget))

    def test_hole_chain_uses_allowed_opposite_side_when_lower_band_is_unavailable(self):
        d=dimension(); g=dict(key=d['key'],side=-1,allowedSides=[-1,1],lane=12)
        before=copy.deepcopy((d,g))
        result=self.solve([d],[g],fits=lambda chosen:scene_envelope(chosen,[0,0,100,50],0)[1]>=-10)
        moved=result[0][0]
        self.assertEqual([0,1],moved['up'])
        self.assertEqual(d['coordinates'],moved['coordinates'])
        self.assertEqual(d['values'],moved['values'])
        self.assertEqual(before,(d,g))
        g['allowedSides']=[-1]
        with self.assertRaises(LayoutOverflow) as caught:
            self.solve([d],[g],fits=lambda chosen:scene_envelope(chosen,[0,0,100,50],0)[1]>=-10)
        self.assertEqual('holes-0',caught.exception.report['blockedObject'])

    def test_native_note_conflict_backtracks_dimension_layer_without_changing_anchors(self):
        d=dimension();g=dict(key=d['key'],side=-1,allowedSides=[-1],lane=12)
        note=dict(key='fixed-note',kind='thickness',text='FIXED',height=2.5,at=[50,-12],tip=None)
        moved,notes,_,report=solve_native_layout([d],[note],[],dict(groups=[g]),box(0,0,100,50),
            dict(bounds=[0,0,100,50],unit=1,viewScale=1,decimals=0,padding=5,fits=lambda _:True))
        self.assertGreater(report['backtracks'],0)
        self.assertGreater(moved[0]['distance'],d['distance'])
        self.assertEqual(d['coordinates'],moved[0]['coordinates'])
        self.assertEqual(note,notes[0])

    def test_column_members_move_together_and_overall_stays_outside(self):
        a=dimension('a',[[0,0],[20,0]],side=1,distance=62,columnGroup='column')
        b=dimension('b',[[40,0],[60,0]],side=1,distance=72,columnGroup='column')
        total=dimension('overall',side=1,distance=60);total['role']='overall'
        dims=[a,b,total]
        groups=[dict(key=d['key'],side=1,allowedSides=[1],lane=12) for d in dims]
        moved,_,_,report=self.solve(dims,groups)
        self.assertEqual(moved[0]['distance'],moved[1]['distance'])
        self.assertGreater(moved[2]['distance'],moved[1]['distance'])
        self.assertEqual(['a','b','overall'],[d['key'] for d in moved])
        self.assertEqual('SOLVED',report['status'])

    def test_crowded_text_cannot_be_fixed_by_deleting_an_interval(self):
        d=dimension(coordinates=[[0,0],[.1,0],[.2,0],[.3,0]])
        before=copy.deepcopy(d)
        with self.assertRaises(LayoutOverflow) as caught:
            self.solve([d],[dict(key=d['key'],side=-1,allowedSides=[-1,1],lane=12)])
        self.assertEqual(before,d)
        self.assertIn('CHAIN_TEXT_OVERLAP',{e['code'] for e in caught.exception.report['conflicts']})

    def test_font_metrics_distinguish_wide_and_narrow_text_and_rotate_dimension_box(self):
        self.assertGreater(text_width('WWW',25),text_width('iii',25)*2)
        d=dimension(side=1,distance=12)
        x=dimension_boxes(d,2.5,0)[0]
        d.update(axis='y',up=[1,0],coordinates=[[0,0],[0,100]])
        y=dimension_boxes(d,2.5,0)[0]
        self.assertAlmostEqual(x['bounds'][2]-x['bounds'][0],y['bounds'][3]-y['bounds'][1])
        self.assertAlmostEqual(x['bounds'][3]-x['bounds'][1],y['bounds'][2]-y['bounds'][0])
