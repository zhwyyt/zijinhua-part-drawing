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
from zijinhua_tekla.part_drawing.independent_layout.boundary_planning import _paths, check_boundary_placements
from zijinhua_tekla.part_drawing.independent_layout.contracts import digest, seal
from zijinhua_tekla.part_drawing.independent_layout.semantic_coverage import audit_semantics
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

    def test_short_witness_candidate_preserves_dimension_and_only_clips_extension(self):
        d=next(o for o in adapt_expressions(self.f['source'])['objects'] if o['objectId']=='dimension/boundary-0-0')
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',return_value=40):
            full=_dimension_candidates(d,[0,-315,1818,75],2,25,None)
            both=_dimension_candidates(d,[0,-315,1818,75],2,25,None,short_witnesses=True)
        self.assertEqual(len(full)*2,len(both))
        for original,normal,short in zip(full,both[::2],both[1::2]):
            self.assertTrue(original['lines'].equals(normal['lines']))
            for field in ('key','axis','side','level','distance','ordinate'):
                self.assertEqual(original[field],short[field])
            self.assertTrue(original['text'].equals(short['text']))
            self.assertLess(short['lines'].length,original['lines'].length)
            self.assertGreater(short['cost'],original['cost'])

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

    def test_second_real_plate_preserves_short_intervals_and_six_visible_witnesses(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-complete-9p15.json'
        self.f=json.loads(path.read_text('utf-8'))
        result=self.audit()
        self.assertEqual('PL20*238',result['profile'])
        self.assertEqual(2,sum(b['text']=='3' for b in result['displayBindings']))
        self.assertEqual(6,sum(len(r.get('witnesses',[])) for r in self.f['saved']['primitives']))
        bad=copy.deepcopy(self.f['saved'])
        row=next(r for r in bad['primitives'] if 'witnesses' in r)
        row['witnesses'][0]['data']['StartPoint']['X']+=1
        with self.assertRaisesRegex(ContractError,'TRIAL_WITNESS'):self.audit(bad)
        bad=copy.deepcopy(self.f['saved'])
        row=next(r for r in bad['primitives'] if 'witnesses' in r)
        row['data']['Attributes']['ExtensionLine']='No'
        with self.assertRaisesRegex(ContractError,'TRIAL_WITNESS'):self.audit(bad)
        bad=copy.deepcopy(self.f['saved'])
        witness=next(r['witnesses'][0] for r in bad['primitives'] if 'witnesses' in r)
        display=next(s for v in bad['page']['presentation'] for s in v['Primitives'] if s.get('Id')==witness['objectId'])
        display['Pen']['LineType']=2
        with self.assertRaisesRegex(ContractError,'TRIAL_WITNESS_DISPLAY'):self.audit(bad)

    def test_second_plate_fixes_source_sides_and_direct_interval_and_keeps_584_placements(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-complete-9p15.json'
        second=json.loads(path.read_text('utf-8'))
        # ASCII FangSong advances in the live evidence are half-em. Stub only the
        # platform font access; exercise complete source adaptation and selection.
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial.TTFont'), \
             patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',
                   side_effect=lambda font,text,height:len(text)*height*.7+height*.6):
            plan=plan_complete_trial(second['source'],path,[150,400,2830,1900])
            base=plan_complete_trial(self.f['source'],path,[150,400,2830,1900])
        self.assertEqual(1,plan['scale'])
        self.assertEqual('INFEASIBLE',plan['attempts'][0]['status'])
        self.assertEqual('OPTIMAL',plan['attempts'][1]['status'])
        expressions=adapt_expressions(second['source'],boundary_paths=True)
        dimensions={d['objectId']:d for d in expressions['objects'] if d['kind']=='DIMENSION'}
        rows={p['key']:p for p in plan['placements']}
        for key,side,values in [('boundary-0-0',1,[401,69,93]),
                                ('boundary-0-1',-1,[11,502,50]),
                                ('boundary-0-3',1,[420,50,93])]:
            self.assertEqual(side,rows['dimension/'+key]['side'])
            self.assertEqual(values,dimensions['dimension/'+key]['displayValues'])
        self.assertEqual('independent-expressions.r3',plan['expressionAdapterVersion'])
        with self.assertRaisesRegex(ContractError,'BOUNDARY_SIDE'):
            check_boundary_placements(expressions,second['plan']['placements'])
        self.assertEqual(self.f['plan']['primitives'],base['primitives'])

    def test_missing_shared_endpoint_or_relaxed_side_cannot_be_resealed_as_complete(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-complete-9p15.json'
        source=json.loads(path.read_text('utf-8'))['source']
        expressions=adapt_expressions(source,boundary_paths=True)
        for field,value in [('points',['vertex:4','vertex:3','vertex:0']),('allowedSides',[-1,1])]:
            bad=copy.deepcopy(expressions)
            group=next(g for g in bad['scheme']['groups'] if g['key']=='boundary-0-3')
            group[field]=value;bad['schemeDigest']=digest(bad['scheme'])
            with self.assertRaisesRegex(ContractError,'BOUNDARY_SCHEME_MISMATCH'):
                audit_semantics(source,bad)
        bad=copy.deepcopy(expressions)
        obj=next(o for o in bad['objects'] if o['objectId']=='dimension/boundary-0-3')
        obj['points'].remove('vertex:2')
        self.assertEqual('UPSTREAM_CONTRACT_GAP',audit_semantics(source,bad)['status'])

    def test_corrected_native_page_has_direct_50_and_cannot_forge_placement_side(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-complete-9p15-corrected.json'
        self.f=json.loads(path.read_text('utf-8'))
        result=self.audit()
        self.assertEqual(18,len(result['displayBindings']))
        group=next(o for o in result['observations'][-1] if o['key']=='dimension/boundary-0-3')
        self.assertEqual(['420','50','93'],[i['text'] for i in group['intervals']])
        direct=group['intervals'][1]
        self.assertEqual(['vertex:3','vertex:2'],direct['sourceRefs'])
        self.assertAlmostEqual(49.9106487,direct['rawProjectedValue'],places=6)
        bad=copy.deepcopy(self.f['plan'])
        bad.pop('digest')
        primitive=next(p for p in bad['primitives'] if p['key']=='dimension/boundary-0-0')
        primitive['up']=[-v for v in primitive['up']]
        with self.assertRaisesRegex(ContractError,'BOUNDARY_SIDE'):
            verify_complete_trial(self.f['source'],seal(bad),[self.f['saved']]*3)

    def test_boundary_paths_translate_mirror_and_reverse_without_using_part_identity(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-complete-9p15.json'
        plan=json.loads(path.read_text('utf-8'))['source']['requirements']
        def sides(facts,axis):
            projected={s['id'] for s in facts['segments']
                       if abs(s['end'][axis]-s['start'][axis])>plan['modelToleranceMm']}
            return {k:p['side'] for p in _paths(facts,axis,plan['modelToleranceMm'])
                    for k in p['segments'] if k in projected}
        facts=plan['facts'];expected=[sides(facts,a) for a in (0,1)]
        for sx,sy in ((1,1),(1,-1),(-1,1)):
            changed=copy.deepcopy(facts)
            for edge in changed['segments']:
                for key in ('start','end'):
                    x,y=edge[key];edge[key]=[sx*x+1000,sy*y-300]
            for axis in (0,1):
                sign=(sy,sx)[axis]
                self.assertEqual({k:v*sign for k,v in expected[axis].items()},sides(changed,axis))
        changed=copy.deepcopy(facts)
        changed['boundaryLoops'][0]['segments'].reverse()
        for edge in changed['segments']:
            edge['start'],edge['end']=edge['end'],edge['start']
            edge['startPoint'],edge['endPoint']=edge['endPoint'],edge['startPoint']
        self.assertEqual(expected,[sides(changed,a) for a in (0,1)])


if __name__=='__main__':
    unittest.main()
