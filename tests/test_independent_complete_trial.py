"""Full native trial regressions, including the real 2.808 mm projected interval."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from shapely.geometry import LineString, box

from zijinhua_tekla.part_drawing.independent_layout.complete_trial import _conflict, _dimension_candidates, _solve, plan_complete_trial
from zijinhua_tekla.part_drawing.independent_layout.complete_trial_readback import verify_complete_trial
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions
from test_independent_expressions import source as hole_source


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/part_drawing/independent-complete-trial.json').read_text('utf-8'))


class CompleteTrialTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture()

    def audit(self, saved=None):
        return verify_complete_trial(self.f['source'],self.f['plan'],
                                     [self.f['saved'],saved or self.f['saved'],saved or self.f['saved']])

    def test_actual_persisted_intervals_contour_label_and_thickness(self):
        report=self.audit()
        self.assertEqual('NATIVE_COMPLETE_PREVIEW',report['status'])
        self.assertEqual(17,len(report['displayBindings']))
        self.assertEqual('PL8*150',report['profile'])
        self.assertLess(report['maximumContourResidual'],1e-6)
        intervals=[d for o in report['observations'][-1] for d in o.get('intervals',[])]
        self.assertEqual(16,len(intervals))
        short=next(d for d in intervals if d['text']=='3')
        self.assertAlmostEqual(2.808309869,short['rawProjectedValue'],places=6)
        self.assertFalse(report['productionEligible'])

    def test_missing_group_and_wrong_value_do_not_pass(self):
        bad=copy.deepcopy(self.f['saved']);bad['primitives'].pop()
        with self.assertRaisesRegex(ContractError,'TRIAL_READBACK_COVERAGE'):self.audit(bad)
        bad=copy.deepcopy(self.f['saved'])
        row=next(r for r in bad['primitives'] if r['children'])
        row['children'][0]['data']['Value'][0]['unformatted']='9999'
        with self.assertRaisesRegex(ContractError,'TRIAL_VALUE'):self.audit(bad)

    def test_shifted_real_anchor_and_changed_reopen_fail(self):
        bad=copy.deepcopy(self.f['saved'])
        next(r for r in bad['primitives'] if r['children'])['children'][0]['data']['StartPoint']['X']+=1
        with self.assertRaisesRegex(ContractError,'TRIAL_ANCHORS'):self.audit(bad)
        with self.assertRaisesRegex(ContractError,'TRIAL_PERSISTENCE'):
            verify_complete_trial(self.f['source'],self.f['plan'],[self.f['saved'],self.f['saved'],bad])

    def test_holes_cannot_be_silently_dropped(self):
        with self.assertRaisesRegex(ContractError,'TRIAL_SCOPE'):
            plan_complete_trial(hole_source(),'unused.ttf',[0,0,1000,1000])

    def test_group_distance_uses_first_actual_anchor_and_integer_display(self):
        d=next(o for o in adapt_expressions(self.f['source'])['objects'] if o['objectId']=='dimension/boundary-0-0')
        calls=[]
        def width(font,text,height):calls.append(text);return 40
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',side_effect=width):
            rows=_dimension_candidates(d,[0,-315,1818,75],2,25,None)
        chosen=next(c for c in rows if c['side']==-1 and c['level']==1)
        self.assertAlmostEqual(chosen['ordinate'],d['coordinates'][0][1]/2-chosen['distance'])
        self.assertIn('429',calls);self.assertNotIn('429.0',calls)

    def test_outer_layer_and_text_crossing_are_hard_conflicts(self):
        a=dict(key='inner',text=box(0,0,2,2),lines=LineString([(0,-1),(2,-1)]),axis=0,side=1,level=2,overall=False)
        b=dict(key='outer',text=box(0,10,2,12),lines=LineString([(0,9),(2,9)]),axis=0,side=1,level=1,overall=True)
        self.assertTrue(_conflict(a,b,.1))
        b['level']=3;self.assertFalse(_conflict(a,b,.1))
        b['lines']=LineString([(-1,1),(3,1)]);self.assertTrue(_conflict(a,b,.1))

    def test_joint_solver_rejects_greedy_first_and_is_deterministic(self):
        def row(key,x,cost):
            text=box(x,10,x+2,12)
            return dict(key=key,text=text,lines=LineString(),extent=text.bounds,cost=cost)
        rows=[row('a',0,0),row('a',5,1),row('b',0,0)]
        args=(rows,['a','b'],[0,0,100,100],1,LineString([(0,0),(10,0)]))
        a,report=_solve(*args);b,_=_solve(*args)
        self.assertEqual('OPTIMAL',report['status'])
        self.assertEqual([1,0],[r['cost'] for r in a])
        self.assertEqual([r['extent'] for r in a],[r['extent'] for r in b])


if __name__=='__main__':
    unittest.main()
